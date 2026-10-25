from pathlib import Path
import hashlib
import json
import shutil
import statistics
import subprocess
import tarfile
import zipfile

repo = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
root = Path('/home/jgett/REPOS/.work/skycache-block-reads-verified-2026-10-25')
old = Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25')
workspace = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-25/based-on-the-latest-architecture-of')
out = repo / 'docs/jsgette/measurements/skycache-block-reads-agent-2026-10-25'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def install_key(path):
    with zipfile.ZipFile(path) as binary:
        return binary.read('install_base_key').decode().strip()


build = read(root / 'build.json')
pilot = read(root / 'agent-pilot/results.json')
matrix = read(root / 'agent-matrix-fixed/results.json')
first_preparation = read(root / 'agent-matrix/results.json')
assert build['exit_code'] == build['shutdown'] == 0 and build['test_cases'] == 578
commit = subprocess.check_output(['git', 'rev-parse', 'cf7456d7b7'], cwd=repo, text=True).strip()
tree = subprocess.check_output(['git', 'rev-parse', commit + '^{tree}'], cwd=repo, text=True).strip()
assert tree == build['source_tree']
assert pilot['status'] == 'failed' and not pilot['cleanup_errors']
assert pilot['runs'][0]['analysis_hits'] == pilot['runs'][0]['execution_hits'] == 0
assert pilot['runs'][0]['remote_committed'] and pilot['runs'][0]['exit_code'] == 0
assert first_preparation['status'] == 'failed' and first_preparation['failure'] == 'AssertionError()'
assert not first_preparation['cleanup_errors']
assert len(first_preparation['runs']) == 2
assert first_preparation['runs'][1]['local-skycache.json']['writes'] == 0
for report, count in [(matrix, 18)]:
    assert report['status'] == 'passed' and not report['cleanup_errors']
    assert report['measured_count'] == sum(row['measured'] for row in report['runs']) == count
    assert report['binary_sha256']['candidate'] == build['binary_sha256']
    assert all(value for key, value in report.items() if key.endswith('_unchanged'))
    for row in report['runs']:
        assert row['exit_code'] == 0 and row['validation'] == 'passed'
        assert not row['remote_skips'] and not row['guard_errors'] and not row['remote_warnings']
        assert row['graph_nodes_after_clean'] == row['graph_edges_after_clean'] == 0
        assert not row['action_cache_after_clean'] and not row['execroot_after_clean']
        assert {name: entry['digest'] for name, entry in row['bep_binaries'].items()} == report['expected_binaries']
        assert row['server_pid_before_build'] == row['server_pid_after_build']
        if row['arm'] in ('local', 'remote'):
            assert (row['analysis_hits'], row['execution_hits']) == (17, 11)
            assert not any(row['local-skycache.json'][key] for key in [
                'failed', 'read_limit_skips', 'scan_limit_hits', 'rejected_publications'])
        if row['arm'] == 'remote':
            assert row['remote']['authentication_cache_bytes'] <= 32 * 1024**2
            assert row['remote']['materialized_publications'] == 0
            assert row['remote']['reused_publications'] == 2

out.mkdir(exist_ok=False)
(out / '.gitattributes').write_text(
    '# Preserve byte-exact raw harness snapshots and their checksums.\n'
    'harness/*.py -text -whitespace\n')
for name, report in [('build', build), ('pilot', pilot), ('initial-preparation', first_preparation), ('matrix', matrix)]:
    save(out / (name + '.json'), report)

old_minimal = read(old / 'agent-steady/results.json')
old_toplevel = read(old / 'agent-toplevel/results.json')
baseline = [row for row in old_minimal['runs'] if row['label'] in {
    'pilot-minimal-' + arm for arm in ['vanilla', 'local', 'remote']}]
baseline += [row for row in old_toplevel['runs'] if row['label'] in {
    'toplevel-' + arm for arm in ['vanilla', 'local', 'remote']}]
assert len(baseline) == 6
fields = ['label', 'arm', 'mode', 'phase', 'preparation', 'validation', 'seconds', 'peak_pss_kib', 'worker_peak_pss_kib',
          'analysis_hits', 'execution_hits', 'wire_download_bytes', 'wire_upload_bytes',
          'remote', 'server_proc_io', 'local-skycache.json']
