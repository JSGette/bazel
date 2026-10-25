#!/usr/bin/env python3
"""Private six-target Datadog comparison: true vanilla, local and remote SkyCache.

Run on WSL/Linux. A pilot always precedes the full 18-run default matrix. Preparation,
the fresh remote primer, pilot, worker restarts and one-round server read warmups are
excluded from measured build times. Existing source/output/cache fixtures are read-only.
"""

import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import socket
import socketserver
import statistics
import subprocess
import sys
import threading
import time


REPOS = Path('/home/jgett/REPOS')
WORK = REPOS / '.work'
SOURCE = REPOS / 'bazel-skycache-remote-mvp'
HELPERS = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work')
DATADOG = REPOS / 'datadog-agent'
REVISION = '0cde96beb70963d0838119c308b9d0422ee66be0'
OLD_OUT = WORK / 'datadog-ssd-fresh-2026-10-25/out-skycache'
PREPARED_LOCK = WORK / 'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after'
LOCK_SHA = '0bb43dc2960626918c20c350491be5cdf6fd18730bb34a956d4608a1e1aa98fc'
COMMON_TOOLS = Path('/home/jgett/.cache/bazel/_bazel_jgett/install/'
                    '30645dc200dd3c4da7733204f17aaae1/embedded_tools')
WARMUP_PYTHON = WORK / 'skycache-remote-mvp/benchmark-rpc-venv/bin/python'
NAMES = ['agent', 'cluster-agent', 'process-agent', 'trace-agent', 'security-agent', 'system-probe']


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(path, *arguments):
    return subprocess.check_output(['git', '-C', str(path), *arguments], text=True).strip()


