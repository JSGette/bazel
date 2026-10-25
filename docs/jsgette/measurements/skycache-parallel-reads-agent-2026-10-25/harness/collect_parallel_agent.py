"""Validate completed parallel-read measurements and freeze their exact evidence bytes."""

from pathlib import Path
import fcntl
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile


REPO = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
ROOT = Path('/home/jgett/REPOS/.work/skycache-parallel-reads-2026-10-25')
WORKSPACE = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-25/based-on-the-latest-architecture-of')
LEGACY = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work')
PRIOR = REPO / 'docs/jsgette/measurements/skycache-block-reads-agent-2026-10-25'
OUT = REPO / 'docs/jsgette/measurements/skycache-parallel-reads-agent-2026-10-25'
MODES = ('minimal', 'toplevel')
ARMS = ('vanilla', 'local', 'remote')
NAMES = {'agent', 'cluster-agent', 'process-agent', 'trace-agent', 'security-agent', 'system-probe'}
COMMON_CHECKS = {
    'main_workspace_unchanged', 'main_lock_unchanged', 'private_workspace_unchanged',
    'common_tools_unchanged', 'benchmark_binaries_unchanged', 'worker_artifacts_unchanged',
    'original_primer_receipts_unchanged', 'original_local_seed_unchanged',
    'original_cas_unchanged', 'copied_frozen_receipts_unchanged',
    'copied_frozen_local_seed_unchanged', 'steady_report_unchanged', 'first_report_unchanged',
}
PRIME_CHECKS = COMMON_CHECKS | {
    'produced_prime_local_unchanged', 'produced_prime_catalog_unchanged', 'produced_prime_cas_unchanged',
}
MATRIX_CHECKS = COMMON_CHECKS | {
    'prime_local_publication_unchanged', 'prime_catalog_unchanged', 'prime_cas_unchanged',
    'prime_frozen_local_unchanged', 'prime_frozen_catalog_unchanged', 'prime_report_unchanged',
}
ROW_FIELDS = [
    'label', 'arm', 'mode', 'phase', 'measured', 'preparation', 'repeat_number', 'validation',
    'seconds', 'peak_pss_kib', 'worker_peak_pss_kib', 'analysis_hits', 'execution_hits',
    'wire_download_bytes', 'wire_upload_bytes', 'remote', 'remote_committed', 'server_proc_io',
    'local-skycache.json', 'local-skycache-validation.json',
]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=REPO, text=True).strip()


def install_key(path):
    with zipfile.ZipFile(path) as binary:
        return binary.read('install_base_key').decode().strip()


def validate_preservation(report, required):
    assert report['status'] == 'passed' and not report.get('failure')
    assert report['cleanup_errors'] == []
    checks = {key: value for key, value in report.items() if key.endswith('_unchanged')}
    assert required <= checks.keys(), ('missing preservation checks', required - checks.keys())
    assert all(value is True for value in checks.values()), checks
    assert report['client_shutdown']['exit_code'] == 0
    assert 'worker_shutdown' in report
    return checks


