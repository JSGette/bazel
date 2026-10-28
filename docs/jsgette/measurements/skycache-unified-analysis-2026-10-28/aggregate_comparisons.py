#!/usr/bin/env python3
"""Verify completed, operator-selected quiet cohorts and export descriptive evidence.

Run only after native measurements finish: verification hashes binaries, raw
artifacts and actual outputs. This does not establish host quietness; supply only
cohorts covered by the separately archived quiet-window record. Contended runs
must be aggregated separately. No Bazel commands are executed.
"""

import argparse
import csv
import itertools
import json
import math
from pathlib import Path
import re

import compare_three_arms as comparison
import compare_datadog as datadog
import measure_retained_server as retained

baseline = comparison.baseline
ARMS = comparison.ARMS
REQUIRED_RAW = {'build.bep.json', 'metrics.json', 'command.json', 'stdout.log', 'stderr.log'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return baseline.digest(path)


def check_hash(path, expected):
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError(f'invalid recorded SHA-256: {path}')
    if sha(path) != expected:
        raise ValueError(f'checksum mismatch: {path}')


def check_files(root, hashes, required=()):
    if not isinstance(hashes, dict) or not set(required).issubset(hashes):
        raise ValueError(f'incomplete artifact checksum inventory: {root}')
    for name, expected in hashes.items():
        check_hash(baseline.inside(root / name, root), expected)


def stream(path):
    events = [json.loads(line) for line in Path(path).read_text().splitlines() if line]
    baseline.validate_bep_terminal(events)
    return events


def successful_metrics(metrics, kind='synthetic'):
    if kind == 'datadog':
        metrics = dict(metrics, resources=dict(metrics['resources'],
                       ru_maxrss_raw=metrics['resources']['max_child_rss_kib'], ru_maxrss_unit='KiB'))
    return comparison.resource_metrics(metrics)


def sample(label, path, arm, number, metrics, observations, kind='synthetic'):
    return dict(workload=label, arm=arm, round=number, source=str(path), status='passed', role='measured',
                configured_target_cache_hits=observations['configured_target_cache_hits'],
                **successful_metrics(metrics, kind))


def source_snapshot(root, manifest):
    for name, binary in manifest['binaries'].items():
        check_hash(Path(binary['path']), binary['sha256'])
        snapshot = binary.get('source_snapshot', {})
        if not re.fullmatch('[0-9a-f]{40}', snapshot.get('commit', '')):
            raise ValueError('missing declared source commit')
        check_hash(root / 'provenance' / (name + '-source.diff'), snapshot['tracked_diff_sha256'])
    for path, expected in manifest['harnesses'].items():
        check_hash(root / 'provenance' / Path(path).name, expected)
    if baseline.inventory(root / 'workspace') != manifest['fixture_sha256']:
        raise ValueError('frozen synthetic fixture inventory changed')


def synthetic(label, root):
    root = Path(root).resolve(strict=True)
    manifest, result = read(root / 'manifest.json'), read(root / 'summary.json')
    if result.get('status') != 'passed':
        raise ValueError('synthetic cohort did not pass: ' + str(root))
    # This also rejects incomplete/reordered runs; never trust cached statistics alone.
    computed = comparison.summarize(result['runs'])
    if read(root / 'statistics.json') != computed:
        raise ValueError('synthetic statistics disagree with recorded raw metrics')
    source_snapshot(root, manifest)
    rows, producers = [], []
    for row in result['runs']:
        directory = root / 'runs' / row['label']
        check_files(directory, row['raw_artifacts_sha256'], REQUIRED_RAW)
        check_files(directory / 'diagnostics', row['diagnostics_sha256'])
        if read(directory / 'metrics.json') != row['metrics']:
            raise ValueError('summary differs from raw command metrics')
        command_spec = manifest['producer_spec'] if row['role'] == 'producer' else manifest['consumer_spec']
        validation = baseline.validate_outputs(directory / 'build.bep.json', directory / 'output-base', command_spec)
        if row.get('validation') != validation:
            raise ValueError('recorded output validation differs from revalidation')
        observations = comparison.bep_observations(stream(directory / 'build.bep.json'))
        comparison.validate_cache_hits(row['arm'], row['role'], observations)
        if observations != row['bep_observations']:
            raise ValueError('BEP observations differ from recorded cache/action counts')
        if row['role'] == 'measured':
            rows.append(sample(label, directory, row['arm'], row['round'], row['metrics'], observations))
        elif row['role'] == 'producer':
            producers.append(dict(workload=label, arm=row['arm'], source=str(directory),
                                  **successful_metrics(row['metrics'])))
    return rows, producers, {
        'kind': 'synthetic', 'path': str(root), 'summary_sha256': sha(root / 'summary.json'),
        'manifest_sha256': sha(root / 'manifest.json'), 'source_binaries': manifest['binaries'],
        'statistics': computed, 'verification': '27 passed commands; raw/source/harness/input hashes, complete BEPs, actual output bytes and cache-hit gates revalidated.',
    }


def real_workload(root):
    root = Path(root).resolve(strict=True)
    result = read(root / 'results.json')
    if result.get('status') != 'passed' or result.get('rounds') != 6 or result.get('smoke_gate') != 'passed':
        raise ValueError('Datadog requires a passed full six-round cohort')
    if not result.get('source_unchanged') or result['workspace_before'] != result['workspace_after']:
        raise ValueError('Datadog source changed during the run')
    if datadog.workspace_state(Path(result['workspace'])) != result['workspace_before']:
        raise ValueError('Datadog fixture no longer matches its recorded source snapshot')
    check_hash(root / 'harness.py', result['harness_sha256'])
    check_hash(root / 'paired_baseline.py', result['paired_helper_sha256'])
    check_hash(Path(result['canonical_flags_source']), result['canonical_flags_source_sha256'])
    provenance = result['provenance']
    for name in ('vanilla', 'candidate'):
        binary = provenance[name]
        check_hash(Path(binary['binary']), binary['sha256'])
        commit = binary.get('source_commit') or binary.get('built_source_commit')
        if not re.fullmatch('[0-9a-f]{40}', commit or ''):
            raise ValueError('Datadog binary lacks its declared source commit')
        build = binary['build_options']
        check_hash(Path(build['bep']), build['bep_sha256'])
    candidate = provenance['candidate']
    check_hash(Path(candidate['source_binding']), candidate['source_binding_sha256'])
    expected = [(index + 1, arm) for index, order in enumerate(itertools.permutations(ARMS)) for arm in order]
    measured = [row for row in result['runs'] if not row['preparation']]
    actual = [(int(row['name'].split('-', 1)[0][1:]), row['arm']) for row in measured]
    if actual != expected or any(row.get('status') != 'passed' for row in result['runs']):
        raise ValueError('Datadog measured rows must be all 18 balanced commands, with every preparation passed')
    oracle = read(root / 'expected-outputs.json')
    rows = []
    for row in result['runs']:
        directory = root / row['name']
        check_files(directory, row['checksums'], REQUIRED_RAW | {'outputs.json'})
        if read(directory / 'metrics.json') != row['metrics']:
            raise ValueError('Datadog raw metrics differ from summary')
        successful_metrics(row['metrics'], 'datadog')
        validation = datadog.output_manifest(directory / 'build.bep.json', directory / 'output-base')
        if not row.get('outputs_match') or validation != read(directory / 'outputs.json') or validation['outputs'] != oracle:
            raise ValueError('Datadog default output bytes differ from vanilla oracle')
        observations = comparison.bep_observations(stream(directory / 'build.bep.json'))
        if not row['preparation']:
            comparison.validate_cache_hits(row['arm'], 'measured', observations)
            rows.append(sample('Datadog six targets', directory, row['arm'],
                               int(row['name'].split('-', 1)[0][1:]) - 1,
                               row['metrics'], observations, 'datadog'))
        elif row['arm'] != 'fork-on':
            comparison.validate_cache_hits(row['arm'], 'warmup', observations)
    return rows, {
        'kind': 'datadog', 'path': str(root), 'results_sha256': sha(root / 'results.json'),
        'provenance': provenance, 'preparation_commands_excluded': len(result['runs']) - 18,
        'producer_accounting': 'Unavailable: first three preparation commands were order/cache-state confounded. No causal producer or amortization comparison is exported.',
        'verification': '18 measured commands plus passed preparations; binary/build/source-binding/raw hashes, fixture state, complete BEPs, actual default bytes and measured cache-hit gates revalidated.',
    }


def nonnegative(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid retained metric: ' + name)
    return value


def successful_gc_phases(arm_dir, diagnostics):
    for key in ('forced-gc', 'heap-after-gc'):
        record = diagnostics.get(key, {})
        if record.get('exit_code') != 0 or record.get('timed_out') is not False:
            return False
        raw = read(arm_dir / key / 'metrics.json')
        if any(record.get(field) != value for field, value in raw.items()):
            raise ValueError('heap diagnostic summary differs from raw process metrics')
    return True


def parse_heap_info(text):
    """Recognize JDK 27 G1 reserved/committed output as well as the legacy format."""
    match = re.search(r'\bgarbage-first heap\s+total reserved\s+(\d+)([KMG]),\s+'
                      r'committed\s+(\d+)([KMG]),\s+used\s+(\d+)([KMG])\b', text)
    if match:
        scale = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}
        return {key: int(match[index]) * scale[match[index + 1]]
                for key, index in (('reserved_heap_bytes', 1), ('committed_heap_bytes', 3), ('used_heap_bytes', 5))}
    return retained.heap_usage(text)


def post_gc_heap(arm_dir, diagnostics):
    """Recover from verified raw text; any previously recognized values must agree."""
    if not successful_gc_phases(arm_dir, diagnostics):
        return None
    path = arm_dir / 'heap-after-gc' / 'stdout.log'
    if not path.is_file():
        return None
    parsed = parse_heap_info(path.read_text())
    recorded = diagnostics['heap-after-gc'].get('heap') or {}
    for key in ('used_heap_bytes', 'committed_heap_bytes', 'reserved_heap_bytes'):
        if recorded.get(key) is not None and recorded[key] != parsed.get(key):
            raise ValueError('recorded heap value disagrees with verified raw heap text')
    return parsed.get('used_heap_bytes')


def retained_series(label, root):
    root = Path(root).resolve(strict=True)
    manifest, result = read(root / 'manifest.json'), read(root / 'summary.json')
    if result.get('status') != 'passed' or [arm['arm'] for arm in result['arms']] != list(ARMS):
        raise ValueError('retained cohort must contain three passed arm series')
    for path, expected in manifest['helpers'].items():
        check_hash(Path(path), expected)
    for binary in manifest['binaries'].values():
        check_hash(Path(binary['path']), binary['sha256'])
    workspace = Path(manifest['workspace'])
    if baseline.inventory(workspace) != manifest['workspace_sha256']:
        raise ValueError('retained fixture changed')
    check_hash(Path(manifest['producer_command']), manifest['producer_command_sha256'])
    producer_manifest_path = workspace.parent / 'manifest.json'
    producer_manifest = read(producer_manifest_path)
    for name, binary in manifest['binaries'].items():
        if binary['sha256'] != producer_manifest['binaries'][name]['sha256']:
            raise ValueError('retained binary differs from source-declared producer binary')
    source_snapshot(workspace.parent, producer_manifest)
    arms = {}
    for arm in result['arms']:
        if arm.get('status') != 'passed' or [build['number'] for build in arm['builds']] != list(range(7)):
            raise ValueError('retained arm must have seven successful builds')
        if arm.get('shutdown', {}).get('status') != 'stopped':
            raise ValueError('retained server shutdown was not verified')
        arm_dir = root / 'runs' / arm['arm']
        required = {f'build-{index:02d}/{name}' for index in range(7) for name in REQUIRED_RAW | {'process.json'}}
        diagnostics = arm.get('heap_diagnostics', {})
        for phase in ('forced-gc', 'heap-after-gc'):
            if phase in diagnostics:
                required.update(f'{phase}/{name}' for name in ('metrics.json', 'stdout.log', 'command.json'))
        check_files(arm_dir, arm['artifacts_sha256'], required)
        latency, cpu = [], []
        for build in arm['builds']:
            directory = arm_dir / f'build-{build["number"]:02d}'
            metrics = read(directory / 'metrics.json')
            if metrics != build['metrics'] or metrics.get('exit_code') != 0 or metrics.get('timed_out'):
                raise ValueError('retained raw metrics do not prove success')
            validation = baseline.validate_outputs(directory / 'build.bep.json', arm_dir / 'output-base', manifest['fixture'])
            if validation != build['validation'] or not validation['passed']:
                raise ValueError('retained output verification differs')
            observations = comparison.bep_observations(stream(directory / 'build.bep.json'))
            if arm['arm'] != 'fork-on' or build['number'] == 0:
                comparison.validate_cache_hits(arm['arm'], 'measured', observations)
            process = read(directory / 'process.json')
            if process != {'before': build['before'], 'after': build['after']}:
                raise ValueError('retained process evidence differs from summary')
            if build['number']:
                delta = retained.server_cpu_delta(process['before'], process['after'])
                if delta != build['server_cpu_between_snapshots_seconds']:
                    raise ValueError('retained CPU delta disagrees with raw ticks')
                latency.append(nonnegative(metrics['elapsed_seconds'], 'latency'))
                cpu.append(nonnegative(delta, 'server CPU'))
        after = arm['builds'][-1]['after']
        rss = retained.kib_fields(after['status_raw'], ('VmRSS',)).get('VmRSS_kib')
        pss = retained.kib_fields(after.get('smaps_rollup_raw', ''), ('Pss',)).get('Pss_kib')
        heap_used = post_gc_heap(arm_dir, diagnostics)
        post_gc = diagnostics.get('heap-after-gc', {}).get('process', {}) if successful_gc_phases(arm_dir, diagnostics) else {}
        if post_gc and post_gc.get('identity') != after.get('identity'):
            raise ValueError('post-GC process identity differs from the retained server')
        arms[arm['arm']] = {
            'retained_latency_seconds': comparison.descriptive(latency),
            'retained_server_cpu_seconds': comparison.descriptive(cpu),
            'after_series_rss_kib': rss, 'after_series_pss_kib': pss,
            'post_requested_gc_heap_used_bytes': heap_used,
            'post_requested_gc_heap_parsing_origin': ('Offline parse of checksum-verified raw GC.heap_info stdout after successful GC.run; native metadata remains unchanged.' if heap_used is not None else None),
            'post_requested_gc_rss_kib': retained.kib_fields(post_gc.get('status_raw', ''), ('VmRSS',)).get('VmRSS_kib'),
            'post_requested_gc_pss_kib': retained.kib_fields(post_gc.get('smaps_rollup_raw', ''), ('Pss',)).get('Pss_kib'),
            'initial_latency_seconds': arm['builds'][0]['metrics']['elapsed_seconds'],
        }
    return {'workload': label, 'path': str(root), 'arms': arms,
            'manifest_sha256': sha(root / 'manifest.json'), 'summary_sha256': sha(root / 'summary.json'),
            'source_producer_manifest_sha256': sha(producer_manifest_path),
            'limitations': ['Initial builds are sequential and install-state confounded, not a balanced comparison.',
                           'Six retained requests reuse resident Skyframe; CPU covers only the owned server between boundary snapshots.',
                           'RSS/PSS are process snapshots; post-requested-GC used heap is not an object retained-size census. Unknown diagnostics are null.']}


def describe(rows):
    result = {}
    for workload in dict.fromkeys(row['workload'] for row in rows):
        result[workload] = {}
        for arm in ARMS:
            samples = [row for row in rows if row['workload'] == workload and row['arm'] == arm]
            if len(samples) != 6 or {row['round'] for row in samples} != set(range(6)):
                raise ValueError('every workload/arm must have exactly six measured rounds')
            result[workload][arm] = {metric: comparison.descriptive([row[metric] for row in samples])
                                      for metric in comparison.METRICS}
    return result


def write_csv(path, rows, fields):
    with Path(path).open('w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n', extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def labeled(value):
    name, separator, path = value.partition('=')
    if not separator or not name.strip() or not path:
        raise argparse.ArgumentTypeError('use LABEL=PATH')
    return name, Path(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=labeled, action='append', default=[])
    parser.add_argument('--datadog', type=Path)
    parser.add_argument('--retained', type=labeled, action='append', default=[])
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    labels = [label for label, unused in args.cohort] + (['Datadog six targets'] if args.datadog else [])
    if not labels or len(labels) != len(set(labels)):
        raise ValueError('supply nonempty, uniquely named batch cohorts')
    if len({label for label, unused in args.retained}) != len(args.retained):
        raise ValueError('retained cohort labels must be unique')
    if args.output_dir.exists() or args.output_dir.is_symlink():
        raise ValueError('output directory must be new')
    rows, producers, sources, resident = [], [], {}, []
    for label, path in args.cohort:
        samples, writes, source = synthetic(label, path)
        rows.extend(samples)
        producers.extend(writes)
        sources[label] = source
    if args.datadog:
        samples, sources['Datadog six targets'] = real_workload(args.datadog)
        rows.extend(samples)
    for label, path in args.retained:
        resident.append(retained_series(label, path))
    summary = {'schema_version': 1, 'status': 'passed', 'batch': describe(rows),
               'sources': sources, 'retained': resident,
               'validation_implementation': {str(Path(module)): sha(module) for module in
                                             (__file__, comparison.__file__, datadog.__file__, retained.__file__, baseline.__file__)},
               'selection': 'Operator-selected quiet cohorts only; host quietness is established by the separately archived quiet-window record, not inferred by this tool.',
               'limits': ['Six samples per measured batch arm; descriptive median/min/max only, no representative p95, confidence interval or causal speedup claim.',
                          'Batch RSS is maximum child-process RSS, not retained heap. Synthetic producer rows are equal-request observations; Datadog preparation rows are excluded from producer economics.']}
    args.output_dir.mkdir()
    baseline.save(args.output_dir / 'rows.json', rows)
    baseline.save(args.output_dir / 'summary.json', summary)
    fields = ['workload', 'arm', 'round', *comparison.METRICS, 'configured_target_cache_hits', 'source']
    write_csv(args.output_dir / 'metrics.csv', rows, fields)
    write_csv(args.output_dir / 'producer.csv', producers, ['workload', 'arm', *comparison.METRICS, 'source'])
    print(args.output_dir)


if __name__ == '__main__':
    main()