def stopped(output):
    """Reject a live fixture server; never stop or modify it."""
    pidfile = output / 'server/server.pid.txt'
    if pidfile.exists():
        pid = int(pidfile.read_text())
        command = Path(f'/proc/{pid}/cmdline')
        if command.exists() and str(output).encode() in command.read_bytes():
            raise RuntimeError('external repository source still has a live server: ' + str(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--bazel', type=Path, required=True)
    parser.add_argument('--vanilla-bazel', type=Path, required=True)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--pilot-only', action='store_true',
                        help='Stop after the primer and one excluded three-arm minimal pilot')
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error('--repetitions must be positive')
    if sys.platform != 'linux':
        parser.error('Run this script in WSL/Linux')
    root = args.root.resolve()
    if root.exists():
        parser.error('--root must be new; this runner never overwrites a previous run')
    binaries = {name: path.resolve(strict=True) for name, path in
                [('candidate', args.bazel), ('vanilla', args.vanilla_bazel), ('worker', args.worker)]}
    for required in [PREPARED_LOCK, COMMON_TOOLS, WARMUP_PYTHON, OLD_OUT / 'external']:
        if not required.exists():
            parser.error('Required fixture missing: ' + str(required))

    # The old helpers set a historical watcher snapshot during import. Preserve the caller's
    # explicit watcher, or use live WSL interop instead of silently accepting a stale default.
    snapshot = os.environ.get('SKYCACHE_WINDOWS_STORAGE_SNAPSHOT')
    w = load('agent_wide', HELPERS / 'datadog-wide.py')
    h = w.h
    if snapshot is None:
        os.environ.pop('SKYCACHE_WINDOWS_STORAGE_SNAPSHOT', None)
    else:
        os.environ['SKYCACHE_WINDOWS_STORAGE_SNAPSHOT'] = snapshot
    storage = load('agent_storage', SOURCE / 'tools/skycache/storage_preflight.py')
    relocation = load('agent_relocation', SOURCE / 'tools/skycache/datadog_worktree_probe.py')
    wire = load('agent_wire', SOURCE / 'tools/skycache/remote_read_benchmark.py')
    h.storage = lambda: storage.check_storage([root], minimum_bytes=30 * 1024**3)
    h.storage()
    lock = (WORK / 'skyframe-measurements.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stopped(OLD_OUT)
    root.mkdir(parents=True, exist_ok=False)
    w.ROOT = h.ROOT = root
    h.WS = root / 'worktree'
    h.OUT = root / 'output-base'
    h.OLD = root / 'scratch'
    for directory in [h.OLD / 'tmp', h.OLD / 'links']:
        directory.mkdir(parents=True)
    h.CACHE = root / 'cache-prepare-vanilla'
    w.CANDIDATE = binaries['candidate']
    w.VANILLA = binaries['vanilla']
    w.NAMES = NAMES
    w.TARGETS = ['//cmd/' + name + ':' + name for name in NAMES]
    h.publications = lambda: {
        str(p.relative_to(h.CACHE)): h.sha(p)
        for p in sorted((h.CACHE / 'leased-v2/publications-v4').rglob('*.manifest'))}
    original_state = (git(DATADOG, 'rev-parse', 'HEAD'), git(DATADOG, 'diff', '--binary', 'HEAD'))
    original_lock = h.sha(DATADOG / 'MODULE.bazel.lock')
    assert h.sha(PREPARED_LOCK) == LOCK_SHA, 'prepared lock changed'
    tools_before = wire.tree_inventory(COMMON_TOOLS)
    w.P = dict(status='preparing', runs=[], started_at=h.stamp(),
               pilot_only=args.pilot_only, repetitions=args.repetitions,
               workspace=str(h.WS), output_base=str(h.OUT), source_revision=REVISION,
               module_lock_sha256=LOCK_SHA, targets=w.TARGETS,
               binary_sha256={key: h.sha(path) for key, path in binaries.items()},
               binary_paths={key: str(path) for key, path in binaries.items()},
               boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
               tools_inventory=tools_before, server_warmups={}, cleanup_errors=[],
               method='One private Datadog linked worktree/output base, identical source/lock/tools. '
               'Fresh JVM and synchronous clean before every build; empty graph/outputs/action '
               'cache checked. Same remote action cache/BES; no disk cache. New binary for local '
               'and remote. One empty-cache remote toplevel primer; identical frozen local seed '
               'and remote catalog. Fresh staging cache each run. Remote backend restart and '
               'one full read/idempotent-commit warmup excluded from timers; new commit writes '
               'not warmed. Pilot excluded. Rotating three-arm minimal/toplevel measurements. '
               'Metadata proxy counts gRPC wire bytes, separate from action-cache gRPC logs.')
    w.persist()
    counter = wire.Counter()
    worker = worker_log = proxy = None
    worker_port = None
    current_arm = None
    primer_cache = root / 'cache-primer'
    primer_catalog = root / 'catalog-primer'
    primer_receipts = None
    private_state = None

    def simple(argv, label, timeout=600, cwd=root):
        h.storage()
        started = time.monotonic()
        with (root / (label + '.log')).open('x') as log:
            completed = subprocess.run(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                       check=True, timeout=timeout)
        h.storage()
        return dict(argv=list(map(str, argv)), seconds=time.monotonic() - started,
                    exit_code=completed.returncode)

    def receipts(catalog):
        return {p.name: h.sha(p) for p in sorted(catalog.glob('receipt-*.pb'))}

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
        if not counter.wait_idle(10):
            raise RuntimeError('metadata proxy connections did not drain')

    def start_worker(label, primer=False):
        nonlocal worker, worker_log
        stop_worker()
        catalog = primer_catalog if primer else root / ('catalog-' + label)
        if not primer:
            shutil.copytree(primer_catalog, catalog)
            assert receipts(catalog) == primer_receipts
        log_path = root / ('worker-' + label + '.log')
        worker_log = log_path.open('x')
        argv = [str(binaries['worker']), '--listen_address=127.0.0.1',
                '--listen_port=' + str(worker_port), '--cas_path=' + str(root / 'metadata-cas'),
                '--skycache_catalog_path=' + str(catalog), '--skycache_instance_name=datadog-bench']
        worker = subprocess.Popen(argv, cwd=root, stdout=worker_log,
                                  stderr=subprocess.STDOUT, start_new_session=True)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                raise RuntimeError('worker exited; see ' + str(log_path))
            if 'Not starting HTTP cache server' in log_path.read_text():
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('worker startup timed out; see ' + str(log_path))
        if not primer:
            output = root / ('warm-server-' + label + '.json')
            argv = [str(WARMUP_PYTHON), str(SOURCE / 'tools/skycache/remote_benchmark_warmup.py'),
                    '--endpoint', '127.0.0.1:' + str(worker_port), '--catalog', str(catalog),
                    '--rounds', '1', '--output', str(output)]
            invocation = simple(argv, 'warm-server-' + label, timeout=600)
            result = json.loads(output.read_text())
            assert (result['passed'] and result['rounds'] == 1
                    and result['catalog_candidates_unchanged']
                    and result['catalog_receipts_unchanged']
                    and result['request_metadata_sent']
                    and result['new_publication_commit_warmed'] is False), result
            w.P['server_warmups'][label] = dict(result, invocation=invocation)
            w.persist()

    old_command = w.command

    def command(label, policy, variant):
        argv = old_command(label, policy, variant)
        argv += ['--override_repository=bazel_tools=' + str(COMMON_TOOLS)]
        if current_arm in ('remote', 'primer'):
            argv += ['--experimental_remote_skycache=grpc://127.0.0.1:'
                     + str(proxy.server_address[1]),
                     '--experimental_remote_skycache_instance_name=datadog-bench',
                     '--experimental_remote_skycache_max_transfer_size=2G',
                     '--experimental_remote_skycache_timeout=600s']
        return argv

    w.command = command

    def run(label, policy, arm, preparation=False, phase='measured'):
        nonlocal current_arm
        current_arm = arm
        h.CACHE = primer_cache if arm == 'primer' else root / ('cache-' + label)
        assert not h.CACHE.exists(), 'client cache must start fresh'
        if arm == 'local':
            for part in ['objects', 'publications-v4']:
                shutil.copytree(primer_cache / 'leased-v2' / part,
                                h.CACHE / 'leased-v2' / part, copy_function=os.link)
        seeded_files, seeded_bytes = wire.seeded_file_sizes(h.CACHE)
        if arm in ('remote', 'primer'):
            start_worker(label, primer=arm == 'primer')
        else:
            stop_worker()
        before = counter.snapshot()
        sample_done = threading.Event()
        backend_samples = []

        def sample_backend():
            while not sample_done.is_set():
                if worker is not None:
                    backend_samples.append(h.process_memory([worker.pid]))
                sample_done.wait(0.1)

        sampler = threading.Thread(target=sample_backend)
        sampler.start()
        try:
            w.run(label, policy, 'vanilla' if arm == 'vanilla' else 'on', preparation)
        finally:
            sample_done.set()
            sampler.join()
            after = counter.snapshot()
            row = w.P['runs'][-1]
            text = (root / (label + '.log')).read_text(errors='replace') if (
                root / (label + '.log')).exists() else ''
            stats = re.findall(r'Remote SkyCache transfers: (\{[^\n]+\})', text)
            row.update(arm=arm, phase=phase, measured=phase == 'measured',
                       wire_download_bytes=after[0] - before[0],
                       wire_upload_bytes=after[1] - before[1],
                       remote=json.loads(stats[-1]) if stats else None,
                       remote_discovered=sum(map(int, re.findall(
                           r'Remote SkyCache: discovered (\d+) publications', text))),
                       remote_committed='Remote SkyCache: publication committed' in text,
                       remote_skips=re.findall(r'Remote SkyCache (?:import|publication) skipped: ([^\n]+)', text),
                       seeded_cache_files=seeded_files, seeded_cache_bytes=seeded_bytes,
                       worker_peak_pss_kib=max((sample[1] for sample in backend_samples), default=0),
                       worker_peak_rss_kib=max((sample[0] for sample in backend_samples), default=0))
            w.persist()
        assert not row['remote_skips'], row['remote_skips']
        assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == w.P['boot_id']
        local = row.get('local-skycache.json', {})
        if arm != 'vanilla':
            assert local and not local['failed'] and not local['read_limit_skips'], local
        if arm == 'primer':
            assert row['remote_committed'] and row['wire_upload_bytes'] > 256 * 1024**2, row
        elif arm in ('local', 'remote'):
            assert row['analysis_hits'] > 0 and row['execution_hits'] > 0, row
            if arm == 'remote':
                assert row['remote_discovered'] > 0 and row['remote']['downloaded_bytes'] > 0, row
        if arm not in ('remote', 'primer'):
            assert row['wire_download_bytes'] == row['wire_upload_bytes'] == 0, row
        w.persist()
        stop_worker()
        return row

    try:
        w.P['setup'] = simple(['git', '-C', str(DATADOG), 'worktree', 'add', '--detach',
                               str(h.WS), REVISION], 'setup-worktree')
        shutil.copy2(PREPARED_LOCK, h.WS / 'MODULE.bazel.lock')
        (h.OUT / 'external').mkdir(parents=True)
        w.P['external_copy'] = simple(['rsync', '-a', '--stats', str(OLD_OUT / 'external') + '/',
                                       str(h.OUT / 'external') + '/'], 'setup-external', timeout=1200)
        stopped(OLD_OUT)
        relocations = relocation.relocate_copied_symlinks(
            h.OUT, [(DATADOG, h.WS), (OLD_OUT, h.OUT)])
        h.save(root / 'external-relocations.json', relocations)
        private_state = git(h.WS, 'diff', '--binary', 'HEAD')
        assert git(h.WS, 'rev-parse', 'HEAD') == REVISION
        assert git(h.WS, 'diff', '--name-only', 'HEAD') == 'MODULE.bazel.lock'
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

        proxy = wire.Proxy(('127.0.0.1', 0), Handler)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        w.P['metadata_endpoint'] = 'grpc://127.0.0.1:' + str(proxy.server_address[1])
        # Stabilize copied external repositories and establish independent vanilla output hashes.
        run('prepare-vanilla-toplevel', 'toplevel', 'vanilla', True, 'preparation')
        run('primer', 'toplevel', 'primer', True, 'primer')
        primer_receipts = receipts(primer_catalog)
        assert primer_receipts, 'primer did not persist a catalog receipt'
        w.P['primer_receipts'] = primer_receipts
        w.P['primer_inventory'] = dict(zip(('files', 'bytes'), wire.seeded_file_sizes(primer_cache)))
        w.P['status'] = 'pilot'
        pilot = {arm: run('pilot-minimal-' + arm, 'minimal', arm, phase='pilot')
                 for arm in ['vanilla', 'local', 'remote']}
        assert pilot['local']['analysis_hits'] == pilot['remote']['analysis_hits']
        assert pilot['local']['execution_hits'] == pilot['remote']['execution_hits']
        w.P['pilot'] = dict(passed=True, seconds={arm: row['seconds'] for arm, row in pilot.items()},
                           remote_over_local=pilot['remote']['seconds'] / pilot['local']['seconds'],
                           remote_over_vanilla=pilot['remote']['seconds'] / pilot['vanilla']['seconds'])
        w.persist()
        print(json.dumps({'pilot': w.P['pilot'], 'pilot_only': args.pilot_only}), flush=True)
        if not args.pilot_only:
            # Excluded per-arm toplevel preparation; fresh caches prevent these runs warming
            # measured local/remote metadata. The JVM is reset by the harness on every run.
            for arm in ['vanilla', 'local', 'remote']:
                run('warmup-toplevel-' + arm, 'toplevel', arm, True, 'preparation')
            w.P['status'] = 'measuring'
            arms = ['vanilla', 'local', 'remote']
            for repetition in range(args.repetitions):
                order = arms[repetition % 3:] + arms[:repetition % 3]
                policies = ['minimal', 'toplevel'] if repetition % 2 == 0 else ['toplevel', 'minimal']
                for policy in policies:
                    for arm in order:
                        run(f'r{repetition + 1}-{policy}-{arm}', policy, arm)
        measured = [row for row in w.P['runs'] if row['measured']]
        w.P['summary'] = []
        for policy in ['minimal', 'toplevel']:
            for arm in ['vanilla', 'local', 'remote']:
                rows = [row for row in measured if row['mode'] == policy and row['arm'] == arm]
                if rows:
                    w.P['summary'].append(dict(arm=arm, policy=policy, count=len(rows),
                                              median_seconds=statistics.median(r['seconds'] for r in rows),
                                              median_peak_pss_kib=statistics.median(
                                                  r['peak_pss_kib'] for r in rows)))
        w.P['measured_count'] = len(measured)
        assert len(measured) == (0 if args.pilot_only else args.repetitions * 6)
        assert receipts(primer_catalog) == primer_receipts, 'frozen primer catalog changed'
        w.P['status'] = 'pilot-passed' if args.pilot_only else 'passed'
    except BaseException as error:
        w.P['status'] = 'failed'
        w.P['failure'] = repr(error)
        raise
    finally:
        for name, action in [('client_shutdown', lambda: h.shutdown('final-shutdown')),
                             ('worker_shutdown', stop_worker)]:
            try:
                w.P[name] = action()
            except BaseException as error:
                w.P['cleanup_errors'].append(name + ': ' + repr(error))
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
        w.P['main_workspace_unchanged'] = original_state == (
            git(DATADOG, 'rev-parse', 'HEAD'), git(DATADOG, 'diff', '--binary', 'HEAD'))
        w.P['main_lock_unchanged'] = h.sha(DATADOG / 'MODULE.bazel.lock') == original_lock
        w.P['private_workspace_unchanged'] = private_state is None or private_state == git(
            h.WS, 'diff', '--binary', 'HEAD')
        w.P['common_tools_unchanged'] = tools_before == wire.tree_inventory(COMMON_TOOLS)
        if w.P['cleanup_errors'] or not all(w.P[key] for key in [
                'main_workspace_unchanged', 'main_lock_unchanged', 'private_workspace_unchanged',
                'common_tools_unchanged']):
            w.P['status'] = 'failed'
        w.P['finished_at'] = h.stamp()
        w.persist()
        lock.close()
    if w.P['status'] == 'failed':
        raise RuntimeError('cleanup or source preservation failed; see results.json')


if __name__ == '__main__':
    main()
