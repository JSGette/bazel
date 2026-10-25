"""Preserve the complete fixture privately and publish the reviewable protocol."""
from pathlib import Path
import hashlib
import json
import re
import shutil
import tarfile

initial = Path('/home/jgett/REPOS/.work/skycache-remote-worktrees-2026-10-25')
preflight = Path('/home/jgett/REPOS/.work/skycache-remote-worktrees-2026-10-25-ready')
root = Path('/home/jgett/REPOS/.work/skycache-remote-worktrees-2026-10-25-verified')
task = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
docs = Path('/mnt/e/scw/skycache-remote-worktrees-docs-20261025/docs/jsgette')
evidence = docs / 'measurements/skycache-remote-worktrees-2026-10-25'
archive_path = Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-remote-worktrees-evidence.tar')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


report = json.loads((root / 'results.json').read_text())
provenance = json.loads((root / 'provenance.json').read_text())
assert report['passed'] and not report['shutdown_failures'], report.get('failure')
assert provenance['worker_classpath_unchanged']
assert len(report['phases']) == 4
assert sum(len(p['builds']) for p in report['phases']) == 7
assert len(report['final_receipts']) == 7
assert not evidence.exists() and not archive_path.exists()
for name in ['remote-worktree-probe.py', 'remote-worktree-ready.py', 'record-remote-worktrees-provenance.py',
             'publish-remote-worktrees.py']:
    shutil.copy2(task / 'work' / name, root / name)
shutil.copy2(Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
             / 'tools/skycache/storage_preflight.py', root / 'storage_preflight.py')
assert digest(root / 'remote-worktree-probe.py') == report['harness_sha256']
assert digest(root / 'remote-worktree-ready.py') == report['readiness_helper_sha256']
assert digest(initial / 'remote-worktree-probe-initial.py') == json.loads(
    (initial / 'results.json').read_text())['harness_sha256']
shutil.copy2(root / 'remote-worktree-probe.py', preflight / 'remote-worktree-probe.py')
assert digest(preflight / 'remote-worktree-probe.py') == json.loads(
    (preflight / 'results.json').read_text())['harness_sha256']
assert digest(preflight / 'remote-worktree-ready-initial.py') == json.loads(
    (preflight / 'results.json').read_text())['readiness_helper_sha256']
attempts = {'initial': initial, 'readiness-preflight': preflight, 'verified': root}
print('Archiving the complete fixture, outputs, remote catalog, and CAS.', flush=True)
with tarfile.open(archive_path, 'w') as archive:
    for label, directory in attempts.items():
        for path in sorted(directory.iterdir()):
            archive.add(path, arcname=Path(label) / path.relative_to(directory))
verified = 0
with tarfile.open(archive_path) as archive:
    for member in archive:
        if member.isfile():
            actual = hashlib.file_digest(archive.extractfile(member), 'sha256').hexdigest()
            relative = Path(member.name)
            assert actual == digest(attempts[relative.parts[0]] / Path(*relative.parts[1:])), member.name
            verified += 1
archive_record = {
    'path': str(archive_path), 'bytes': archive_path.stat().st_size,
    'sha256': digest(archive_path), 'verified_regular_files': verified,
    'scope': 'Complete initial, readiness-preflight, and verified probe roots before this archive-verification record; links preserved. '
             'Bazel binary and worker runfiles are external, identified by hashes and source provenance.',
}
(root / 'archive-verification.json').write_text(json.dumps(archive_record, indent=2) + '\n')
evidence.mkdir(parents=True)
private_raw = []
for label, directory in attempts.items():
    public = evidence if label == 'verified' else evidence / label
    public.mkdir(exist_ok=True)
    for path in sorted(directory.iterdir()):
        if not path.is_file():
            continue
        if path.name.endswith('.bep.json'):
            private_raw.append({'path': label + '/' + path.name, 'bytes': path.stat().st_size, 'sha256': digest(path)})
        else:
            data = path.read_bytes()
            assert b'\r\n' not in data, path
            (public / path.name).write_bytes(data)
(evidence / 'private-raw-hashes.json').write_text(json.dumps(private_raw, indent=2) + '\n')
# Preserve only tracked fixture sources publicly; no generated files or Git internals.
fixture = evidence / 'fixture'
for path in sorted((root / 'seed').rglob('*')):
    relative = path.relative_to(root / 'seed')
    if '.git' in relative.parts or path.is_symlink() or not path.is_file():
        continue
    if path.name not in ('BUILD', 'input', 'MODULE.bazel', '.gitignore'):
        continue
    destination = fixture / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, destination)

