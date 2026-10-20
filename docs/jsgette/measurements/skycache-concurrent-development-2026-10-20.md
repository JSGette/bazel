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


## Step 4 — skip guaranteed-miss execution lookups

Implementation: [dce7468cdfe6](https://github.com/JSGette/bazel/commit/dce7468cdfe64e32bfbd8f60ba44b3b149ec5544).
A backend retrieval capability lets the existing retrieval utility bypass execution keys before
key serialization and filesystem lookup. Analysis retrieval and the default upstream backend
remain enabled. This adds one small gate in SkyValueRetrieverUtils, not an evaluator change.

All six Java targets (66 test cases) and the full 12-build worktree protocol pass. Corresponding
outputs and analysis-hit counts match Step 3. Restarted-reader reads fall from 459 to 410 per
server; cold-writer reads fall from 117 to 68. These are the 49 execution keys that cannot hit in
this backend. Pair restart time was 10.731 → 10.571 seconds in single ordered samples: no
statistically supported elapsed-time improvement is claimed.

**Correction to the Step 3 hypothesis:** writes remain 51 per restarted reader. Therefore they
were not caused by execution lookup-key serialization as initially suspected. Analysis-key
serialization and duplicate shared chunks need separate investigation; do not present an
unmeasured zero-write result. This change is retained for the demonstrated removal of 49
guaranteed-miss filesystem lookups.

[Raw after protocol](skycache-concurrent-2026-10-20/skip-execution-lookups-results.json);
the before protocol is Step 3's analysis-only-upload result. Cache corruption fallback and
independent source/Starlark edits continue to pass.


## Step 5 — source directory links and first large-workload attempt

Implementation: [389c0570d7a8](https://github.com/JSGette/bazel/commit/389c0570d7a8bf0ac0e089fac27a1f1c20515d1f).
Source hashing follows directory symlinks, includes referent contents/modes, and checks an
ancestor-realpath set with a depth bound. A cycle disables this optional cache; it cannot cause
unbounded traversal or an incomplete successful source snapshot. The new test detects edits
behind a directory link and rejects a cycle. All six test targets pass (67 test cases).

The public Datadog Agent worktree at `0cde96beb70963d0838119c308b9d0422ee66be0` now passes the
source scan with caching enabled. A saved resolved lockfile is copied into the isolated worktree
and kept unchanged with lockfile_mode=off. Its checksum, dirty status, commands and binary hash
are in the [raw first-attempt protocol](skycache-concurrent-2026-10-20/datadog-first-attempt.json).
The benchmark intentionally uses the source-built binary through Datadog's wrapper, overriding
the repository's usual pinned-binary guidance for this explicitly requested fork experiment.
No .bazelrc change or internal Datadog cache is used.

Cache-off cluster-agent control: 5,473 loaded packages, 57,298 configured targets, 60 aspect
applications. Cold graph: 35.360 seconds and
3413.4 MiB peak process-tree PSS. Warm:
0.709 seconds. Both output hashes:
`7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`.
The ordinary action disk cache and OS/repository caches were already warm; the first run used
a fresh output base. These are single controls, not a comparison establishing cache benefit.

**The cache-enabled writer failed**, exit 37 after 22.503 seconds.
The source scanner stayed enabled. The stack trace reaches FingerprintValueService.put's
latency listener, where DecimalBucketer rejects a negative elapsed value measured with
Instant.now. A wall-clock interval became negative on this WSL host, and a statistics callback
propagated the error through analysis-key serialization into the evaluator. No successful
Datadog SkyCache publication/reuse is claimed. Next fix: use a monotonic elapsed-time source,
test asynchronous latency accounting, and repeat the same workload.


## Step 6 — monotonic store latency accounting

Implementation: [478adf722d2b](https://github.com/JSGette/bazel/commit/478adf722d2b50151177cf974ccf00563b2c2d7d).
FingerprintValueService measures put/get completion latency with System.nanoTime rather than
Instant.now. A regression test holds both operations pending, advances an injected nanosecond
clock across signed-long wraparound, and verifies the recorded 10-microsecond bucket only after
completion. This changes statistics, not graph evaluation or cache identities. Clamping negative
wall-clock durations was discarded because it would hide incorrect elapsed-time measurements.

Validation: all seven test targets passed, including the 290-case serialization suite and the
67 local/analysis test cases (357 total). The initial test compile caught ignored futures; the
final test consumes and asserts both results. The custom Bazel binary built successfully.

Datadog repeat, same commit, target, resolved lockfile, JVM and action-cache policy as step 5:

| Run | Exit | Wall seconds | Peak process-tree PSS MiB | Analysis hits |
| --- | ---: | ---: | ---: | ---: |
| control-cold | 0 | 36.421 | 3346.7 | 0 |
| control-warm | 0 | 0.727 | 3363.0 | 0 |
| writer-cold | 1 | 38.582 | 1791.5 | 0 |

The [raw protocol](skycache-concurrent-2026-10-20/datadog-monotonic-clock-results.json)
contains exact commands, binary/source/lock hashes and output hashes.

The timing exception did not recur. The writer analyzed 57,298 targets and reached successful
action completion, but the command failed during upload because FileDependencySerializer
rejects an absolute symlink from the Datadog Go coverage SDK into rules_go's Go SDK.
The optional cache produced 57,140 private records (233 MiB allocated on disk), with no
publication. Thus the lower sampled PSS is not a successful-cache memory result or a speedup.
The two control output hashes match step 5. No writer output hash is asserted on the failed run.

Next action: preserve the upstream service's default restrictions and add a narrow opt-in
dependency-validation hook for the local backend, with complete symlink-chain evidence.
Skipping absolute links without validating their referents was discarded as unsafe.



## Step 7 — external SDK symlinks and complete resolution chains

Implementation: [c98bd27836c7](https://github.com/JSGette/bazel/commit/c98bd27836c7037fe796b2e28c3dba53dce83ddc).
The local content validator opts into a new default-off LongVersionGetter hook for links outside
the serializer's versioned root. It persists witnesses for each intermediate link and the
resolved referent. FileDependencySerializer uses this evidence for absolute or above-root links;
ordinary version services retain the previous restrictions. No evaluator logic changed.

Component-by-component traversal preserves `link/..` filesystem semantics, bounds link depth,
and catches intermediate directory-link retargeting even when the outer text and final directory
kind/mode remain unchanged. The source identity changes to v2 to exclude older incomplete
evidence, with an explicit environment count and source section separator. Validation errors
propagate and prevent publication; unsupported links are never silently accepted.

Validation: seven test targets, 79 cases, passed. New cases cover intermediate-link retargeting,
parent traversal after a symlink, external evidence acceptance, and evidence-recording failure.
The custom Bazel binary builds successfully.

Datadog rerun under the same target/resource/action-cache policy:

| Run | Exit | Wall seconds | Peak process-tree PSS MiB | Analysis hits |
| --- | ---: | ---: | ---: | ---: |
| control-cold | 0 | 35.361 | 3317.1 | 0 |
| control-warm | 0 | 0.735 | 3335.2 | 0 |
| writer-cold | 0 | 171.884 | 4929.9 | 0 |
| writer-warm | 0 | 4.023 | 4943.5 | 0 |
| reader-restart | 0 | 126.282 | 4929.5 | 0 |

See the [raw protocol](skycache-concurrent-2026-10-20/datadog-external-symlinks-results.json)
for commands and hashes.

All five builds complete and produce the same cluster-agent output hash as the controls.
However, both graph-cold cache commands report `failed:true,published:false`, and the restarted
command has zero analysis hits. This is **not a successful Datadog reuse result**. The 171.884-second
writer and 126.282-second restart costs include substantial serialization/private writing, with
no reuse benefit; warm retained-graph time is 4.023 seconds versus 0.735 seconds cache-off.
The graph stays in RAM; peak PSS is about 4.8 GiB with the cache enabled.

The first command writes 281,494 records; the restarted command writes 217,915. The current
failure flag does not expose whether a conflicting immutable put or changed input caused the
rejection. A likely candidate is the upstream file-invalidation key space, which uses a
root-relative path and version while this local backend observes multiple repository roots.
This remains a hypothesis until the precise rejection is captured. Next: expose a concise
failure reason and run a diagnostic reproduction before changing keying or storage behavior.
Accepting conflicting values or removing the publication validity check is explicitly rejected.

