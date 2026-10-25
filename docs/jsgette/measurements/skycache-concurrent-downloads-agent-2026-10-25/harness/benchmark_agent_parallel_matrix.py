#!/usr/bin/env python3
"""Stabilize and measure a validated successful cross-binary Datadog prime.

Require benchmark_agent_parallel_prime.py's successful preparation report, matching
binary/runtime hashes, exact zero-hit prime and its preserved immutable artifacts.
Register only the new publication into a copied catalog, stabilize minimal/toplevel,
then freeze after the second pass writes zero and has the expected 17/11 hits.
First stabilization needs positive hits; its first-use target count may exceed 17.
Reuse original worktree/output/scratch paths; rotate configurable fresh-JVM samples.
"""

import argparse
import fcntl
import hashlib
import hmac
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


def proc_io(pid):
    """Cumulative fresh-server I/O, including startup and clean; not build-only deltas."""
    try:
        return {key: int(value) for key, value in (
            line.split(':', 1) for line in Path(f'/proc/{int(pid)}/io').read_text().splitlines())}
    except (OSError, ValueError, TypeError) as error:
        return {'unavailable': str(error)}


def proto_integer(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError('truncated protobuf varint')
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
    raise ValueError('oversized protobuf varint')


def proto_fields(data):
    result = {}
    offset = 0
    while offset < len(data):
        tag, offset = proto_integer(data, offset)
        number, kind = tag >> 3, tag & 7
        if number < 1:
            raise ValueError('invalid protobuf field')
        if kind == 0:
            value, offset = proto_integer(data, offset)
        elif kind in (1, 2, 5):
            if kind == 2:
                length, offset = proto_integer(data, offset)
            else:
                length = 8 if kind == 1 else 4
            end = offset + length
            if end > len(data):
                raise ValueError('truncated protobuf field')
            value, offset = data[offset:end], end
        else:
            raise ValueError('unsupported protobuf wire type')
        result.setdefault(number, []).append(value)
    return result


def proto_one(data, number, default=None):
    values = proto_fields(data).get(number, [])
    if len(values) > 1:
        raise ValueError('duplicate protobuf singular field')
    return values[0] if values else default


def proto_digest(data):
    sha, size = proto_one(data, 1).decode('ascii'), proto_one(data, 2, 0)
    if not re.fullmatch('[0-9a-f]{64}', sha) or not 0 <= size <= 1024 * 1024:
        raise ValueError('invalid or oversized SHA-256 digest')
    return sha, size


def decode_receipt(path, secret):
    assert 32 < path.stat().st_size <= 1024 * 1024 + 32, 'receipt size invalid'
    encoded, key = path.read_bytes(), secret.read_bytes()
    assert len(key) == 32, 'catalog signing key size invalid'
    assert hmac.compare_digest(hmac.digest(key, encoded[:-32], 'sha256'), encoded[-32:]), (
        'catalog receipt authentication failed')
    request, publication = proto_one(encoded[:-32], 1), proto_one(encoded[:-32], 2)
    assert proto_digest(proto_one(request, 2)) == proto_digest(proto_one(publication, 1))
    assert proto_one(request, 3, b'') and proto_one(publication, 2, 0) > 0
    return request, publication


def validate_prime(cache, catalog, baseline_catalog, cas):
    """Authenticate the sole added receipt and match its bundle to local manifests."""
    def receipt_hashes(directory):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.glob('receipt-*.pb')}

    before, after = receipt_hashes(baseline_catalog), receipt_hashes(catalog)
    assert before and all(after.get(name) == digest for name, digest in before.items())
    added = set(after) - set(before)
    assert len(added) == 1, ('expected exactly one newly published receipt', sorted(added))
    name = added.pop()
    assert re.fullmatch(r'receipt-[0-9]{8}\.pb', name)
    assert (catalog / 'token.key').read_bytes() == (baseline_catalog / 'token.key').read_bytes()
    request, publication = decode_receipt(catalog / name, catalog / 'token.key')
    scope = proto_one(request, 1)
    assert proto_one(scope, 1, b'') == b'datadog-bench'
    namespace = proto_one(scope, 2).decode('ascii')
    assert re.fullmatch('[0-9a-f]{64}', namespace)
    manifest = proto_digest(proto_one(request, 2))

    def read_blob(digest):
        sha, size = digest
        path = cas / 'cas' / sha[:2] / sha
        assert path.is_file() and path.stat().st_size == size, 'manifest/chunk missing from prime CAS'
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha, 'prime CAS digest mismatch'
        return data

    bundle = read_blob(manifest)
    assert proto_one(bundle, 1, 0) == 1 and proto_one(bundle, 2).decode('ascii') == namespace
    root_id = proto_one(bundle, 3).decode('ascii')
    manifests = {path.relative_to(cache / 'leased-v2/publications-v4').as_posix(): path
                 for path in (cache / 'leased-v2/publications-v4').rglob('*.manifest')}
    matched = {}
    for local in proto_fields(bundle).get(4, []):
        identifier = proto_one(local, 1).decode('ascii')
        assert re.fullmatch('[0-9a-f]{64}', identifier)
        relative = namespace + '/' + identifier + '.manifest'
        assert relative in manifests and relative not in matched, 'local publication closure mismatch'
        entries = [entry for entry in proto_fields(local).get(3, [])
                   if proto_one(entry, 1) == b'manifest']
        assert len(entries) == 1
        entry = entries[0]
        assert 0 <= proto_one(entry, 2, 0) <= 1024 * 1024, 'local manifest exceeds inspection bound'
        chunks = [proto_digest(chunk) for chunk in proto_fields(entry).get(3, [])]
        assert sum(size for _, size in chunks) == proto_one(entry, 2, 0)
        contents = b''.join(read_blob(chunk) for chunk in chunks)
        assert contents == manifests[relative].read_bytes(), 'remote and local manifests differ'
        matched[relative] = hashlib.sha256(contents).hexdigest()
    assert set(matched) == set(manifests) and namespace + '/' + root_id + '.manifest' in matched
    return dict(receipt=name, receipt_sha256=after[name], request=request,
                request_sha256=hashlib.sha256(request).hexdigest(),
                sequence=proto_one(publication, 2), manifest=list(manifest), root_id=root_id,
                namespace=namespace, local_manifests=matched,
                sole_new_receipt=True, receipt_authenticated=True, local_closure_matched=True)


