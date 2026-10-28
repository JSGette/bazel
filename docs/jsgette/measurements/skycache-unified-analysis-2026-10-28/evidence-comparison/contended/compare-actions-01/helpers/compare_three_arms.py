#!/usr/bin/env python3
"""Compare vanilla, fork without Skycache, and fork with Skycache using fixed balanced rounds."""

import argparse
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import sys

HERE = Path(__file__).resolve().parent
WORKER = HERE / 'paired_baseline.py'
module_spec = importlib.util.spec_from_file_location('three_arm_paired_baseline', WORKER)
baseline = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(baseline)

ARMS = ('vanilla', 'fork-off', 'fork-on')
CUSTOM_PREFIXES = ('--experimental_local_skycache', '--experimental_remote_skycache',
                   '--experimental_analysis_cache_idle_retention')
METRICS = ('elapsed_seconds', 'user_cpu_seconds', 'system_cpu_seconds', 'total_cpu_seconds', 'maxrss_kib')


def schedule():
    rows = []
    for round_number, arms in enumerate((ARMS, tuple(reversed(ARMS)))):
        rows.extend({'label': f'w{round_number:02d}-{arm}', 'role': 'warmup',
                     'arm': arm, 'round': round_number} for arm in arms)
    rows.extend({'label': 'producer-' + arm, 'role': 'producer', 'arm': arm, 'round': None}
                for arm in ARMS)
    for round_number, arms in enumerate(itertools.permutations(ARMS)):
        rows.extend({'label': f'r{round_number:02d}-{arm}', 'role': 'measured',
                     'arm': arm, 'round': round_number} for arm in arms)
    return rows


def command(binary, binary_root, run, cache, targets, heap, jobs, arm):
    if arm not in ARMS or not 1 <= jobs <= 6 or heap <= 0:
        raise ValueError('invalid arm, heap, or jobs (must be 1..6)')
    if (arm == 'fork-on') != (cache is not None):
        raise ValueError('only fork-on commands must receive a Skycache directory')
    # Explicit upstream-compatible common flags: vanilla never receives custom fork options.
    argv = [str(binary), '--batch', '--ignore_all_rc_files',
            f'--output_user_root={binary_root / "bazel-user"}', f'--output_base={run / "output-base"}',
            f'--host_jvm_args=-Xmx{heap}m',
            f'--host_jvm_args=-Xlog:gc*:file={run / "gc.log"}:time,uptime,level,tags',
            'build', '--color=no', '--curses=no', '--noshow_progress', '--lockfile_mode=off',
            f'--jobs={jobs}', f'--repository_cache={binary_root / "repository-cache"}',
            f'--symlink_prefix={binary_root / "links" / (run.name + "-")}',
            f'--build_event_json_file={run / "build.bep.json"}', f'--profile={run / "build.profile.gz"}',
            '--remote_cache=', '--disk_cache=']
    if arm != 'vanilla':
        fork_command = baseline.command(binary, binary_root, run, cache, targets, heap)
        argv.extend(flag for flag in fork_command if flag.startswith(CUSTOM_PREFIXES))
    return argv + ['--', *targets]


def resource_metrics(metrics):
    if metrics.get('exit_code') != 0 or metrics.get('timed_out'):
        raise ValueError('command failed or timed out')
    resources = metrics.get('resources', {})
    if resources.get('ru_maxrss_unit') != 'KiB':
        raise ValueError('Linux child-process maximum RSS in KiB is required')
    values = {'elapsed_seconds': metrics['elapsed_seconds'],
              'user_cpu_seconds': resources['user_cpu_seconds'],
              'system_cpu_seconds': resources['system_cpu_seconds'],
              'maxrss_kib': resources['ru_maxrss_raw']}
    values['total_cpu_seconds'] = values['user_cpu_seconds'] + values['system_cpu_seconds']
    for key, value in values.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f'invalid resource metric: {key}')
    if values['elapsed_seconds'] == 0 or values['maxrss_kib'] == 0:
        raise ValueError('elapsed time and maximum RSS must be positive')
    return values