def validate_row(row, report, measured=False):
    assert row['exit_code'] == 0 and row['validation'] == 'passed', row['label']
    assert not any(row[key] for key in [
        'remote_skips', 'guard_errors', 'remote_warnings', 'publication_failures', 'cache_disabled'])
    assert row['graph_nodes_after_clean'] == row['graph_edges_after_clean'] == 0
    assert not row['action_cache_after_clean'] and not row['execroot_after_clean']
    assert row['publications_preserved'] and row['clean']['exit_code'] == 0
    if row['shutdown_before'] is None:
        # Each report starts only after verifying that its output-base JVM is stopped.
        assert row is report['runs'][0]
    else:
        assert row['shutdown_before']['exit_code'] == 0
    assert row['server_pid_before_build'] == row['server_pid_after_build'] > 0
    assert math.isfinite(row['seconds']) and row['seconds'] > 0
    expected = report['expected_binaries']
    assert set(expected) == NAMES
    assert {name: value['digest'] for name, value in row['bep_binaries'].items()} == expected
    inventory = row['output_inventory']
    actual = {name: value['sha256'] for name, value in inventory['binaries'].items()}
    if row['mode'] == 'toplevel':
        assert actual == expected
    elif not row['preparation']:
        assert not actual
    assert inventory['archives'] == inventory['exports'] == 0
    binary = 'vanilla' if row['arm'] == 'vanilla' else 'candidate'
    assert row['binary_sha256'] == report['binary_sha256'][binary]
    if row['arm'] != 'vanilla':
        local = row['local-skycache.json']
        assert not any(local[key] for key in [
            'failed', 'read_limit_skips', 'scan_limit_hits', 'rejected_publications'])
    if measured:
        assert row['phase'] == 'measured' and row['measured'] is True
        assert row['preparation'] is False
        assert row['repeat_number'] in (1, 2, 3)
        assert not row['server_proc_io'].get('unavailable')
        assert row['server_proc_io']['rchar'] >= 0
        if row['arm'] in ('local', 'remote'):
            assert (row['analysis_hits'], row['execution_hits']) == (17, 11)
            assert row['grpc']['method_attempt_counts'].get(
                'build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0) == 0
            if row['mode'] == 'minimal':
                assert row['grpc']['bytestream_read_calls'] == 0
            if row['arm'] == 'local':
                assert local['writes'] == 0
        else:
            assert (row['analysis_hits'], row['execution_hits']) == (0, 0)
        if row['arm'] != 'remote':
            assert row['wire_download_bytes'] == row['wire_upload_bytes'] == 0
        else:
            remote = row['remote']
            assert row['remote_discovered'] > 0 and remote['downloaded_bytes'] > 0
            assert 1 <= remote['peak_cached_reads'] <= 8
            assert 0 <= remote['authentication_cache_bytes'] <= 32 * 1024**2
            for key in ['cached_read_nanos', 'download_rpc_nanos', 'cached_read_bytes']:
                assert isinstance(remote[key], int) and remote[key] >= 0


def sample(row):
    result = {key: row.get(key) for key in ROW_FIELDS}
    if row.get('remote') is not None:
        for source, target in [('cached_read_nanos', 'cached_read_seconds'),
                               ('download_rpc_nanos', 'download_rpc_seconds')]:
            result[target] = row['remote'][source] / 1e9
            result[target + '_over_build_wall'] = result[target] / row['seconds']
        result['duration_scope'] = (
            'Cumulative operation durations, not exclusive wall time. Cached reads may overlap '
            'one another and RPCs; their durations must not be summed as a wall-time breakdown.')
    return result


def main():
    assert not OUT.exists(), 'Evidence destination must be new'
    lock = (ROOT.parent / 'skyframe-measurements.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        sources = {
            'build.json': ROOT / 'build.json',
            'prime.json': ROOT / 'agent-prime/results.json',
            'matrix.json': ROOT / 'agent-matrix/results.json',
            'prior-comparison.json': PRIOR / 'comparison.json',
        }
        raw = {name: path.read_bytes() for name, path in sources.items()}
        build, prime, matrix, prior = [json.loads(raw[name]) for name in sources]
        prior_matrix_path = PRIOR / 'matrix.json'
        prior_verification_path = PRIOR / 'verification.json'
        prior_matrix = json.loads(prior_matrix_path.read_bytes())
        prior_verification = json.loads(prior_verification_path.read_bytes())
        assert build['exit_code'] == build['shutdown'] == 0 and build['test_cases'] == 582
        assert sum(suite['cases'] for suite in build['tests']) == 582
        assert not any(suite['failures'] or suite['errors'] for suite in build['tests'])
        commit = git('rev-parse', 'd1289931bc')
        tree = git('rev-parse', commit + '^{tree}')
        assert tree == build['source_tree']
        checks = {'prime': validate_preservation(prime, PRIME_CHECKS),
                  'matrix': validate_preservation(matrix, MATRIX_CHECKS)}
        assert prime['purpose'] == 'cross-binary-prime' and prime['prime']['passed'] is True
        assert prime['measured_count'] == 0 and prime['preparation_count'] == 1
        assert len(prime['runs']) == 1
        preparation = prime['runs'][0]
        validate_row(preparation, prime)
        assert preparation['label'] == prime['prime']['label'] == 'prime-minimal-remote'
        assert preparation['phase'] == 'prime' and preparation['measured'] is False
        assert preparation['arm'] == 'remote' and preparation['mode'] == 'minimal'
        assert (preparation['analysis_hits'], preparation['execution_hits']) == (0, 0)
        assert preparation['remote_committed'] and preparation['local-skycache.json']['published']
        assert preparation['local-skycache.json']['writes'] > 0
        assert len(prime['prime']['added_receipts']) == 1
        assert matrix['purpose'] == 'parallel-read-matrix'
        assert matrix['accepted_prime']['passed'] is True
        assert matrix['prime_report_sha256'] == hashlib.sha256(raw['prime.json']).hexdigest()
        assert Path(matrix['prime_root']) == ROOT / 'agent-prime'
        assert matrix['prime_local_inventory'] == prime['prime']['local_inventory']
        assert matrix['prime_catalog_inventory'] == prime['prime']['catalog_inventory']
        assert matrix['prime_cas_inventory'] == prime['prime']['cas_inventory']
        assert set(matrix['policies']) == set(MODES) and set(matrix['arms']) == set(ARMS)
        assert matrix['repeats'] == 3 and len(matrix['runs']) == 20
        measured = [row for row in matrix['runs'] if row['measured']]
        stabilization = [row for row in matrix['runs'] if not row['measured']]
        assert matrix['measured_count'] == len(measured) == 18 and len(stabilization) == 2
        assert [row['label'] for row in stabilization] == ['primer-minimal', 'primer-toplevel']
        for row in matrix['runs']:
            validate_row(row, matrix, measured=row['measured'])
        for row in stabilization:
            assert row['phase'] == 'stabilization' and row['arm'] == 'primer'
            assert row['preparation'] is True and row['analysis_hits'] > 0 and row['execution_hits'] > 0
        assert (stabilization[-1]['analysis_hits'], stabilization[-1]['execution_hits']) == (17, 11)
        assert stabilization[-1]['local-skycache.json']['writes'] == 0
        assert matrix['primer_receipts'] and matrix['frozen_seed_inventory']
        remote_labels = {row['label'] for row in measured if row['arm'] == 'remote'}
        assert set(matrix['server_warmups']) == remote_labels
        for warm in matrix['server_warmups'].values():
            assert warm['passed'] and warm['rounds'] == 1 and warm['request_metadata_sent']
            assert warm['catalog_candidates_unchanged'] and warm['catalog_receipts_unchanged']
            assert warm['new_publication_commit_warmed'] is False
        assert prime['binary_sha256'] == matrix['binary_sha256']
        assert prime['worker_artifacts_sha256'] == matrix['worker_artifacts_sha256']
        assert matrix['binary_sha256']['candidate'] == build['binary_sha256']
        assert Path(matrix['binary_paths']['candidate']) == ROOT / 'bin/bazel'
        assert Path(matrix['binary_paths']['worker']) == Path(build['worker'])
        for name, digest in matrix['binary_sha256'].items():
            assert sha(Path(matrix['binary_paths'][name])) == digest, name + ' binary changed'
        runtime = matrix['worker_artifacts_sha256']
        implementation = [Path(path) for path in runtime if path.endswith(
            '/src/tools/remote/src/main/java/com/google/devtools/build/remote/worker/libworker.jar')]
        assert len(implementation) == 1, 'runtime implementation JAR missing from provenance'
        for path, digest in runtime.items():
            assert sha(Path(path)) == digest, 'worker runtime artifact changed: ' + path
        with zipfile.ZipFile(implementation[0]) as jar:
            jar.getinfo('com/google/devtools/build/remote/worker/RemoteWorker.class')
        for key in ['workspace', 'output_base', 'source_revision', 'module_lock_sha256',
                    'expected_binaries', 'targets']:
            assert prime[key] == matrix[key] == prior_matrix[key], key
        assert prior_verification['passed'] and prior_matrix['status'] == 'passed'
        assert not prior_matrix['cleanup_errors'] and prior_matrix['measured_count'] == 18
        assert all(value is True for key, value in prior_matrix.items() if key.endswith('_unchanged'))
        assert prior['source_commit'] == prior_verification['source_commit']
        assert prior['source_tree'] == prior_verification['source_tree']
        assert prior_matrix['binary_sha256']['candidate'] == prior_verification['binary_sha256']
        assert matrix['binary_sha256']['vanilla'] == prior_matrix['binary_sha256']['vanilla']

        xml_paths = sorted((ROOT / 'tests').rglob('test.xml'))
        xml_suites = []
        for path in xml_paths:
            assert path.is_file() and not path.is_symlink()
            xml = ET.parse(path).getroot()
            assert not xml.findall('.//failure') and not xml.findall('.//error')
            xml_suites.append(dict(target=path.parent.relative_to(ROOT / 'tests').as_posix(),
                                   cases=len(xml.findall('.//testcase')), failures=0, errors=0))
        assert sorted(xml_suites, key=lambda value: value['target']) == sorted(
            build['tests'], key=lambda value: value['target'])
        assert sum(suite['cases'] for suite in xml_suites) == 582

        prior_cells = {(row['mode'], row['arm']): row for row in prior['summary']}
        assert len(prior['summary']) == len(prior_cells) == 6
        summary = []
        for mode in MODES:
            for arm in ARMS:
                rows = [row for row in measured if (row['mode'], row['arm']) == (mode, arm)]
                assert len(rows) == 3 and {row['repeat_number'] for row in rows} == {1, 2, 3}
                old = prior_cells[mode, arm]
                assert old['samples'] == len(old['seconds']) == 3
                assert old['median_seconds'] == statistics.median(old['seconds'])
                prior_rows = [row for row in prior_matrix['runs'] if row['measured']
                              and (row['mode'], row['arm']) == (mode, arm)]
                assert len(prior_rows) == 3 and sorted(row['seconds'] for row in prior_rows) == sorted(old['seconds'])
                times = [row['seconds'] for row in rows]
                median = statistics.median(times)
                summary.append(dict(mode=mode, arm=arm, samples=3, seconds=times,
                                    median_seconds=median, range_seconds=[min(times), max(times)],
                                    prior_samples=3, prior_seconds=old['seconds'],
                                    prior_median_seconds=old['median_seconds'],
                                    prior_range_seconds=[min(old['seconds']), max(old['seconds'])],
                                    prior_over_current=old['median_seconds'] / median,
                                    median_peak_pss_kib=statistics.median(row['peak_pss_kib'] for row in rows),
                                    median_worker_peak_pss_kib=statistics.median(row['worker_peak_pss_kib'] for row in rows),
                                    median_rchar=statistics.median(row['server_proc_io']['rchar'] for row in rows),
                                    rows=[sample(row) for row in rows]))

        prior_paths = {'comparison': PRIOR / 'comparison.json', 'matrix': prior_matrix_path,
                       'verification': prior_verification_path, 'build': PRIOR / 'build.json'}
        report_hashes = {name: dict(path=str(path), sha256=sha(path)) for name, path in prior_paths.items()}
        for label, report in [('prime', prime), ('matrix', matrix)]:
            for field, root_field in [('seed_report_sha256', 'seed_root'),
                                      ('original_report_sha256', 'original_root')]:
                path = Path(report[root_field]) / 'results.json'
                assert sha(path) == report[field]
                report_hashes[label + '-' + root_field] = dict(path=str(path), sha256=report[field])
        previous_binary = Path(prior_matrix['binary_paths']['candidate'])
        assert sha(previous_binary) == prior_verification['binary_sha256']
        source_hashes = {name: dict(path=str(path), sha256=hashlib.sha256(raw[name]).hexdigest())
                         for name, path in sources.items()}

        OUT.mkdir(exist_ok=False)
        (OUT / '.gitattributes').write_text(
            '# Preserve byte-exact raw reports, harness snapshots and checksums at every depth.\n'
            '* -text -whitespace\n')
        for name, encoded in raw.items():
            (OUT / name).write_bytes(encoded)
        save(OUT / 'comparison.json', dict(
            source_commit=commit, source_tree=tree, summary=summary,
            prime=sample(preparation), stabilization=[sample(row) for row in stabilization],
            remote_duration_samples=[sample(row) for row in measured if row['arm'] == 'remote'],
            prior_source_commit=prior['source_commit'], prior_source_tree=prior['source_tree'],
            prior_report_hashes=report_hashes,
            duration_scope='Counters are cumulative operation durations, not exclusive build-wall components; cached reads may overlap each other and RPCs.',
            caveats=[
                'Three samples per arm/policy in each matrix; results are descriptive, not significance estimates.',
                'Each binary has its own prepared seed because installation identity versions analysis keys.',
                'Within a matrix, local and remote readers share the same frozen publication seed.',
                'Fresh JVM/client metadata cache and clean graph/actions per sample; warm backend CAS and OS caches.',
                'Loopback metadata backend with a single RPC stream; production-network performance is unmeasured.',
                'Build wall time excludes startup/clean/backend warmup and includes publication work.',
                'rchar is cumulative fresh-JVM I/O including startup/clean, not physical disk or network bytes.',
                'PSS is sampled process-tree memory; worker peak is separate and filesystem page cache is excluded.',
            ]))
        save(OUT / 'verification.json', dict(
            passed=True, source_commit=commit, source_tree=tree,
            source_head_at_build=build['source_head'], collector_head=git('rev-parse', 'HEAD'),
            binary_sha256=matrix['binary_sha256'], binary_paths=matrix['binary_paths'],
            worker_artifacts_sha256=runtime,
            prior_binary_sha256=prior_matrix['binary_sha256'],
            prior_worker_artifacts_sha256=prior_matrix['worker_artifacts_sha256'],
            install_ids=dict(previous=install_key(previous_binary), current=install_key(ROOT / 'bin/bazel')),
            test_cases=582, measured_samples=18, prime_preparation_samples=1, stabilization_samples=2,
            checks=checks, expected_binaries=matrix['expected_binaries'],
            exact_raw_reports=source_hashes, prior_report_hashes=report_hashes,
            fixture=dict(workspace=matrix['workspace'], output_base=matrix['output_base'],
                         source_revision=matrix['source_revision'], module_lock_sha256=matrix['module_lock_sha256']),
            raw_paths=dict(build=str(ROOT), prime=str(ROOT / 'agent-prime'), matrix=str(ROOT / 'agent-matrix'))))

        archived = []
        with tarfile.open(OUT / 'logs-profiles-tests.tar.gz', 'w:gz', compresslevel=9) as archive:
            for directory, prefix in [(ROOT, 'build'), (ROOT / 'agent-prime', 'prime'),
                                      (ROOT / 'agent-matrix', 'matrix')]:
                for path in sorted(directory.iterdir()):
                    if not path.is_file() or path.is_symlink() or path.name in ('results.json', 'build.json'):
                        continue
                    assert path.name != 'token.key' and not path.name.startswith('catalog')
                    name = prefix + '/' + path.name
                    archive.add(path, arcname=name, recursive=False)
                    archived.append(name)
            for path in xml_paths:
                name = 'tests/' + path.relative_to(ROOT / 'tests').as_posix()
                archive.add(path, arcname=name, recursive=False)
                archived.append(name)
        with tarfile.open(OUT / 'logs-profiles-tests.tar.gz', 'r:gz') as archive:
            assert archive.getnames() == archived
            assert all(member.isfile() and 'token.key' not in Path(member.name).parts
                       and not any(part.startswith('catalog') for part in Path(member.name).parts)
                       for member in archive.getmembers())

        harness = OUT / 'harness'
        harness.mkdir()
        harness_sources = [WORKSPACE / name for name in [
            'build_parallel_reads.py', 'benchmark_agent_parallel_prime.py',
            'benchmark_agent_parallel_matrix.py', 'collect_parallel_agent.py', 'render_parallel_agent.py']]
        harness_sources += [LEGACY / name for name in [
            'datadog-wide.py', 'datadog-concurrent.py', 'datadog-download-modes.py', 'datadog-clean-benchmark.py']]
        harness_sources += [REPO / 'tools/skycache' / name for name in [
            'storage_preflight.py', 'remote_read_benchmark.py', 'remote_benchmark_warmup.py']]
        assert len({path.name for path in harness_sources}) == len(harness_sources)
        for path in harness_sources:
            shutil.copy2(path, harness / path.name)
            assert sha(path) == sha(harness / path.name)
        for name, path in sources.items():
            assert sha(path) == source_hashes[name]['sha256'] == sha(OUT / name)
        for entry in report_hashes.values():
            assert sha(Path(entry['path'])) == entry['sha256']
        sums = OUT / 'SHA256SUMS'
        sums.write_text(''.join(sha(path) + '  ' + path.relative_to(OUT).as_posix() + '\n'
                               for path in sorted(OUT.rglob('*')) if path.is_file() and path != sums))
        print(json.dumps([dict(mode=row['mode'], arm=row['arm'], median_seconds=row['median_seconds'],
                               prior_median_seconds=row['prior_median_seconds'],
                               prior_over_current=row['prior_over_current']) for row in summary], indent=2))
    finally:
        lock.close()


if __name__ == '__main__':
    main()
