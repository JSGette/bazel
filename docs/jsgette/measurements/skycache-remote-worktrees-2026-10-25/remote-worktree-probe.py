#!/usr/bin/env python3
"""Check remote SkyCache reuse and publication from two edited Git worktrees.

Every build uses an initially absent local staging cache. The changed pair starts
from the same remote baseline, repeats after clean and a catalog-server restart,
then reverts and repeats after clean. Generated output bytes are checked exactly.
This is a correctness probe, not a performance comparison or cross-host test.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import threading
import time


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bazel', required=True, type=Path)
    parser.add_argument('--worker', required=True, type=Path)
    parser.add_argument('--rpc-python', required=True, type=Path,
                        help='Python interpreter with grpcio for read-only worker readiness checks')
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--support-dir', required=True, type=Path,
                        help='Repository tools/skycache directory containing storage_preflight.py')
    parser.add_argument('--lock-file', required=True, type=Path)
    parser.add_argument('--action-cache', required=True)
    parser.add_argument('--packages', type=int, default=32)
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    if args.packages < 4:
        parser.error('--packages must be at least 4')
    sys.path.insert(0, str(args.support_dir.resolve(strict=True)))
    from storage_preflight import check_storage

    bazel = args.bazel.resolve(strict=True)
    worker_binary = args.worker.resolve(strict=True)
    # Preserve a virtualenv's interpreter path; resolving its symlink loses that environment.
    rpc_python = args.rpc_python.absolute()
    assert rpc_python.is_file(), rpc_python
    ready_helper = Path(__file__).resolve().with_name('remote-worktree-ready.py')
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    report = {
        'passed': False,
        'scope': 'Two linked Git worktrees, distinct edits, common remote metadata catalog/CAS',
        'limitations': ['Single WSL host and loopback metadata RPC',
                        'Correctness fixture; elapsed times are not comparative benchmarks'],
        'bazel': str(bazel), 'bazel_sha256': sha256(bazel),
        'worker': str(worker_binary), 'worker_sha256': sha256(worker_binary),
        'harness_sha256': sha256(Path(__file__)),
        'readiness_helper_sha256': sha256(ready_helper),
        'action_cache': args.action_cache, 'download_outputs': 'toplevel',
        'package_count': args.packages, 'genrule_count': 3 * args.packages + 1,
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'phases': [], 'commands': [], 'worker_starts': [], 'shutdown_failures': [],
    }
    report_lock = threading.Lock()
    started_bazel = set()
    worker = None
    worker_log = None
    result_path = root / 'results.json'
    seed = root / 'seed'
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    endpoint = f'grpc://127.0.0.1:{port}'
    report['metadata_endpoint'] = endpoint

    def save():
        with report_lock:
            temporary = root / 'results.json.tmp'
            temporary.write_text(json.dumps(report, indent=2) + '\n')
            temporary.replace(result_path)

    def record_command(command, cwd, label):
        with report_lock:
            report['commands'].append({'label': label, 'cwd': str(cwd), 'argv': command})

    def command(command, cwd, label, timeout=90):
        record_command(command, cwd, label)
        completed = subprocess.run(command, cwd=cwd, env=dict(os.environ, PWD=str(cwd)),
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=timeout)
        (root / (label + '.log')).write_bytes(completed.stdout)
        if completed.returncode:
            raise RuntimeError(f'{label} returned {completed.returncode}; see its log')
        return completed.stdout.decode().strip()

    def git(workspace, label, *arguments):
        return command(['git', '-C', str(workspace), *arguments], root, label)

    def startup(name):
        return [str(bazel), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc',
                '--host_jvm_args=-Xmx1g', '--max_idle_secs=120',
                '--output_base=' + str(root / ('out-' + name))]

    def shutdown(name, label):
        command(startup(name) + ['shutdown'], root / name, label)
        started_bazel.discard(name)

    def start_worker(label):
        nonlocal worker, worker_log
        log_path = root / f'worker-{label}.log'
        worker_log = log_path.open('w')
        argv = [str(worker_binary), '--listen_address=127.0.0.1', f'--listen_port={port}',
                '--cas_path=' + str(root / 'server-cas'),
                '--skycache_catalog_path=' + str(root / 'server-catalog'),
                '--skycache_instance_name=concurrent-worktrees']
        record_command(argv, root, 'worker-' + label)
        worker = subprocess.Popen(argv, cwd=root, stdout=worker_log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        report['worker_starts'].append({'label': label, 'pid': worker.pid, 'log': str(log_path)})
        save()
        ready = command([str(rpc_python), str(ready_helper), endpoint,
                         'concurrent-worktrees'], root, 'worker-ready-' + label, timeout=60)
        if worker.poll() is not None:
            raise RuntimeError(f'Worker exited with {worker.returncode}; see {log_path}')
        report['worker_starts'][-1]['readiness'] = json.loads(ready)
        save()

    def active_worker_processes(group):
        active = []
        for path in Path('/proc').glob('[0-9]*/stat'):
            try:
                # Everything after the final ')' starts with state, ppid, pgrp.
                fields = path.read_text().rsplit(')', 1)[1].split()
                if int(fields[2]) == group and fields[0] != 'Z':
                    active.append(int(path.parent.name))
            except (OSError, ValueError, IndexError):
                pass
        return active

    def stop_worker():
        nonlocal worker, worker_log
        if worker is not None:
            group = worker.pid
            if worker.poll() is None:
                os.killpg(group, signal.SIGTERM)
                try:
                    worker.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(group, signal.SIGKILL)
                    worker.wait(timeout=15)
            # The launcher can exit before its Java child finishes shutting down.
            # A TCP probe at that point can reach the old worker, not the new one.
            deadline = time.monotonic() + 15
            while active_worker_processes(group) and time.monotonic() < deadline:
                time.sleep(0.05)
            if active_worker_processes(group):
                os.killpg(group, signal.SIGKILL)
                deadline = time.monotonic() + 5
                while active_worker_processes(group) and time.monotonic() < deadline:
                    time.sleep(0.05)
            assert not active_worker_processes(group), f'Worker process group {group} survived stop'
            worker = None
        if worker_log is not None:
            worker_log.close()
            worker_log = None

    def receipts():
        return {p.name: sha256(p) for p in sorted((root / 'server-catalog').glob('receipt-*.pb'))}

    def leaf_build(marker):
        first = "cat $(SRCS) > $@; printf '%s\\n' '" + marker + "' >> $@"
        return ('genrule(name="one", srcs=["input"], outs=["one.txt"], cmd='
                + json.dumps(first) + ', visibility=["//visibility:public"])\n'
                'genrule(name="two", srcs=[":one"], outs=["two.txt"], '
                'cmd="cat $(SRCS) > $@", visibility=["//visibility:public"])\n'
                'genrule(name="three", srcs=[":two"], outs=["three.txt"], '
                'cmd="cat $(SRCS) > $@", visibility=["//visibility:public"])\n')

    def expected(edit=None):
        chunks = []
        for i in range(args.packages):
            if edit is not None and i == edit[0]:
                chunks.append(f'edited-source-{edit[1]}:{i:02d}\nedited-rule-{edit[1]}:{i:02d}\n')
            else:
                chunks.append(f'base-source:{i:02d}\nbase-rule:{i:02d}\n')
        return ''.join(chunks).encode()

    def build(name, phase, expected_bytes, barrier=None):
        workspace = root / name
        prefix = f'{phase}-{name}'
        staging = root / ('cache-' + prefix)
        assert not staging.exists(), f'Staging cache must start absent: {staging}'
        argv = startup(name) + [
            'build', '//app:bundle', '--jobs=2', '--local_resources=memory=1024',
            '--lockfile_mode=off', '--experimental_strict_repo_env', '--color=no', '--curses=no',
            '--remote_cache=' + args.action_cache, '--disk_cache=',
            '--remote_download_outputs=toplevel', '--remote_upload_local_results',
            '--remote_timeout=30s', '--experimental_active_directories=app',
            '--repo_env=SKYCACHE_PROBE=stable', '--action_env=SKYCACHE_PROBE=stable',
            '--experimental_local_skycache=' + str(staging),
            '--experimental_remote_skycache=' + endpoint,
            '--experimental_remote_skycache_instance_name=concurrent-worktrees',
            '--experimental_remote_skycache_timeout=60s',
            '--build_event_json_file=' + str(root / (prefix + '.bep.json')),
        ]
        record_command(argv, workspace, prefix)
        with report_lock:
            started_bazel.add(name)
        if barrier is not None:
            barrier.wait(timeout=30)
        start_ns = time.monotonic_ns()
        with (root / (prefix + '.log')).open('w') as log:
            result = subprocess.run(argv, cwd=workspace, env=dict(os.environ, PWD=str(workspace)),
                                    stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
        end_ns = time.monotonic_ns()
        log = (root / (prefix + '.log')).read_text()

        def counter(pattern):
            matches = re.findall(pattern, log)
            return json.loads(matches[-1]) if matches else None

        output_path = workspace / 'bazel-bin/app/bundle.txt'
        output = output_path.read_bytes() if result.returncode == 0 else None
        if output is not None:
            (root / (prefix + '.output.txt')).write_bytes(output)
        (root / (prefix + '.expected.txt')).write_bytes(expected_bytes)
        row = {
            'worktree': name, 'phase': phase, 'exit_code': result.returncode,
            'start_monotonic_ns': start_ns, 'end_monotonic_ns': end_ns,
            'seconds': (end_ns - start_ns) / 1e9, 'staging_cache_initially_absent': True,
            'staging_cache': str(staging), 'output_base': str(root / ('out-' + name)),
            'output_matches_expected': output == expected_bytes,
            'output_sha256': hashlib.sha256(output).hexdigest() if output is not None else None,
            'expected_sha256': hashlib.sha256(expected_bytes).hexdigest(),
            'analysis_hits': sum(int(n) for n in re.findall(r'CONFIGURED_TARGET: (\d+)/\d+', log)),
            'local_stats': counter(r'Local SkyCache: (\{[^\n]+\})'),
            'validation': counter(r'Local SkyCache dependency validation: (\{[^\n]+\})'),
            'transfers': counter(r'Remote SkyCache transfers: (\{[^\n]+\})'),
            'remote_committed': 'Remote SkyCache: publication committed' in log,
            'remote_import_skipped': 'Remote SkyCache import skipped:' in log,
            'remote_publish_skipped': 'Remote SkyCache publication skipped:' in log,
            'local_disabled': 'Local SkyCache disabled:' in log,
            'process_summary': re.findall(r'INFO: .* processes: [^\n]+', log),
        }
        (root / (prefix + '.json')).write_text(json.dumps(row, indent=2) + '\n')
        print(json.dumps(row), flush=True)
        return row

    def validate(row, remote_hit=True):
        assert row['exit_code'] == 0 and row['output_matches_expected'], row
        assert not row['local_disabled'] and row['local_stats'] is not None, row
        assert not row['local_stats']['failed'], row
        assert row['remote_committed'] and not row['remote_publish_skipped'], row
        assert not row['remote_import_skipped'], row
        assert row['transfers'] is not None, row
        assert row['transfers']['materialized_publications'] == 0, row
        if remote_hit:
            assert row['analysis_hits'] > 0 and row['local_stats']['shared_reads'] > 0, row
            assert row['transfers']['discovered_publications'] > 0, row
            assert row['transfers']['reused_publications'] > 0, row

    def pair(phase, edits):
        storage = check_storage([root])
        before = receipts()
        barrier = threading.Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(build, name, phase, expected(edits.get(name)), barrier)
                       for name in ('a', 'b')]
            rows = [future.result() for future in futures]
        after = receipts()
        overlap = (min(r['end_monotonic_ns'] for r in rows)
                   - max(r['start_monotonic_ns'] for r in rows)) / 1e9
        record = {'phase': phase, 'storage': storage, 'builds': rows,
                  'overlap_seconds': overlap,
                  'receipts_before': before, 'receipts_after': after,
                  'new_receipts': sorted(set(after) - set(before))}
        report['phases'].append(record)
        save()
        for row in rows:
            validate(row)
        assert overlap > 0, 'Bazel processes did not overlap'
        assert len(record['new_receipts']) == 2, record
        assert all(after.get(k) == v for k, v in before.items()), 'Prior receipts changed'
        if edits:
            assert rows[0]['output_sha256'] != rows[1]['output_sha256'], rows
        print('PASS phase: ' + phase, flush=True)
        return rows

    def clean_pair(label):
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(command, startup(name) + ['clean'], root / name,
                                   label + '-clean-' + name) for name in ('a', 'b')]
            for future in futures:
                future.result()
        # Each output is required again after clean, so stale workspace output cannot pass.
        for name in ('a', 'b'):
            assert not (root / name / 'bazel-bin/app/bundle.txt').exists(), name
        report.setdefault('clean_checks', []).append({'phase': label, 'outputs_absent': True})
        save()

    save()
    lock = args.lock_file.open('a')
    print('Waiting for shared measurement lock: ' + str(args.lock_file), flush=True)
    wait_start = time.monotonic()
    fcntl.flock(lock, fcntl.LOCK_EX)
    report['lock_wait_seconds'] = time.monotonic() - wait_start
    print('Acquired measurement lock', flush=True)
    save()
    try:
        report['initial_storage'] = check_storage([root])
        seed.mkdir()
        (seed / 'MODULE.bazel').write_text('module(name="skycache_remote_worktrees_probe")\n')
        (seed / 'BUILD').write_text('# Remote SkyCache concurrent worktree probe.\n')
        (seed / '.gitignore').write_text('/bazel-*\n/MODULE.bazel.lock\n')
        for i in range(args.packages):
            package = seed / f'lib{i:02d}'
            package.mkdir()
            (package / 'input').write_text(f'base-source:{i:02d}\n')
            (package / 'BUILD').write_text(leaf_build(f'base-rule:{i:02d}'))
        (seed / 'app').mkdir()
        labels = [f'//lib{i:02d}:three' for i in range(args.packages)]
        (seed / 'app/BUILD').write_text('genrule(name="bundle", srcs=' + json.dumps(labels)
                                      + ', outs=["bundle.txt"], cmd="cat $(SRCS) > $@")\n')
        git(seed, 'git-init', 'init', '--quiet')
        git(seed, 'git-add', 'add', '.')
        git(seed, 'git-commit', '-c', 'user.name=SkyCache Probe',
            '-c', 'user.email=skycache-probe@example.invalid', 'commit', '--quiet',
            '-m', 'Remote SkyCache concurrent worktrees fixture')
        report['fixture_commit'] = git(seed, 'git-head', 'rev-parse', 'HEAD')
        common = []
        for name in ('a', 'b'):
            git(seed, 'git-worktree-' + name, 'worktree', 'add', '--detach', str(root / name), 'HEAD')
            common.append(git(root / name, 'git-common-' + name, 'rev-parse',
                              '--path-format=absolute', '--git-common-dir'))
        assert common[0] == common[1] == str(seed / '.git'), common
        report['git_common_directories'] = common
        start_worker('initial')
        baseline = build('seed', 'baseline', expected())
        report['phases'].append({'phase': 'baseline', 'builds': [baseline], 'receipts_after': receipts()})
        save()
        validate(baseline, remote_hit=False)
        assert len(receipts()) == 1, receipts()
        shutdown('seed', 'shutdown-seed')
        edits = {'a': (3, 'a'), 'b': (args.packages - 2, 'b')}
        report['edits'] = edits
        for name, (i, marker) in edits.items():
            (root / name / f'lib{i:02d}/input').write_text(f'edited-source-{marker}:{i:02d}\n')
            (root / name / f'lib{i:02d}/BUILD').write_text(leaf_build(f'edited-rule-{marker}:{i:02d}'))
            diff = git(root / name, 'git-edit-' + name, 'diff', '--binary')
            (root / ('edit-' + name + '.patch')).write_text(diff + '\n')
        pair('concurrent-edits', edits)
        clean_pair('changed-restart')
        committed = receipts()
        report['receipts_before_restart'] = committed
        stop_worker()
        start_worker('restart')
        assert receipts() == committed, 'Catalog receipts did not survive restart'
        pair('after-clean-and-restart', edits)
        for name, (i, _) in edits.items():
            git(root / name, 'git-revert-' + name, 'restore', f'lib{i:02d}/BUILD', f'lib{i:02d}/input')
        clean_pair('reverted')
        reverted = pair('reverted-after-clean', {})
        assert all(r['output_sha256'] == baseline['output_sha256'] for r in reverted)
        report['passed'] = True
    except BaseException as error:
        report['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        for name in sorted(started_bazel):
            try:
                shutdown(name, 'shutdown-final-' + name)
            except Exception as error:
                report['shutdown_failures'].append({'server': name, 'error': str(error)})
        try:
            stop_worker()
        except Exception as error:
            report['shutdown_failures'].append({'server': 'worker', 'error': str(error)})
        report['final_receipts'] = receipts()
        report['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if report['shutdown_failures']:
            report['passed'] = False
        save()
        lock.close()
    if report['shutdown_failures']:
        raise RuntimeError('Shutdown failed; see ' + str(result_path))
    print('PASS: ' + str(result_path), flush=True)


if __name__ == '__main__':
    main()