def bep_observations(events):
    """Missing upstream cache metrics mean zero observed hits, not a parse failure."""
    entries = [event['buildMetrics'] for event in events if 'buildMetrics' in event]
    if len(entries) > 1:
        raise ValueError('BEP contains duplicate buildMetrics events')
    metrics = entries[0] if entries else {}
    cache = metrics.get('remoteAnalysisCacheStatistics', {})
    hits = sum(int(item.get('value', 0)) for item in cache.get('cacheHitsBySkyfunction', [])
               if item.get('key') == 'CONFIGURED_TARGET')
    if hits < 0:
        raise ValueError('negative configured-target cache-hit count')
    actions = metrics.get('actionSummary', {})
    return {
        'configured_target_cache_hits': hits,
        'cache_statistics_present': 'remoteAnalysisCacheStatistics' in metrics,
        'actions_executed': int(actions.get('actionsExecuted', 0)) if actions else None,
        'action_data': [{'mnemonic': item.get('mnemonic'),
                         'actions_created': int(item.get('actionsCreated', 0)),
                         'actions_executed': int(item.get('actionsExecuted', 0))}
                        for item in actions.get('actionData', [])],
    }


def validate_cache_hits(arm, role, observations):
    hits = observations['configured_target_cache_hits']
    if arm in ('vanilla', 'fork-off') and hits != 0:
        raise ValueError(f'{arm} unexpectedly restored {hits} configured targets')
    if arm == 'fork-on' and role == 'measured' and hits <= 0:
        raise ValueError('measured fork-on command restored no configured targets')


def descriptive(values):
    values = [value for value in values if value is not None]
    return {'count': len(values), 'median': statistics.median(values),
            'min': min(values), 'max': max(values)} if values else {'count': 0}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def totals(rows):
    values = [resource_metrics(row['metrics']) for row in rows]
    return {key: (max(value[key] for value in values) if key == 'maxrss_kib'
                  else sum(value[key] for value in values)) for key in METRICS}


def summarize(rows):
    if len(rows) != 27 or any(row.get('status') != 'passed' for row in rows):
        raise ValueError('statistics require the complete successful 27-command sequence')
    if [{key: row[key] for key in ('label', 'role', 'arm', 'round')} for row in rows] != schedule():
        raise ValueError('recorded sequence differs from the fixed balanced schedule')
    measured = [row for row in rows if row['role'] == 'measured']
    by_arm = {arm: [row for row in measured if row['arm'] == arm] for arm in ARMS}
    descriptive_by_arm = {arm: {metric: descriptive([resource_metrics(row['metrics'])[metric]
                                                    for row in arm_rows]) for metric in METRICS}
                          for arm, arm_rows in by_arm.items()}
    round_ratios = []
    comparisons = [('fork-off', 'vanilla'), ('fork-on', 'vanilla'), ('fork-on', 'fork-off')]
    for number in range(6):
        values = {row['arm']: resource_metrics(row['metrics']) for row in measured if row['round'] == number}
        round_ratios.append({'round': number, 'ratios': {
            f'{numerator}/{denominator}': {metric: ratio(values[numerator][metric], values[denominator][metric])
                                         for metric in METRICS}
            for numerator, denominator in comparisons}})
    ratio_summary = {f'{a}/{b}': {metric: descriptive([row['ratios'][f'{a}/{b}'][metric]
                                                       for row in round_ratios]) for metric in METRICS}
                     for a, b in comparisons}
    producers = {row['arm']: row for row in rows if row['role'] == 'producer'}
    amortization = []
    for count in range(1, 7):
        demand = {arm: totals(arm_rows[:count]) for arm, arm_rows in by_arm.items()}
        charged = {arm: totals([producers[arm]] + by_arm[arm][:count]) for arm in ARMS}
        amortization.append({'consumer_commands': count, 'demand_only': demand,
                             'producer_plus_consumers': charged,
                             'producer_inclusive_ratios': {
                                 f'{a}/{b}': {metric: ratio(charged[a][metric], charged[b][metric])
                                             for metric in METRICS} for a, b in comparisons}})
    return {
        'schema_version': 1, 'descriptive_by_arm': descriptive_by_arm,
        'round_ratios': round_ratios, 'descriptive_round_ratios': ratio_summary,
        'producer_by_arm': {arm: resource_metrics(row['metrics']) for arm, row in producers.items()},
        'amortization': amortization, 'entire_recorded_sequence': totals(rows),
        'recorded_sequence_by_role': {role: totals([row for row in rows if row['role'] == role])
                                      for role in ('producer', 'warmup', 'measured')},
        'interpretation': {
            'sample': 'Six measured observations per arm; descriptive summaries only, no p95/tail/confidence claim.',
            'ratios': 'Numerator divided by denominator within each balanced round; lower is less. Zero CPU denominators produce null, not infinity.',
            'rss': 'Maximum child-process RSS, not retained heap or simultaneous process-tree sum. Sequence RSS is the maximum, never a sum.',
            'amortization': 'Equal-request accounting: one separately measured producer-spec command on each arm plus its first N consumer commands. Producer can use a broader manifest; all arms use the same producer spec. Producer results are single observations, not causal overhead estimates.',
            'sequence': 'Entire recorded sequence includes three producers and six warmups; measured arm summaries exclude them. Sequence elapsed/CPU values sum recorded command costs, not idle/interleaving time. No estimated break-even is inferred.',
            'environment': 'Fresh batch JVM/output base per command; per-binary warmed install/repository caches; shared host OS pages and network remain uncontrolled.',
        },
    }


