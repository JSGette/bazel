"""Validate completed retained-chunk measurements and freeze their exact evidence bytes."""

from pathlib import Path
import argparse
import fcntl
import hashlib
import json
import math
import re
import shutil
import statistics
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile


REPO = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
ROOT = Path('/home/jgett/REPOS/.work/skycache-retained-chunks-2026-10-25')
WORKSPACE = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-25/based-on-the-latest-architecture-of')
LEGACY = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work')
PRIOR = REPO / 'docs/jsgette/measurements/skycache-concurrent-downloads-agent-2026-10-25'
OUT = REPO / 'docs/jsgette/measurements/skycache-retained-chunks-agent-2026-10-25'
MODES = ('minimal', 'toplevel')
ARMS = ('vanilla', 'local', 'remote', 'remote-warm')
REMOTE_ARMS = ('remote', 'remote-warm')
LOCAL_CACHE_MAX_BYTES = 5 * 1024**3
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
    'retained_seed_unchanged',
}
ROW_FIELDS = [
    'label', 'arm', 'mode', 'phase', 'measured', 'preparation', 'repeat_number', 'validation',
    'seconds', 'peak_pss_kib', 'worker_peak_pss_kib', 'analysis_hits', 'execution_hits',
    'wire_download_bytes', 'wire_upload_bytes', 'remote', 'remote_committed', 'server_proc_io',
    'local-skycache.json', 'local-skycache-validation.json',
    'retained_seed_files', 'retained_seed_bytes', 'retained_seed_sha256',
    'seeded_cache_files', 'seeded_cache_bytes', 'retained_warmup_inventory',
]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def retained_inventory(cache):
    """Authenticate retained CAS content only; exclude lockfiles, pins and timestamps."""
    directory = cache / 'leased-v2/remote-chunks-v1/sha256'
    assert directory.is_dir() and not directory.is_symlink(), 'retained CAS missing or unsafe'
    entries = []
    for path in sorted(directory.rglob('*')):
        assert not path.is_symlink(), 'retained seed must not contain symlinks'
        if path.is_dir():
            assert re.fullmatch('[0-9a-f]{2}', path.relative_to(directory).as_posix())
            continue
        assert path.is_file(), 'unexpected retained CAS entry'
        relative = path.relative_to(directory).as_posix()
        match = re.fullmatch(r'([0-9a-f]{2})/([0-9a-f]{64})-([0-9]+)', relative)
        assert match and match[1] == match[2][:2], 'invalid retained CAS filename'
        size = path.stat().st_size
        assert size == int(match[3]) and size <= 1024 * 1024, 'retained chunk size mismatch'
        digest = hashlib.sha256()
        with path.open('rb') as contents:
            for block in iter(lambda: contents.read(1024 * 1024), b''):
                digest.update(block)
        assert digest.hexdigest() == match[2], 'retained seed checksum mismatch'
        entries.append(dict(path=relative, size=size, sha256=digest.hexdigest()))
    inventory_hash = hashlib.sha256()
    for entry in entries:
        inventory_hash.update(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode())
        inventory_hash.update(b'\n')
    return dict(path=str(directory), sha256=inventory_hash.hexdigest(), files=len(entries),
                file_bytes=sum(entry['size'] for entry in entries), entries=entries)


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
        if row['arm'] in ('local', *REMOTE_ARMS):
            assert (row['analysis_hits'], row['execution_hits']) == (17, 11)
            assert row['grpc']['method_attempt_counts'].get(
                'build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0) == 0
            if row['mode'] == 'minimal':
                assert row['grpc']['bytestream_read_calls'] == 0
            if row['arm'] == 'local':
                assert local['writes'] == 0
        else:
            assert (row['analysis_hits'], row['execution_hits']) == (0, 0)
        if row['arm'] not in REMOTE_ARMS:
            assert row['wire_download_bytes'] == row['wire_upload_bytes'] == 0
        else:
            remote = row['remote']
            assert row['remote_discovered'] > 0
            assert remote['max_concurrency'] == 100
            assert remote['local_cache_max_bytes'] == LOCAL_CACHE_MAX_BYTES
            assert remote['local_cache_errors'] == 0
            assert 1 <= remote['peak_cached_reads'] <= remote['max_concurrency']
            assert 0 <= remote['authentication_cache_bytes'] <= 32 * 1024**2
            for key in ['cached_read_nanos', 'download_rpc_nanos', 'cached_read_bytes',
                        'downloaded_bytes', 'downloaded_chunks', 'local_cache_hits',
                        'local_cache_bytes', 'local_cache_errors']:
                assert isinstance(remote[key], int) and remote[key] >= 0
            if row['arm'] == 'remote':
                assert remote['downloaded_bytes'] > 0 and remote['downloaded_chunks'] > 0
                assert 1 <= remote['peak_chunk_downloads'] <= remote['max_concurrency']
                assert row['seeded_cache_files'] == row['seeded_cache_bytes'] == 0
                assert row['retained_seed_files'] == row['retained_seed_bytes'] == 0
                assert row['retained_seed_sha256'] is None
            else:
                assert remote['downloaded_bytes'] == remote['downloaded_chunks'] == 0
                assert remote['peak_chunk_downloads'] == 0
                assert remote['local_cache_hits'] > 0 and remote['local_cache_bytes'] > 0
                seed = report['retained_seed']['inventory']
                assert row['retained_seed_files'] == seed['files'] > 0
                assert row['retained_seed_bytes'] == seed['file_bytes'] > 0
                assert row['retained_seed_sha256'] == seed['sha256']


def sample(row):
    result = {key: row.get(key) for key in ROW_FIELDS}
    if row.get('remote') is not None:
        for source, target in [('cached_read_nanos', 'cached_read_seconds'),
                               ('download_rpc_nanos', 'download_rpc_seconds')]:
            result[target] = row['remote'][source] / 1e9
            result[target + '_over_build_wall'] = result[target] / row['seconds']
        result['duration_scope'] = (
            'Cumulative operation durations, not exclusive wall time. Cached reads and download '
            'RPCs can overlap within and across both groups. Download-RPC duration sums parallel '
            'calls; neither counter nor their sum is an exclusive wall-time breakdown.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-commit', default='b368beb0dd', help='Product commit matching build.json source_tree; '
                        'default: b368beb0dd')
    parser.add_argument('--expected-test-cases', type=int, default=612,
                        help='Expected passing XML test count (default: 612)')
    args = parser.parse_args()
    if args.expected_test_cases is not None and args.expected_test_cases <= 0:
        parser.error('--expected-test-cases must be positive')
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
        assert build['exit_code'] == build['shutdown'] == 0
        test_cases = build['test_cases']
        assert isinstance(test_cases, int) and test_cases > 0
        if args.expected_test_cases is not None:
            assert test_cases == args.expected_test_cases, 'unexpected build test count'
        assert sum(suite['cases'] for suite in build['tests']) == test_cases
        assert not any(suite['failures'] or suite['errors'] for suite in build['tests'])
        source_ref = args.source_commit or build.get('source_commit') or build['source_head']
        commit = git('rev-parse', '--verify', source_ref + '^{commit}')
        tree = git('rev-parse', commit + '^{tree}')
        assert tree == build['source_tree'], (
            'commit does not match tested tree; pass --source-commit for a staged-tree build',
            commit, tree, build['source_tree'])
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
        assert matrix['purpose'] == 'retained-chunk-matrix'
        assert matrix['accepted_prime']['passed'] is True
        assert matrix['prime_report_sha256'] == hashlib.sha256(raw['prime.json']).hexdigest()
        assert Path(matrix['prime_root']) == ROOT / 'agent-prime'
        assert matrix['prime_local_inventory'] == prime['prime']['local_inventory']
        assert matrix['prime_catalog_inventory'] == prime['prime']['catalog_inventory']
        assert matrix['prime_cas_inventory'] == prime['prime']['cas_inventory']
        assert set(matrix['policies']) == set(MODES) and set(matrix['arms']) == set(ARMS)
        assert matrix['repeats'] == 3 and len(matrix['runs']) == 28
        measured = [row for row in matrix['runs'] if row['measured']]
        stabilization = [row for row in matrix['runs'] if row['phase'] == 'stabilization']
        retained_warmups = [row for row in matrix['runs'] if row['phase'] == 'retained-warmup']
        assert matrix['measured_count'] == len(measured) == 24
        assert len(stabilization) == len(retained_warmups) == 2
        assert matrix['preparation_count'] == 4
        assert matrix['runs'][:4] == stabilization + retained_warmups
        assert [row['label'] for row in stabilization] == ['primer-minimal', 'primer-toplevel']
        for row in matrix['runs']:
            validate_row(row, matrix, measured=row['measured'])
        for row in stabilization:
            assert row['phase'] == 'stabilization' and row['arm'] == 'primer'
            assert row['preparation'] is True and row['analysis_hits'] > 0 and row['execution_hits'] > 0
        assert (stabilization[-1]['analysis_hits'], stabilization[-1]['execution_hits']) == (17, 11)
        assert stabilization[-1]['local-skycache.json']['writes'] == 0
        assert matrix['primer_receipts'] and matrix['frozen_seed_inventory']
        assert [row['label'] for row in retained_warmups] == [
            'retained-seed-' + mode for mode in matrix['policies']]
        retained_seed = matrix['retained_seed']
        assert retained_seed['produced_by_current_binary'] is True
        assert retained_seed['excluded_from_measurement'] is True
        assert retained_seed['lockfiles_excluded'] is True
        assert retained_seed['copied_parts'] == ['leased-v2/remote-chunks-v1/sha256']
        assert retained_seed['policies'] == matrix['policies']
        assert retained_seed['frozen_catalog_receipts'] == matrix['primer_receipts']
        assert Path(retained_seed['cache']) == ROOT / 'agent-matrix/cache-retained-seed'
        assert retained_seed['inventory']['files'] > 0
        assert retained_seed['inventory']['file_bytes'] > 0
        assert retained_inventory(Path(retained_seed['cache'])) == retained_seed['inventory']
        assert isinstance(retained_seed['coverage'], str) and retained_seed['coverage']
        assert len(retained_seed['warmups']) == 2
        retained_union = {}
        for row, compact in zip(retained_warmups, retained_seed['warmups'], strict=True):
            assert all(row[key] == value for key, value in compact.items())
            assert row['arm'] == 'retained-warmup' and row['measured'] is False
            assert row['preparation'] is True
            assert row['seeded_cache_files'] == row['seeded_cache_bytes'] == 0
            inventory = row['retained_warmup_inventory']
            assert inventory['files'] == len(inventory['entries']) > 0
            assert inventory['file_bytes'] == sum(entry['size'] for entry in inventory['entries']) > 0
            assert inventory['entries'] == sorted(inventory['entries'], key=lambda entry: entry['path'])
            digest = hashlib.sha256()
            for entry in inventory['entries']:
                digest.update(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode())
                digest.update(b'\n')
                if entry['path'] in retained_union:
                    assert retained_union[entry['path']] == entry, 'warmup chunk conflict'
                retained_union[entry['path']] = entry
            assert digest.hexdigest() == inventory['sha256']
            assert (row['analysis_hits'], row['execution_hits']) == (17, 11)
            assert row['remote_discovered'] > 0
            assert row['remote']['max_concurrency'] == 100
            assert row['remote']['local_cache_max_bytes'] == LOCAL_CACHE_MAX_BYTES
            assert row['remote']['local_cache_errors'] == 0
            assert row['grpc']['method_attempt_counts'].get(
                'build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0) == 0
            if row['mode'] == 'minimal':
                assert row['grpc']['bytestream_read_calls'] == 0
        assert sorted(retained_union.values(), key=lambda entry: entry['path']) == retained_seed['inventory']['entries']
        remote_labels = {row['label'] for row in measured if row['arm'] in REMOTE_ARMS}
        remote_labels.update(row['label'] for row in retained_warmups)
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
        assert prior_matrix['binary_sha256'] == prior_verification['binary_sha256']
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
        assert sum(suite['cases'] for suite in xml_suites) == test_cases

        prior_cells = {(row['mode'], row['arm']): row for row in prior['summary']}
        assert len(prior['summary']) == len(prior_cells) == 6
        summary = []
        for mode in MODES:
            for arm in ARMS:
                rows = [row for row in measured if (row['mode'], row['arm']) == (mode, arm)]
                assert len(rows) == 3 and {row['repeat_number'] for row in rows} == {1, 2, 3}
                prior_arm = 'remote' if arm == 'remote-warm' else arm
                old = prior_cells[mode, prior_arm]
                assert old['samples'] == len(old['seconds']) == 3
                assert old['median_seconds'] == statistics.median(old['seconds'])
                prior_rows = [row for row in prior_matrix['runs'] if row['measured']
                              and (row['mode'], row['arm']) == (mode, prior_arm)]
                assert len(prior_rows) == 3 and sorted(row['seconds'] for row in prior_rows) == sorted(old['seconds'])
                times = [row['seconds'] for row in rows]
                median = statistics.median(times)
                summary.append(dict(mode=mode, arm=arm, prior_arm=prior_arm, samples=3, seconds=times,
                                    median_seconds=median, range_seconds=[min(times), max(times)],
                                    prior_samples=3, prior_seconds=old['seconds'],
                                    prior_median_seconds=old['median_seconds'],
                                    prior_range_seconds=[min(old['seconds']), max(old['seconds'])],
                                    prior_over_current=old['median_seconds'] / median,
                                    median_peak_pss_kib=statistics.median(row['peak_pss_kib'] for row in rows),
                                    median_worker_peak_pss_kib=statistics.median(row['worker_peak_pss_kib'] for row in rows),
                                    median_rchar=statistics.median(row['server_proc_io']['rchar'] for row in rows),
                                    rows=[sample(row) for row in rows]))

        current_cells = {(row['mode'], row['arm']): row for row in summary}
        for cell in summary:
            local_median = current_cells[cell['mode'], 'local']['median_seconds']
            cold_median = current_cells[cell['mode'], 'remote']['median_seconds']
            cell.update(current_local_median_seconds=local_median,
                        current_local_over_current=local_median / cell['median_seconds'],
                        current_cold_remote_median_seconds=cold_median,
                        current_cold_remote_over_current=cold_median / cell['median_seconds'])

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
        assert sha(previous_binary) == prior_verification['binary_sha256']['candidate']
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
            retained_warmups=[sample(row) for row in retained_warmups],
            retained_seed=retained_seed,
            remote_duration_samples=[sample(row) for row in measured if row['arm'] in REMOTE_ARMS],
            prior_source_commit=prior['source_commit'], prior_source_tree=prior['source_tree'],
            prior_report_hashes=report_hashes,
            duration_scope='Counters sum operation durations, including parallel download calls; cached reads and RPCs can overlap within and across both groups. These are not exclusive build-wall components.',
            caveats=[
                'Three samples per arm/policy in each matrix; results are descriptive, not significance estimates.',
                'Each binary has its own prepared seed because installation identity versions analysis keys.',
                'Within a matrix, local and both remote readers share the same frozen publication seed.',
                'Warm remote is compared with the prior cold-remote arm; the historical matrix has no warm-retained arm.',
                'Warm samples receive only copied authenticated-content chunks from two excluded post-freeze policy warmups; local publication metadata and locks are not seeded.',
                'Fresh JVM and clean graph/actions per sample; remote starts empty while remote-warm receives only retained chunks. Backend CAS and OS caches are warm.',
                'The 5 GiB persistent cache is a soft retained-file limit; active private pins and invocation scratch are outside that bound.',
                'Loopback metadata backend with max_concurrency=100 for cached reads and chunk downloads; production-network performance is unmeasured.',
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
            test_cases=test_cases, expected_test_cases=args.expected_test_cases,
            source_commit_argument=args.source_commit, max_concurrency=100,
            local_cache_max_bytes=LOCAL_CACHE_MAX_BYTES,
            measured_samples=24, prime_preparation_samples=1, stabilization_samples=2,
            retained_warmup_samples=2, retained_seed=retained_seed,
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
            'build_retained_chunks.py', 'benchmark_agent_parallel_prime.py',
            'benchmark_agent_retained_matrix.py', 'collect_retained_agent.py', 'render_retained_agent.py']]
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
        assert retained_inventory(Path(retained_seed['cache'])) == retained_seed['inventory']
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
