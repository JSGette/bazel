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


## Step 2 — real analysis reuse and independent worktrees

Implementation: [9f084c37f4f7](https://github.com/JSGette/bazel/commit/9f084c37f4f75f5cd151e687a30a0d4e453ec136).
Enable with `--experimental_local_skycache=/absolute/shared/cache` on each server,
using separate output bases. Each server retains its normal in-memory graph.
The module selects upstream asynchronous bidirectional analysis caching. Local lookups
reject execution-result envelopes; ordinary action execution and action caching remain responsible
for outputs. The current uploader still writes execution records, which is avoidable overhead for
the next performance step.

Records are private to one command until a successful build has finished and its inputs still
match. An atomically linked, checksummed publication makes the invocation visible to other
readers. Every server may publish; no distinguished writer, five-minute timer or global writer
lock is used. Readers discover new publications with a bounded refresh interval. Used parent
publications are recorded transitively. There is no garbage collector yet.

The initial validity policy is deliberately conservative: a digest of the whole source tree
(including ignored/untracked files and directory membership) and client environment partitions
publications. Observed file bytes, missing paths, listings, symlinks and external repository
bindings are checked separately. Git administration is excluded from the source tree digest but
observed Git paths are witnessed. Cache identity includes Bazel's normal configuration,
semantics and binary compatibility keys. Inputs must remain stable during each build, as for
ordinary Bazel; pre/post validation does not provide a transactional filesystem snapshot or
detect arbitrary change-and-revert races.

Local codecs relocate typed filesystem paths and restore source package roots. A narrow,
opt-in variant of RemoteConfiguredTargetValue's codec retains transitive package metadata.
Bazel's merged analysis/execution path needs this metadata to plant external-repository
symlinks. The default upstream codec keeps its original wire layout. No evaluator/node-residency
code under `com/google/devtools/build/skyframe` changed.

### Validation protocol

- Six Java targets pass, including the local store/session/path tests and upstream
  SelectedEntrySerializerTest, AnalysisCacheInvalidatorTest and BazelSkycacheIntegrationTest.
  [Test inventory](skycache-concurrent-2026-10-20/integration-tests.json).
- Relocation probe: a fresh reader reused analysis after the writer's workspace and output base
  were renamed out of reach. Every output hash matched and no missing-input warning remained.
  [Raw results](skycache-concurrent-2026-10-20/relocation-v9-results.json).
- Two real Git worktrees, distinct output bases, two simultaneous JVMs, jobs=2 and Xmx2g each.
  A generated 16-package graph has 49 actions. The client environment is identical, with PWD
  and OLDPWD unset. No action disk cache is supplied. Every build checks exact expected bytes.
- Both empty-cache writers published 790 records independently. Warm builds retained their
  graphs and performed zero cache reads/writes. Different edits in both source and Starlark files
  produced the expected distinct outputs, without cross-contamination.
- After restoring both worktrees and restarting both servers, each reported 49 configured-target
  cache hits and 456 shared record reads while producing the original output.
- Deliberately truncated shared chunks, while retaining possible analysis-root records,
  caused deserialization misses and local evaluation. Both builds still produced the original
  output. This is an actual missing-chunk test, beyond an empty-cache test.

| Concurrent phase | Pair wall time | Peak combined server RSS | Analysis hits per server |
|---|---:|---:|---:|
| concurrent-empty-cache | 11.362 s | 953.5 MiB | 0/0 |
| warm | 0.384 s | 1012.4 MiB | 0/0 |
| independent-source-edits | 1.191 s | 1080.3 MiB | 0/0 |
| independent-starlark-edits | 0.925 s | 1191.1 MiB | 0/0 |
| reverted-restarted-readers | 11.177 s | 876.8 MiB | 49/49 |
| corrupt-chunks-fallback | 11.465 s | 1017.5 MiB | 0/0 |


These are single-run correctness timings, including server startup where applicable. RSS is sampled
every 200 ms from both server processes; it excludes action children and filesystem page cache.
The workload is small. These numbers are **not evidence of a large-repository speedup**.
[Full protocol and exact commands](skycache-concurrent-2026-10-20/worktrees-v11-results.json).
Reproduce with `tools/skycache/local_worktree_probe.py --bazel /path/to/copied/bazel
--root /new/run/directory`; optionally pass the host's shared measurement lock.

### Decisions, failures and remaining limits

- Discarded mutating the object returned by OptionsParser.getOptions: it returns a fresh object,
  so the first probe silently performed no caching. The module now updates the parser.
- Discarded upstream's `experimental_skycache_analysis_only` flag: this fork's executor does not
  support its MTSV contract. The local lookup adapter rejects execution records instead.
- Deferred publication validation until lookup. Validation before repository materialization
  produced false misses; temporary input mismatches can be retried within a command.
- A first cross-worktree hit failed execution because upstream's compact target value omits
  package metadata. Merely rebasing paths and loading source packages was insufficient for
  Skymeld. Preserving transitive package metadata fixed this without an evaluator change.
- Validate external repository-name bindings as well as physical shared-repository bytes.
  Old physical bytes remaining in the repository cache do not prove that a reader uses them.
- Retain content checks despite their cost. Whole-source scanning and whole-publication input
  validation need measurements before optimization. Nearby revisions currently use separate
  source identities, so safe partial reuse across differing snapshots is not implemented.
- Source directory symlinks currently disable this experimental cache. Arbitrary absolute strings
  embedded by rules are not rewritten; only typed Paths are relocated. More rule families,
  external repository changes, host environment differences and large Datadog builds remain
  validation work. The current identical-environment probe does not prove reuse across differing
  shell PWD/OLDPWD values.
- Cache size is unbounded, per-record durability is expensive, candidate lookup is linear in
  publications, and statistics from the upstream service adapter are not yet representative
  (the local JSON and configured-target hit counters are the evidence used here).
- This is a working experimental local analysis-sharing slice, not a production-maturity claim.
  Next: remove execution serialization overhead, measure the Datadog workload, address lifecycle/
  bounded storage and finer reuse, repeating tests and measurements before each commit.


## Step 3 — omit execution records the local reader cannot use

Implementation: [f5b9c1c1bebe](https://github.com/JSGette/bazel/commit/f5b9c1c1bebe09664a9bd6e4e46633206987f99b).
The backend now declares whether it uploads execution values. The existing asynchronous uploader
skips those values for local SkyCache, before serialization starts. The default supplier still
accepts execution values, preserving the upstream behavior. This does not enable the unsupported
MTSV-based analysis-only flag and does not change the evaluator.

All six Java targets passed again (66 test cases), and the complete 12-build concurrent-worktree
protocol passed before and after this change. Every corresponding output hash matches.
Each restarted reader still reports 49 analysis hits, but now performs **51 writes**, versus
149 before (98 fewer, 65.8% reduction). This removes unnecessary execution serialization,
record files and fsyncs. The original hypothesis of zero writes was disproved: execution lookup
keys themselves still serialize shared chunks. Skipping those always-miss lookups is the next
separate performance change. Publication is still needed for the remaining writes.

| Phase | New records per server before → after | Pair elapsed before → after |
|---|---:|---:|
| concurrent-empty-cache | 790 → 741 | 11.828 → 14.011 s |
| warm | 0 → 0 | 0.238 → 0.235 s |
| independent-source-edits | 487 → 483 | 1.216 → 1.481 s |
| independent-starlark-edits | 787 → 738 | 0.871 → 0.862 s |
| reverted-restarted-readers | 149 → 51 | 10.573 → 10.731 s |
| corrupt-chunks-fallback | 790 → 741 | 10.314 → 12.081 s |


These are single ordered samples, not randomized performance statistics. Several elapsed times
are worse despite fewer writes; startup, first binary extraction, JIT and filesystem noise are
not isolated. **The demonstrated benefit is reduced serialization/storage work, not a proven
wall-time speedup.** Raw data includes record counts/bytes, sampled server RSS, exact commands and
all output hashes:
[before](skycache-concurrent-2026-10-20/execution-upload-baseline-results.json),
[after](skycache-concurrent-2026-10-20/analysis-only-uploads-results.json).

Next compatibility finding from inspecting Datadog: the checkout contains directory symlinks
(`.claude/skills` and a procfs test fixture), which the conservative source scanner rejects.
Support must include referent contents and cycle detection before claiming a Datadog SkyCache run.
