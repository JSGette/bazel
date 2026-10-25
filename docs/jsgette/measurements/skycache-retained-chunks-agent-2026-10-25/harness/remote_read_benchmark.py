#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Paired local-loopback benchmark of revisions or vanilla/local/remote storage.

Uses warm Bazel JVMs, clean analysis/action state, fresh client metadata directories,
and frozen primer state. Storage comparisons use one primer for local and remote
readers; revision comparisons retain a primer catalog per binary. A TCP proxy reports gRPC
wire bytes (including framing); no latency or bandwidth shaping is applied.
"""

import argparse
import base64
from datetime import datetime, timezone
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
import stat
import statistics
import subprocess
import threading
import time


class Counter:
    def __init__(self):
        self.lock = threading.Lock()
        self.idle = threading.Condition(self.lock)
        self.connections = 0
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

    def connected(self):
        with self.idle:
            self.connections += 1

    def disconnected(self):
        with self.idle:
            self.connections -= 1
            self.idle.notify_all()

    def wait_idle(self, timeout):
        with self.idle:
            return self.idle.wait_for(lambda: self.connections == 0, timeout)


class Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def tree_inventory(directory):
    """Hash names, kinds, permissions and contents; exclude timestamps and ownership."""
    entries = []
    file_bytes = files = symlinks = 0
    for base, directories, filenames in os.walk(directory, followlinks=False):
        for name in directories + filenames:
            path = Path(base) / name
            metadata = path.lstat()
            entry = {'path': path.relative_to(directory).as_posix(),
                     'mode': stat.S_IMODE(metadata.st_mode)}
            if stat.S_ISLNK(metadata.st_mode):
                entry.update(kind='symlink', target=os.readlink(path))
                symlinks += 1
            elif stat.S_ISREG(metadata.st_mode):
                digest = hashlib.sha256()
                with path.open('rb') as contents:
                    for chunk in iter(lambda: contents.read(1024 * 1024), b''):
                        digest.update(chunk)
                entry.update(kind='file', size=metadata.st_size, sha256=digest.hexdigest())
                files += 1
                file_bytes += metadata.st_size
            elif stat.S_ISDIR(metadata.st_mode):
                entry['kind'] = 'directory'
            else:
                raise ValueError('unsupported tool tree entry: ' + str(path))
            entries.append(entry)
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item['path']):
        digest.update(json.dumps(entry, sort_keys=True, separators=(',', ':')).encode())
        digest.update(b'\n')
    return {'path': str(directory), 'sha256': digest.hexdigest(),
            'files': files, 'file_bytes': file_bytes, 'symlinks': symlinks}


def seeded_file_sizes(directory):
    files = file_bytes = 0
    for base, _, filenames in os.walk(directory, followlinks=False):
        for name in filenames:
            metadata = (Path(base) / name).lstat()
            if stat.S_ISREG(metadata.st_mode):
                files += 1
                file_bytes += metadata.st_size
    return files, file_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    reference = parser.add_mutually_exclusive_group(required=True)
    reference.add_argument('--old-bazel', type=Path)
    reference.add_argument('--vanilla-bazel', type=Path)
    parser.add_argument('--new-bazel', type=Path, required=True)
    parser.add_argument('--tools-dir', type=Path)
    parser.add_argument('--server-warmup-python', type=Path,
                        help='Python with grpcio; warm frozen server read paths before timing')
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
    storage_comparison = args.vanilla_bazel is not None
    server_warmup_python = None
    if args.server_warmup_python is not None:
        # Keep a virtualenv's interpreter path: resolving its symlink loses site-packages.
        server_warmup_python = args.server_warmup_python.absolute()
        if not server_warmup_python.is_file():
            parser.error('--server-warmup-python must be a Python executable file')
    tools_dir = None
    if args.tools_dir is not None:
        try:
            tools_dir = args.tools_dir.resolve(strict=True)
        except OSError as error:
            parser.error('--tools-dir must be an existing directory: ' + str(error))
        if not tools_dir.is_dir():
            parser.error('--tools-dir must be an existing directory')
    new_bazel = args.new_bazel.resolve(strict=True)
    if storage_comparison:
        modes = ['vanilla', 'local', 'demand']
        binaries = {'vanilla': args.vanilla_bazel.resolve(strict=True), 'local': new_bazel,
                    'demand': new_bazel, 'primer-demand': new_bazel}
        mode_labels = {'vanilla': 'Upstream Bazel; SkyCache disabled',
                       'local': 'Local SkyCache; identical primer',
                       'demand': 'Remote SkyCache; demand reads'}
        primer_modes = ['demand']
        skycache_modes = {'local', 'demand'}
        remote_modes = {'demand'}
    else:
        modes = ['normal', 'full', 'demand']
        old_bazel = args.old_bazel.resolve(strict=True)
        binaries = {'normal': new_bazel, 'full': old_bazel, 'demand': new_bazel,
                    'primer-full': old_bazel, 'primer-demand': new_bazel}
        mode_labels = {'normal': 'SkyCache disabled',
                       'full': args.old_label, 'demand': args.new_label}
        primer_modes = ['full', 'demand']
        skycache_modes = remote_modes = {'full', 'demand'}
    tools_before = tree_inventory(tools_dir) if tools_dir is not None else None
    worker_binary = args.worker.resolve(strict=True)
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    seed = root / 'seed'
    seed.mkdir()
    counter = Counter()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        worker_port = reservation.getsockname()[1]

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            counter.connected()
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
            finally:
                counter.disconnected()

    proxy = Proxy(('127.0.0.1', 0), Handler)
    threading.Thread(target=proxy.serve_forever, daemon=True).start()
    endpoint = 'grpc://127.0.0.1:' + str(proxy.server_address[1])
    report = {'passed': False, 'method': __doc__, 'parameters': vars(args).copy(),
              'comparison': 'storage' if storage_comparison else 'revisions',
              'mode_labels': mode_labels, 'started_utc': utc_now(),
              'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
              'tools_inventory_before': tools_before,
              'server_warmups': {},
              'server_warmup_policy': ('read paths and existing idempotent commits only; '
                                       'new-publication commit NOT warmed'
                                       if server_warmup_python else 'cold server'),
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
                if frozen and server_warmup_python is not None:
                    warmup_output = root / ('server-warmup-' + label + '.json')
                    warmup_log = root / ('server-warmup-' + label + '.log')
                    warmup_helper = Path(__file__).resolve().with_name('remote_benchmark_warmup.py')
                    warmup_argv = [str(server_warmup_python), str(warmup_helper),
                                   '--endpoint', f'127.0.0.1:{worker_port}',
                                   '--catalog', str(catalog), '--output', str(warmup_output)]
                    with warmup_log.open('w') as log:
                        subprocess.run(warmup_argv, stdout=log, stderr=subprocess.STDOUT,
                                       check=True, timeout=600)
                    result = json.loads(warmup_output.read_text())
                    assert (result['passed'] and result['catalog_candidates_unchanged']
                            and result['catalog_receipts_unchanged']
                            and result['request_metadata_sent']
                            and result['new_publication_commit_warmed'] is False), result
                    report['server_warmups'][label] = dict(result, argv=warmup_argv,
                                                          log=str(warmup_log))
                    save()
                return
            time.sleep(0.1)
        raise RuntimeError('worker startup timeout')

    def prepare_backend(mode, label):
        if mode in remote_modes:
            start_worker(label, primer_mode=mode)
        else:
            stop_worker()
            if not counter.wait_idle(10):
                raise RuntimeError('proxy connections did not drain before non-remote build')

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
        profile = root / (label + '.profile.gz')
        arguments = ['build', '//app:wide', '//app:narrow'] if primer else ['build', '//app:narrow']
        arguments += ['--jobs=4', '--local_resources=memory=2048', '--lockfile_mode=off',
                      '--experimental_strict_repo_env', '--color=no', '--curses=no',
                      '--profile=' + str(profile),
                      '--remote_cache=', '--disk_cache=',
                      '--repo_env=SKYCACHE_BENCH=stable', '--action_env=SKYCACHE_BENCH=stable']
        if not storage_comparison:
            arguments += ['--experimental_active_directories=app']
        if tools_dir is not None:
            arguments += ['--override_repository=bazel_tools=' + str(tools_dir)]
        if primer:
            arguments += ['--nobuild']
        seeded_files = seeded_bytes = 0
        cache = root / ('cache-' + label)
        if primer or mode in skycache_modes:
            if cache.exists():
                raise RuntimeError('client cache must start fresh: ' + str(cache))
            if mode == 'local':
                managed = cache / 'leased-v2'
                managed.mkdir(parents=True)
                for part in ['objects', 'publications-v4']:
                    shutil.copytree(root / 'cache-primer-demand' / 'leased-v2' / part,
                                    managed / part, symlinks=True)
                seeded_files, seeded_bytes = seeded_file_sizes(cache)
            arguments += ['--experimental_local_skycache=' + str(cache)]
        if primer or mode in remote_modes:
            arguments += ['--experimental_remote_skycache=' + endpoint,
                          '--experimental_remote_skycache_instance_name=bench',
                          '--experimental_remote_skycache_timeout=120s']
        before = counter.snapshot()
        started_utc = utc_now()
        elapsed, text, argv = command(mode, arguments, label)
        finished_utc = utc_now()
        after = counter.snapshot()
        local = re.findall(r'Local SkyCache: (\{[^\n]+\})', text)
        remote = re.findall(r'Remote SkyCache transfers: (\{[^\n]+\})', text)
        bazel_elapsed = re.findall(r'Elapsed time: ([0-9.]+)s', text)
        row = {'mode': mode, 'label': label, 'changed': changed, 'seconds': elapsed,
               'profile': str(profile),
               'server_read_paths_warmed': label in report['server_warmups'],
               'started_utc': started_utc, 'finished_utc': finished_utc,
               'seeded_cache_files': seeded_files, 'seeded_cache_file_bytes': seeded_bytes,
               'bazel_seconds': float(bazel_elapsed[-1]) if bazel_elapsed else None,
               'wire_download_bytes': after[0] - before[0],
               'wire_upload_bytes': after[1] - before[1], 'argv': argv,
               'analysis_hits': sum(map(int, re.findall(r'CONFIGURED_TARGET: (\d+)/\d+', text))),
               'local': json.loads(local[-1]) if local else None,
               'remote': json.loads(remote[-1]) if remote else None,
               'remote_committed': 'Remote SkyCache: publication committed' in text,
               'import_skipped': 'Remote SkyCache import skipped:' in text}
        if not primer and mode not in remote_modes:
            assert row['wire_download_bytes'] == row['wire_upload_bytes'] == 0, row
        if not primer:
            output = (root / mode / 'bazel-bin/app/narrow.txt').read_bytes()
            expected = ''.join(('edited' if changed and i == 0 else 'base')
                               + f':{i:04d}\n' for i in range(args.selected)).encode()
            row['output_correct'] = output == expected
            assert row['output_correct'], row
            if mode in skycache_modes:
                expected_hits = args.selected if changed else args.selected + 1
                assert row['analysis_hits'] == expected_hits and not row['import_skipped'], row
                assert row['local'] is not None and not row['local']['failed'], row
                assert row['local']['read_limit_skips'] == row['local']['scan_limit_hits'] == 0, row
            else:
                assert row['analysis_hits'] == 0, row
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
        # Storage comparisons share one primer's exact publications between local and remote.
        # Revision comparisons retain independent primers for each binary's tool bindings.
        report['primers'] = {}
        for mode in primer_modes:
            name = 'primer-' + mode
            start_worker(name, frozen=False)
            report['primers'][mode] = build(name, name, False, primer=True)
            assert report['primers'][mode]['remote_committed'], report['primers'][mode]
            stop_worker()
        # Each mode's warm-up is excluded. Clean resets graph/actions but preserves the JVM/JIT.
        for mode in modes:
            prepare_backend(mode, 'warmup-' + mode)
            report['warmups'].append(build(mode, 'warmup-' + mode, False))
            stop_worker()
            save()
        # Rotating mode order limits drift; remote runs see a frozen primer catalog.
        for repetition in range(args.repetitions):
            for changed in [False, True]:
                for mode in modes[repetition % 3:] + modes[:repetition % 3]:
                    label = f'{mode}-r{repetition}-' + ('changed' if changed else 'unchanged')
                    command(mode, ['clean'], 'clean-' + label)
                    (root / mode / 'lib0000/BUILD').write_text(leaf(0, changed))
                    prepare_backend(mode, label)
                    row = build(mode, label, changed)
                    stop_worker()
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
        if tools_dir is not None:
            try:
                report['tools_inventory_after'] = tree_inventory(tools_dir)
                report['tools_inventory_unchanged'] = (
                    report['tools_inventory_after']['sha256'] == tools_before['sha256'])
                if not report['tools_inventory_unchanged']:
                    report['cleanup_errors'].append('shared tool tree changed during benchmark')
            except Exception as error:
                report['cleanup_errors'].append('tool tree inventory failed: ' + str(error))
        if report['cleanup_errors']:
            report['passed'] = False
        report['finished_utc'] = utc_now()
        save()
    if not report['passed']:
        raise RuntimeError('benchmark cleanup or tool inventory check failed; see results.json')
    print(json.dumps(report['summary'], indent=2), flush=True)


if __name__ == '__main__':
    main()