summary = []
for mode in matrix['policies']:
    for arm in matrix['arms']:
        rows = [row for row in matrix['runs'] if row['measured'] and row['mode'] == mode and row['arm'] == arm]
        times = [row['seconds'] for row in rows]
        summary.append(dict(mode=mode, arm=arm, samples=len(rows), seconds=times,
                            median_seconds=statistics.median(times),
                            median_peak_pss_kib=statistics.median(row['peak_pss_kib'] for row in rows),
                            median_rchar=statistics.median(row['server_proc_io']['rchar'] for row in rows),
                            rows=[{key: row.get(key) for key in fields} for row in rows]))
save(out / 'comparison.json', dict(
    source_commit=commit, source_tree=tree, summary=summary,
    invalid_cross_binary_pilot=[{key: row.get(key) for key in fields} for row in pilot['runs']],
    prior_single_samples=[{key: row.get(key) for key in fields} for row in baseline],
    prior_raw_statuses=dict(minimal=old_minimal['status'], minimal_failure=old_minimal.get('failure'),
                           toplevel=old_toplevel['status'],
                           accepted_minimal_failure='Completed builds and output checks passed; remote wrote 15 duplicate records and the historical zero-write harness assertion failed.'),
    caveats=['Prior implementation has one prepared sample per cell; new matrix has three.',
             'Each binary uses its own prepared cache because the Bazel install ID versions analysis keys; cache files differ.',
             'Loopback metadata backend, warm backend CAS, fresh JVM and client cache per sample.',
             'rchar includes startup/clean and all read-like I/O; it is not physical disk or network traffic.',
             'Memory is sampled process-tree PSS; worker peak separate; filesystem page cache excluded.']))

save(out / 'verification.json', dict(
    passed=True, source_commit=commit, source_tree=tree, binary_sha256=build['binary_sha256'],
    install_ids=dict(previous=install_key(old / 'bin/bazel'), current=install_key(root / 'bin/bazel')),
    test_cases=build['test_cases'], measured_samples=18,
    checks={name: {key: value for key, value in report.items() if key.endswith('_unchanged')}
            for name, report in [('invalid_cross_binary_pilot', pilot), ('matrix', matrix)]},
    expected_binaries=matrix['expected_binaries'],
    prior_minimal_report_sha256=sha(old / 'agent-steady/results.json'),
    prior_toplevel_report_sha256=sha(old / 'agent-toplevel/results.json'),
    earlier_build_failure='New test initially lacked its direct packed_store dependency; fixed before successful build.',
    earlier_preparation_failure='Both builds and output checks passed; first preparation had 1708 analysis hits, so an overstrict 17-hit assertion failed. Fixed gate applies only to final stable pass; rerun in fresh root.',
    raw_paths={'build': str(root), 'prior': str(old)}))

with tarfile.open(out / 'logs-profiles-tests.tar.gz', 'w:gz', compresslevel=9) as archive:
    for directory, prefix in [(root, 'build'), (root / 'agent-pilot', 'pilot'),
                              (root / 'agent-matrix', 'initial-preparation'),
                              (root / 'agent-matrix-fixed', 'matrix'),
                              (root.parent / 'skycache-block-reads-2026-10-25', 'initial-build-failure')]:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name not in ['results.json', 'build.json']:
                archive.add(path, arcname=prefix + '/' + path.name)
    for path in sorted((root / 'tests').rglob('test.xml')):
        archive.add(path, arcname='tests/' + str(path.relative_to(root / 'tests')))
    archive.add(root.parent / 'skycache-block-reads-2026-10-25/build.json',
                arcname='initial-build-failure/build.json')

harness = out / 'harness'
harness.mkdir()
for name in ['build_block_reads.py', 'build_block_reads_verified.py',
             'benchmark_agent_remote_optimized.py', 'benchmark_agent_remote_reprimed.py',
             'benchmark_agent_remote_reprimed_fixed.py', 'collect_block_agent.py', 'render_block_agent.py']:
    shutil.copy2(workspace / name, harness / name)
for name in ['datadog-wide.py', 'datadog-concurrent.py', 'datadog-download-modes.py', 'datadog-clean-benchmark.py']:
    shutil.copy2(Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work') / name, harness / name)
(out / 'SHA256SUMS').write_text(''.join(sha(path) + '  ' + str(path.relative_to(out)) + '\n'
    for path in sorted(out.rglob('*')) if path.is_file()))
print(json.dumps([dict(mode=row['mode'], arm=row['arm'], median_seconds=row['median_seconds'],
                      median_peak_pss_kib=row['median_peak_pss_kib'], median_rchar=row['median_rchar'])
                  for row in summary], indent=2))
