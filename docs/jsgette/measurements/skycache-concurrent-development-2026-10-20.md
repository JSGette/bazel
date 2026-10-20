# Concurrent SkyCache development and measurements

Started 2026-10-20. Goal: a disk analysis cache that independent Bazel servers can safely
read and write concurrently while building the same project. Each server keeps its own
in-memory Skyframe graph and output base.

Implementation: [`jsgette/skycache-concurrent`](https://github.com/JSGette/bazel/tree/jsgette/skycache-concurrent).
Base: `83e163f0a7`; this branch does not contain the experimental graph paging/Rust changes.
The previous [paging experiment](skyframe-lazy-go-datadog-linux-2026-10-19.md) remains historical evidence.

## Current status

Concurrent immutable byte storage is tested. Integration with Bazel's current analysis
serialization/service APIs is in progress. Two-server analysis reuse is not yet demonstrated
by this checkpoint.

## Step 1: duplicate-write fast path

Code: [`9f584bbbc65e`](https://github.com/JSGette/bazel/commit/9f584bbbc65e54be75773505d3e7f32dd7a0b399).
The baseline always wrote and fsynced a temporary file before discovering that the completed
entry already existed. The new path reads and verifies that entry first and avoids rewriting
identical bytes. It still syncs the namespace and shard: a competing publisher could have linked
its entry before reaching the directory durability barriers. Missing entries still use the
original atomic no-replace publication path. Conflicts and corruption are still errors.

Validation: all five store tests passed, including independent writer JVMs, a live pre-existing
reader, conflicting publication, corruption/truncation, and a duplicate put in a non-writable
shard (demonstrating that no new temporary file is needed). The standalone benchmark verifies
every retrieved payload and novel/duplicate status.

Environment: WSL2 Linux/ext4, JDK 25, warm filesystem cache, fixed 256 MiB benchmark heap.
Each size uses three separate JVMs/directories, 200 unmeasured warmup writes, 1,000 measured novel
entries, and five duplicate/read passes. Baseline was measured before the change; runs were
serial under the shared exclusive measurement lock. These are storage microbenchmarks, not
Bazel build speedups. No randomized confidence interval is claimed.

| Payload | Operation, per 1,000 entries | Baseline median | Fast-path median |
|---|---|---:|---:|
| 4 KiB | First duplicate pass | 3667.1 ms | 1206.8 ms |
| 4 KiB | Subsequent duplicate passes | 2624.6 ms | 10.1 ms |
| 4 KiB | Novel writes | 3660.7 ms | 3711.4 ms |
| 4 KiB | Reads | 6.7 ms | 6.3 ms |
| 64 KiB | First duplicate pass | 4073.3 ms | 1263.3 ms |
| 64 KiB | Subsequent duplicate passes | 2836.6 ms | 42.5 ms |
| 64 KiB | Novel writes | 4001.1 ms | 4107.8 ms |
| 64 KiB | Reads | 35.3 ms | 36.1 ms |

The first duplicate pass remains substantially slower than later passes. A plausible contributor
is synchronization of staging-file unlink metadata left dirty after the prior write pass; this
has not been isolated with syscall/block tracing. Report both rather than hiding the first pass.
The read and novel-write measurements are controls, not changes targeted by this patch.

Raw data: [baseline](skycache-concurrent-2026-10-20/baseline-store-results.json),
[fast path](skycache-concurrent-2026-10-20/duplicate-fast-path-store-results.json),
[summary](skycache-concurrent-2026-10-20/summary.json).
Harness: `//src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:ConcurrentFileStoreBenchmark`.
Pass a fresh directory, entry count, and payload bytes; do not reuse an existing directory.

## Decision log

- Start `jsgette/skycache-concurrent` from `origin/jsgette/master` at 83e163f0a7.
  Discard the paging implementation as a basis: it changes evaluator residency,
  adds Rust/IPC and has a measured performance regression.
- Reuse upstream analysis-cache serialization and service interfaces. Prefer
  module/backend changes over evaluator changes.
- Carry over only the independently tested immutable concurrent file store and
  its tests. Establish a storage benchmark, then measure duplicate-write savings
  before adding integration complexity.
- Use same-user Linux/ext4 first. Defer remote gRPC transport; preserve the
  existing FingerprintValueStore/service boundary.
- Never use Git HEAD or dirty filenames alone as validity evidence. Exact-snapshot
  and input-evidence validation must precede reuse.
- Keep raw logs under .work; publish curated measurements and decisions on
  jsgette/master. Commit implementation milestones independently.
- Use jobs=4 and at most a 4 GiB source-build JVM; performance measurements hold
  the existing shared measurement lock exclusively.

- Retain atomic hard-link publication and directory fsync in the duplicate optimization.
  Discard the faster but incorrect assumption that seeing another writer's link proves that
  writer has already synced its directory.
- Do not interpret the storage microbenchmark as proof of analysis-cache benefit. The next
  checkpoint must exercise the real Bazel reader/writer path and report actual analysis hits.