# Run only inside the existing grpcio venv, after validation and fixture copying. This uses
# the public RPC to assign a contiguous authenticated receipt without editing receipt bytes.
REGISTER_SEED = r'''
import importlib.util
import sys
import uuid
from pathlib import Path
spec = importlib.util.spec_from_file_location('warmup', sys.argv[1])
warmup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(warmup)
metadata = (('build.bazel.remote.execution.v2.requestmetadata-bin',
             warmup.blob(1, warmup.blob(1, b'skycache-benchmark-prime'))
             + warmup.blob(2, b'new-publication-registration')
             + warmup.blob(3, uuid.uuid4().hex.encode('ascii'))),)
with warmup.grpc.insecure_channel(sys.argv[2]) as channel:
    warmup.grpc.channel_ready_future(channel).result(timeout=30)
    commit = channel.unary_unary('/build.bazel.skycache.v1.SkyCacheCatalog/CommitPublication')
    response = commit(Path(sys.argv[3]).read_bytes(), metadata=metadata, timeout=90)
    Path(sys.argv[4]).write_bytes(response)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--seed-root', type=Path, required=True,
                        help='Stopped agent-steady root with the specific remote writes=15 assertion')
    parser.add_argument('--prime-root', type=Path, required=True,
                        help='Stopped successful cross-binary prime root with one committed publication')
    parser.add_argument('--bazel', type=Path, required=True)
    parser.add_argument('--vanilla-bazel', type=Path, required=True)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--policies', default='minimal',
                        help='Comma-separated policies: minimal,toplevel (default: minimal)')
    parser.add_argument('--arms', default='remote',
                        help='Comma-separated arms: vanilla,local,remote (default: remote)')
    parser.add_argument('--repeats', type=int, default=1)
    args = parser.parse_args()
    policies = [value.strip() for value in args.policies.split(',')]
    arms = [value.strip() for value in args.arms.split(',')]
    for name, selected, allowed in [('policies', policies, {'minimal', 'toplevel'}),
                                    ('arms', arms, {'vanilla', 'local', 'remote'})]:
        if any(value not in allowed for value in selected) or len(set(selected)) != len(selected):
            parser.error('--' + name + ' must contain distinct values from ' + ','.join(sorted(allowed)))
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    if sys.platform != 'linux':
        parser.error('Run this script in WSL/Linux')
    root = args.root.resolve()
    seed_root = args.seed_root.resolve(strict=True)
    prime_root = args.prime_root.resolve(strict=True)
    if root.exists():
        parser.error('--root must be new; this runner never overwrites a previous run')
    if root.is_relative_to(seed_root) or seed_root.is_relative_to(root):
        parser.error('--root and --seed-root must be separate, non-nested directories')
    if any(root.is_relative_to(path) or path.is_relative_to(root)
           for path in [prime_root]):
        parser.error('--root and --prime-root must be separate, non-nested directories')
    assert seed_root != prime_root
    binaries = {name: path.resolve(strict=True) for name, path in
                [('candidate', args.bazel), ('vanilla', args.vanilla_bazel), ('worker', args.worker)]}
    for required in [COMMON_TOOLS, WARMUP_PYTHON, seed_root / 'results.json',
                     prime_root / 'results.json']:
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
    wire = load('agent_wire', SOURCE / 'tools/skycache/remote_read_benchmark.py')
    h.storage = lambda: storage.check_storage([root], minimum_bytes=30 * 1024**3)
    h.storage()
    lock = (WORK / 'skyframe-measurements.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    seed_report = json.loads((seed_root / 'results.json').read_text())
    prior_rows = {row['label']: row for row in seed_report['runs']}
    required_rows = ['pilot-minimal-' + arm for arm in ['vanilla', 'local', 'remote']]
    for label in required_rows:
        row = prior_rows[label]
        assert row['validation'] == 'passed' and row['exit_code'] == 0, label
        assert not row.get('remote_skips'), label
        assert not row['guard_errors'] and not row['remote_warnings'], label
        expected_hits = (0, 0) if row['arm'] == 'vanilla' else (17, 11)
        assert (row['analysis_hits'], row['execution_hits']) == expected_hits, label
        assert {name: value['digest'] for name, value in row['bep_binaries'].items()} == (
            seed_report['expected_binaries']), label
        if row['arm'] != 'vanilla':
            local = row['local-skycache.json']
            assert not any(local[key] for key in [
                'failed', 'read_limit_skips', 'scan_limit_hits', 'rejected_publications']), label
            assert row['grpc']['method_attempt_counts'].get(
                'build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0) == 0, label
            assert row['grpc']['bytestream_read_calls'] == 0, label
            assert row['output_inventory']['archives'] == row['output_inventory']['exports'] == 0
    failed_row = seed_report['runs'][-1]
    assert failed_row['label'] == 'pilot-minimal-remote' and failed_row['arm'] == 'remote'
    failed_stats = failed_row['local-skycache.json']
    assert failed_stats['writes'] == 15 and failed_stats['deduplicated_writes'] == 15
    assert prior_rows['pilot-minimal-local']['local-skycache.json']['writes'] == 0
    assert seed_report['status'] == 'failed'
    assert seed_report['failure'] == repr(AssertionError(failed_stats)), 'unexpected seed failure'
    assert not seed_report.get('cleanup_errors'), 'first-run cleanup failed'
    for key in ['main_workspace_unchanged', 'main_lock_unchanged',
                'private_workspace_unchanged', 'common_tools_unchanged']:
        assert seed_report.get(key), 'first-run preservation check failed: ' + key
    assert seed_report['source_revision'] == REVISION
    workspace = Path(seed_report['workspace']).resolve(strict=True)
    output_base = Path(seed_report['output_base']).resolve(strict=True)
    original_root = Path(seed_report['seed_root']).resolve(strict=True)
    assert original_root != seed_root and original_root != root
    scratch = original_root / 'scratch'
    assert workspace == original_root / 'worktree' and output_base == original_root / 'output-base'
    assert scratch.is_dir()
    stopped(output_base)
    assert h.sha(binaries['vanilla']) == seed_report['binary_sha256']['vanilla'], 'vanilla binary changed'
    assert h.sha(workspace / 'MODULE.bazel.lock') == LOCK_SHA
    prime_report = json.loads((prime_root / 'results.json').read_text())
    assert len(prime_report['runs']) == 1, 'prime preparation must contain exactly one build'
    prime_row = prime_report['runs'][0]
    assert prime_row['label'] == 'prime-minimal-remote'
    assert prime_row['arm'] == 'remote' and prime_row['mode'] == 'minimal'
    assert prime_row['validation'] == 'passed' and prime_row['exit_code'] == 0
    assert (prime_row['analysis_hits'], prime_row['execution_hits']) == (0, 0)
    assert prime_report['status'] == 'passed' and not prime_report.get('failure')
    assert prime_report['purpose'] == 'cross-binary-prime'
    assert prime_report['prime']['passed'] and prime_report['prime']['label'] == prime_row['label']
    assert prime_report['prime']['phase'] == prime_row['phase'] == 'prime'
    assert prime_row['measured'] is False
    assert prime_report['measured_count'] == 0 and prime_report['preparation_count'] == 1
    assert prime_report['policies'] == ['minimal'] and prime_report['arms'] == ['remote']
    assert prime_report['repeats'] == 1
    assert prime_report['expected_prime_hits'] == {'analysis_hits': 0, 'execution_hits': 0}
    assert not prime_row['guard_errors'] and not prime_row['remote_warnings']
    assert not prime_row['remote_skips'] and not prime_report['cleanup_errors']
    prime_stats = prime_row['local-skycache.json']
    assert prime_stats['published'] and prime_stats['writes'] > 0 and prime_row['remote_committed']
    assert not any(prime_stats[key] for key in [
        'failed', 'read_limit_skips', 'scan_limit_hits', 'rejected_publications'])
    assert {name: value['digest'] for name, value in prime_row['bep_binaries'].items()} == (
        seed_report['expected_binaries'])
    assert prime_report['expected_binaries'] == seed_report['expected_binaries']
    assert prime_row['output_inventory']['archives'] == prime_row['output_inventory']['exports'] == 0
    assert prime_report['source_revision'] == REVISION
    assert Path(prime_report['seed_root']).resolve() == seed_root
    assert prime_report['seed_report_sha256'] == h.sha(seed_root / 'results.json')
    assert prime_report['original_report_sha256'] == h.sha(original_root / 'results.json')
    assert Path(prime_report['workspace']).resolve() == workspace
    assert Path(prime_report['output_base']).resolve() == output_base
    for key in ['main_workspace_unchanged', 'main_lock_unchanged', 'private_workspace_unchanged',
                'common_tools_unchanged', 'benchmark_binaries_unchanged', 'worker_artifacts_unchanged',
                'original_primer_receipts_unchanged', 'original_local_seed_unchanged',
                'original_cas_unchanged', 'copied_frozen_receipts_unchanged',
                'copied_frozen_local_seed_unchanged', 'steady_report_unchanged',
                'first_report_unchanged', 'produced_prime_local_unchanged',
                'produced_prime_catalog_unchanged', 'produced_prime_cas_unchanged']:
        assert prime_report.get(key), 'prime preservation check failed: ' + key
    for name, path in binaries.items():
        assert h.sha(path) == prime_report['binary_sha256'][name], name + ' differs from prime binary'
    assert prime_row['binary_sha256'] == prime_report['binary_sha256']['candidate']
    root.mkdir(parents=True, exist_ok=False)
    w.ROOT = h.ROOT = root
    h.WS = workspace
    h.OUT = output_base
    h.OLD = scratch
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
    tools_before = wire.tree_inventory(COMMON_TOOLS)

    def worker_artifacts():
        # The adjacent worker.jar can be only a stub. Resolve every runtime JAR from the
        # runfiles manifest, including the implementation, and hash contents outside timers.
        import zipfile

        launcher = binaries['worker']
        manifests = [Path(str(launcher) + '.runfiles_manifest'),
                     Path(str(launcher) + '.runfiles') / 'MANIFEST']
        manifest = next((path for path in manifests if path.is_file()), None)
        if manifest is None:
            raise RuntimeError('Cannot identify worker runtime: runfiles manifest is missing')
        manifest = manifest.resolve(strict=True)
        paths = {launcher.resolve(strict=True), manifest}
        implementations = set()
        implementation_name = 'src/tools/remote/src/main/java/com/google/devtools/build/remote/worker/libworker.jar'

        def unescape(value):
            # Bazel escaped manifests start with a space. Decode backslashes last so an
            # encoded literal backslash followed by s/n is not mistaken for another escape.
            return value.replace('\\s', ' ').replace('\\n', '\n').replace('\\b', '\\')

        for line in manifest.read_text().splitlines():
            if not line:
                continue
            escaped = line.startswith(' ')
            logical, separator, target = (line[1:] if escaped else line).partition(' ')
            if not separator:
                target = logical
            if escaped:
                logical, target = unescape(logical), unescape(target)
            if not (logical.endswith('.jar') or target.endswith('.jar')):
                continue
            resolved = Path(target)
            if not resolved.is_absolute():
                raise RuntimeError('Cannot resolve relative worker runtime JAR: ' + logical)
            resolved = resolved.resolve(strict=True)
            if not resolved.is_file():
                raise RuntimeError('Worker runtime JAR is not a file: ' + logical)
            paths.add(resolved)
            if logical == implementation_name or logical.endswith('/' + implementation_name):
                implementations.add(resolved)
        if len(implementations) != 1:
            raise RuntimeError('Cannot identify exactly one worker implementation JAR in runfiles')
        implementation = next(iter(implementations))
        try:
            with zipfile.ZipFile(implementation) as jar:
                jar.getinfo('com/google/devtools/build/remote/worker/RemoteWorker.class')
        except (OSError, KeyError, zipfile.BadZipFile) as error:
            raise RuntimeError('Worker implementation JAR lacks runnable RemoteWorker code') from error
        return {str(path): h.sha(path) for path in sorted(paths)}

    runtime_artifacts = worker_artifacts()
    assert runtime_artifacts == prime_report['worker_artifacts_sha256'], 'prime worker runtime changed'
    w.P = dict(status='preparing', purpose='parallel-read-matrix', runs=[], started_at=h.stamp(),
               policies=policies, arms=arms, repeats=args.repeats,
               workspace=str(h.WS), output_base=str(h.OUT), source_revision=REVISION,
               module_lock_sha256=LOCK_SHA, targets=w.TARGETS,
               binary_sha256={key: h.sha(path) for key, path in binaries.items()},
               binary_paths={key: str(path) for key, path in binaries.items()},
               baseline_binary_sha256=seed_report['binary_sha256'],
               worker_artifacts_sha256=runtime_artifacts,
               boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
               tools_inventory=tools_before, server_warmups={}, cleanup_errors=[],
               expected_binaries=seed_report['expected_binaries'],
               seed_root=str(seed_root), seed_report_sha256=h.sha(seed_root / 'results.json'),
               original_root=str(original_root),
               original_report_sha256=h.sha(original_root / 'results.json'),
               original_upload=seed_report['original_upload'],
               accepted_seed_failure=dict(label=failed_row['label'], failure=seed_report['failure'],
                                          reason='Only the overstrict remote zero-write assertion failed; '
                                          'all three completed minimal builds passed output/cache checks.'),
               prime_root=str(prime_root), prime_report_sha256=h.sha(prime_root / 'results.json'),
               accepted_prime=dict(passed=True, label=prime_row['label'],
                   reason='Deliberate cross-binary preparation passed with zero old-seed hits and '
                   'one new publication. Candidate and runtime artifacts are pinned to that prime.'),
               new_binary_upload={key: prime_row.get(key) for key in [
                   'label', 'seconds', 'binary_sha256', 'local-skycache.json', 'remote',
                   'remote_committed', 'wire_upload_bytes', 'wire_download_bytes', 'server_proc_io']},
               expected_cache_hits={'analysis_hits': 17, 'execution_hits': 11},
               method='Reuse original Datadog worktree/output/scratch paths. Validate historical '
               'steady seed and successful deliberate zero-hit prime. Hardlink only the new '
               'local publication closure and prime CAS; authenticate the sole new source receipt '
               'and replay its original CommitRequest into a new catalog to assign valid sequence 1. '
               'Stabilize minimal then toplevel with the same copied local cache/catalog; both must '
               'hit, final pass must write zero. Freeze after stabilization. Fresh JVM and synchronous '
               'clean before every build; empty graph/outputs/action cache checked. Same remote '
               'action cache/BES; no disk cache. Identical frozen local seed and remote catalog. '
               'Fresh staging cache each measured run. Remote backend restart and one full '
               'read/idempotent-commit warmup excluded from timers; new commit writes not warmed. '
               'Selected arms rotate and policy order alternates across repeats. Candidate, vanilla '
               'and worker runtime must match the prime preparation. Local writes must be zero; remote '
               'uncached cache-only writes are recorded, not capped. Metadata proxy counts gRPC '
               'wire bytes separately from action-cache gRPC logs; no additional pilot is run.')
    w.persist()
    counter = wire.Counter()
    worker = worker_log = proxy = None
    worker_port = None
    current_arm = None
    primer_cache = root / 'cache-primer'
    primer_catalog = root / 'catalog-primer'
    primer_receipts = None
    private_state = None
    source_seed_inventory = None
    source_cas_inventory = None
    original_primer_receipts = None
    frozen_seed_inventory = None
    prime_local_inventory = prime_cas_inventory = prime_catalog_inventory = None
    prime_frozen_inventory = prime_primer_catalog_inventory = None
    prime_cache = prime_root / 'cache-prime-minimal-remote'
    prime_catalog = prime_root / 'catalog-prime-minimal-remote'

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

    def cache_inventory(cache):
        return {part: wire.tree_inventory(cache / 'leased-v2' / part)
                for part in ['objects', 'publications-v4']}

    def immutable_seed_inventory(directory):
        return cache_inventory(directory / 'cache-primer')

    def content_inventory(inventory):
        return {part: {key: value for key, value in entry.items() if key != 'path'}
                for part, entry in inventory.items()}

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
        assert worker_artifacts() == w.P['worker_artifacts_sha256'], 'worker artifacts changed'
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

    def run(label, policy, arm, preparation=False, phase='measured', repeat_number=1):
        nonlocal current_arm
        current_arm = arm
        assert (phase == 'stabilization' and arm == 'primer') or (policy in policies and arm in arms)
        h.CACHE = primer_cache if arm == 'primer' else root / ('cache-' + label)
        if phase == 'stabilization':
            assert h.CACHE.is_dir(), 'stabilization uses the copied seed'
        else:
            assert not h.CACHE.exists(), 'reader cache must start fresh'
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
            row.update(arm=arm, phase=phase, measured=phase == 'measured', repeat_number=repeat_number,
                       wire_download_bytes=after[0] - before[0],
                       wire_upload_bytes=after[1] - before[1],
                       remote=json.loads(stats[-1]) if stats else None,
                       remote_discovered=sum(map(int, re.findall(
                           r'Remote SkyCache: discovered (\d+) publications', text))),
                       remote_committed='Remote SkyCache: publication committed' in text,
                       remote_skips=re.findall(r'Remote SkyCache (?:import|publication) skipped: ([^\n]+)', text),
                       seeded_cache_files=seeded_files, seeded_cache_bytes=seeded_bytes,
                       server_proc_io=proc_io(row.get('server_pid_after_build')),
                       server_proc_io_scope='Cumulative fresh JVM including startup/clean; not build-only',
                       worker_peak_pss_kib=max((sample[1] for sample in backend_samples), default=0),
                       worker_peak_rss_kib=max((sample[0] for sample in backend_samples), default=0))
            w.persist()
        assert not row['remote_skips'], row['remote_skips']
        binary_name = 'vanilla' if arm == 'vanilla' else 'candidate'
        assert row['binary_sha256'] == w.P['binary_sha256'][binary_name], 'sample binary changed'
        assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == w.P['boot_id']
        local = row.get('local-skycache.json', {})
        if arm != 'vanilla':
            assert local and not any(local[key] for key in [
                'failed', 'read_limit_skips', 'scan_limit_hits', 'rejected_publications']), local
        if arm == 'primer':
            assert row['analysis_hits'] > 0 and row['execution_hits'] > 0, row
        if arm in ('local', 'remote'):
            if arm == 'local':
                assert local['writes'] == 0, local
            assert not local['scan_limit_hits'], local
            assert not local['rejected_publications'], local
            # Rejected dependency roots are recorded by the existing validation metrics.
            # Historical steady reads legitimately reject one root; do not require zero.
            hits = {key: row[key] for key in ['analysis_hits', 'execution_hits']}
            assert hits == w.P['expected_cache_hits'], (label, hits)
            methods = row['grpc']['method_attempt_counts']
            assert methods.get('build.bazel.remote.execution.v2.ActionCache/GetActionResult', 0) == 0
            assert row['output_inventory']['archives'] == row['output_inventory']['exports'] == 0
            if policy == 'minimal':
                assert row['grpc']['bytestream_read_calls'] == 0
            if arm == 'remote':
                assert row['remote_discovered'] > 0 and row['remote']['downloaded_bytes'] > 0, row
        if arm not in ('remote', 'primer'):
            assert row['wire_download_bytes'] == row['wire_upload_bytes'] == 0, row
        w.persist()
        stop_worker()
        return row

    try:
        private_state = git(h.WS, 'diff', '--binary', 'HEAD')
        assert git(h.WS, 'rev-parse', 'HEAD') == REVISION
        assert git(h.WS, 'diff', '--name-only', 'HEAD') == 'MODULE.bazel.lock'
        source_seed_inventory = immutable_seed_inventory(seed_root)
        assert source_seed_inventory == seed_report['frozen_seed_inventory'], 'steady local seed changed'
        source_cas_inventory = wire.tree_inventory(seed_root / 'metadata-cas')
        original_primer_receipts = receipts(seed_root / 'catalog-primer')
        assert original_primer_receipts == seed_report['primer_receipts']
        prime_frozen_inventory = immutable_seed_inventory(prime_root)
        assert prime_frozen_inventory == prime_report['frozen_seed_inventory']
        prime_primer_catalog_inventory = wire.tree_inventory(prime_root / 'catalog-primer')
        assert receipts(prime_root / 'catalog-primer') == prime_report['primer_receipts']
        assert prime_report['primer_receipts'] == original_primer_receipts
        prime_local_inventory = cache_inventory(prime_cache)
        prime_cas_inventory = wire.tree_inventory(prime_root / 'metadata-cas')
        prime_catalog_inventory = wire.tree_inventory(prime_catalog)
        assert Path(prime_report['prime']['local_cache']).resolve() == prime_cache
        assert Path(prime_report['prime']['catalog']).resolve() == prime_catalog
        assert prime_local_inventory == prime_report['prime']['local_inventory'], 'prime local metadata changed'
        assert prime_cas_inventory == prime_report['prime']['cas_inventory'], 'prime CAS changed'
        assert prime_catalog_inventory == prime_report['prime']['catalog_inventory'], 'prime catalog changed'
        added = validate_prime(prime_cache, prime_catalog, prime_root / 'catalog-primer',
                                prime_root / 'metadata-cas')
        assert prime_report['prime']['added_receipts'] == {added['receipt']: added['receipt_sha256']}
        for part in ['objects', 'publications-v4']:
            shutil.copytree(prime_cache / 'leased-v2' / part,
                            primer_cache / 'leased-v2' / part, copy_function=os.link)
        shutil.copytree(prime_root / 'metadata-cas', root / 'metadata-cas', copy_function=os.link)
        primer_catalog.mkdir()
        shutil.copy2(prime_catalog / 'token.key', primer_catalog / 'token.key')
        # Retain source receipt bytes as evidence, outside the new server catalog. Catalog replay
        # forbids gaps and signs the sequence: use the public Commit RPC to issue valid receipt 1.
        shutil.copy2(prime_catalog / added['receipt'], root / 'prime-source-receipt.pb')
        (root / 'prime-commit-request.pb').write_bytes(added.pop('request'))
        assert content_inventory(immutable_seed_inventory(root)) == content_inventory(prime_local_inventory)
        w.P['original_seed_inventory'] = source_seed_inventory
        w.P['original_cas_inventory'] = source_cas_inventory
        w.P['original_primer_receipts'] = original_primer_receipts
        w.P['prime_local_inventory'] = prime_local_inventory
        w.P['prime_cas_inventory'] = prime_cas_inventory
        w.P['prime_catalog_inventory'] = prime_catalog_inventory
        w.P['prime_frozen_inventory'] = prime_frozen_inventory
        w.P['prime_primer_catalog_inventory'] = prime_primer_catalog_inventory
        w.P['new_publication'] = added
        w.P['fixture_reuse'] = dict(workspace=str(h.WS), output_base=str(h.OUT), scratch=str(h.OLD),
                                   git_worktree_created=False, external_repositories_copied=False,
                                   local_seed='new-binary immutable objects/publications hardlinked; no leases',
                                   cas_seed='pilot immutable CAS hardlinked; source catalog never mutated',
                                   catalog_seed='only new source CommitRequest registered into empty catalog')
        h.storage()
        w.persist()
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
        w.P['status'] = 'registering-new-seed'
        start_worker('register-new-seed', primer=True)
        output = root / 'prime-publication-ref.pb'
        invocation = simple([str(WARMUP_PYTHON), '-c', REGISTER_SEED,
                             str(SOURCE / 'tools/skycache/remote_benchmark_warmup.py'),
                             '127.0.0.1:' + str(worker_port),
                             str(root / 'prime-commit-request.pb'), str(output)],
                            'register-new-seed', timeout=120)
        reference = output.read_bytes()
        assert proto_digest(proto_one(reference, 1)) == tuple(added['manifest'])
        assert proto_one(reference, 2, 0) == 1
        stop_worker()
        assert set(receipts(primer_catalog)) == {'receipt-00000001.pb'}
        registered = decode_receipt(primer_catalog / 'receipt-00000001.pb', primer_catalog / 'token.key')
        assert registered[0] == (root / 'prime-commit-request.pb').read_bytes()
        assert registered[1] == reference
        w.P['new_seed_registration'] = dict(invocation=invocation, original_sequence=added['sequence'],
                                            new_sequence=1, receipts=receipts(primer_catalog),
                                            exact_original_request=True, excluded_from_measurement=True)
        w.P['status'] = 'stabilizing'
        w.persist()
        minimal = run('primer-minimal', 'minimal', 'primer', True, 'stabilization')
        toplevel = run('primer-toplevel', 'toplevel', 'primer', True, 'stabilization')
        w.P['stabilization'] = [{key: row.get(key) for key in [
            'label', 'seconds', 'analysis_hits', 'execution_hits', 'local-skycache.json',
            'local-skycache-validation.json', 'remote', 'remote_committed',
            'wire_upload_bytes', 'wire_download_bytes', 'server_proc_io']}
            for row in [minimal, toplevel]]
        w.persist()
        assert toplevel['local-skycache.json']['writes'] == 0, (
            'stabilization still writes; stop for review before any third pass', w.P['stabilization'])
        for row in [toplevel]:
            assert {key: row[key] for key in ['analysis_hits', 'execution_hits']} == w.P['expected_cache_hits']
        primer_receipts = receipts(primer_catalog)
        assert primer_receipts, 'stabilized catalog has no receipts'
        frozen_seed_inventory = immutable_seed_inventory(root)
        w.P['frozen_seed_inventory'] = frozen_seed_inventory
        w.P['primer_receipts'] = primer_receipts
        w.P['primer_inventory'] = dict(zip(('files', 'bytes'), wire.seeded_file_sizes(primer_cache)))
        w.P['status'] = 'measuring'
        w.persist()
        for repetition in range(1, args.repeats + 1):
            offset = (repetition - 1) % len(arms)
            order = arms[offset:] + arms[:offset]
            policy_order = policies if repetition % 2 else list(reversed(policies))
            for policy in policy_order:
                for arm in order:
                    run(f'r{repetition}-{policy}-{arm}', policy, arm, repeat_number=repetition)
        measured = [row for row in w.P['runs'] if row['measured']]
        w.P['summary'] = [{key: row.get(key) for key in [
            'label', 'repeat_number', 'arm', 'mode', 'seconds', 'peak_pss_kib', 'worker_peak_pss_kib', 'analysis_hits',
            'execution_hits', 'local-skycache.json', 'remote', 'remote_committed',
            'wire_upload_bytes', 'wire_download_bytes', 'server_proc_io']} for row in measured]
        w.P['measured_count'] = len(measured)
        assert len(measured) == len(policies) * len(arms) * args.repeats
        w.P['status'] = 'passed'
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
            try:
                proxy.shutdown()
                proxy.server_close()
            except BaseException as error:
                w.P['cleanup_errors'].append('proxy_shutdown: ' + repr(error))
        # Run every preservation check, even when a build, cleanup or another check fails.
        checks = {
            'main_workspace_unchanged': lambda: original_state == (
                git(DATADOG, 'rev-parse', 'HEAD'), git(DATADOG, 'diff', '--binary', 'HEAD')),
            'main_lock_unchanged': lambda: h.sha(DATADOG / 'MODULE.bazel.lock') == original_lock,
            'private_workspace_unchanged': lambda: private_state is None or private_state == git(
                h.WS, 'diff', '--binary', 'HEAD'),
            'common_tools_unchanged': lambda: tools_before == wire.tree_inventory(COMMON_TOOLS),
            'benchmark_binaries_unchanged': lambda: w.P['binary_sha256'] == {
                key: h.sha(path) for key, path in binaries.items()},
            'worker_artifacts_unchanged': lambda: w.P['worker_artifacts_sha256'] == worker_artifacts(),
            'original_primer_receipts_unchanged': lambda: original_primer_receipts is None or (
                original_primer_receipts == receipts(seed_root / 'catalog-primer')),
            'original_local_seed_unchanged': lambda: source_seed_inventory is None or (
                source_seed_inventory == immutable_seed_inventory(seed_root)),
            'original_cas_unchanged': lambda: source_cas_inventory is None or (
                source_cas_inventory == wire.tree_inventory(seed_root / 'metadata-cas')),
            'copied_frozen_receipts_unchanged': lambda: primer_receipts is None or (
                primer_receipts == receipts(primer_catalog)),
            'copied_frozen_local_seed_unchanged': lambda: frozen_seed_inventory is None or (
                frozen_seed_inventory == immutable_seed_inventory(root)),
            'prime_local_publication_unchanged': lambda: prime_local_inventory is None or (
                prime_local_inventory == cache_inventory(prime_cache)),
            'prime_catalog_unchanged': lambda: prime_catalog_inventory is None or (
                prime_catalog_inventory == wire.tree_inventory(prime_catalog)),
            'prime_cas_unchanged': lambda: prime_cas_inventory is None or (
                prime_cas_inventory == wire.tree_inventory(prime_root / 'metadata-cas')),
            'prime_frozen_local_unchanged': lambda: prime_frozen_inventory is None or (
                prime_frozen_inventory == immutable_seed_inventory(prime_root)),
            'prime_frozen_catalog_unchanged': lambda: prime_primer_catalog_inventory is None or (
                prime_primer_catalog_inventory == wire.tree_inventory(prime_root / 'catalog-primer')),
            'prime_report_unchanged': lambda: w.P['prime_report_sha256'] == h.sha(
                prime_root / 'results.json'),
            'steady_report_unchanged': lambda: w.P['seed_report_sha256'] == h.sha(
                seed_root / 'results.json'),
            'first_report_unchanged': lambda: w.P['original_report_sha256'] == h.sha(
                original_root / 'results.json'),
        }
        for name, check in checks.items():
            try:
                w.P[name] = bool(check())
            except BaseException as error:
                w.P[name] = False
                w.P['cleanup_errors'].append(name + ': ' + repr(error))
        if w.P['cleanup_errors'] or not all(w.P[key] for key in checks):
            w.P['status'] = 'failed'
        w.P['finished_at'] = h.stamp()
        try:
            w.persist()
        finally:
            lock.close()
    if w.P['status'] == 'failed':
        raise RuntimeError('cleanup or source preservation failed; see results.json')


if __name__ == '__main__':
    main()

