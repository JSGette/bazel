from pathlib import Path
import gzip, hashlib, importlib.util, json, shutil, subprocess, tarfile

repo = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
work = Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25')
first_root, steady_root = work / 'agent', work / 'agent-steady'
top_root = work / 'agent-toplevel'
out = repo / 'docs/jsgette/measurements/skycache-large-agent-2026-10-25'
task = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-25/based-on-the-latest-architecture-of')
helpers = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work')
first, steady, build = [json.loads(path.read_text()) for path in
    [first_root / 'results.json', steady_root / 'results.json', work / 'build.json']]
top = json.loads((top_root / 'results.json').read_text())
assert build['exit_code'] == 0 and build['test_cases'] == 573
assert first['pilot']['passed'] and top['status'] == 'passed'
assert first['status'] == 'failed' and first['failure'] == 'KeyboardInterrupt()'
assert (first_root / 'pilot-stop-reason.txt').exists()
assert steady['status'] == 'failed'
assert steady['runs'][-1]['label'] == 'pilot-minimal-remote'
assert steady['failure'] == repr(AssertionError(steady['runs'][-1]['local-skycache.json']))
assert steady['runs'][-1]['local-skycache.json']['writes'] == 15
for root, report in [(first_root, first), (steady_root, steady), (top_root, top)]:
    assert not report['cleanup_errors']
    assert all(report[key] for key in ['main_workspace_unchanged', 'main_lock_unchanged',
                                     'private_workspace_unchanged', 'common_tools_unchanged'])
assert all(steady[key] for key in ['original_primer_receipts_unchanged',
                                 'original_local_seed_unchanged', 'original_cas_unchanged'])
assert all(top[key] for key in ['original_primer_receipts_unchanged', 'original_local_seed_unchanged',
    'original_cas_unchanged', 'copied_frozen_receipts_unchanged', 'copied_frozen_local_seed_unchanged',
    'steady_report_unchanged', 'first_report_unchanged'])
spec = importlib.util.spec_from_file_location('wire_inventory', repo / 'tools/skycache/remote_read_benchmark.py')
wire = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wire)
for root, report in [(steady_root, steady), (top_root, top)]:
    assert {part: wire.tree_inventory(root / 'cache-primer/leased-v2' / part)
            for part in ['objects', 'publications-v4']} == report['frozen_seed_inventory']
    assert {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((root / 'catalog-primer').glob('receipt-*.pb'))} == report['primer_receipts']
