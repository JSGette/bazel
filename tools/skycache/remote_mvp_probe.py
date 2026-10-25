#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Exercise the remote SkyCache MVP with real Bazel and worker binaries.

Uses independent local caches/output bases and linked worktrees of one Git seed.
This is a correctness fixture, not a performance or cross-machine portability test.
Logs, exact commands, output hashes, and results.json remain in a new exclusive root.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
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
    parser.add_argument('--root', required=True, type=Path,
                        help='New exclusive output directory; never reused or deleted.')
    parser.add_argument('--build-timeout', type=int, default=300)
    args = parser.parse_args()
    bazel = args.bazel.resolve(strict=True)
    worker_binary = args.worker.resolve(strict=True)
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    seed = root / 'seed'
    phases = []
    report = {
        'passed': False,
        'bazel': str(bazel),
        'bazel_sha256': sha256(bazel),
        'worker': str(worker_binary),
        'worker_sha256': sha256(worker_binary),
        'scope': 'Linked worktrees; separate local caches and output bases; metadata-only remote cache.',
        'action_caches': 'Disabled; all generated outputs execute locally.',
        'phases': phases,
        'worker_starts': [],
        'shutdown_failures': [],
    }
    results_path = root / 'results.json'
    started_bazel = set()
    worker = None
    worker_log = None
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    endpoint = f'grpc://127.0.0.1:{port}'
    report['endpoint'] = endpoint

    def save():
        results_path.write_text(json.dumps(report, indent=2) + '\n')

    def git(*arguments):
        completed = subprocess.run(['git', '-C', str(seed), *arguments],
                                   capture_output=True, text=True, timeout=60)
        if completed.returncode:
            raise RuntimeError('git failed: ' + completed.stderr)

    def start_worker(label):
        nonlocal worker, worker_log
        log_path = root / f'worker-{label}.log'
        worker_log = log_path.open('w')
        command = [str(worker_binary), f'--listen_port={port}',
                   '--listen_address=127.0.0.1',
                   '--cas_path=' + str(root / 'server-cas'),
                   '--skycache_catalog_path=' + str(root / 'server-catalog'),
                   '--skycache_instance_name=probe']
        worker = subprocess.Popen(command, cwd=root, stdout=worker_log,
                                  stderr=subprocess.STDOUT, start_new_session=True)
        report['worker_starts'].append({'label': label, 'argv': command,
                                       'pid': worker.pid, 'log': str(log_path)})
        save()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise RuntimeError(f'Worker exited with {worker.returncode}; see {log_path}')
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError(f'Worker did not become ready; see {log_path}')

    def stop_worker():
        nonlocal worker, worker_log
        if worker is not None:
            if worker.poll() is None:
                os.killpg(worker.pid, signal.SIGTERM)
                try:
                    worker.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(worker.pid, signal.SIGKILL)
                    worker.wait(timeout=15)
            worker = None
        if worker_log is not None:
            worker_log.close()
            worker_log = None

    def startup(name):
        return [str(bazel), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc',
                '--host_jvm_args=-Xmx1g', '--max_idle_secs=120',
                '--output_base=' + str(root / ('out-' + name))]

    def environment(name):
        return dict(os.environ, PWD=str(root / name))

    def shutdown(name):
        command = startup(name) + ['shutdown']
        with (root / f'shutdown-{name}.log').open('w') as log:
            completed = subprocess.run(command, cwd=root / name, env=environment(name),
                                       stdout=log, stderr=subprocess.STDOUT, timeout=90)
        if completed.returncode:
            raise RuntimeError(f'Bazel shutdown for {name} returned {completed.returncode}')
        started_bazel.discard(name)

    def build(name, expected, *, timeout='30s'):
        workspace = root / name
        command = startup(name) + [
            'build', '//app:all', '--jobs=2', '--local_resources=memory=1024',
            '--lockfile_mode=off', '--experimental_strict_repo_env',
            '--color=no', '--curses=no', '--remote_cache=', '--disk_cache=',
            '--experimental_active_directories=app',
            '--repo_env=SKYCACHE_PROBE=stable', '--action_env=SKYCACHE_PROBE=stable',
            '--experimental_local_skycache=' + str(root / ('cache-' + name)),
            '--experimental_remote_skycache=' + endpoint,
            '--experimental_remote_skycache_instance_name=probe',
            '--experimental_remote_skycache_timeout=' + timeout,
        ]
        log_path = root / f'build-{name}.log'
        row = {'phase': name, 'argv': command, 'log': str(log_path), 'exit_code': None}
        phases.append(row)
        save()
        started_bazel.add(name)
        started = time.monotonic()
        with log_path.open('w') as log:
            completed = subprocess.run(command, cwd=workspace, env=environment(name),
                                       stdout=log, stderr=subprocess.STDOUT,
                                       timeout=args.build_timeout)
        text = log_path.read_text()
        local_stats = re.findall(r'Local SkyCache: (\{[^\n]+\})', text)
        imports = re.findall(r'Remote SkyCache: imported (\d+) publications', text)
        output_path = workspace / 'bazel-bin/app/all.txt'
        output = output_path.read_bytes() if completed.returncode == 0 else None
        row.update({
            'exit_code': completed.returncode,
            'seconds': round(time.monotonic() - started, 3),
            'output_matches_expected': output == expected,
            'output_sha256': hashlib.sha256(output).hexdigest() if output is not None else None,
            'expected_sha256': hashlib.sha256(expected).hexdigest(),
            'imported_publications': int(imports[-1]) if imports else 0,
            'remote_committed': 'Remote SkyCache: publication committed' in text,
            'remote_import_skipped': 'Remote SkyCache import skipped:' in text,
            'remote_publish_skipped': 'Remote SkyCache publication skipped:' in text,
            'analysis_hits': sum(int(value) for value in
                                 re.findall(r'CONFIGURED_TARGET: (\d+)/\d+', text)),
            'local_disabled': 'Local SkyCache disabled:' in text,
            'local_stats': json.loads(local_stats[-1]) if local_stats else None,
        })
        save()
        print(json.dumps(row), flush=True)
        assert row['exit_code'] == 0, row
        assert row['output_matches_expected'], row
        assert not row['local_disabled'], row
        assert row['local_stats'] is not None, row
        assert not row['local_stats']['failed'], row
        return row

    def require_remote_hit(row):
        assert row['imported_publications'] > 0, row
        assert row['analysis_hits'] > 0, row
        assert row['local_stats']['shared_reads'] > 0, row
        assert not row['remote_import_skipped'], row

    def leaf_build(marker):
        command = "printf '%s\\n' '" + marker + "' > $@"
        return ('genrule(name="value", outs=["value.txt"], cmd=' + json.dumps(command)
                + ', visibility=["//visibility:public"])\n')

    save()
    try:
        seed.mkdir()
        (seed / 'MODULE.bazel').write_text('module(name="skycache_remote_mvp_probe")\n')
        (seed / 'BUILD').write_text('# Remote SkyCache correctness fixture.\n')
        for number in range(6):
            package = seed / f'lib{number:02d}'
            package.mkdir()
            (package / 'BUILD').write_text(leaf_build(f'base:{number:02d}'))
        (seed / 'app').mkdir()
        labels = [f'//lib{number:02d}:value' for number in range(6)]
        (seed / 'app/BUILD').write_text(
            'genrule(name="all", srcs=' + json.dumps(labels)
            + ', outs=["all.txt"], cmd="cat $(SRCS) > $@")\n')
        git('init', '--quiet')
        git('add', '.')
        git('-c', 'user.name=SkyCache Probe', '-c', 'user.email=skycache-probe@example.invalid',
            'commit', '--quiet', '-m', 'Remote SkyCache correctness fixture')
        for name in ('writer', 'reader', 'restarted', 'mutated', 'offline'):
            git('worktree', 'add', '--detach', str(root / name), 'HEAD')
        expected = ''.join(f'base:{number:02d}\n' for number in range(6)).encode()
        start_worker('initial')
        first = build('writer', expected)
        assert first['remote_committed'], first
        assert not first['remote_publish_skipped'], first
        shutdown('writer')
        require_remote_hit(build('reader', expected))
        shutdown('reader')

        receipts = sorted((root / 'server-catalog').glob('receipt-*.pb'))
        assert receipts, 'No durable catalog receipts were written'
        report['receipts_before_restart'] = {path.name: sha256(path) for path in receipts}
        stop_worker()
        start_worker('restart')
        require_remote_hit(build('restarted', expected))
        shutdown('restarted')
        assert all(sha256(root / 'server-catalog' / name) == digest
                   for name, digest in report['receipts_before_restart'].items())

        # Change a dependency package's actual rule, so stale analysis would produce wrong bytes.
        (root / 'mutated/lib00/BUILD').write_text(leaf_build('edited:00'))
        mutated = b'edited:00\n' + b''.join(
            f'base:{number:02d}\n'.encode() for number in range(1, 6))
        mutation = build('mutated', mutated)
        assert mutation['output_sha256'] != first['output_sha256'], mutation
        shutdown('mutated')

        stop_worker()
        offline = build('offline', expected, timeout='1s')
        assert offline['remote_import_skipped'], offline
        assert offline['imported_publications'] == 0, offline
        shutdown('offline')
        report['passed'] = True
    except BaseException as error:
        report['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        for name in sorted(started_bazel):
            try:
                shutdown(name)
            except Exception as error:
                report['shutdown_failures'].append({'server': name, 'error': str(error)})
        try:
            stop_worker()
        except Exception as error:
            report['shutdown_failures'].append({'server': 'worker', 'error': str(error)})
        if report['shutdown_failures']:
            report['passed'] = False
        save()
    if report['shutdown_failures']:
        raise RuntimeError('Probe cleanup failed; see ' + str(results_path))
    print('PASS: ' + str(results_path), flush=True)


if __name__ == '__main__':
    main()
