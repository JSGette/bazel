# Remote SkyCache: concurrent edited worktrees — 2026-10-25

**PASS: seven builds, including three overlapping pairs, with exact output checks.**
Two linked Git worktrees reused one remote baseline, built different source and rule edits,
and published separate durable children. Both variants still built correctly after `bazel clean`
and a metadata-server restart. Reverting both worktrees and cleaning again restored the baseline bytes.

## Configuration and scope

- Tested product integration: `6931630c83d9222049e30bf7c50190ae810519f5`; checkout `c635cafd98789c51646aa1938b6ed8d7ddddfaa4` (subsequent documentation merge only).
- Bazel SHA-256: `518de2101dbf40346f17200a520d3ce4dfd65a998f5909c537f3dd2a72689d3f`. Worker launcher and all 1,046 runtime JAR hashes were checked; runtime JARs were unchanged during the run.
- Native WSL ext4 on E:; one host. The SkyCache catalog and metadata CAS used a dedicated gRPC worker on loopback, with a shared `concurrent-worktrees` instance.
- Cache & Carry at `grpc://10.55.0.1:8080` served the ordinary action cache/CAS, with `--remote_download_outputs=toplevel` and local result upload enabled.
- Fixture: 32 dependency packages, three chained genrules per package, one aggregate target: **97 actions**. This is a synthetic correctness fixture, not Datadog Agent or a cross-host portability test.
- A changed `lib03/BUILD` and `lib03/input`; B changed `lib30/BUILD` and `lib30/input`. Each edit changes the actual action definition as well as its source bytes. The expected aggregate includes only that worktree's edit.
- Distinct Bazel output bases/servers and a new, initially absent local staging directory for **every** build. The linked worktrees share the seed repository's Git common directory. All builds use the same effective configured environment.
- The initial changed pair used cold Bazel servers. Later pairs followed explicit `bazel clean` commands; the harness checked that the previous output was absent before rebuilding. Both servers remained running across clean.

## Results

| Phase | Worktree | Wall seconds | Analysis / execution hits | New records | Deduplicated writes | Discovered publications | Reused remote parents | Downloaded metadata bytes |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| baseline | seed | 11.510 | 0 / 0 | 1407 | 0 | 0 | 0 | 0 |
| concurrent-edits | a | 10.472 | 32 / 31 | 122 | 324 | 1 | 1 | 409225 |
| concurrent-edits | b | 10.269 | 32 / 31 | 122 | 324 | 1 | 1 | 409225 |
| after-clean-and-restart | a | 7.756 | 33 / 32 | 3 | 32 | 3 | 2 | 480706 |
| after-clean-and-restart | b | 8.013 | 33 / 32 | 3 | 32 | 3 | 3 | 520322 |
| reverted-after-clean | a | 7.636 | 33 / 32 | 3 | 32 | 5 | 1 | 448092 |
| reverted-after-clean | b | 7.893 | 33 / 32 | 3 | 32 | 5 | 1 | 448092 |

Times include client and server work and concurrent contention. They are individual correctness-run observations, not a speedup comparison against vanilla Bazel.

Execution hits are cached frontier values, not a count of every skipped action. In the verified run,
each changed build restored 31 execution frontiers and submitted four actions to Cache & Carry.
After clean/restart and after revert, each restored 32 execution frontiers and submitted only the
aggregate action to Cache & Carry. The earlier initial run populated action outputs: its changed
pair had 93 ordinary remote action-cache hits and executed four actions per worktree. The verified
run reused those action outputs; it still began with an empty remote **SkyCache** catalog.

- `concurrent-edits`: processes overlapped for 10.269 seconds; two new durable receipts, catalog size 1 -> 3.
- `after-clean-and-restart`: processes overlapped for 7.756 seconds; two new durable receipts, catalog size 3 -> 5.
- `reverted-after-clean`: processes overlapped for 7.636 seconds; two new durable receipts, catalog size 5 -> 7.

