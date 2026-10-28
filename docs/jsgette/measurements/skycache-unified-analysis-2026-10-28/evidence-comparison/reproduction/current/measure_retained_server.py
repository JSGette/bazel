#!/usr/bin/env python3
"""Supplement three-arm batch evidence with seven builds per owned persistent server."""

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import compare_three_arms as comparison

baseline = comparison.baseline
HERE = Path(__file__).resolve().parent


def persistent_command(binary, binary_root, run, cache, targets, heap, jobs, arm, output_base):
    argv = comparison.command(binary, binary_root, run, cache, targets, heap, jobs, arm)
    result = []
    for flag in argv:
        if flag == '--batch':
            continue
        if flag.startswith('--output_base='):
            flag = '--output_base=' + str(output_base)
        elif flag.startswith('--symlink_prefix='):
            flag = '--symlink_prefix=' + str(output_base.parent / 'links' / (run.name + '-'))
        elif flag.startswith('--host_jvm_args=-Xlog:gc*:file='):
            # Startup arguments must remain identical or Bazel restarts the retained server.
            flag = f'--host_jvm_args=-Xlog:gc*:file={output_base.parent / "server-gc.log"}:time,uptime,level,tags'
        if flag == 'build':
            result.append('--max_idle_secs=120')
        result.append(flag)
    return result


def producer_binary_states(workspace):
    """Retain repository bindings authenticated by the batch producer's witnesses."""
    roots = {}
    for name, arm in (('vanilla', 'vanilla'), ('current', 'fork-on')):
        root = workspace.parent / 'binary-state' / name
        command = json.loads((workspace.parent / 'runs' / ('producer-' + arm) / 'command.json').read_text())
        if Path(command['cwd']).resolve() != workspace.resolve():
            raise ValueError('producer command refers to a different workspace')
        for option, directory in (('--output_user_root=', 'bazel-user'), ('--repository_cache=', 'repository-cache')):
            if [flag for flag in command['argv'] if flag.startswith(option)] != [option + str(root / directory)]:
                raise ValueError('producer command uses unexpected install/repository paths')
            if not (root / directory).is_dir():
                raise ValueError('existing producer install/repository state is required')
        roots[name] = root
    return roots

def parse_proc_stat(text):
    """The comm field can contain spaces and parentheses; fields after its final ')' are fixed."""
    tail = text[text.rindex(')') + 2:].split()
    return {'state': tail[0], 'user_ticks': int(tail[11]), 'system_ticks': int(tail[12]),
            'start_ticks': int(tail[19])}


def owns_output_base(arguments, output_base):
    expected = str(Path(output_base).resolve())
    return '--output_base=' + expected in arguments or any(
        flag == '--output_base' and arguments[index + 1:index + 2] == [expected]
        for index, flag in enumerate(arguments))


