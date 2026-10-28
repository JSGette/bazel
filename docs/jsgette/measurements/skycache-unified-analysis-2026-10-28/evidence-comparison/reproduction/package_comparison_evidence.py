#!/usr/bin/env python3
"""Package completed comparison evidence without copying build state.

Run only after native work finishes. The default cohort names match this study.
A terminal failed cohort may be archived with --failed-cohort NAME; it is then
placed under failures/ and cannot become primary evidence. Running cohorts are
always rejected. --verify DIRECTORY audits a completed package without sources.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

THRESHOLD = 128 * 1024
ARMS = ('vanilla', 'fork-off', 'fork-on')
COHORTS = (
    ('compare-actions-quiet-01', 'synthetic', 'primary'),
    ('compare-provider-quiet-01', 'synthetic', 'primary'),
    ('datadog-three-arm-01', 'datadog', 'primary'),
    ('retained-actions-02', 'retained', 'supplemental'),
    ('retained-provider-02', 'retained', 'supplemental'),
    ('retained-actions-01', 'retained', 'failures'),
    ('compare-actions-01', 'synthetic', 'contended'),
    ('compare-provider-01', 'synthetic', 'contended'),
)
CONTENDED_DATADOG = {'smoke-vanilla', 'smoke-fork-off', 'smoke-fork-on', 'smoke-restore-1'}
SAFE_SUFFIXES = {'.json', '.jsonl', '.log', '.xml', '.diff', '.gz', '.py', '.bzl',
                 '.bazel', '.md', '.txt', '.csv', '.svg', '.png', '.patch', '.sh', '.toml'}
FORBIDDEN = {'output-base', 'binary-state', 'bazel-user', 'caches', 'cache', 'home',
             'tmp', 'repository-cache', 'repository-contents', '__pycache__'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def safe_relative(value):
    value = str(value)
    parts = PurePosixPath(value)
    require(not parts.is_absolute() and bool(parts.parts) and '..' not in parts.parts
            and '\\' not in value and ':' not in value, f'unsafe relative path: {value}')
    return parts.as_posix()


def successful_metrics(row):
    metrics = row.get('metrics', {})
    return metrics.get('exit_code') == 0 and metrics.get('timed_out') is False


def check_cache_hits(arm, hits, restored):
    require(type(hits) is int and hits >= 0, 'invalid configured-target cache-hit count')
    require(arm in ARMS, 'unknown comparison arm')
    require(hits == 0 if arm != 'fork-on' else (hits > 0 if restored else True),
            'configured-target restoration/control gate failed')


def datadog_hits(row):
    stats = row.get('build_metrics', {}).get('remoteAnalysisCacheStatistics', {})
    return sum(int(item.get('value', 0)) for item in stats.get('cacheHitsBySkyfunction', [])
               if item.get('key') == 'CONFIGURED_TARGET')

def validate_cohort(source, kind, failed=False):
    """Require complete native evidence, not merely a folder bearing a pass name."""
    state_name = 'results.json' if kind == 'datadog' else 'summary.json'
    state = read_json(source / state_name)
    require(state.get('status') == ('failed' if failed else 'passed'),
            f'{source.name}: expected terminal {"failed" if failed else "passed"} state')
    if failed:
        return state
    if kind == 'retained':
        rows = state.get('arms', [])
        require(len(rows) == 3 and {r['arm'] for r in rows} == set(ARMS), 'retained arm set incomplete')
        for row in rows:
            builds = row.get('builds', [])
            require(row.get('status') == 'passed' and len(builds) == 7
                    and [r['number'] for r in builds] == list(range(7)), 'retained series incomplete')
            require(all(successful_metrics(r) and r.get('validation', {}).get('passed') is True
                        for r in builds), 'retained validation failed')
            require(row.get('shutdown', {}).get('status') == 'stopped', 'retained shutdown unverified')
            for build in builds:
                check_cache_hits(row['arm'], build.get('configured_target_hits'), build['number'] == 0)
        return state
    rows = state.get('runs', [])
    require(all(r.get('status') == 'passed' and successful_metrics(r) for r in rows),
            'one or more commands failed, timed out, or lack metrics')
    if kind == 'synthetic':
        manifest = read_json(source / 'manifest.json')
        require(len(rows) == 27 and Counter(r['role'] for r in rows) ==
                Counter(warmup=6, producer=3, measured=18), 'synthetic 27-command cohort incomplete')
        require([r['label'] for r in rows] == [r['label'] for r in manifest['schedule']],
                'synthetic rows differ from original schedule')
        require(len({r['label'] for r in rows}) == 27, 'duplicate synthetic command label')
        require(all(r.get('validation', {}).get('passed') is True for r in rows), 'synthetic output validation missing')
        require((source / 'statistics.json').is_file(), 'synthetic statistics missing')
        for row in rows:
            check_cache_hits(row['arm'], row.get('bep_observations', {}).get('configured_target_cache_hits'), row['role'] == 'measured')
    else:
        expected = {f'r{i}-{arm}' for i in range(1, 7) for arm in ARMS}
        expected |= {f'smoke-{arm}' for arm in ARMS} | {'smoke-restore-1', 'smoke-restore-2'}
        require(len(rows) == 23 and {r['name'] for r in rows} == expected, 'Datadog 23-command cohort incomplete')
        require(sum(r.get('preparation') is True for r in rows) == 5, 'Datadog preparation count differs')
        require(state.get('rounds') == 6 and state.get('smoke_gate') == 'passed'
                and state.get('source_unchanged') is True, 'Datadog completion/source gate missing')
        require(all(r.get('outputs_match') is True for r in rows), 'Datadog output validation missing')
        for row in rows:
            restored = row.get('seeded') is True
            check_cache_hits(row['arm'], datadog_hits(row), restored)
            if restored:
                check_cache_hits(row['arm'], row.get('configured_target_cache_hits'), True)
    return state


class PackagePlan:
    def __init__(self):
        self.files = {}
        self.cohorts = []

    def add(self, source, logical, expected=None):
        source, logical = Path(source), safe_relative(logical)
        require(source.is_file() and not source.is_symlink(), f'missing/nonregular/symlink input: {source}')
        require(source.suffix in SAFE_SUFFIXES or re.search(r'\.log\.\d+$', source.name),
                f'unsupported artifact type (never copy binaries): {source}')
        require(not any(part in FORBIDDEN for part in PurePosixPath(logical).parts),
                f'build state cannot be packaged: {logical}')
        actual = digest(source)
        require(expected is None or actual == expected, f'source checksum mismatch: {source}')
        if logical in self.files:
            require(self.files[logical]['sha256'] == actual, f'logical path collision: {logical}')
            return
        self.files[logical] = {'source': source, 'sha256': actual, 'bytes': source.stat().st_size}

    def top_files(self, source, logical):
        require(source.is_dir() and not source.is_symlink(), f'missing/symlink directory: {source}')
        for path in sorted(source.iterdir()):
            require(not path.is_symlink(), f'symlink artifact forbidden: {path}')
            if path.is_file():
                self.add(path, f'{logical}/{path.name}')

    def raw_run(self, source, logical, hashes=None, diagnostic_hashes=None):
        self.top_files(source, logical)
        if (source / 'diagnostics').is_dir():
            self.top_files(source / 'diagnostics', logical + '/diagnostics')
        for name, expected in (hashes or {}).items():
            self.add(source / safe_relative(name), f'{logical}/{safe_relative(name)}', expected)
        for name, expected in (diagnostic_hashes or {}).items():
            self.add(source / 'diagnostics' / safe_relative(name), f'{logical}/diagnostics/{safe_relative(name)}', expected)

    def helper_inputs(self, manifest, source, logical):
        helper_hashes = manifest.get('helpers', manifest.get('harnesses', {}))
        for path, expected in helper_hashes.items():
            # Synthetic runs snapshot their helper at run creation; prefer that immutable copy.
            archived = source / 'provenance' / Path(path).name
            chosen = archived if archived.is_file() else Path(path)
            self.add(chosen, f'{logical}/helpers/{Path(path).name}', expected)
        for role, item in manifest.get('manifest_files', {}).items():
            self.add(Path(item['path']), f'{logical}/inputs/{role}.json', item['sha256'])
        inventory = manifest.get('fixture_sha256', manifest.get('workspace_sha256', {}))
        workspace = Path(manifest['workspace']) if 'workspace' in manifest else source / 'workspace'
        for name, expected in inventory.items():
            self.add(workspace / safe_relative(name), f'{logical}/inputs/files/{safe_relative(name)}', expected)
        if 'fixture_manifest' in manifest:
            self.add(Path(manifest['fixture_manifest']), f'{logical}/inputs/fixture.json', manifest['fixture_manifest_sha256'])
        if 'producer_command' in manifest:
            self.add(Path(manifest['producer_command']), f'{logical}/inputs/producer-command.json', manifest['producer_command_sha256'])

    def cohort(self, source, name, kind, classification, failed=False):
        source = source.resolve(strict=True)
        state = validate_cohort(source, kind, failed)
        category = 'failures' if failed else classification
        logical = f'{category}/{name}'
        self.top_files(source, logical)
        record = {'name': name, 'kind': kind, 'classification': category,
                  'source': str(source), 'native_status': state['status'], 'commands': []}
        self.cohorts.append(record)
        if (source / 'manifest.json').exists():
            self.helper_inputs(read_json(source / 'manifest.json'), source, logical)
        if (source / 'provenance').is_dir():
            self.top_files(source / 'provenance', logical + '/provenance')
        if kind == 'retained':
            for row in state.get('arms', []):
                arm_dir = source / 'runs' / safe_relative(row['arm'])
                self.top_files(arm_dir, f'{logical}/runs/{row["arm"]}')
                for directory in sorted(arm_dir.iterdir()):
                    if directory.is_dir() and (re.fullmatch(r'build-\d\d', directory.name)
                                               or directory.name in {'heap-before-gc', 'forced-gc', 'heap-after-gc', 'shutdown'}):
                        self.raw_run(directory, f'{logical}/runs/{row["arm"]}/{directory.name}')
                for relative, expected in row.get('artifacts_sha256', {}).items():
                    relative = safe_relative(relative)
                    self.add(arm_dir / relative, f'{logical}/runs/{row["arm"]}/{relative}', expected)
                for build in row.get('builds', []):
                    record['commands'].append({'name': f'{row["arm"]}/build-{build["number"]:02d}',
                                               'role': build['role'], 'primary_latency_eligible': False})
            return
        for row in state.get('runs', []):
            label = safe_relative(row['name' if kind == 'datadog' else 'label'])
            directory = source / label if kind == 'datadog' else source / 'runs' / label
            if directory.is_dir():
                hashes = row.get('checksums') if kind == 'datadog' else row.get('raw_artifacts_sha256')
                self.raw_run(directory, f'{logical}/runs/{label}', hashes, row.get('diagnostics_sha256'))
            else:
                require(failed, f'missing command directory: {directory}')
            role = ('preparation' if row.get('preparation') else 'measured') if kind == 'datadog' else row['role']
            contended = category == 'contended' or (kind == 'datadog' and label in CONTENDED_DATADOG)
            record['commands'].append({'name': label, 'role': role, 'contended': contended,
                                       'primary_latency_eligible': category == 'primary' and role == 'measured' and not contended})
        if kind == 'datadog':
            self.add(source / 'harness.py', logical + '/helpers/compare_datadog.py', state.get('harness_sha256'))
            self.add(source / 'paired_baseline.py', logical + '/helpers/paired_baseline.py', state.get('paired_helper_sha256'))
            # Explicit configuration is already captured in results.json. Do not copy the
            # historic JFR: it contains inherited environment values and is not publication input.


def compressible(path, size):
    return size > THRESHOLD and (path.suffix in {'.json', '.jsonl', '.log', '.xml', '.diff'}
                                 or re.search(r'\.log\.\d+$', path.name))


def store_file(source, destination, compress):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as incoming, destination.open('xb') as raw:
        if compress:
            with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0, compresslevel=9) as output:
                shutil.copyfileobj(incoming, output, 1024 * 1024)
        else:
            shutil.copyfileobj(incoming, raw, 1024 * 1024)


def write_package(plan, destination):
    require(not destination.exists() and not destination.is_symlink(), 'destination must not exist')
    require(all(not destination.resolve().is_relative_to(Path(cohort['source']).resolve())
                for cohort in plan.cohorts), 'destination cannot be inside a native cohort')
    # All native-completion and recorded-checksum checks have happened before any destination write.
    destination.mkdir(parents=True)
    index = {'schema_version': 1, 'status': 'writing', 'compression_threshold_bytes': THRESHOLD,
             'gzip': {'mtime': 0, 'filename': '', 'compression_level': 9},
             'cohorts': plan.cohorts, 'files': {},
             'exclusions': ['Native binaries, caches, output bases, installed JDKs, built workspace outputs, and historic environment JFR recordings are never copied.'],
             'interpretation': ['Only measured commands explicitly marked primary_latency_eligible contribute primary latency evidence.',
                                'Retained-server series are supplemental sequential diagnostics, not a balanced initial-latency comparison.',
                                'Original contended cohorts remain complete audit evidence and are not primary performance evidence.',
                                'The index intentionally does not hash itself; verify its Git blob separately when staging.']}
    occupied = set()
    try:
        for logical, item in sorted(plan.files.items()):
            compress = bool(compressible(item['source'], item['bytes']))
            stored = logical + '.gz' if compress else logical
            require(stored not in occupied and stored != 'index.json', f'storage collision: {stored}')
            occupied.add(stored)
            require(digest(item['source']) == item['sha256'], f'input changed before copy: {item["source"]}')
            output = destination / stored
            store_file(item['source'], output, compress)
            require(digest(item['source']) == item['sha256'], f'input changed during copy: {item["source"]}')
            index['files'][logical] = {'storage_path': stored, 'encoding': 'gzip' if compress else 'identity',
                                      'original_sha256': item['sha256'], 'original_bytes': item['bytes'],
                                      'stored_sha256': digest(output), 'stored_bytes': output.stat().st_size}
        index['status'] = 'complete'
    except Exception as error:
        index['status'] = 'failed'
        index['failure'] = str(error)
        raise
    finally:
        (destination / 'index.json').write_text(json.dumps(index, indent=2, sort_keys=True) + '\n')
    verify_package(destination)
    return index


def verify_package(destination):
    index = read_json(destination / 'index.json')
    require(index.get('status') == 'complete', 'package is not complete')
    expected = {'index.json'}
    for logical, item in index['files'].items():
        safe_relative(logical)
        stored = safe_relative(item['storage_path'])
        require(stored not in expected, f'duplicate storage path: {stored}')
        expected.add(stored)
        path = destination / stored
        require(path.is_file() and not path.is_symlink(), f'missing packaged file: {stored}')
        require(digest(path) == item['stored_sha256'] and path.stat().st_size == item['stored_bytes'],
                f'stored checksum/size mismatch: {stored}')
        require(item['encoding'] in ('gzip', 'identity'), 'unknown storage encoding')
        opener = gzip.open if item['encoding'] == 'gzip' else open
        actual, count = hashlib.sha256(), 0
        with opener(path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                actual.update(chunk)
                count += len(chunk)
        require(actual.hexdigest() == item['original_sha256'] and count == item['original_bytes'],
                f'original checksum/size mismatch: {logical}')
    actual = {p.relative_to(destination).as_posix() for p in destination.rglob('*') if p.is_file()}
    require(actual == expected, 'package has unindexed or missing files')
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--docs', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--verify', type=Path)
    parser.add_argument('--failed-cohort', action='append', default=[])
    parser.add_argument('--extra', action='append', default=[], metavar='LOGICAL=FILE',
                        help='Explicit additional summary/plot/provenance file; no directory traversal')
    args = parser.parse_args()
    if args.verify:
        index = verify_package(args.verify)
    else:
        if not args.docs or not args.destination:
            parser.error('--docs and --destination are required for packaging')
        known = {name for name, _, _ in COHORTS}
        require(set(args.failed_cohort) <= known, 'unknown failed cohort')
        plan = PackagePlan()
        for name, kind, classification in COHORTS:
            plan.cohort(args.source_root / name, name, kind, classification, classification == 'failures' or name in args.failed_cohort)
        provenance_path = args.source_root / 'three-arm-baseline-provenance.json'
        plan.add(provenance_path, 'provenance/three-arm-baseline-provenance.json')
        provenance = read_json(provenance_path)
        for arm in ('vanilla', 'candidate'):
            record = provenance[arm]
            for field, filename in (('raw_build_command', 'command.json'), ('raw_build_result', 'result.json'),
                                    ('build_log', 'build.log'), ('source_diff', 'source.diff'),
                                    ('build_script', 'build-script.py'), ('source_binding', 'source-binding.json')):
                if field in record:
                    plan.add(Path(record[field]), f'provenance/build-{arm}/{filename}', record.get(field + '_sha256'))
            build = record['build_options']
            plan.add(Path(build['bep']), f'provenance/build-{arm}/build.bep.json', build['bep_sha256'])
        plan.add(args.source_root / 'quiet-window.json', 'provenance/quiet-window.json')
        for path in sorted(args.source_root.glob('interference-*.json')):
            plan.add(path, 'provenance/' + path.name)
        for name in ('run_three_arm_cohorts.py', 'run_three_arm_quiet.py', 'run_retained_comparisons.py', 'quiet_window_guard.py'):
            plan.add(args.source_root / name, 'reproduction/' + name)
        plan.add(Path(__file__), 'reproduction/package_comparison_evidence.py')
        plan.add(Path(__file__).with_name('test_package_comparison_evidence.py'), 'reproduction/test_package_comparison_evidence.py')
        # The committed source snapshot includes currently untracked analysis helpers and tests.
        # Per-run helper copies above, with their recorded hashes, remain the execution provenance.
        for pattern in ('compare_*.py', 'measure_retained_server.py', 'test_compare_*.py', 'test_measure_retained_server.py',
                        'paired_baseline.py', 'plot_comparison.py', 'aggregate_comparisons.py', 'test_aggregate_comparisons.py'):
            for path in sorted(args.docs.glob(pattern)):
                plan.add(path, 'reproduction/current/' + path.name)
        for value in args.extra:
            logical, separator, source = value.partition('=')
            require(separator and logical and source, '--extra requires LOGICAL=FILE')
            plan.add(Path(source), logical)
        index = write_package(plan, args.destination)
    print(json.dumps({'status': index['status'], 'files': len(index['files']),
                      'original_bytes': sum(f['original_bytes'] for f in index['files'].values()),
                      'stored_bytes': sum(f['stored_bytes'] for f in index['files'].values())}))


if __name__ == '__main__':
    main()