All seven outputs match independently constructed expected bytes. A and B's edited outputs differ;
both reverted outputs match the baseline hash. Every non-baseline build has positive analysis hits,
shared reads, and remote-parent reuse. All seven builds committed remotely, with no skipped import
or publication, no failed cache session, and **zero fully materialized parent publications**.

The first pair's receipts survived the metadata-server restart byte for byte. Each pair added two
receipts without changing prior receipts; seven remain at the end. This checks concurrent builds
and durable publication from both clients; it does not prove simultaneous execution inside a single
server commit critical section or exhaustively explore every possible schedule.

## Initial failures retained

The [initial run](measurements/skycache-remote-worktrees-2026-10-25/initial/results.json) passed its baseline and concurrent changed pair.
After restarting the metadata worker, both clients reported `UNAVAILABLE`, built correct outputs
using Cache & Carry, and committed new complete publications. The remote-reuse assertion failed.
The probe's TCP readiness check had accepted a connection while the old worker's Java child was
still shutting down: the next build started at `13:22:14.130`, before the new worker logged server
startup at `13:22:14.671` (times as recorded in the WSL logs). No catalog corruption was observed.

The harness now waits for the old worker's entire process group to exit and requires a successful,
read-only `DiscoverPublications` RPC against an unused namespace before launching builds. Its
[first readiness preflight](measurements/skycache-remote-worktrees-2026-10-25/readiness-preflight/results.json) omitted the mandatory REAPI
`RequestMetadata` header and stopped before any Bazel build; that helper was corrected as well.
The final seven-build run then passed with unchanged Bazel product code. Both failed attempts,
their exact harness revisions, and their logs are retained alongside the successful run.

## Reproduction and evidence

The [probe script](measurements/skycache-remote-worktrees-2026-10-25/remote-worktree-probe.py) creates a new exclusive evidence root and uses
an advisory lock to avoid interference with other benchmark runs. Invoke it with the same tested
Bazel and worker binaries, an unused output root, and the repository's `tools/skycache` directory:

```sh
python3 remote-worktree-probe.py \
  --bazel /path/to/bazel-remote-sync \
  --worker /path/to/bazel-out/k8-fastbuild/bin/src/tools/remote/worker \
  --rpc-python /path/to/python-with-grpcio \
  --root /new/exclusive/probe-root \
  --support-dir /path/to/bazel/tools/skycache \
  --lock-file /path/to/skyframe-measurements.lock \
  --action-cache grpc://10.55.0.1:8080
```

The WSL storage guard requires a fresh Windows capacity observation; this run used the existing
Windows storage watcher. Without `SKYCACHE_WINDOWS_STORAGE_SNAPSHOT`, the guard checks through WSL interop.
Keep `remote-worktree-ready.py` beside the probe script; the readiness interpreter used `grpcio==1.76.0`.

- [Full protocol, exact commands, counters, output hashes, overlap intervals and receipts](measurements/skycache-remote-worktrees-2026-10-25/results.json)
- [Binary/source provenance and worker runtime inventory](measurements/skycache-remote-worktrees-2026-10-25/provenance.json)
- [A's source/rule edit](measurements/skycache-remote-worktrees-2026-10-25/edit-a.patch) and [B's source/rule edit](measurements/skycache-remote-worktrees-2026-10-25/edit-b.patch)
- [Baseline fixture sources](measurements/skycache-remote-worktrees-2026-10-25/fixture/MODULE.bazel), expected/actual text outputs and per-build logs are in the evidence directory.
- [SHA-256 inventory](measurements/skycache-remote-worktrees-2026-10-25/SHA256SUMS) and [verified private archive](measurements/skycache-remote-worktrees-2026-10-25/archive-verification.json)

The private archive at `D:\Codex\wsl-reset-2026-10-25\skycache-remote-worktrees-evidence.tar`
preserves all three attempts, their fixtures, local output bases/staging caches, remote catalog/CAS, raw BEP, and logs.
Its SHA-256 is `b684ee3ed7635d521c912a216ef23b9e60564442d72e3c62f9f91a42ed5579ed`; 2223 regular files were verified against the originals.
All probe-owned Bazel servers and the metadata worker were stopped successfully. No product source changed.
