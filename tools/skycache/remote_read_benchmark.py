#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Paired local-loopback benchmark of normal, reference and current builds.

Uses warm Bazel JVMs, clean analysis/action state, fresh client metadata directories,
and a frozen primer catalog per binary. A counting TCP proxy reports gRPC
wire bytes (including framing); no latency or bandwidth shaping is applied.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import random
import re
import selectors
import shutil
import signal
import socket
import socketserver
import statistics
import subprocess
import threading
import time


class Counter:
    def __init__(self):
        self.lock = threading.Lock()
        self.received = self.sent = 0

    def add(self, received, size):
        with self.lock:
            if received:
                self.received += size
            else:
                self.sent += size

    def snapshot(self):
        with self.lock:
            return self.received, self.sent


class Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--old-bazel', type=Path, required=True)
    parser.add_argument('--new-bazel', type=Path, required=True)
    parser.add_argument('--old-label', default='full import')
    parser.add_argument('--new-label', default='demand reads')
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--packages', type=int, default=512)
    parser.add_argument('--selected', type=int, default=16)
    parser.add_argument('--payload-bytes', type=int, default=32768)
    parser.add_argument('--repetitions', type=int, default=3)
    args = parser.parse_args()
    if not (1 <= args.selected <= args.packages and args.payload_bytes > 0 and args.repetitions > 0):
        parser.error('require 1 <= selected <= packages, positive payload bytes and repetitions')
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    binaries = {'normal': args.new_bazel.resolve(strict=True),
                'full': args.old_bazel.resolve(strict=True),
                'demand': args.new_bazel.resolve(strict=True),
                'primer-full': args.old_bazel.resolve(strict=True),
                'primer-demand': args.new_bazel.resolve(strict=True)}
    worker_binary = args.worker.resolve(strict=True)
    seed = root / 'seed'
    seed.mkdir()
    counter = Counter()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        worker_port = reservation.getsockname()[1]

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                with socket.create_connection(('127.0.0.1', worker_port), timeout=10) as backend:
                    backend.settimeout(None)
                    with selectors.DefaultSelector() as ready:
                        ready.register(self.request, selectors.EVENT_READ, (backend, False))
                        ready.register(backend, selectors.EVENT_READ, (self.request, True))
                        while True:
                            for key, _ in ready.select():
                                data = key.fileobj.recv(65536)
                                if not data:
                                    return
                                target, received = key.data
                                target.sendall(data)
                                counter.add(received, len(data))
            except OSError:
                return

    proxy = Proxy(('127.0.0.1', 0), Handler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    endpoint = 'grpc://127.0.0.1:' + str(proxy.server_address[1])
    report = {'passed': False, 'method': __doc__, 'parameters': vars(args).copy(),
              'mode_labels': {'normal': 'SkyCache disabled',
                              'full': args.old_label, 'demand': args.new_label},
              'endpoint': endpoint, 'runs': [], 'warmups': [], 'cleanup_errors': [],
              'binary_sha256': {key: hashlib.sha256(path.read_bytes()).hexdigest()
                                for key, path in binaries.items()}}
    report['parameters'] = {key: str(value) if isinstance(value, Path) else value
                            for key, value in report['parameters'].items()}
    worker = worker_log = None
    started_clients = set()

    def save():
        (root / 'results.json').write_text(json.dumps(report, indent=2) + '\n')

    def git(*arguments):
        subprocess.run(['git', '-C', str(seed), *arguments], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)

    def startup(mode):
        return [str(binaries[mode]), '--nosystem_rc', '--nohome_rc', '--noworkspace_rc',
                '--host_jvm_args=-Xmx2g', '--max_idle_secs=600',
                '--output_base=' + str(root / ('out-' + mode))]

    def command(mode, arguments, label, timeout=600):
        started_clients.add(mode)
        argv = startup(mode) + arguments
        log_path = root / (label + '.log')
        started = time.monotonic()
        with log_path.open('w') as log:
            result = subprocess.run(argv, cwd=root / mode,
                                    env=dict(os.environ, PWD=str(root / mode)),
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        elapsed = time.monotonic() - started
        text = log_path.read_text()
        if result.returncode:
            raise RuntimeError(f'{label}: exit {result.returncode}; see {log_path}')
        return elapsed, text, argv

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

    def start_worker(label, primer_mode='demand', frozen=True):
        nonlocal worker, worker_log
        stop_worker()
        catalog = root / ('catalog-' + label)
        if frozen:
            shutil.copytree(root / ('catalog-primer-' + primer_mode), catalog)
        log_path = root / ('worker-' + label + '.log')
        worker_log = log_path.open('w')
        argv = [str(worker_binary), '--listen_address=127.0.0.1',
                f'--listen_port={worker_port}', '--cas_path=' + str(root / 'cas'),
                '--skycache_catalog_path=' + str(catalog), '--skycache_instance_name=bench']
        worker = subprocess.Popen(argv, cwd=root, stdout=worker_log,
                                  stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise RuntimeError('worker failed: ' + str(log_path))
            # This message follows Server.start(), unlike a mere open listening socket.
            if 'Not starting HTTP cache server' in log_path.read_text():
                return
            time.sleep(0.1)
        raise RuntimeError('worker startup timeout')

    payloads = []
    rng = random.Random(83271)
    for _ in range(args.packages):
        payloads.append(base64.b64encode(rng.randbytes(args.payload_bytes)).decode())

    def leaf(number, changed=False):
        marker = ('edited' if changed else 'base') + f':{number:04d}'
        cmd = "printf '%s\\n' '" + marker + "' > $@ # " + payloads[number]
        return ('genrule(name="value", outs=["value.txt"], cmd=' + json.dumps(cmd)
                + ', visibility=["//visibility:public"])\n')

    def build(mode, label, changed, primer=False):
        arguments = ['build', '//app:wide', '//app:narrow'] if primer else ['build', '//app:narrow']
        arguments += ['--jobs=4', '--local_resources=memory=2048', '--lockfile_mode=off',
                      '--experimental_strict_repo_env', '--color=no', '--curses=no',
                      '--remote_cache=', '--disk_cache=', '--experimental_active_directories=app',
                      '--repo_env=SKYCACHE_BENCH=stable', '--action_env=SKYCACHE_BENCH=stable']
        if primer:
            arguments += ['--nobuild']
        if mode != 'normal':
            arguments += ['--experimental_local_skycache=' + str(root / ('cache-' + label)),
                          '--experimental_remote_skycache=' + endpoint,
                          '--experimental_remote_skycache_instance_name=bench',
                          '--experimental_remote_skycache_timeout=120s']
        before = counter.snapshot()
        elapsed, text, argv = command(mode, arguments, label)
        after = counter.snapshot()
        local = re.findall(r'Local SkyCache: (\{[^\n]+\})', text)
        remote = re.findall(r'Remote SkyCache transfers: (\{[^\n]+\})', text)
        bazel_elapsed = re.findall(r'Elapsed time: ([0-9.]+)s', text)
        row = {'mode': mode, 'label': label, 'changed': changed, 'seconds': elapsed,
               'bazel_seconds': float(bazel_elapsed[-1]) if bazel_elapsed else None,
               'wire_download_bytes': after[0] - before[0],
               'wire_upload_bytes': after[1] - before[1], 'argv': argv,
               'analysis_hits': sum(map(int, re.findall(r'CONFIGURED_TARGET: (\d+)/\d+', text))),
               'local': json.loads(local[-1]) if local else None,
               'remote': json.loads(remote[-1]) if remote else None,
               'remote_committed': 'Remote SkyCache: publication committed' in text,
               'import_skipped': 'Remote SkyCache import skipped:' in text}
        if not primer:
            output = (root / mode / 'bazel-bin/app/narrow.txt').read_bytes()
            expected = ''.join(('edited' if changed and i == 0 else 'base')
                               + f':{i:04d}\n' for i in range(args.selected)).encode()
            row['output_correct'] = output == expected
            assert row['output_correct'], row
            if mode != 'normal':
                assert row['analysis_hits'] > 0 and not row['import_skipped'], row
                assert row['local'] is not None and not row['local']['failed'], row
        return row

    try:
        (seed / 'MODULE.bazel').write_text('module(name="skycache_remote_benchmark")\n')
        (seed / 'BUILD').write_text('# Synthetic remote analysis workload.\n')
        for number in range(args.packages):
            package = seed / f'lib{number:04d}'
            package.mkdir()
            (package / 'BUILD').write_text(leaf(number))
        (seed / 'app').mkdir()
        app = []
        for name, count in [('wide', args.packages), ('narrow', args.selected)]:
            labels = [f'//lib{i:04d}:value' for i in range(count)]
            app.append('genrule(name=' + json.dumps(name) + ', srcs=' + json.dumps(labels)
                       + ', outs=[' + json.dumps(name + '.txt') + '], cmd="cat $(SRCS) > $@")\n')
        (seed / 'app/BUILD').write_text(''.join(app))
        git('init', '--quiet')
        git('add', '.')
        git('-c', 'user.name=SkyCache Benchmark', '-c', 'user.email=bench@example.invalid',
            'commit', '--quiet', '-m', 'Synthetic wide graph with narrow reader')
        for mode in binaries:
            git('worktree', 'add', '--detach', str(root / mode), 'HEAD')
        # Built-in-tool repository bindings include each binary's installation directory.
        # Prime each binary independently, retaining identical source graph and cache state.
        report['primers'] = {}
        for mode in ['full', 'demand']:
            name = 'primer-' + mode
            start_worker(name, frozen=False)
            report['primers'][mode] = build(name, name, False, primer=True)
            assert report['primers'][mode]['remote_committed'], report['primers'][mode]
            stop_worker()
        # Each mode's warm-up is excluded. Clean resets graph/actions but preserves the JVM/JIT.
        for mode in ['normal', 'full', 'demand']:
            start_worker('warmup-' + mode, primer_mode='full' if mode == 'full' else 'demand')
            report['warmups'].append(build(mode, 'warmup-' + mode, False))
            save()
        # Rotating mode order limits drift; each binary's remote runs see its frozen primer.
        modes = ['normal', 'full', 'demand']
        for repetition in range(args.repetitions):
            for changed in [False, True]:
                for mode in modes[repetition % 3:] + modes[:repetition % 3]:
                    label = f'{mode}-r{repetition}-' + ('changed' if changed else 'unchanged')
                    command(mode, ['clean'], 'clean-' + label)
                    (root / mode / 'lib0000/BUILD').write_text(leaf(0, changed))
                    start_worker(label, primer_mode='full' if mode == 'full' else 'demand')
                    row = build(mode, label, changed)
                    report['runs'].append(row)
                    save()
                    print(json.dumps({key: row[key] for key in
                                      ['label', 'seconds', 'bazel_seconds', 'analysis_hits',
                                       'wire_download_bytes', 'wire_upload_bytes']}), flush=True)
        report['summary'] = []
        for changed in [False, True]:
            for mode in modes:
                rows = [row for row in report['runs']
                        if row['mode'] == mode and row['changed'] == changed]
                report['summary'].append({
                    'mode': mode, 'changed': changed, 'n': len(rows),
                    **{key: statistics.median(row[key] for row in rows) for key in
                       ['seconds', 'bazel_seconds', 'wire_download_bytes', 'wire_upload_bytes']}})
        report['passed'] = True
    except BaseException as error:
        report['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        stop_worker()
        for mode in sorted(started_clients):
            try:
                command(mode, ['shutdown'], 'shutdown-' + mode, timeout=90)
            except Exception as error:
                report['cleanup_errors'].append(str(error))
        proxy.shutdown()
        proxy.server_close()
        if report['cleanup_errors']:
            report['passed'] = False
        save()
    print(json.dumps(report['summary'], indent=2), flush=True)


if __name__ == '__main__':
    main()