def owned_server(output_base, expected=None):
    pidfile = output_base / 'server/server.pid.txt'
    pid = int(pidfile.read_text().strip())
    proc = Path('/proc') / str(pid)
    arguments = [os.fsdecode(part) for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
    identity = {'pid': pid, 'start_ticks': parse_proc_stat((proc / 'stat').read_text())['start_ticks']}
    if not owns_output_base(arguments, output_base) or (expected is not None and identity != expected):
        raise ValueError('server PID/start time/output-base ownership differs from this arm')
    if int(pidfile.read_text().strip()) != pid:
        raise ValueError('server PID file changed during ownership check')
    return identity, arguments


def kib_fields(text, names):
    result = {}
    for name in names:
        match = re.search(r'^' + re.escape(name) + r':\s+(\d+)\s+kB$', text, re.MULTILINE)
        result[name + '_kib'] = int(match[1]) if match else None
    return result


def sample_server(output_base, expected=None):
    identity, arguments = owned_server(output_base, expected)
    proc = Path('/proc') / str(identity['pid'])
    status = (proc / 'status').read_text()
    sample = {'identity': identity, 'cmdline': arguments, 'status_raw': status,
              'cpu_ticks': parse_proc_stat((proc / 'stat').read_text()),
              'clock_ticks_per_second': os.sysconf('SC_CLK_TCK'),
              **kib_fields(status, ('VmRSS', 'VmHWM', 'VmSize'))}
    try:
        rollup = (proc / 'smaps_rollup').read_text()
        sample.update(smaps_rollup_raw=rollup, **kib_fields(rollup, ('Rss', 'Pss', 'Private_Clean', 'Private_Dirty')))
    except OSError as error:
        sample['smaps_rollup_unavailable'] = str(error)
    owned_server(output_base, identity)
    return sample


def server_cpu_delta(before, after):
    if before is None:
        return None  # The server did not exist before its first timed build.
    if before['identity'] != after['identity']:
        raise ValueError('cannot compare CPU across different server processes')
    if before['clock_ticks_per_second'] != after['clock_ticks_per_second']:
        raise ValueError('process clock frequency changed')
    ticks = sum(after['cpu_ticks'][key] - before['cpu_ticks'][key]
                for key in ('user_ticks', 'system_ticks'))
    if ticks < 0:
        raise ValueError('server CPU ticks decreased')
    return ticks / after['clock_ticks_per_second']


def copy_diagnostics(directory, output_base):
    paths = sorted(output_base.glob('local-skycache*.json'))
    if not paths:
        return {}
    (directory / 'diagnostics').mkdir()
    result = {}
    for path in paths:
        baseline.inside(path, output_base)
        copy = directory / 'diagnostics' / path.name
        shutil.copyfile(path, copy)
        result[path.name] = baseline.digest(copy)
    return result


def run_process(directory, argv, workspace, environment, timeout):
    directory.mkdir()
    baseline.save(directory / 'command.json', {'argv': argv, 'cwd': str(workspace),
                                             'environment': environment, 'timeout_seconds': timeout})
    clock_id = getattr(time, 'CLOCK_MONOTONIC_RAW', None)
    clock = (lambda: time.clock_gettime(clock_id)) if clock_id is not None else time.monotonic
    result = {'clock': 'CLOCK_MONOTONIC_RAW' if clock_id is not None else 'monotonic',
              'started_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'timed_out': False}
    with (directory / 'stdout.log').open('wb') as stdout, (directory / 'stderr.log').open('wb') as stderr:
        start = clock()
        child = subprocess.Popen(argv, cwd=workspace, env=environment, stdout=stdout, stderr=stderr)
        try:
            result['exit_code'] = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            result['timed_out'] = True
            child.kill()  # Only this newly started client; the owned server is shut down in finally.
            result['exit_code'] = child.wait()
        result['elapsed_seconds'] = clock() - start
    # RUSAGE_CHILDREN describes this client, not its retained daemon; do not report it as server data.
    baseline.save(directory / 'metrics.json', result)
    return result


def heap_usage(text):
    match = re.search(r'\bheap\s+total\s+(\d+)([KMG]),\s+used\s+(\d+)([KMG])', text)
    if not match:
        return {'unavailable': 'Unsupported GC.heap_info format; raw output retained.'}
    scale = {'K': 1024, 'M': 1024 ** 2, 'G': 1024 ** 3}
    return {'committed_heap_bytes': int(match[1]) * scale[match[2]],
            'used_heap_bytes': int(match[3]) * scale[match[4]]}


def heap_diagnostics(arm_dir, output_base, identity, explicit_jcmd, workspace, environment):
    java = (Path('/proc') / str(identity['pid']) / 'exe').resolve(strict=True)
    jcmd = explicit_jcmd or java.parent / 'jcmd'
    result = {'timing_scope': 'Outside all build latency intervals', 'server_java': str(java), 'jcmd': str(jcmd)}
    if not jcmd.is_file():
        return {**result, 'unavailable': 'No jcmd beside the server java; supply --jcmd.'}
    result['jcmd_sha256'] = baseline.digest(jcmd)
    for label, operation in (('heap-before-gc', 'GC.heap_info'), ('forced-gc', 'GC.run'), ('heap-after-gc', 'GC.heap_info')):
        owned_server(output_base, identity)  # Recheck the owned PID before every attach/GC command.
        directory = arm_dir / label
        metrics = run_process(directory, [str(jcmd), str(identity['pid']), operation], workspace, environment, 30)
        result[label] = metrics
        if metrics['exit_code'] != 0 or metrics['timed_out']:
            result['unavailable'] = f'{operation} failed; no live-heap inference is made.'
            break
        if operation == 'GC.heap_info':
            result[label]['heap'] = heap_usage((directory / 'stdout.log').read_text())
        result[label]['process'] = sample_server(output_base, identity)
    return result


def shutdown_server(arm_dir, startup, output_base, identity, workspace, environment):
    pidfile = output_base / 'server/server.pid.txt'
    if not pidfile.exists():
        if identity is not None and (Path('/proc') / str(identity['pid'])).exists():
            raise ValueError('owned server still exists but its PID file disappeared; shutdown unverified')
        return {'status': 'not_running'}
    try:
        actual, unused = owned_server(output_base, identity)
    except FileNotFoundError:
        return {'status': 'already_exited'}
    result = run_process(arm_dir / 'shutdown', startup + ['shutdown'], workspace, environment, 45)
    if result['exit_code'] != 0 or result['timed_out']:
        raise ValueError('owned server shutdown failed; do not start another arm')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            process = parse_proc_stat((Path('/proc') / str(actual['pid']) / 'stat').read_text())
        except FileNotFoundError:
            process = None
        if process is None or process['start_ticks'] != actual['start_ticks'] or process['state'] == 'Z':
            result['status'] = 'stopped'
            return result
        time.sleep(0.1)
    raise ValueError('owned server remains after shutdown; do not start another arm')


def run(args):
    if not sys.platform.startswith('linux') or args.heap_mb <= 0 or not 1 <= args.jobs <= 6:
        raise ValueError('Linux/WSL, positive heap, and 1..6 jobs are required')
    workspace = args.workspace.resolve(strict=True)
    seed = args.seed.resolve(strict=True)
    manifest_path = args.manifest.resolve(strict=True)
    spec = baseline.load_fixture(manifest_path)
    # Namespace witnesses include effective environment values, not just source contents and path.
    producer_command = workspace.parent / 'runs/producer-fork-on/command.json'
    producer = json.loads(producer_command.read_text())
    if Path(producer['cwd']).resolve() != workspace or '--experimental_local_skycache=' + str(seed) not in producer['argv']:
        raise ValueError('seed/workspace must match the three-arm producer command')
    environment = producer['environment']
    binary_states = producer_binary_states(workspace)
    inputs, seed_inputs = baseline.inventory(workspace), baseline.inventory(seed)
    if not any(name.endswith('.manifest') for name in seed_inputs):
        raise ValueError('seed contains no published manifest')
    binaries = {'vanilla': args.vanilla_bazel.resolve(strict=True), 'current': args.candidate_bazel.resolve(strict=True)}
    jcmd = args.jcmd.resolve(strict=True) if args.jcmd else None
    root = args.run_dir.absolute()
    if root.exists() or root.is_symlink() or root.resolve().is_relative_to(workspace) or root.resolve().is_relative_to(seed):
        raise ValueError('run directory must be new and outside workspace/seed')
    root.mkdir()
    root = root.resolve()
    for name in ('runs', 'caches', 'provenance'):
        (root / name).mkdir()
    helpers = {str(path): baseline.digest(path) for path in (Path(__file__), Path(comparison.__file__), Path(baseline.__file__))}
    for path, expected in helpers.items():
        snapshot = root / 'provenance' / Path(path).name
        shutil.copyfile(path, snapshot)
        if baseline.digest(snapshot) != expected:
            raise ValueError('helper changed while archiving provenance')
    hashes = {name: baseline.digest(path) for name, path in binaries.items()}
    baseline.save(root / 'manifest.json', {
        'schema_version': 1, 'workspace': str(workspace), 'fixture': spec, 'fixture_manifest': str(manifest_path),
        'fixture_manifest_sha256': baseline.digest(manifest_path), 'workspace_sha256': inputs,
        'seed': str(seed), 'seed_sha256': seed_inputs, 'producer_command': str(producer_command),
        'producer_command_sha256': baseline.digest(producer_command), 'environment': environment,
        'binaries': {name: {'path': str(path), 'sha256': hashes[name]} for name, path in binaries.items()},
        'helpers': helpers, 'heap_mb': args.heap_mb, 'jobs': args.jobs, 'warm_builds_per_arm': 6,
        'producer_binary_state': {name: str(path) for name, path in binary_states.items()},
        'producer_binary_state_policy': 'Reuse producer install/repository trees to preserve authenticated external bindings; each arm has a new owned output base, logs and symlinks.',
        'forced_gc': 'After all seven builds, outside latency/CPU build samples; raw jcmd output is retained.',
        'limitations': [
            'One sequential server series per arm, not randomized statistical evidence or a peak-memory census.',
            'Initial latency includes server startup with the batch producer install/repository trees already warm; the sequential series is not a balanced initial-latency comparison.',
            'Initial build CPU delta is unavailable because its server did not exist before the command.',
            'Warm CPU deltas use the same server PID/start time; they exclude client, child tools and kernel/page-cache accounting.',
            'RSS/PSS are boundary process snapshots; VmHWM is lifetime process high-water RSS, not retained Java heap.',
            'Post-requested-GC used heap is an operational snapshot including diagnostic overhead, not an object retained-size census.',
            'Warm commands use resident Skyframe state and ordinary local incremental execution; disk/remote action caches remain disabled.',
            'The producer environment and frozen workspace are reused to preserve seed namespace; temporary files may use its existing TMPDIR.',
        ]})
    summary = {'schema_version': 1, 'status': 'running', 'arms': []}

    def unchanged():
        if baseline.inventory(workspace) != inputs:
            raise ValueError('frozen workspace changed')
        if any(baseline.digest(path) != digest for path, digest in helpers.items()):
            raise ValueError('measurement helper changed')
        if any(baseline.digest(path) != hashes[name] for name, path in binaries.items()):
            raise ValueError('binary changed')

    try:
        for arm in comparison.ARMS:
            binary_name = 'vanilla' if arm == 'vanilla' else 'current'
            arm_dir = root / 'runs' / arm
            arm_dir.mkdir()
            (arm_dir / 'links').mkdir()
            output_base = arm_dir / 'output-base'
            cache = root / 'caches/fork-on' if arm == 'fork-on' else None
            if cache is not None:
                shutil.copytree(seed, cache)
            row = {'arm': arm, 'builds': [], 'status': 'running'}
            summary['arms'].append(row)
            identity = startup = None
            try:
                for number in range(7):
                    unchanged()
                    directory = arm_dir / f'build-{number:02d}'
                    argv = persistent_command(binaries[binary_name], binary_states[binary_name],
                                              directory, cache, spec['targets'], args.heap_mb, args.jobs, arm, output_base)
                    prefix = argv[:argv.index('build')]
                    if startup is not None and prefix != startup:
                        raise ValueError('startup options changed during retained series')
                    startup = prefix
                    before = sample_server(output_base, identity) if identity is not None else None
                    metrics = run_process(directory, argv, workspace, environment, 180)
                    after = sample_server(output_base, identity)
                    identity = after['identity']
                    record = {'number': number, 'role': 'initial' if number == 0 else 'retained',
                              'metrics': metrics, 'server_cpu_between_snapshots_seconds': server_cpu_delta(before, after),
                              'before': before, 'after': after}
                    row['builds'].append(record)
                    baseline.save(directory / 'process.json', {'before': before, 'after': after})
                    if metrics['exit_code'] != 0 or metrics['timed_out']:
                        raise ValueError('build failed or timed out')
                    record['validation'] = baseline.validate_outputs(directory / 'build.bep.json', output_base, spec)
                    events = [json.loads(line) for line in (directory / 'build.bep.json').read_text().splitlines() if line]
                    builds = [event['buildMetrics'] for event in events if 'buildMetrics' in event]
                    if len(builds) != 1:
                        raise ValueError('expected exactly one buildMetrics event')
                    stats = builds[0].get('remoteAnalysisCacheStatistics', {})
                    hits = sum(int(item.get('value', 0)) for item in stats.get('cacheHitsBySkyfunction', [])
                               if item.get('key') == 'CONFIGURED_TARGET')
                    record['configured_target_hits'] = hits
                    record['action_summary'] = builds[0].get('actionSummary', {})
                    record['diagnostics_sha256'] = copy_diagnostics(directory, output_base)
                    if number == 0 and arm == 'fork-on' and hits <= 0:
                        raise ValueError('initial fork-on build restored no configured targets')
                    if arm != 'fork-on' and hits != 0:
                        raise ValueError('control arm unexpectedly restored configured targets')
                    unchanged()
                    baseline.save(root / 'summary.json', summary)
                row['heap_diagnostics'] = heap_diagnostics(arm_dir, output_base, identity, jcmd, workspace, environment)
                row['status'] = 'passed'
            except Exception as error:
                row['status'] = 'failed'
                row['error'] = str(error)
                raise
            finally:
                if startup is not None:
                    row['shutdown'] = shutdown_server(arm_dir, startup, output_base, identity, workspace, environment)
                row['artifacts_sha256'] = {
                    str(path.relative_to(arm_dir)): baseline.digest(path)
                    for directory in arm_dir.iterdir() if directory.is_dir() and directory.name != 'output-base'
                    for path in directory.rglob('*') if path.is_file()}
                if (arm_dir / 'server-gc.log').is_file():
                    row['artifacts_sha256']['server-gc.log'] = baseline.digest(arm_dir / 'server-gc.log')
                baseline.save(root / 'summary.json', summary)
        unchanged()
        if baseline.inventory(seed) != seed_inputs:
            raise ValueError('original seed changed during retained measurement')
        summary['status'] = 'passed'
    except Exception as error:
        summary['status'] = 'failed'
        summary['error'] = str(error)
    finally:
        baseline.save(root / 'summary.json', summary)
    print(root)
    return 0 if summary['status'] == 'passed' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('vanilla-bazel', 'candidate-bazel', 'manifest', 'workspace', 'seed', 'run-dir'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--jcmd', type=Path)
    parser.add_argument('--heap-mb', type=int, default=2048)
    parser.add_argument('--jobs', type=int, default=6)
    return run(parser.parse_args())


if __name__ == '__main__':
    sys.exit(main())