rows = []
summary = []
for phase in report['phases']:
    for row in phase['builds']:
        transfers = row['transfers']
        log = (root / (phase['phase'] + '-' + row['worktree'] + '.log')).read_text()
        execution_hits = sum(int(n) for n in re.findall(r'ACTION_EXECUTION: (\d+)/\d+', log))
        summary.append({'phase': phase['phase'], 'worktree': row['worktree'],
                        'seconds': row['seconds'], 'analysis_hits': row['analysis_hits'],
                        'execution_hits': execution_hits, 'process_summary': row['process_summary']})
        rows.append('| ' + ' | '.join([
            phase['phase'], row['worktree'], f"{row['seconds']:.3f}",
            str(row['analysis_hits']) + ' / ' + str(execution_hits), str(row['local_stats']['writes']),
            str(row['local_stats']['deduplicated_writes']),
            str(transfers['discovered_publications']), str(transfers['reused_publications']),
            str(transfers['downloaded_bytes']),
        ]) + ' |')
(evidence / 'validated-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
pair_details = '\n'.join(
    f"- `{p['phase']}`: processes overlapped for {p['overlap_seconds']:.3f} seconds; "
    f"two new durable receipts, catalog size {len(p['receipts_before'])} -> {len(p['receipts_after'])}."
    for p in report['phases'][1:])
build_commands = sum('build' in c['argv'] for c in report['commands'])
clean_commands = sum('clean' in c['argv'] for c in report['commands'])
assert build_commands == 7 and clean_commands == 4
prefix = 'measurements/skycache-remote-worktrees-2026-10-25/'
text = f'''# Remote SkyCache: concurrent edited worktrees — 2026-10-25

**PASS: seven builds, including three overlapping pairs, with exact output checks.**
Two linked Git worktrees reused one remote baseline, built different source and rule edits,
and published separate durable children. Both variants still built correctly after `bazel clean`
and a metadata-server restart. Reverting both worktrees and cleaning again restored the baseline bytes.

## Configuration and scope

- Tested product integration: `{provenance['tested_product_commit']}`; checkout `{provenance['source_commit']}` (subsequent documentation merge only).
- Bazel SHA-256: `{report['bazel_sha256']}`. Worker launcher and all 1,046 runtime JAR hashes were checked; runtime JARs were unchanged during the run.
- Native WSL ext4 on E:; one host. The SkyCache catalog and metadata CAS used a dedicated gRPC worker on loopback, with a shared `concurrent-worktrees` instance.
- Cache & Carry at `{report['action_cache']}` served the ordinary action cache/CAS, with `--remote_download_outputs=toplevel` and local result upload enabled.
- Fixture: 32 dependency packages, three chained genrules per package, one aggregate target: **97 actions**. This is a synthetic correctness fixture, not Datadog Agent or a cross-host portability test.
- A changed `lib03/BUILD` and `lib03/input`; B changed `lib30/BUILD` and `lib30/input`. Each edit changes the actual action definition as well as its source bytes. The expected aggregate includes only that worktree's edit.
- Distinct Bazel output bases/servers and a new, initially absent local staging directory for **every** build. The linked worktrees share the seed repository's Git common directory. All builds use the same effective configured environment.
- The initial changed pair used cold Bazel servers. Later pairs followed explicit `bazel clean` commands; the harness checked that the previous output was absent before rebuilding. Both servers remained running across clean.

## Results

| Phase | Worktree | Wall seconds | Analysis / execution hits | New records | Deduplicated writes | Discovered publications | Reused remote parents | Downloaded metadata bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(rows)}

Times include client and server work and concurrent contention. They are individual correctness-run observations, not a speedup comparison against vanilla Bazel.

Execution hits are cached frontier values, not a count of every skipped action. In the verified run,
each changed build restored 31 execution frontiers and submitted four actions to Cache & Carry.
After clean/restart and after revert, each restored 32 execution frontiers and submitted only the
aggregate action to Cache & Carry. The earlier initial run populated action outputs: its changed
pair had 93 ordinary remote action-cache hits and executed four actions per worktree. The verified
run reused those action outputs; it still began with an empty remote **SkyCache** catalog.

{pair_details}

All seven outputs match independently constructed expected bytes. A and B's edited outputs differ;
both reverted outputs match the baseline hash. Every non-baseline build has positive analysis hits,
shared reads, and remote-parent reuse. All seven builds committed remotely, with no skipped import
or publication, no failed cache session, and **zero fully materialized parent publications**.

The first pair's receipts survived the metadata-server restart byte for byte. Each pair added two
receipts without changing prior receipts; seven remain at the end. This checks concurrent builds
and durable publication from both clients; it does not prove simultaneous execution inside a single
server commit critical section or exhaustively explore every possible schedule.

## Initial failures retained

The [initial run]({prefix}initial/results.json) passed its baseline and concurrent changed pair.
After restarting the metadata worker, both clients reported `UNAVAILABLE`, built correct outputs
using Cache & Carry, and committed new complete publications. The remote-reuse assertion failed.
The probe's TCP readiness check had accepted a connection while the old worker's Java child was
still shutting down: the next build started at `13:22:14.130`, before the new worker logged server
startup at `13:22:14.671` (times as recorded in the WSL logs). No catalog corruption was observed.

The harness now waits for the old worker's entire process group to exit and requires a successful,
read-only `DiscoverPublications` RPC against an unused namespace before launching builds. Its
[first readiness preflight]({prefix}readiness-preflight/results.json) omitted the mandatory REAPI
`RequestMetadata` header and stopped before any Bazel build; that helper was corrected as well.
The final seven-build run then passed with unchanged Bazel product code. Both failed attempts,
their exact harness revisions, and their logs are retained alongside the successful run.

## Reproduction and evidence

The [probe script]({prefix}remote-worktree-probe.py) creates a new exclusive evidence root and uses
an advisory lock to avoid interference with other benchmark runs. Invoke it with the same tested
Bazel and worker binaries, an unused output root, and the repository's `tools/skycache` directory:

```sh
python3 remote-worktree-probe.py \\
  --bazel /path/to/bazel-remote-sync \\
  --worker /path/to/bazel-out/k8-fastbuild/bin/src/tools/remote/worker \\
  --rpc-python /path/to/python-with-grpcio \\
  --root /new/exclusive/probe-root \\
  --support-dir /path/to/bazel/tools/skycache \\
  --lock-file /path/to/skyframe-measurements.lock \\
  --action-cache grpc://10.55.0.1:8080
```

The WSL storage guard requires a fresh Windows capacity observation; this run used the existing
Windows storage watcher. Without `SKYCACHE_WINDOWS_STORAGE_SNAPSHOT`, the guard checks through WSL interop.
Keep `remote-worktree-ready.py` beside the probe script; the readiness interpreter used `grpcio==1.76.0`.

- [Full protocol, exact commands, counters, output hashes, overlap intervals and receipts]({prefix}results.json)
- [Binary/source provenance and worker runtime inventory]({prefix}provenance.json)
- [A's source/rule edit]({prefix}edit-a.patch) and [B's source/rule edit]({prefix}edit-b.patch)
- [Baseline fixture sources]({prefix}fixture/MODULE.bazel), expected/actual text outputs and per-build logs are in the evidence directory.
- [SHA-256 inventory]({prefix}SHA256SUMS) and [verified private archive]({prefix}archive-verification.json)

The private archive at `D:\\Codex\\wsl-reset-2026-10-25\\skycache-remote-worktrees-evidence.tar`
preserves all three attempts, their fixtures, local output bases/staging caches, remote catalog/CAS, raw BEP, and logs.
Its SHA-256 is `{archive_record['sha256']}`; {verified} regular files were verified against the originals.
All probe-owned Bazel servers and the metadata worker were stopped successfully. No product source changed.
'''
report_path = docs / 'skycache-remote-worktrees-2026-10-25.md'
assert not report_path.exists()
report_path.write_text(text)
output = task / 'outputs/SkyCache-remote-worktrees.md'
output.parent.mkdir(exist_ok=True)
output.write_text(text)
files = sorted(p for p in evidence.rglob('*') if p.is_file())
(evidence / 'SHA256SUMS').write_text(''.join(
    digest(p) + '  ' + p.relative_to(evidence).as_posix() + '\n' for p in files))
print(json.dumps({'archive': archive_record, 'public_files': len(files) + 1,
                  'report': str(report_path), 'builds': build_commands, 'cleans': clean_commands}, indent=2))