selected = [(steady_root, row) for row in steady['runs'] if row['label'].startswith('pilot-minimal-')]
selected += [(top_root, row) for row in top['runs']]
assert len(selected) == 6
rows = []
for case_root, row in selected:
    assert row['validation'] == 'passed' and row['exit_code'] == 0 and not row['guard_errors']
    assert row['graph_nodes_after_clean'] == row['graph_edges_after_clean'] == 0
    assert not row['execroot_after_clean'] and not row['action_cache_after_clean']
    assert row['server_pid_before_build'] == row['server_pid_after_build']
    assert {key: value['digest'] for key, value in row['bep_binaries'].items()} == steady['expected_binaries']
    assert not row['remote_skips'] and not row['remote_warnings']
    methods = row['grpc']['method_attempt_counts']
    if row['mode'] == 'toplevel':
        assert {key: value['sha256'] for key, value in row['output_inventory']['binaries'].items()} == steady['expected_binaries']
    else:
        assert not row['output_inventory']['binaries']
    local = row.get('local-skycache.json', {})
    if row['arm'] != 'vanilla':
        assert row['analysis_hits'] == 17 and row['execution_hits'] == 11
        assert local['read_limit_skips'] == local['scan_limit_hits'] == 0
        if row['arm'] == 'local':
            assert local['writes'] == 0
        assert not local['failed'] and not local['rejected_publications']
        assert row['output_inventory']['archives'] == row['output_inventory']['exports'] == 0
        assert methods.get('build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0) == 0
        if row['mode'] == 'minimal':
            assert row['grpc']['bytestream_read_calls'] == 0
    if row['arm'] == 'remote':
        assert row['remote']['materialized_publications'] == 0
        assert row['remote']['downloaded_bytes'] > 0
    item = {key: row[key] for key in ['label', 'mode', 'arm', 'seconds', 'peak_pss_kib',
        'worker_peak_pss_kib', 'wire_download_bytes', 'wire_upload_bytes', 'analysis_hits',
        'execution_hits', 'remote', 'server_proc_io']}
    item.update(local=local, action_cache_rpc_count=methods.get(
        'build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0),
        output_read_bytes=row['grpc']['bytestream_read_bytes'],
        output_read_calls=row['grpc']['bytestream_read_calls'])
    profile_path = case_root / (row['label'] + '.profile.gz')
    profile = json.loads(gzip.decompress(profile_path.read_bytes()))
    assert profile['otherData']['build_id'] in (case_root / (row['label'] + '.log')).read_text()
    item['profile_spans_seconds'] = {}
    for name in ['compute main repo mapping', 'runAnalysisAndExecutionPhase', 'LocalSkycacheModule.beforeCommand']:
        spans = [event for event in profile['traceEvents'] if event.get('ph') == 'X' and event.get('name') == name]
        assert len(spans) <= 1
        item['profile_spans_seconds'][name] = spans[0]['dur'] / 1e6 if spans else None
    rows.append(item)
out.mkdir(exist_ok=False)
def save(name, value):
    (out / name).write_text(json.dumps(value, indent=2) + '\n')
save('comparison.json', {'passed': True, 'sample_count_per_cell': 1, 'rows': rows,
    'scope': 'Exploratory fresh-JVM, empty-graph comparisons from three completed minimal pilots and three toplevel continuation samples; not medians. Planned repetitions stopped after expensive initial reads. The steady minimal harness zero-write assertion was too strict for remote cached-only put dedup; its build/output checks passed. Toplevel continuation records remote writes and preserves local zero-write checks.',
    'memory_scope': 'Client process-tree PSS and backend process-tree PSS sampled independently; do not sum independent peaks. Page cache excluded.',
    'io_scope': 'Cumulative fresh Bazel server including startup/clean/dump; rchar includes page-cache hits and all read-like I/O, not network payload.',
    'profile_scope': 'Skymeld analysis/execution span is combined, not exclusive analysis. Spans can overlap. Post-command publication may be outside profile.',
    'warmup_scope': 'Fresh backend each remote run; one full read/idempotent-commit warmup outside timer; no WAN latency/bandwidth shaping.',
    'output_sha256': steady['expected_binaries'], 'first_use': first['pilot'],
    'initial_upload': steady['original_upload'], 'prepared_cache': steady['primer_inventory']})
save('verification.json', {'passed': True, 'java_test_cases': 573, 'java_test_targets': len(build['tests']),
    'completed_initial_builds': 5, 'completed_stabilization_builds': 2,
    'completed_steady_builds': 6, 'outputs_match': True, 'clean_state_checks_passed': True,
    'source_and_seed_preservation_passed': True, 'local_steady_writes': 0,
    'remote_steady_writes': {row['mode']: row['local']['writes'] for row in rows if row['arm'] == 'remote'},
    'intentional_stops': {'agent': (first_root / 'pilot-stop-reason.txt').read_text()},
    'harness_correction': 'agent-steady remote build passed; strict zero-write assertion rejected15records. Toplevel continuation permits and records remote writes. Raw failure retained. Only the minimal pilot records received a separate byte-identity audit.',
    'product_commit': 'b7cf340bf0815086cfa213f4a8bd85f05bd35654',
    'product_tree': build['source_tree'], 'binary_sha256': build['binary_sha256'],
    'vanilla_sha256': steady['binary_sha256']['vanilla'],
    'source_commit': steady['source_revision']})
for source, name in [(work / 'build.json', 'build.json'), (first_root / 'results.json', 'first-use.json'),
                     (steady_root / 'results.json', 'steady.json'),
                     (top_root / 'results.json', 'toplevel.json'),
                     (first_root / 'pilot-remote-process-io.json', 'first-use-io-snapshot.json')]:
    shutil.copy2(source, out / name)
for name in ['agent_duplicate_records.py', 'agent_duplicate_records.json']:
    shutil.copy2(work / name, out / name)
(out / 'harness').mkdir()
for name in ['benchmark_agent_remote.py', 'benchmark_agent_remote_steady.py', 'benchmark_agent_remote_toplevel.py', 'build_large_remote.py',
             'stop_after_agent_pilot.py', 'stop_after_agent_steady.py', 'collect_large_agent.py']:
    shutil.copy2(task / name, out / 'harness' / name)
for name in ['datadog-wide.py', 'datadog-concurrent.py', 'datadog-download-modes.py', 'datadog-clean-benchmark.py']:
    shutil.copy2(helpers / name, out / 'harness' / name)
with tarfile.open(out / 'logs-profiles-tests.tar.gz', 'w:gz') as archive:
    for root, report in [(first_root, first), (steady_root, steady), (top_root, top)]:
        for row in report['runs']:
            if row.get('validation') != 'passed':
                continue
            label = row['label']
            for suffix in ['.log', '.profile.gz', '.samples.json', '.storage.json', '-clean.log', '-graph.log']:
                path = root / (label + suffix)
                if path.exists():
                    archive.add(path, arcname=root.name + '/' + path.name)
        for path in sorted(root.glob('warm-server-*.json')):
            archive.add(path, arcname=root.name + '/' + path.name)
        if (root / 'pilot-stop-reason.txt').exists():
            archive.add(root / 'pilot-stop-reason.txt', arcname=root.name + '/pilot-stop-reason.txt')
    archive.add(work / 'tests', arcname='tests')
    archive.add(work / 'build.log', arcname='build.log')
files = sorted(path for path in out.rglob('*') if path.is_file())
(out / 'SHA256SUMS').write_text(''.join(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(out)) + '\n' for path in files))
print(json.dumps({'out': str(out), 'bytes': sum(path.stat().st_size for path in out.rglob('*') if path.is_file()),
                  'rows': [{key: row[key] for key in ['mode','arm','seconds','wire_download_bytes','wire_upload_bytes']} for row in rows]}, indent=2))