def experiment(args):
    if not sys.platform.startswith('linux'):
        raise ValueError('run experiments on Linux/WSL')
    if not 1 <= args.jobs <= 6 or args.heap_mb <= 0 or args.timeout_seconds <= 0:
        raise ValueError('jobs must be 1..6; heap and timeout must be positive')
    fixture = args.manifest.resolve(strict=True)
    producer_fixture = (args.producer_manifest or fixture).resolve(strict=True)
    if producer_fixture.parent != fixture.parent:
        raise ValueError('producer and consumer manifests must share one fixture directory')
    spec, producer_spec = baseline.load_fixture(fixture), baseline.load_fixture(producer_fixture)
    inputs = baseline.inventory(fixture.parent)
    binary_paths = {'vanilla': args.vanilla_bazel.resolve(strict=True),
                    'current': args.current_bazel.resolve(strict=True)}
    sources = {}
    patches = {}
    for name in binary_paths:
        repo = getattr(args, name + '_source_repo').resolve(strict=True)
        sources[name], patches[name] = baseline.source_provenance(repo, getattr(args, name + '_source_commit'))
    root = args.run_dir.absolute()
    if root.exists() or root.is_symlink() or root.resolve().is_relative_to(fixture.parent):
        raise ValueError('run directory must be new and outside the fixture')
    root.mkdir()
    root = root.resolve()
    workspace = root / 'workspace'
    shutil.copytree(fixture.parent, workspace)
    for name in ('home', 'tmp', 'runs', 'caches', 'provenance'):
        (root / name).mkdir()
    for name in binary_paths:
        (root / 'binary-state' / name / 'links').mkdir(parents=True)
    environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'HOME': str(root / 'home'),
                   'TMPDIR': str(root / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'TZ': 'UTC'}
    if 'JAVA_HOME' in os.environ:
        environment['JAVA_HOME'] = os.environ['JAVA_HOME']
    manifest = {
        'schema_version': 1, 'binaries': {name: {'path': str(path), 'sha256': baseline.digest(path),
                                               'source_snapshot': sources[name]}
                                         for name, path in binary_paths.items()},
        'binary_source_binding': 'Operator-declared source snapshots; byte hashes are verified during the run, but source-to-binary derivation is not verified.',
        'harnesses': {str(Path(__file__).resolve()): baseline.digest(__file__),
                      str(WORKER): baseline.digest(WORKER)},
        'fixture_sha256': inputs, 'consumer_spec': spec, 'producer_spec': producer_spec,
        'manifest_files': {'consumer': {'path': str(fixture), 'sha256': baseline.digest(fixture)},
                           'producer': {'path': str(producer_fixture), 'sha256': baseline.digest(producer_fixture)}},
        'environment': environment, 'host': baseline.host_description(),
        'heap_mb': args.heap_mb, 'jobs': args.jobs, 'fork_settings': baseline.SETTINGS,
        'cache_hit_gate': 'Every measured fork-on command must report positive CONFIGURED_TARGET cache hits in BEP; vanilla and fork-off must report zero. Missing upstream cache statistics count as zero observed hits. Producer/warmup fork-on hits are recorded without a positive-hit requirement.',
        'schedule': schedule(),
        'method': 'Two excluded initialization warmup rounds in opposite orders, with independent empty caches for fork-on warmups; one separately measured producer-spec command per arm; six measured rounds using all six arm permutations. Fork-off/on share current-binary install/repository state; vanilla has separate binary state. Every command has a fresh output base; measured fork-on consumers copy only the designated producer seed.',
    }
    baseline.save(root / 'manifest.json', manifest)
    for name, patch in patches.items():
        (root / 'provenance' / (name + '-source.diff')).write_bytes(patch)
    for path in manifest['harnesses']:
        shutil.copyfile(path, root / 'provenance' / Path(path).name)
    summary = {'schema_version': 1, 'status': 'running', 'runs': []}
    seed = root / 'caches' / 'producer-seed'

    def verify_identity():
        for path, expected in manifest['harnesses'].items():
            if baseline.digest(path) != expected:
                raise ValueError('harness changed during the experiment: ' + path)
        for name, path in binary_paths.items():
            if baseline.digest(path) != manifest['binaries'][name]['sha256']:
                raise ValueError('binary changed during the experiment: ' + name)
        if baseline.inventory(workspace) != inputs:
            raise ValueError('fixture inputs changed during the experiment')

    try:
        for planned in schedule():
            row = dict(planned, status='running')
            summary['runs'].append(row)
            baseline.save(root / 'summary.json', summary)
            directory = root / 'runs' / row['label']
            try:
                verify_identity()
                cache = None
                if row['arm'] == 'fork-on':
                    cache = seed if row['role'] == 'producer' else root / 'caches' / row['label']
                    if row['role'] == 'measured':
                        shutil.copytree(seed, cache, symlinks=False)
                binary_name = 'vanilla' if row['arm'] == 'vanilla' else 'current'
                command_spec = producer_spec if row['role'] == 'producer' else spec
                row['metrics'] = baseline.run_command(
                    directory, command(binary_paths[binary_name], root / 'binary-state' / binary_name,
                                       directory, cache, command_spec['targets'], args.heap_mb, args.jobs, row['arm']),
                    workspace, environment, args.timeout_seconds, manifest['harnesses'][str(WORKER)])
                verify_identity()
                resource_metrics(row['metrics'])
                row['validation'] = baseline.validate_outputs(directory / 'build.bep.json',
                                                                directory / 'output-base', command_spec)
                events = [json.loads(line) for line in (directory / 'build.bep.json').read_text().splitlines() if line]
                row['bep_observations'] = bep_observations(events)
                validate_cache_hits(row['arm'], row['role'], row['bep_observations'])
                if row['role'] == 'producer' and row['arm'] == 'fork-on' and not list(seed.rglob('*.manifest')):
                    raise ValueError('producer did not publish a Skycache seed')
                row['status'] = 'passed'
            except Exception as error:
                row['status'] = 'failed'
                row['error'] = str(error)
                raise
            finally:
                if directory.exists():
                    row['diagnostics_sha256'] = baseline.copy_diagnostics(directory)
                    row['raw_artifacts_sha256'] = {path.name: baseline.digest(path)
                                                   for path in directory.iterdir() if path.is_file()}
                baseline.save(root / 'summary.json', summary)
        baseline.save(root / 'statistics.json', summarize(summary['runs']))
        summary['status'] = 'passed'
    except Exception as error:
        summary['status'] = 'failed'
        summary['error'] = str(error)
        raise
    finally:
        baseline.save(root / 'summary.json', summary)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('vanilla', 'current'):
        parser.add_argument('--' + name + '-bazel', type=Path, required=True)
        parser.add_argument('--' + name + '-source-repo', type=Path, required=True)
        parser.add_argument('--' + name + '-source-commit', required=True, help='full expected source HEAD')
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--producer-manifest', type=Path)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--heap-mb', type=int, default=2048)
    parser.add_argument('--jobs', type=int, default=6, help='same cap for all arms; 1..6')
    parser.add_argument('--timeout-seconds', type=float, default=180)
    print(experiment(parser.parse_args()))


if __name__ == '__main__':
    main()
