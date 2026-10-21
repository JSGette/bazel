# Concurrent SkyCache development and measurements

Started 2026-10-20. Goal: a disk analysis cache that independent Bazel servers can safely
read and write concurrently while building the same project. Each server keeps its own
in-memory Skyframe graph and output base.

Implementation: [`jsgette/skycache-concurrent`](https://github.com/JSGette/bazel/tree/jsgette/skycache-concurrent).
Base: `83e163f0a7`; this branch does not contain the experimental graph paging/Rust changes.
The previous [paging experiment](skyframe-lazy-go-datadog-linux-2026-10-19.md) remains historical evidence.

For setup, flags and interpreting hits, see the [local usage guide](../skycache-local-usage.md).

## Current status

Updated 2026-10-21 through Step 61. Experimental concurrent analysis reuse is implemented with
full in-memory graphs and no evaluator changes. Current focused checks pass 284 Java cases across
18 targets. The latest batch passes six sequential NVMe builds (two preparations, four measured):
reader 25.285 s versus 19.348 s control mean, still 30.7% slower despite matching action reuse.
See the [batch report](../skycache-optimization-batch-2026-10-21.md).

Earlier evidence includes a 34-build concurrent membership matrix and the capped Datadog matrix
of 16 attempts: 14 passed, two timed out, with the last six correctness checks passing. Those
remain evidence for their recorded binaries; this batch does not rerun the full concurrency matrix.
Sources are restored and servers stopped. No production maturity or net performance benefit is
claimed. Numbered steps preserve chronological evidence, including failures and superseded plans.

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
- Initial resource policy used jobs=4 and a 4 GiB source-build JVM. The later user instruction
  allows all available resources for standalone work with memory headroom; paired benchmark
  phases retain matching limits. Performance measurements hold the shared lock exclusively.

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



## Step 8 — explain rejected publications and require actual benchmark reuse

Implementation: [f705441bf72a](https://github.com/JSGette/bazel/commit/f705441bf72a964583948d5662fd97a459c5131e).
Discarded local publications now emit their reason. A conflicting-put regression verifies
that failure remains optional for the build, reports the cause, and exposes no publication.
Seven targets / 80 cases passed, and the custom binary built successfully.

The diagnostic Datadog run completed with the expected output hash in 113.418
seconds, peak process-tree PSS 5180.8 MiB, and
155,450 private writes. It failed the newly strict benchmark acceptance
check because publication was discarded. The script therefore stops before a useless warm/restart
comparison. This is diagnostic evidence, not an optimization result.

The warning identifies a conflicting immutable record; decoding its stored key gives
`:bin/ld.lld` (11 bytes, existing value length zero). This confirms a collision in the legacy
file-invalidation key space, whose keys use root-relative paths and a version while the local
backend uses multiple repository roots and MINIMAL versions. Source inspection identifies
three writes in FileDependencySerializer. Its invalidation payloads are unused by the local
reader: reuse is gated by the publication's content witnesses instead.

Next choice: omit those legacy invalidation writes for the independently content-validated
local backend, while retaining dependency traversal and evidence recording. Preserve normal
upstream behavior. Accepting conflicting writes, suppressing publication validation, and
inventing a global process-local version number are rejected.

[Diagnostic protocol](skycache-concurrent-2026-10-20/datadog-rejection-diagnostics-results.json)
and [decoded key](skycache-concurrent-2026-10-20/datadog-rejection-key.json).
A standalone reproducible Linux harness is now in `tools/skycache/datadog_probe.py`; it accepts
explicit binary/repository/lockfile/action-cache/measurement-lock paths, records output hashes,
and requires a publication and actual configured-target hits. It factors out the diagnostic
runner's algorithm; argument parsing and Python syntax were checked. Its first full invocation
will be the next implementation measurement.


## Step 9 — independent input manifests; first successful Datadog publication

Implementation: [e41a096844c9](https://github.com/JSGette/bazel/commit/e41a096844c916d9bee7222f3c74d5816a22c911).
A default-off LongVersionGetter capability lets the local backend omit the three legacy
FileDependencySerializer record writes while retaining full dependency traversal. Directory
and root existence are explicitly observed. Every symlink chain uses local filesystem evidence;
normal upstream version services retain their prior behavior. Source identity v3 excludes
publications predating the stronger directory/root evidence. Strict immutable conflict checks
remain unchanged.

The first trial omitted writes but still used graph-based resolution for ordinary relative links.
Datadog upload failed on two GCC symlink targets whose FILE_STATE nodes were absent. The final
change uses the independently recorded chain for relative links too. Regression tests cover this
case, directory/root observation, and absence of legacy writes; seven targets / 82 cases pass.

The before and final after probes each pass all 12 builds in two live servers/worktrees,
including independent source/Starlark edits, reverts/restarts and corrupt-chunk fallback.
Restarted readers retain 49 configured-target hits each and identical expected output bytes.

| Phase | Before writes/server | After writes/server | Before pair seconds | After pair seconds |
| --- | ---: | ---: | ---: | ---: |
| concurrent-empty-cache | 741 | 445 | 11.597 | 11.204 |
| warm | 0 | 0 | 0.238 | 0.214 |
| independent-source-edits | 483 | 187 | 1.158 | 1.235 |
| independent-starlark-edits | 738 | 442 | 0.865 | 0.685 |
| reverted-restarted-readers | 51 | 51 | 10.805 | 10.565 |
| corrupt-chunks-fallback | 741 | 445 | 10.523 | 10.579 |

Cold writes fall from 741 to 445 (39.9%); the 51 writes in a fully reused command are unaffected.
Through the restarted-reader phase, stored entry count falls from 4,026 to 2,250 (44.1%).
These small fixtures establish correctness and less storage work, not a general wall-time gain.

Datadog final attempt:

| Run | Exit | Wall seconds | Peak process-tree PSS MiB |
| --- | ---: | ---: | ---: |
| control-cold | 0 | 34.848 | 3302.9 |
| control-warm | 0 | 0.684 | 3324.6 |
| writer-cold | 0 | 185.519 | 3122.6 |
| writer-warm | 0 | 1.148 | 3129.8 |
| reader-restart | 37 | 22.316 | 2663.2 |

The writer successfully publishes 278,627 records and produces the expected cluster-agent hash
`7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`. The warm writer hash matches.
The restarted reader reports **seven remote configured-target hits in progress output**, with
only 13 configured targets at analysis completion, but then crashes in CppCompileAction:
StarlarkProvider.rewrapDepset has no element class for a deserialized, unwrapped NestedSet.
This is the next serialization compatibility issue, not successful end-to-end reuse.

The failed reader's raw protocol contains zero hits because the old parser only consumed the
final hit summary, which a crash never emitted. Its `skycache` object is stale from the warm
writer because the old harness left that output file in place. Disregard those two fields for
this failed row. The exit code/time/PSS are from the failed reader itself. The next harness
revision must remove stale command statistics and retain progress-hit evidence on crashes.

The writer is much slower than the cache-off control; there is no performance-win claim.
Next priorities are restoring provider depset type metadata, then duplicate writes, packed I/O,
validation cost and resource bounds.

Raw protocols: [before](skycache-concurrent-2026-10-20/independent-invalidation-before.json),
[final after](skycache-concurrent-2026-10-20/independent-invalidation-final-after.json),
[first Datadog trial](skycache-concurrent-2026-10-20/datadog-independent-invalidation-first.json),
[final Datadog attempt](skycache-concurrent-2026-10-20/datadog-independent-invalidation-final.json).


## Step 10 — provider type metadata; first complete Datadog reader

Implementation: [ef4033337614](https://github.com/JSGette/bazel/commit/ef40333376148e398f3c8f756e8487192ee06c3f).
Schemaful Starlark providers optimize depset fields by retaining only a NestedSet in the instance
and predicting its element type on the provider. The exported provider is resolved in the reader's
process, where that predictor can be empty or predict a different type. Reflective serialization
of the raw fields therefore either crashed or silently assigned the wrong element class.

A dedicated codec covers Schema0 through Schema5 and SchemaN. It sends typed depsets and reapplies
the receiver's existing optimization after restoring the canonical provider. A conflicting
prediction retains a wrapped depset. No evaluator change is needed. The Bazel installation identity
already separates cache entries across these binary/codec changes.

Before the fix, focused regressions reproduced the null element-class crash and a string depset
being reported as StarlarkInt when the reader had learned that other type. Afterward, eight targets /
86 tests pass, including all compact/array-backed schema sizes, a fresh receiver, a differently
primed receiver, absent fields, None, scalars, empty depsets and the prior schemaless round trip.
The None test explicitly registers the singleton, as the production registry does.

The measurement harness now removes the previous command's statistics before each run. If a crash
prevents the final configured-target summary, it records progress-hit evidence and labels that
source rather than silently reporting zero hits or stale statistics.

### Storage interruption and recovery

The first large run was interrupted when Windows C:, which held the WSL virtual disk, reached
zero free bytes. WSL returned EIO and could not restart. Linux's reported free space inside its
expandable virtual disk did not reflect the backing host volume's exhaustion. Its two completed
control rows are preserved separately; the writer and reader results are incomplete.

Following the user's cleanup request, 7,828,633,275 bytes of old diagnostic traces, crash dumps
and shader caches were removed. Ubuntu's approximately 141 GiB VHD was moved with WSL's supported
move operation from C: through D: to `E:\\WSL\\Ubuntu`. D: was initially selected for capacity;
after checking media type, its mechanical storage was rejected for final benchmark placement.
E: is a WD Blue SN570 NVMe SSD. Linux paths and worktrees are unchanged. WSL restarted with a
writable filesystem; full Git fsck passed for the Bazel and Datadog object databases, and the
benchmark binary SHA-256 matched the pre-interruption binary. C: then had about 150 GiB free.

The rerun checked both Linux free space and the Windows backing-volume capacity (30 GiB minimum),
plus a 10 GiB reserve on C:. The operational helper is recorded locally as
`work/resume_after_storage.py`. A reusable guard in the checked-in harness remains follow-up work.
Because the backing SSD changed, **do not attribute the writer-time improvement versus Step 9
to this codec fix**. Subsequent optimization comparisons must use fresh baselines on E:.

### Complete Datadog run on E:

Same Datadog commit, target, lockfile and resource limits as the preceding attempts. The action
disk cache is warm. Every row exits successfully and produces the same cluster-agent SHA-256:
`7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`.

| Run | Wall seconds | Peak process-tree PSS MiB | Configured-target hits | Local writes |
| --- | ---: | ---: | ---: | ---: |
| control-cold | 32.613 | 3305.1 | 0 | — |
| control-warm | 0.743 | 3324.9 | 0 | — |
| writer-cold | 104.114 | 2857.9 | 0 | 278629 |
| writer-warm | 1.123 | 2891.9 | 0 | 0 |
| reader-restart | 31.174 | 4148.3 | 4501 | 4477 |

The cold writer publishes 278,629 records. The restarted reader completes restored C++/Go action
processing, reports 4,501/4,507 configured-target hits, reads 152,623 shared chunks, and produces
the correct executable. It still writes 4,477 records and publishes another manifest.
This is the first complete large-repository analysis-reuse result, not production-maturity proof.
The reader's peak memory exceeds the control and the single-pair wall-time difference is small.
The writer remains substantially slower. There is no demonstrated general speed or RAM win.

The separate two-worktree probe passes all 12 builds: simultaneous writes, warm commands,
independent source/Starlark edits, reverts/restarts and corrupt-chunk fallback. Restarted readers
each get 49 configured-target hits with exact expected output bytes. These are small fixtures;
two concurrent Datadog worktrees still need validation.

Next: avoid rewriting identical records from validated publications, then investigate storage I/O,
validation cost, environment compatibility, memory retention and bounded lifecycle.

Raw evidence: [Datadog](skycache-concurrent-2026-10-20/datadog-depset-metadata.json),
[worktrees](skycache-concurrent-2026-10-20/depset-metadata-worktrees.json),
[unit checks](skycache-concurrent-2026-10-20/depset-metadata-tests.json),
[interrupted attempt](skycache-concurrent-2026-10-20/datadog-depset-metadata-interrupted.json),
[recovery checks](skycache-concurrent-2026-10-20/storage-recovery-verification.json).


## Step 11 — reuse published bytes without another private write

Implementation: [1d523c408f14](https://github.com/JSGette/bazel/commit/1d523c408f1451a18d2fd812e8d8243aab3cc495).
The prior restarted Datadog reader rewrote 4,477 records even though it reused 4,501 configured
targets. Each private write incurs filesystem work; any novel write also triggers final input
validation and another publication.

The session now compares a put against **validated publications**. Identical bytes reuse that
publication and record it as a parent. A private namespace is created only when a real write
is necessary. A command with no novel writes creates no namespace or manifest. The new
`deduplicated_writes` statistic counts skipped put calls, not a scan of the whole store.

Decisions and rejected shortcuts:

- A small per-key pin remembers the chosen publication and value digest only for skipped puts.
  This preserves read-your-writes if other publications use the same logical key with different
  bytes. Searching publications again arbitrarily after a successful put was rejected.
- Striped put locks preserve the existing immutable-key rule when matching and conflicting
  puts race. Conflicts discard the optional publication; they cannot silently overwrite a key.
  The final pool has 4,096 locks; the first 64-lock version caused unrelated writers to block
  behind disk syncs and was rejected after measurement.
- Reuse inherits the parent's input evidence and publication closure. Matching bytes from an
  invalid publication are not enough. A global unvalidated key-existence shortcut was rejected.
- Missing or corrupt pinned bytes are a miss and may be repaired with the original bytes in a
  private namespace. A later different value remains a conflict.
- Cold writes do not perform an extra private lookup unless a matching shared candidate exists.
  No whole-store payload map, evaluator modification or graph eviction was added.

Eight targets / 93 cases pass, including seven new session tests for these invariants. The
two-worktree probe passes all 12 builds with independent source/Starlark edits, reverts, restarts
and corrupted-record fallback. Each reverted reader retains 49 hits while writes fall from 51
to zero; neither publishes. Before the corruption phase the store has 2,148 entries instead of
2,250. The small probe is a correctness check; its startup timings do not establish a speed win.

### Datadog comparison on the same backing SSD

Same target, source revision, resource limits, warmed action cache and E: WSL backing volume
as Step 10. The fork is compiled/tested with read-only Cache&Carry access; the measured Datadog
commands use the local action cache. Both host and guest free space were checked before phases.

| Run | Before seconds | After seconds | Before peak PSS MiB | After peak PSS MiB | Before writes | After writes | Skipped puts |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control-cold | 32.613 | 33.774 | 3305.1 | 3217.8 | — | — | — |
| control-warm | 0.743 | 0.690 | 3324.9 | 3275.4 | — | — | — |
| writer-cold | 104.114 | 108.706 | 2857.9 | 3032.7 | 278629 | 278629 | 0 |
| writer-warm | 1.123 | 1.151 | 2891.9 | 3078.5 | 0 | 0 | 0 |
| reader-restart | 31.174 | 22.474 | 4148.3 | 4334.6 | 4477 | 0 | 4477 |

All outputs retain SHA-256 `7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664` and the restarted reader retains
4,501 configured-target hits. Reader publication after the change:
`false`. These are one before/after sequence, not
repeated statistical trials. The measured values establish the saved writes and observed
runtime/memory for this run; they do not establish a general performance guarantee. The graph
stays in RAM, and large-reader memory and cold-writer cost remain work items.

### Rejected 64-lock pool and contention diagnosis

The first implementation eliminated reader writes but made the cold writer slower:
123.895 seconds versus 104.114.
Four further graph-cold writes alternated the baseline and that initial binary, each with a
fresh SkyCache directory and its existing warmed action/output cache. All write 278,629 records,
publish successfully and retain the expected executable hash. CPU covers the Bazel server
process from startup to command completion, not child processes.

| Sequence | Variant | Wall seconds | Peak process-tree PSS MiB | Server CPU seconds |
| --- | --- | ---: | ---: | ---: |
| 1 | before | 108.720 | 3262.7 | 587.93 |
| 2 | after | 132.548 | 3113.4 | 572.68 |
| 3 | before | 107.223 | 2988.6 | 578.31 |
| 4 | after | 125.700 | 3120.6 | 571.72 |

The fourth repeat is diagnostic: six `jcmd Thread.print` samples ran during it, so it is not an
unprofiled timing trial. The samples show 23, 26 and 30 writers parked at `LocalCacheSession.put`
while other workers are in `FileChannel.force`. Sixty-four lock stripes unnecessarily serialize
different keys when about 63 writers are active. This explains a concrete source of the regression;
similar I/O totals and lower CPU alone would not have proved it. The final 4,096-lock pool keeps
the conflict guarantee while reducing accidental contention. The table above uses the rebuilt
final binary and rerun correctness/probe suite, not the rejected binary.

The final unprofiled cold writer takes 108.706 seconds, close to the
baseline repeat range of 107.223
to 108.720 seconds. An additional
final-binary diagnostic writer takes 106.873 seconds and
produces the same output. Its active-writing samples show 62/63 runnable writers with only
one parked writer in one sample, compared with 23–30 parked writers in the initial version.
This confirms the targeted contention reduction. It does not eliminate the underlying cost
of writing hundreds of thousands of separately synced files.

Next: make the disk-capacity guard reusable, then exercise natural per-worktree environments
and two concurrent Datadog worktrees. Storage packing, resource bounds and nearby-revision
reuse remain incomplete.

Raw evidence: [Datadog](skycache-concurrent-2026-10-20/datadog-shared-put.json),
[two worktrees](skycache-concurrent-2026-10-20/shared-put-worktrees.json),
[tests](skycache-concurrent-2026-10-20/shared-put-tests.json),
[writer repetitions](skycache-concurrent-2026-10-20/shared-put-writer-repeats.json),
[rejected version](skycache-concurrent-2026-10-20/datadog-shared-put-rejected-64-stripes.json),
[lock samples](skycache-concurrent-2026-10-20/shared-put-lock-diagnostics.json),
[final lock samples](skycache-concurrent-2026-10-20/shared-put-final-lock-diagnostics.json),
[final diagnostic writer](skycache-concurrent-2026-10-20/shared-put-final-writer-diagnostic.json),
[storage checks](skycache-concurrent-2026-10-20/shared-put-storage.json).


## Step 12 — check Windows backing capacity before WSL measurements

Implementation: [f1a2d75b7be2](https://github.com/JSGette/bazel/commit/f1a2d75b7be2bc16aba405f7e0c48841307c3247).
Both checked-in probes now check storage before creating a run directory and again before each
measured command/pair. The checks run outside the timed interval and their results are saved in
the protocol. Linux filesystem capacity alone was insufficient in the Step 10 interruption:
an expandable WSL VHD can report free virtual space when its Windows backing volume is full.

On WSL the helper resolves `WSL_DISTRO_NAME` against the current user's distro registry, then
checks the backing volume and Windows system volume. No distro GUID or E: path is hardcoded.
Ordinary Linux runs only require filesystem checks. A WSL host query that cannot be completed
stops the probe with an explanation instead of silently assuming the host has capacity.

Chosen reserves are 30 GiB on the probe/action-cache filesystems and WSL backing volume, plus
10 GiB on the Windows system volume. These are conservative measurement preconditions, not
runtime cache quotas. They do not replace the planned cache size limits and garbage collection.
The helper makes no deletions, moves or filesystem repairs. A destructive automatic cleanup
fallback was rejected.

Four focused tests pass: host full with guest space, system-volume reserve on a different
backing drive, guest full, and ordinary Linux without Windows. A live check correctly finds
Ubuntu at `E:\\WSL\\Ubuntu`, with 562.0 GiB backing
space and 150.4 GiB system-drive space at that instant.
The final Step 11 Datadog sequence and all six two-worktree phases also exercise the guard.
Python compilation checks pass for both probes and the new helper/tests.

Evidence: [live storage check](skycache-concurrent-2026-10-20/storage-preflight-live.json) and
the `storage_preflight` fields in Step 11's [Datadog](skycache-concurrent-2026-10-20/datadog-shared-put.json)
and [worktree](skycache-concurrent-2026-10-20/shared-put-worktrees.json) protocols.


## Step 13 — worktree environments and repository discovery

Implementation: [3c29d668c33d](https://github.com/JSGette/bazel/commit/3c29d668c33dc4ca7a49863dfd3908848406f341).
The former fixture unset PWD/OLDPWD and supplied identical client environments. Giving each
worktree its natural PWD and a different session marker reproduced a real compatibility problem:
both reverted/restarted readers built correct outputs but got zero analysis hits and rewrote
445 records each. The cache identity hashed all client variables even under strict repository env.

The local module now hashes Bazel's effective repository environment and inherited action/test
values in separate scopes. Repository overrides are resolved by the existing CommandEnvironment
API. Without strict repository env, the effective repository environment still includes the full
client environment. Fixed action/test options remain covered by the configuration identity.
The Bzlmod client variable `BZLMOD_ALLOW_YANKED_VERSIONS` is included explicitly because module
resolution reads it outside the repository environment. The source-identity domain is now v4.

Blindly deleting PWD or terminal variables was rejected: explicitly exposed variables must
remain meaningful. Separate scopes prevent a fixed repository value from hiding a different
inherited action value with the same name. No evaluator or option semantics were changed.

### Discovery timing exposed by a fresh external repository

The environment probe's first fresh reader still missed after that change. Its publication
identity and all 94 eventual input witnesses matched the writer, but the first lookup happened
before the external repository existed. The one-second negative refresh interval hid its
availability. A frozen-clock regression reproduced this without relying on wall-time sleeps.

An initial missing-path-only retry fixed that case but failed when an existing repository was
regenerated after a fixed environment override. The final implementation retains a cheap
inode/size/mtime hint for each first mismatching input. A changed hint triggers full witness
validation immediately. **Metadata is never validity evidence**: content, modes, links and
directory evidence are still checked before accepting a publication. Unchanged mismatches
remain throttled, and the periodic retry still works when bytes change while size/mtime/inode
are preserved. First discovery also handles negative values from a monotonic clock.

The pre-fix regression run failed two cases. The final eight-target suite passes 97 cases,
including four new session checks: newly materialized inputs, regenerated-but-still-wrong
inputs and throttling, metadata-preserving edits after the TTL, and negative monotonic time.

### Real Bazel environment matrix

Nine cases / eighteen successful builds use two real Git worktrees and separate restarted
servers. The fixture generates an external repository file from a repository variable and an
action output from an inherited variable. Exact output bytes are asserted independently for
each case. Both positive cases start their reader against a fresh or regenerated repository.

| Case | Expected reader outcome | Configured-target hits | Reader writes |
| --- | --- | ---: | ---: |
| strict-unrelated | hit | 2 | 14 |
| repository-inherited | miss | 0 | 74 |
| repository-fixed | hit | 2 | 14 |
| action-inherited | miss | 0 | 74 |
| separate-repo-action-scopes | miss | 0 | 74 |
| test-inherited | miss | 0 | 74 |
| pwd-explicit | miss | 0 | 74 |
| nonstrict | miss | 0 | 74 |
| bzlmod-client | miss | 0 | 74 |

The strengthened original two-worktree probe also passes all twelve builds with natural PWDs
and different session markers, independent source/Starlark edits, reverts/restarts and corrupt
records. Each restarted reader now gets 49 hits, writes zero records and produces the same
golden bytes. Pair wall time was 10.420 seconds before versus
10.864 seconds after. This small fixture proves compatibility and saved
work; startup dominates, so it is not a general performance claim.

Next is the substantial two-server Datadog probe with independent semantic Go edits, separate
cache-disabled controls, and edits/reverts after restoring a graph. The control edits will
execute without the shared action disk cache to avoid accepting a bad cached result from the
experimental build. Packing, bounded lifecycle, memory profiling and nearby-revision partial
reuse remain unfinished.

Evidence: [before](skycache-concurrent-2026-10-20/environment-before-worktrees.json),
[worktrees](skycache-concurrent-2026-10-20/environment-worktrees.json),
[environment matrix](skycache-concurrent-2026-10-20/environment-matrix.json),
[tests](skycache-concurrent-2026-10-20/environment-tests.json),
[regression](skycache-concurrent-2026-10-20/environment-discovery-regression.json),
[first miss](skycache-concurrent-2026-10-20/environment-first-repository-miss.json),
[matching witnesses](skycache-concurrent-2026-10-20/environment-manifest-comparison.json),
[regeneration miss](skycache-concurrent-2026-10-20/environment-regenerated-repository-miss.json).


## Step 14 — substantial concurrency gate exposes Go archive duplication

Harness: [ce20336fd298](https://github.com/JSGette/bazel/commit/ce20336fd2982f3909e7ed488d2ec1cfb62b5959).
Tested binary SHA-256: `d546b6cae2da579c263be5d9f8391004faef074ab8b669b84172674d8f5ef78d`.
Datadog revision: `0cde96beb70963d0838119c308b9d0422ee66be0`.
**This gate failed; the implementation is not yet reliable for the complete worktree-edit
workflow.** Twelve builds succeeded, followed by two failed restarted edited builds.
The remaining repeat/revert phases were not reached.

Four real Git worktrees provide two simultaneously running SkyCache-enabled servers and
two separate controls. The workload is `//cmd/cluster-agent:cluster-agent`, with each JVM
limited to 4 GiB heap, two action jobs and 3 GiB local execution memory. Both experimental
servers share one local SkyCache directory. Cache&Carry remains enabled for Bazel source
builds/tests (`--config=efcache`, uploads disabled). The measurement uses a warmed local
action cache; experimental and control edits disable shared action caching to verify their
outputs independently. Raw BEP/environment/JVM logs remain local.

| Concurrent phase | Pair wall seconds | Combined peak PSS GiB | Analysis hits A/B | Exit codes A/B |
| --- | ---: | ---: | ---: | ---: |
| control-base | 40.594 | 6.281 | 0/0 | 0/0 |
| concurrent-base-writers | 145.147 | 5.925 | 0/0 | 0/0 |
| warm-base | 1.799 | 5.558 | 0/0 | 0/0 |
| restarted-base-readers | 60.306 | 8.370 | 4501/4501 | 0/0 |
| edited-warm-graphs | 455.187 | 13.586 | 0/0 | 0/0 |
| control-edits-no-disk-cache | 358.658 | 9.629 | 0/0 | 0/0 |
| concurrent-edited-writers | 69.614 | 8.090 | 4501/4501 | 1/1 |

PSS/RSS samples cover both servers, clients and descendants every 200 ms, excluding the
probe and filesystem page cache. Pair time includes output hashing; per-build elapsed
times and full commands are in the JSON. These are single observations, not estimates
of typical performance. The restarted baseline reader pair is slower and uses more RAM
than the control pair; this is not a demonstrated performance improvement.

### What passed

Both initial cache writers concurrently published 278,629 records and produced the same
baseline binary. Warm graph builds succeeded. Both restarted baseline readers obtained
4,501 configured-target hits, wrote zero records and matched the baseline SHA-256.

The probe changed the version-command string independently to
`Cluster Agent SkyCache-feature-a` and `Cluster Agent SkyCache-feature-b` in
`cmd/cluster-agent/subcommands/version/command.go`. Both experimental builds succeeded,
each binary contained only its own feature marker, and their SHA-256 values exactly
matched separate cache-disabled control builds:

- Feature A: `ca4b2473d6dd8ed84177048b9a7ee1bcf84a3e3362770f393f8249d32962587e`.
- Feature B: `bf973002d45ecbbbe74ba9a1dd5f9df5c83b23419cbef264af468347a6f0e674`.

Disabling the shared action cache caused full 6,789-action rebuilds in both experimental
and control worktrees. That behavior is not specific to SkyCache. Experimental builds
took 455.187 seconds as a pair versus 358.658 seconds for the controls, with materially
higher peak process memory.

### What failed and what remains unproven

After the next restart, both edited readers restored 4,501 configured targets but Go
linking failed: the protobuf `internal/detrand` package appeared repeatedly in the linker
inputs. One captured parameter file contains the exact same archive argument five times.
The first failures were the `protoc-gen-go` / `protoc-gen-go-grpc` tools. This is a real
build failure, not merely a benchmark assertion. Cache statistics reported no store
failure, so successful byte retrieval does not establish correct graph reconstruction.

The next investigation must isolate provider/depset sharing or field-equality loss with
a small regression before applying a serialization fix. Do not deduplicate linker strings
as a workaround: that could hide inconsistent providers. Repeat the substantial test,
including restarted edited readers and reverts, after the underlying defect is fixed.

Separately, the harness originally demanded new writes in a restarted phase whose edited
graph had already been published. The harness now correctly requires publication during
the edit phase and shared reads on the next restart, and supports revalidating checkpoints
before resuming. That correction does not excuse either nonzero build exit; failed phases
are still rejected. The original protocol is retained unchanged.

A private packed-store draft was prepared while these builds ran, and its six unit tests
passed with Cache&Carry configured. It is not connected to the implementation or committed;
integration and performance claims wait for the graph-correctness fix. Runtime quotas/GC,
reader memory profiling, and partial nearby-revision reuse remain unfinished.

Evidence: [protocol](skycache-concurrent-2026-10-20/datadog-concurrent-before-archive-fix.json),
[duplicate archive](skycache-concurrent-2026-10-20/datadog-archive-duplication.json).


## Step 15 — preserve Runfiles identity across serialized branches

Implementation: [f7a44a9412a8](https://github.com/JSGette/bazel/commit/f7a44a9412a847f365245a526a9e2adda669e000).
The Step 14 failure was reduced to a three-node diamond of Starlark provider values, each
containing a Runfiles object. A writer expands the depset into three command-line arguments;
the former reader expands it into four, duplicating the common leaf. The command-line
expansion happens at execution, after restoration. Precomputing a string at analysis time
did not reproduce the failure, explaining why that initial diagnostic was insufficient.
Removing only the Runfiles field restores the expected three arguments in the old binary.

Runfiles uses reference equality. Its former inline dynamic serialization produced separate
copies when the same Runfiles appeared in independently shared nested arrays. StructImpl
compares provider fields, so those copies made otherwise equal provider leaves unequal and
defeated depset deduplication. Two of four new unit tests failed before the fix: cross-root
identity and diamond uniqueness.

The local registry now installs a shared Runfiles payload codec and treats Runfiles.EMPTY
as a reference constant. A per-payload UUID distinguishes different original objects with
identical fields; FingerprintValueService's existing identity-keyed writer cache encodes
each original once, and its reader cache restores a single object for that fingerprint.
Field serialization uses the existing dynamic field handlers and waits for asynchronous
field restoration before exposing the value. The codec is only enabled by local SkyCache.
There are no changes to Skyframe evaluation, Runfiles equality or rules_go linker behavior.

Content-only interning was rejected because distinct Runfiles with identical fields must
remain distinct. The unit suite checks this, the shared diamond, identity across separate
serialized roots, and the EMPTY singleton. All four now pass. The broader nine-target suite
passes 101 cases; source build/test commands used Cache&Carry with uploads disabled.

The real-worktree reproduction now produces the exact same three arguments and ordering
on writer and reader. Writer records rise from 94 to
97; reader writes remain zero. Its elapsed writer/reader seconds
were 10.835/10.368 before and
11.706/9.900 after. These tiny, startup-dominated runs
are correctness observations, not speed claims.

The twelve-build native two-worktree probe also passes, including independent source and
Starlark edits, restarted reverts and corrupted chunks. Each restarted reader obtains
49 configured-target hits with zero new writes.

**The substantial Datadog gate remains pending a new full run.** This fixes the isolated
mechanism and does not retroactively turn the Step 14 failures into successes. Packing
remains an unintegrated draft until that gate is repeated. Quotas/GC, memory profiling,
nearby-revision partial reuse and optional-serialization failure handling are unfinished.

Evidence: [before](skycache-concurrent-2026-10-20/runfiles-identity-before.json),
[without Runfiles](skycache-concurrent-2026-10-20/runfiles-identity-control.json),
[after](skycache-concurrent-2026-10-20/runfiles-identity-after.json),
[worktree probe](skycache-concurrent-2026-10-20/runfiles-worktrees.json),
[tests](skycache-concurrent-2026-10-20/runfiles-tests.json).


## Step 16 — complete Datadog two-worktree edit/restart/revert gate

The full gate passes after the Runfiles identity fix in `f7a44a9412a8`: nine phases,
eighteen successful builds of `//cmd/cluster-agent:cluster-agent` at Datadog revision
`0cde96beb70963d0838119c308b9d0422ee66be0`. Each phase runs two independent Bazel servers
concurrently, each with a 4 GiB JVM limit, two jobs and a 3072 MiB local-resource setting.
Each server has its own output base and worktree. Both writers publish to the same local
SkyCache. Source builds/tests use Cache&Carry with uploads disabled; measured Datadog
commands use the protocol's local action-cache controls.

| Concurrent phase | Pair wall seconds | Peak combined process PSS (GiB) | Analysis hits (A, B) |
| --- | ---: | ---: | --- |
| control-base | 40.929 | 6.220 | 0, 0 |
| concurrent-base-writers | 147.322 | 5.871 | 0, 0 |
| warm-base | 1.637 | 5.780 | 0, 0 |
| restarted-base-readers | 57.288 | 8.311 | 4501, 4501 |
| edited-warm-graphs | 430.389 | 13.578 | 0, 0 |
| control-edits-no-disk-cache | 337.663 | 10.282 | 0, 0 |
| first-restarted-edited-readers | 85.882 | 12.178 | 4501, 4501 |
| restarted-edited-readers | 63.792 | 7.632 | 4501, 4501 |
| reverted-restarted-readers | 75.757 | 8.157 | 4501, 4501 |

During the edited-build memory peak, one auxiliary WSL attachment used for a source-file
inspection timed out with `Wsl/Service/0x8007274c`; other pending reads completed and both
builds succeeded. No resource limits or running servers were changed in response. PSS
excludes filesystem page cache and is sampled, so it does not describe total VM pressure.

The two semantic Go edits embed different feature markers in the version command. Edited
experimental builds and independent controls disable shared action caching and execute the
large workload. Each experimental binary's SHA-256 exactly matches its independent control;
the two edited hashes differ from each other and from baseline. Both restart passes retain
the appropriate feature hash. Reverting both worktrees and restarting restores the baseline
hash, with actual analysis-cache hits. This exercises deferred Go linker argument expansion
that failed in Step 14, without suppressing duplicate arguments or weakening output checks.

This establishes this workload's concurrent correctness gate, not production maturity.
The restarted base-reader pair remains slower and uses more process memory than the
ordinary action-cache control. File-per-record publication is also expensive. The next
measured change is packed storage: private append-only packs, durable sealing before
manifest publication, and compact reader indexes. The RAM-resident Skyframe graph remains.
GC/quotas, nearby-revision partial reuse, memory profiling and optional-serialization failure
handling remain unfinished. Raw BEP/environment logs are retained locally only.

Full commands, binary/source hashes, outputs, counters and host storage checks:
[Datadog concurrent gate](skycache-concurrent-2026-10-20/datadog-concurrent-runfiles.json).


## Step 17 — transient WSL host-storage query recovery

Harness fix: [c567bf2639c8](https://github.com/JSGette/bazel/commit/c567bf2639c8dc9cde5ea6e556e8825d33cc9c4a).
The first packed Datadog attempt completed four phases/eight builds successfully: both
writers, warm commands and restarted readers produced the baseline hash. Writer pair time
was 56.464 s versus 147.322 s with separate files, but peak PSS rose to 9,765,751 KiB from
6,156,028 KiB. Both writer logs report 5,704 disk-cache hits and 1,085 internal actions, with
no local compiler actions; the memory increase requires a separate JVM diagnostic rather
than an assumption that it is pack-index overhead. Restarted readers took 50.496 s and each
obtained 4,501 hits, zero writes and the expected hash. These are partial results, not a full gate.

The next phase stopped before its timer/builds because `powershell.exe` returned status 1
inside the WSL host-capacity query. A fresh query subsequently succeeded. Direct host checks
also showed approximately 449.9 GB free on E: and 158.5 GB on C:, so this was an observation
failure, not a reported capacity shortage. Auxiliary WSL attachment attempts also timed out
under load; the exact interop failure cause was not captured by the original harness.

The capacity query now retries transient subprocess/decoding failures up to three times,
waiting 0.5 and 1.0 seconds between attempts. Each retry obtains current Windows data; it
does not reuse stale capacity or disable thresholds. Persistent failure remains fatal and
now includes the captured stderr diagnostic. Six guard tests pass, covering transient recovery,
persistent rejection, host/guest limits and the independent system-drive reserve. Live WSL
validation succeeds. These checks run outside measured phases; there is no Bazel code change
in this commit, and packed implementation work remains uncommitted pending its full gate.

The interrupted probe shut down its servers. Although saved successful phases can be resumed,
that would change the next phase's warm-graph condition. The chosen protocol is a fresh full
run with the same binary and resources. Preserve the interrupted attempt instead of merging
its timings silently with a resumed run.

Evidence: [interrupted packed attempt](skycache-concurrent-2026-10-20/datadog-packed-preflight-interruption.json).


## Step 18 — durable packed storage

Implementation: [99bb0cc4389b](https://github.com/JSGette/bazel/commit/99bb0cc4389b35d2b86987517e88a93cf9b035af).
Each writer invocation now owns an append-only `data.pack` and a checksummed, sorted `index`.
Threads reserve disjoint offsets and use positional I/O. Private puts support read-your-writes;
they become durable when `seal` syncs the pack and index. Only after sealing does the session
publish its manifest. A killed unsealed writer is undiscoverable. Independent writers own
different namespaces and require no common writer lock. `publications-v2` and `objects/packed-v1`
keep this layout separate from prior caches; old data is preserved without automatic migration.

The reader keeps a 48-byte index row per record plus one descriptor per accepted publication;
it does not cache record bytes. Validate input witnesses before opening an index. Rejected
closures close newly opened descriptors; command completion closes all stores and drops index
and writer-map references on success, failure and no-op paths. The active deferred nested-set
codec waits for shared children before exposing restored values, and the lifecycle write lock
excludes concurrent get/put calls during closure. Skyframe evaluation and its RAM graph are unchanged.

Eight pack tests cover threaded puts, independent writer JVMs, process death, checksum/index
corruption, symlink namespace rejection, bounds and visibility. Session tests additionally
verify failed sealing cannot publish and repeated/rejected readers do not leak descriptors.
Ten focused targets pass 111 cases; Bazel source builds/tests use Cache&Carry, uploads disabled.
The first integration run caught changed conflict-message wording, which was restored to the
existing diagnostic. A separate corruption-harness run caught a counter shadowing the fixture
package count; the corrected full twelve-build worktree probe passes. Its corruption phase
keeps root records and indexes intact while damaging nested-chunk checksums, preserving actual
deserialization-fallback coverage. The Runfiles diamond also retains exactly three arguments.

The synthetic sequential benchmark alternated file/pack backends across six fresh directories,
20,000 measured 1 KiB records per trial, with 200 JVM warm-up records. Publication includes
immediately following packed fsync and index sealing. Median publication time is
58.682 seconds for files and 0.147 seconds for packs.
Physical files fall from 20,200 to 2; allocated file bytes fall from 82,739,200 to 22,626,304,
although the index slightly increases logical payload size. This isolates synchronous storage
cost; it is not a prediction of whole-build speedup. Published pack readers are immutable, so
their duplicate-put timing is not applicable. Full microbenchmark commands and rounds are retained.

The substantial Datadog gate is repeated on the same revision, target, resources and host.
All eighteen builds pass with identical corresponding output hashes across the old/new backends,
including both feature controls, repeated restored reads and reverts.

| Concurrent phase | Files wall s | Packs wall s | Files peak PSS GiB | Packs peak PSS GiB |
| --- | ---: | ---: | ---: | ---: |
| control-base | 40.929 | 38.549 | 6.220 | 6.010 |
| concurrent-base-writers | 147.322 | 56.637 | 5.871 | 9.307 |
| warm-base | 1.637 | 1.705 | 5.780 | 8.933 |
| restarted-base-readers | 57.288 | 49.191 | 8.311 | 8.492 |
| edited-warm-graphs | 430.389 | 352.072 | 13.578 | 14.228 |
| control-edits-no-disk-cache | 337.663 | 324.745 | 10.282 | 10.447 |
| first-restarted-edited-readers | 85.882 | 61.166 | 12.178 | 8.595 |
| restarted-edited-readers | 63.792 | 29.891 | 7.632 | 8.592 |
| reverted-restarted-readers | 75.757 | 53.167 | 8.157 | 8.846 |

Each table cell is one phase run, with warm OS/action caches as specified in the protocol.
The first edited restart in the file run executed ten local tool actions per server; these
were disk-cache hits in the packed run. Do not attribute all of that phase's reduction to
packing. Other action summaries are retained for comparison. Cache-off initial controls have
fresh output bases, so comparisons with later restarted readers are not matched output-base
histories; add cache-off restart controls before claiming a general analysis-cache advantage.
The writer speedup repeats in the interrupted attempt, while its higher PSS also repeats.
Saved build events show approximately the same graph size (318,340/318,341 nodes) and tens
of GiB collected in Eden in both backends, but no live/committed heap snapshot. Use a separate
intrusive diagnostic to distinguish retained objects from heap expansion. Do not interpret
ordinary run variation as a reliable regression or gain. GC/quotas,
candidate/validation costs, nearby-revision partial reuse, memory profiling, failure policy
and partial-publication identity combinations still need work. The pack index has bounded
input parsing; that is not yet a runtime disk quota or global candidate-memory budget.

Evidence: [store comparison](skycache-concurrent-2026-10-20/packed-store-comparison.json),
[tests](skycache-concurrent-2026-10-20/packed-tests.json),
[provider identity](skycache-concurrent-2026-10-20/packed-provider-identity.json),
[small worktrees](skycache-concurrent-2026-10-20/packed-worktrees.json),
[Datadog packed gate](skycache-concurrent-2026-10-20/datadog-concurrent-packed.json),
[action summaries](skycache-concurrent-2026-10-20/packed-action-summaries.json),
[writer build metrics](skycache-concurrent-2026-10-20/packed-writer-bep-memory.json).


## Step 19 — independent-publication identity, then a warm-graph counterexample

Implementation: `bd2dac14190f18e5cedf8b6b181ecb552f270b46`. This is a correctness fix with a deliberately recorded
remaining failure; the previous eighteen-build Datadog gate does not establish arbitrary
graph-composition correctness.

The new fixture builds the left and right halves of a three-node provider diamond in independent
servers/caches. It then copies both complete immutable publications into a fresh shared cache
and asks a third worktree/server to expand the combined depset through an actual action command
line. Both writers saw zero shared reads, so this exercises publications that could have finished
independently in a concurrent run. No action disk cache is used. The fixture saves results before
assertions and always shuts down its own servers.

Both the packed and previous file backends produce **four arguments instead of three**, with the
leaf duplicated, despite successful builds and four analysis hits. Removing only the Runfiles
provider field gives the expected three. Per-writer UUIDs preserve one writer's shared identity
but cannot identify the same producer across independent writers.

Runfiles creation now records the complete configured-target/aspect key and a per-evaluation
creation ordinal in a weak identity-keyed provenance map. The factory scopes are thread-local
and nested scopes restore their parent. No evaluator change, global equality override or
content-only interning is introduced. Metadata is recorded even with the cache off so values
retained in RAM can later be serialized. Separate originals within one producer retain separate
ordinals; different producers remain distinct. Unscoped values retain the UUID fallback.
The codec preserves provenance on restore and excludes the derived allArtifacts memo, whose
presence depends on getter history. Its serialized contents remain part of the shared fingerprint.
The source/cache schema discriminator is bumped, so old-format payloads cannot be reused.

Ten test targets / 117 cases pass, including six new origin cases (independent writers, distinct
ordinals, distinct producers, changed contents, nested scope cleanup and reserialization).
Build/test uses `--config=efcache --bes_backend= --remote_upload_local_results=false`;
the source build records eleven remote cache hits. The fresh real-worktree probe now produces
exactly three arguments, four analysis hits, 82 shared reads and seven writes. Its reader takes
9.266 s versus 9.227 s before; these single startup-dominated samples are correctness evidence,
not a performance result. Additional provenance storage/creation cost needs the large-workload
measurement after the remaining correctness issue is addressed.

The next fixture first builds the reader's left branch against an empty cache, retains that
graph and server, exposes both other writers' publications, then builds the full app. This still
produces **four arguments**, with one analysis hit and 63 shared reads (0.252 s). The existing
leaf is a live original while the newly restored branch contains its decoded copy. Stable payload
identity alone cannot reconnect these objects. This is now the next correctness blocker; do not
call this implementation mature or use cold-reader success as proof for warm graph composition.
An earlier attempted mixed test changed active directories, but source inspection shows those
flags control serialization selection rather than forcing retrieval misses. That test was
discarded as mixed-evaluation evidence; the retained-server fixture is the valid reproducer.

Rejected shortcuts: collapsing Runfiles by file contents would merge genuinely separate objects;
changing all Runfiles equality would alter ordinary analysis semantics; filtering duplicate linker
arguments would hide a graph error; discarding the in-memory graph conflicts with the requested
design. The next change must preserve producer identity when restoring into an existing graph,
including the opposite arrival order, and verify content compatibility before aliasing objects.

Evidence: [packed failure](skycache-concurrent-2026-10-20/partitioned-identity-packed-before.json),
[file failure](skycache-concurrent-2026-10-20/partitioned-identity-files-before.json),
[field-removed control](skycache-concurrent-2026-10-20/partitioned-identity-no-runfiles-control.json),
[cold reader fix](skycache-concurrent-2026-10-20/partitioned-identity-origin-after.json),
[warm reader failure](skycache-concurrent-2026-10-20/warm-partitioned-identity-before.json),
[tests](skycache-concurrent-2026-10-20/runfiles-origin-tests.json).


## Step 20 — retained graph identity, cache-mode transitions and optional publication failures

Implementation: [5ddc5832719b](https://github.com/JSGette/bazel/commit/5ddc5832719b7e841952a05f4364682c0c126e82).
The Step 19 warm-reader counterexample is fixed while keeping the graph in RAM. Runfiles and
SymlinkEntry retain their existing reference equality. Typed weak indexes reconnect restored
objects to matching live values from the same complete producing key and creation ordinal.
Field checks include ordered artifacts, nested-set order, conflict policy, prefix and empty-file
supplier equality. The allArtifacts memo is deliberately excluded. Symlink entries require
their own identity codec: fixing Runfiles alone still produced four arguments instead of three
when the provider contained symlinks. Distinct same-path entries keep distinct creation ordinals,
preserving conflict precedence. EMPTY never consumes a producer ordinal on class initialization.
Starlark sets conflict policy before construction so identity reconciliation sees final policy.

Two lifecycle hazards were found during review. Artifact.equals tolerates a missing action index
while a rule is being evaluated; matching a new output against an old command's artifact could
therefore reuse the wrong action. Construction now reuses only an identity restored and validated
in the current command. Restore requires complete generating-action keys and is serialized against
producer entry; a producer still being evaluated makes the lookup miss instead of exposing unfinished
objects. Completed compatible objects can reconnect across commands. Weak-reference collection,
concurrent identity selection, changed action indices, abandoned evaluation and command epochs have
focused tests. This assumes deterministic rule evaluation under the validated input snapshot.

The prior UUID fallback for unscoped values was rejected because independent writers cannot agree
on that identity. Nonconstant Runfiles/SymlinkEntry without creator provenance now reject publication.
The payload/schema discriminator is bumped to local-source-v7-provenance-only. Content-only
interning, global equality changes and unconditional interning of previous command values were
discarded because they can merge distinct semantic objects or retain a wrong generating action.

Enabling SkyCache after a cache-off build exposed a separate optional-upload failure: the retained
graph lacked a FILE dependency witness for left/input. The action output was correct, but the
command previously exited 1 after upload. The local backend now atomically discards the entire
unpublished session and warns, allowing the build to succeed. Missing witnesses are not invented
and partially serialized roots are not exposed. The default remote backend retains its fatal
upload behavior. Errors, cancellation, interruption and their nested/suppressed causes cannot be
swallowed by the optional-failure hook; regression tests cover both policies. Repairing upload
coverage after cache-off warmup remains a reuse-efficiency limitation.

Eleven focused targets / 156 cases pass. Source compilation and tests use Cache&Carry via
`--config=efcache --bes_backend= --remote_upload_local_results=false`; the final build records three
remote hits, with unchanged tests served by Bazel's action cache. An initial missing strict test
dependency was corrected before this successful run. Cold and warm partial-publication probes,
generated output artifacts, symlinks and both cache-mode transitions produce exactly three arguments.
The final cold-generated reader has four analysis hits; the warm-generated reader has one.
The off-to-on reader also succeeds with one hit while intentionally discarding its optional upload.
On-to-off is tested both with child actions as inputs and with analysis-only provider use. Existing
SkyframeExecutor.invalidateWithExternalService already deletes imported DeserializedSkyValue nodes
when caching is disabled, because those values lack file/directory transitive edges. That existing
behavior explains why the transition is safe without a new invalidation path or whole-graph flush.
These small startup-dominated runs establish correctness, not speed.

The large Datadog gate now includes populated-output-base cache-off restart controls at the base,
edited and reverted states. All 24 builds pass: simultaneous writers/readers, retained warm graphs,
two distinct semantic Go edits, independently executed cache-off edit controls, repeated restarts
and reverts. Corresponding outputs have identical SHA-256 hashes and only their own feature marker.
Changed builds and their correctness controls use no action disk cache; other phases use the same
local action cache. Remote action caching is disabled only for this measurement to isolate SkyCache.
Each server has Xmx4g, jobs=2 and a 3072 MiB action-memory limit; pair PSS includes descendants,
sampled every 200 ms, and excludes the OS page cache. No compiler/profiler overlaps timed phases.

| Concurrent phase | Pair wall s | Peak combined PSS GiB | Analysis hits A, B |
| --- | ---: | ---: | --- |
| control-base | 39.391 | 6.242 | 0, 0 |
| restarted-control-base | 23.405 | 5.653 | 0, 0 |
| concurrent-base-writers | 56.887 | 9.072 | 0, 0 |
| warm-base | 1.685 | 8.743 | 0, 0 |
| restarted-base-readers | 50.202 | 8.428 | 4501, 4501 |
| edited-warm-graphs | 353.396 | 14.117 | 0, 0 |
| control-edits-no-disk-cache | 328.116 | 10.101 | 0, 0 |
| restarted-control-edited | 37.252 | 6.771 | 0, 0 |
| first-restarted-edited-readers | 49.360 | 8.460 | 4501, 4501 |
| restarted-edited-readers | 28.801 | 8.516 | 4501, 4501 |
| reverted-restarted-readers | 52.752 | 8.278 | 4501, 4501 |
| reverted-restarted-control | 26.391 | 5.822 | 0, 0 |

These are individual phase samples, not statistically controlled estimates. Compare restarted
readers with restarted controls, not only with the slower initial fresh-output-base control.
The base SkyCache restart is 2.14 times the wall time and
1.49 times the peak PSS of its
cache-off restart control despite 4,501 analysis hits per server. This is a negative performance
result, not evidence that analysis reuse is already faster or smaller on this workload.
Action summaries are retained to distinguish execution-cache differences. The memory overhead and
reader latency still need profiling; correctness success is not a production-maturity claim.
Next work remains bounded candidate/index memory, quotas and reader-safe collection, per-entry
validation for nearby revisions, meaningful hit/miss statistics and further rule/portability audits.

Evidence: [tests](skycache-concurrent-2026-10-20/live-identity-tests.json),
[symlink counterexample](skycache-concurrent-2026-10-20/warm-symlink-identity-before.json),
[upload counterexample](skycache-concurrent-2026-10-20/off-to-on-upload-failure-before.json),
[guard matrix](skycache-concurrent-2026-10-20/live-identity-guard-matrix.json),
[final provenance matrix](skycache-concurrent-2026-10-20/live-identity-provenance-matrix.json),
[on-to-off](skycache-concurrent-2026-10-20/live-identity-on-to-off.json),
[analysis-only on-to-off](skycache-concurrent-2026-10-20/live-identity-on-to-off-analysis.json),
[Datadog gate](skycache-concurrent-2026-10-20/datadog-concurrent-live-identity.json),
[action summaries](skycache-concurrent-2026-10-20/live-identity-action-summaries.json).


## Step 21 — bounded retry cadence and remembered mismatches

Implementation: [3df66c61e22f](https://github.com/JSGette/bazel/commit/3df66c61e22f66996514287b983daee734f55d9a).
This is a retry-cost fix, followed by a newly identified limit on the previous large-workload proof.

Separate intrusive JFR/heap diagnostics used dedicated worktrees/output bases and the same target,
resource limits and action cache. The cache-off process had 1,379,257 KiB used heap before full GC
and 251,907 KiB afterward; the writer had 2,714,367 and 362,984 KiB respectively. Committed heap
also fell, while process PSS remained high immediately after the diagnostic. These samples distinguish
collectible allocation from live Java objects; they do not explain native/process memory or prove
absence of leaks. Raw JFR recordings and class histograms remain under the local measurement roots.
They are diagnostic runs, not uninstrumented timing comparisons.

The fresh-output-base reader barely advanced beyond six configured targets. A thread dump showed
one analysis thread hashing publication witnesses while other analysis threads waited for the
LocalCacheSession.refresh monitor. The owned client was interrupted after 215.531 seconds, producing
exit 8; its JFR recording, cancellation reason and thread dump were preserved and its server shut
down. The one-second negative interval started before validation, so a sufficiently expensive scan
finished with its interval already expired. Each waiting lookup could then repeat the full scan.

The interval now starts when discovery/validation completes, including exceptional completion.
A rejected publication remembers its mismatching witness and checks that witness first on retry.
If it still fails, the rest of the manifest is skipped. If it matches, the entire immutable manifest
is read and all witnesses validated again. Metadata remains only an early retry hint; unchanged
size/mtime/inode still get a content retry after the interval. An earlier witness changing while
the previous mismatch is repaired cannot cause stale admission. The mismatch state is command-local
and cleared on finish. No input validity requirement, graph state or serialized format is relaxed.

A new slow-validation regression fails before the change (two rejections instead of one) and passes
after it. A second test verifies full validation after repairing a remembered mismatch. All eleven
focused targets / 158 cases pass; the source/test run used Cache&Carry and records three remote hits.
The twelve-build two-worktree source/Starlark edit, revert, restart and damaged-chunk fallback probe
also passes, preserving outputs and 49 analysis hits per restarted server.

The synthetic benchmark publishes 4,096 external 1 KiB files and a final witness, removes that final
file, and repeats rejected lookups while advancing an injected clock past the negative interval.
It measures current-thread allocated bytes with ThreadMXBean and verifies that restoring the missing
file eventually admits the publication. Excluding the initial full scan, median rejection falls from
35.373 to 0.102 ms,
and median allocation from 278,890,440 to
8,736 bytes. The initial full scan still allocates
about 280 MB. This isolates rejected-candidate work; it is not a whole-build speedup claim.

The new-binary fresh Datadog build completes in 97.244 seconds with the expected output hash and
34 rejected publication attempts, but **zero analysis/shared reads**. The diagnostic intentionally
fails its reuse assertion. The original run was cancelled rather than completed, and the new binary
changes its embedded-tool binding; do not interpret these as a controlled before/after build ratio.

Inspection of old and new manifests found differing witnesses for external/bazel_tools and
external/rules_java++toolchains+local_jdk/BUILD.bazel. This host has no system JDK. rules_java generates
a fail_rule whose error message embeds its output base's absolute nosystemjdk path. Even the two
original writer manifests disagree on those BUILD.bazel bytes. Their own restarted servers can
validate their own publications, while a genuinely new worktree cannot. The validator correctly
rejects those different bytes; blindly rewriting arbitrary strings would be unsafe.

**Correction to the scope of the Step 20 evidence:** its 24-build gate proves simultaneous operation,
correct edits/reverts and reuse on each populated output base. It does not prove that the Datadog
readers consumed the other worktree's publication. The small independent-publication fixtures do
prove cross-writer reuse for their tested rules. The next large probe must give a new reader access
only to another worktree's publication and use an explicit valid, consistent JDK configuration.
Keep the original failure recorded. Arbitrary host-specific generated content and nearby-revision
per-entry validation remain limitations, not problems solved by the retry optimization.

Evidence: [tests](skycache-concurrent-2026-10-20/candidate-retry-tests.json),
[before microbenchmark](skycache-concurrent-2026-10-20/candidate-validation-before.json),
[after microbenchmark](skycache-concurrent-2026-10-20/candidate-validation-after.json),
[two-worktree gate](skycache-concurrent-2026-10-20/candidate-retry-worktrees.json),
[fresh fallback](skycache-concurrent-2026-10-20/candidate-retry-fresh-reader.json),
[manifest differences](skycache-concurrent-2026-10-20/fresh-reader-publication-differences.json),
[cache-off heap](skycache-concurrent-2026-10-20/heap-identity-off.json),
[writer heap](skycache-concurrent-2026-10-20/heap-identity-writer.json),
[cancelled diagnostic](skycache-concurrent-2026-10-20/heap-identity-reader-cancelled.json).


## Step 22 — preserve aspect metadata for a genuinely independent reader

Implementation: [24b3c00e35a1](https://github.com/JSGette/bazel/commit/24b3c00e35a1a6b91411aa6faef128c1b19fb6e5).
The stricter Datadog probe starts from three different worktrees and output bases at the same pinned
revision. The reader is created after the writer stops and initially has access to exactly one
publication, made by that writer. This prevents success through reuse of its own prior publication.

An explicit common JDK removes the generated local_java_repository error message that differed by
output base in Step 21. Passing the downloaded Bazel repository itself as JAVA_HOME initially failed:
its generated BUILD.bazel collided with local_java_repository's own file. The successful fixture uses
a dedicated JDK view containing symlinks to distribution files only, excluding Bazel repository
metadata. The original downloaded repository is unchanged. This is an explicit benchmark flag,
not a global environment change, and does not normalize arbitrary build-file strings.

That setup exposed a real correctness bug: the old reader reached 174 configured-target cache hits
then crashed with exit 37 after 43.623 seconds. A locally computed parent's ConfiguredAspectProducer
requested the key of a restored AspectValue for transitive-package tracking. The ordinary generated
aspect codec omits that key and its transitive-package nested set, so the method threw
UnsupportedOperationException. The failing log and protocol are retained.

The local module now explicitly selects an aspect codec preserving its actions, aspect, providers,
master-log flag, alias status, key and transitive-package metadata. Restored values implement the
existing DeserializedSkyValue marker and support reserialization and metadata clearing. Both alias
subclasses are explicitly registered, including the one with its own generated codec. The default
remote codec remains unchanged. No evaluator edit is needed. The local source discriminator advances
to v8-aspect-package-metadata, so older incompatible publications cannot be reused.

All twelve focused targets / 165 cases pass with Cache&Carry enabled; the final run records three
remote action hits. New tests cover tracked/untracked values, aliases, reserialization, the master-log
flag, clearing, and unchanged default-codec behavior. The initial test fixture lacked its native-aspect
reference dependency; that test-only failure was corrected and its build log preserved locally.

The new independent Datadog reader completes successfully with 3,449
configured-target hits and 26,327 shared store reads. All three builds
produce the identical baseline executable SHA-256 `7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`. These JFR and heap
diagnostic runs are correctness and allocation evidence, not an uninstrumented speed comparison.
Their measured durations are saved in the protocol. The next gate extends this independent-reader
setup to two concurrent servers and independent feature edits/reverts before further performance work.

Evidence: [focused tests](skycache-concurrent-2026-10-20/aspect-metadata-tests.json),
[independent-worktree diagnostic](skycache-concurrent-2026-10-20/aspect-metadata-cross-worktree.json),
[reader's sole input publication](skycache-concurrent-2026-10-20/aspect-metadata-reader-publications.json),
[original crash](skycache-concurrent-2026-10-20/aspect-metadata-reader-before.log),
[patched reader log](skycache-concurrent-2026-10-20/aspect-metadata-reader-after.log).

The committed log copies trim trailing whitespace only; raw logs remain in the local run directories.


## Step 23 — concurrent independent readers, feature edits and reverts

Harness: [26dbf43ea3ed](https://github.com/JSGette/bazel/commit/26dbf43ea3ed6cad5885ff9ae2172d4c47babe40).
The test now accepts an explicit common JDK and an inactive seed cache produced in other worktrees.
It copies that cache into a new test root, records and verifies publication checksums, and requires
both fresh servers to report configured-target hits and shared store reads. Resume checks include
the JDK and seed evidence. The Python syntax and diff checks pass, and the actual 24-build run below
validates the harness. The product binary is the Step 22 build, whose 165 focused cases passed.

Both new Datadog worktrees consume independent publications while running simultaneously, and both
publish successfully. A reports 4,580 configured-target hits and B
9,101; their executable hashes equal the cache-disabled baseline.
The seed contains two completed publications from the separate Step 22 writer and reader. Neither
new worktree has a prior publication of its own. Subsequent phases verify warm graphs, restarts,
different semantic version-string edits, separately built cache-disabled controls, and reverts.
All 24 builds pass, and both edited outputs match their respective independent controls while
differing from each other and from the baseline. No stale feature marker appears after reverting.

| Phase (two servers) | Wall seconds | Peak combined PSS GiB | Configured-target hits A/B |
| --- | ---: | ---: | ---: |
| control-base | 40.184 | 5.615 | 0/0 |
| restarted-control-base | 22.793 | 5.713 | 0/0 |
| concurrent-fresh-cross-worktree-readers | 152.481 | 8.448 | 4580/9101 |
| warm-base | 27.028 | 8.400 | 3949/3966 |
| restarted-base-readers | 35.811 | 8.786 | 4501/4501 |
| edited-warm-graphs | 367.943 | 13.443 | 0/0 |
| control-edits-no-disk-cache | 340.642 | 9.278 | 0/0 |
| restarted-control-edited | 38.611 | 6.079 | 0/0 |
| first-restarted-edited-readers | 53.056 | 8.339 | 4501/4501 |
| restarted-edited-readers | 29.599 | 8.214 | 4501/4501 |
| reverted-restarted-readers | 41.831 | 8.462 | 4501/4501 |
| reverted-restarted-control | 26.955 | 5.875 | 0/0 |

These are single local measurements with disk/action-cache and graph states described by the raw
argv, not statistical speedup estimates. Pair wall time includes client completion and publication;
Bazel's own elapsed-time line ends earlier. Edited phases and their correctness controls disable the
action disk cache. The first fresh readers are slower and use more memory than the fresh cache-off
controls despite correct sharing. This gate establishes behavior, not production performance.

The unexpectedly expensive warm phase has a concrete cause: both servers hit the upstream 10-second
deadline while submitting invalidation requests (4,594 and 9,127 nodes), then invalidate all imported
nodes. The versions were unchanged. This is safe fallback with avoidable work; preserving imported
nodes without rechecking their inputs would be unsafe because those nodes lack normal file edges.
The next optimization targets local validation cost while retaining those checks and the in-RAM graph.

The separate Step 22 reader JFR reports LocalInputSnapshot.hashContents as 36.60% of sampled allocation
pressure. That method currently creates a 64 KiB array for every file. This percentage is sampled
weighted allocation evidence, not a measured fraction of wall time. Next measure a bounded reusable
buffer pool, verify digest equivalence and concurrent isolation, then repeat end-to-end measurements.
Reject unbounded thread-local retention and any shortcut that trusts size/mtime in place of content.
Nearby-revision reuse, runtime candidate bounds, reader-safe collection and quotas remain open.

Evidence: [full protocol](skycache-concurrent-2026-10-20/datadog-independent-gate.json),
[warm invalidation logs](skycache-concurrent-2026-10-20/independent-warm-invalidation.json),
[JFR allocation view](skycache-concurrent-2026-10-20/independent-reader-allocation-sites.txt).


## Step 24 — bounded reuse of hashing scratch buffers

Implementation: [f098fe591e4e](https://github.com/JSGette/bazel/commit/f098fe591e4e494dd28ba3b584434164fd7d6237).
The file-witness hasher now borrows 64 KiB scratch arrays from a thread-safe pool with sixteen slots.
It retains at most 1 MiB per process. Concurrent overflow allocates temporarily and is discarded if
the pool is full on return; buffers return in a finally block. Reads and hashing run outside the pool
lock. Every content byte is still checked, including a partial final read. There is no digest/schema,
publication, evaluator or graph-retention change. Unbounded thread-local retention and metadata-only
validation were rejected. This fixes a measured allocation source, not all resident-memory overhead.

The full-validation microbenchmark uses 4,096 external 1 KiB files plus eight files of lengths
0, 1, 65,535, 65,536, 65,537, 131,071, 131,072 and 262,145 bytes. It warms up twice, measures five
passes with ThreadMXBean, and uses the same 256 MiB initial / 1 GiB maximum heap. Median allocated
bytes fall from 276,609,760 to 8,005,192 (97.11% reduction), and
median time from 30.720 to 17.364 ms. The aggregate witness-digest signature is identical
before and after. This is a hot-file full-validation benchmark, not a whole-build speedup estimate.

Two new regression cases pass with the original and modified hasher: 24 concurrent threads validate
32 distinct files spanning multiple buffers; and alternating large/short/empty files are checked
before changing the last byte while preserving size and timestamp. All twelve focused targets /
167 cases pass. Cache&Carry is enabled in both benchmark builds/tests (one remote action hit each)
and the final full source/test run (three remote hits).

The new binary passes a fresh three-worktree Datadog control/writer/reader diagnostic. The reader
starts with exactly one publication from the other writer, obtains 4,285
configured-target hits and 30,347 shared reads, and produces the
baseline executable. The JFR/heap runs are diagnostic. Their hit counts differ from Step 22, so do
not attribute the difference in fresh-reader wall time solely to the buffer pool.

A separate uninstrumented check restarts each binary in its own populated diagnostic output base,
then runs a second command on the same server. Runs are serialized under the measurement lock;
both use the same target, resource limits, action cache and explicit JDK. Both restarted readers get
4,501 configured-target hits and 153,430 shared store reads; all outputs match. Both warm commands
retain imported nodes except for five invalidated keys and have zero configured-target retrievals.

| Single-server check | Before seconds | After seconds | Before peak PSS GiB | After peak PSS GiB |
| --- | ---: | ---: | ---: | ---: |
| Restarted reader | 36.471 | 26.013 | 4.097 | 4.460 |
| Warm reader | 8.043 | 7.598 | 4.224 | 4.482 |

These are single runs, not repeated randomized performance trials. Process PSS is higher after the
change, so **no PSS reduction is established**, despite the large microbenchmark allocation reduction.
Neither isolated warm check hits the invalidation deadline. They do not reproduce or resolve the
two-server timeout with the larger publication set from Step 23. The next issue is the cost of
repeated publication/witness validation under concurrent load, followed by candidate/index bounds,
reader-safe collection/quotas and nearby-revision reuse. The prior 24-build gate used the pre-buffer
binary; this step's runtime proof is the fresh independent reader plus restart/warm comparisons.

Evidence: [before benchmark](skycache-concurrent-2026-10-20/input-hashing-before.json),
[after benchmark](skycache-concurrent-2026-10-20/input-hashing-after.json),
[167 cases](skycache-concurrent-2026-10-20/hashing-buffer-tests.json),
[fresh independent reader](skycache-concurrent-2026-10-20/hashing-buffer-cross-worktree.json),
[before restart/warm](skycache-concurrent-2026-10-20/hashing-warm-before.json),
[after restart/warm](skycache-concurrent-2026-10-20/hashing-warm-after.json),
[new allocation profile](skycache-concurrent-2026-10-20/hashing-reader-allocation-sites.txt).


## Step 25 — reuse identical complete input checks within one discovery

Implementation: [8621a2c9ecf9](https://github.com/JSGette/bazel/commit/8621a2c9ecf940f32eb924640795da1760bfd994).
The Step 23 base cache has four publications with exactly the same ordered 84,613 input witnesses:
338,452 checks, of which 253,839 repeat identical path/operation/digest evidence. A simulated broad
per-witness memo needed substantially more retained metadata and covered less of that duplication.
The selected optimization instead remembers at most 32 complete, successfully validated lists
inside one candidate-discovery pass. Equality checks the complete records, not just a hash or path.
Matching candidates share the immutable list. Memo keys already belong to opened candidates, so
the map adds bounded references without retaining another set of input strings.

Each new discovery pass starts empty. Lists retained from earlier passes do not seed the memo;
changed digests still receive full validation. Failed-witness retry, parent-closure checks and the
final publication validation remain unchanged. The 10-second deadline and Skyframe evaluator are
unchanged. A cross-command memo and a metadata-only shortcut were rejected.

The eight-publication / 4,096-file microbenchmark runs two warmups and five measured fresh readers
with a 256 MiB initial / 1 GiB maximum heap. Median discovery time falls from 114.292 to
22.794 ms; measured allocation falls from 79,322,352 to 26,990,600 bytes
(65.97% less). This isolates duplicate validation on hot files, not whole-build
speed. Two new regression cases pass before and after: identical paths with different content
digests cannot substitute for one another, and a later discovery observes changed content even
when size and timestamp are preserved. All twelve focused targets / 169 cases pass. Cache&Carry
was enabled for both benchmark builds/tests (one actual remote hit each) and the full source/test
run (three remote hits).

The new binary also passes a separate off/writer/fresh-reader Datadog diagnostic. The fresh reader
starts with exactly one other worktree's publication, gets 3,729 configured-target
hits and 28,236 shared store reads, and produces the expected executable.
That JFR/heap run creates the binary-compatible seed for the following uninstrumented comparison.
Old and new binaries need separate seeds because their embedded bazel_tools bindings differ.

Both paired runs use two fresh worktrees, two independent seed publications, the same pinned
Datadog revision/target, explicit JDK, jobs/heap limits, local action cache and exclusive measurement
lock. All twenty builds pass and have the baseline executable hash. The new --base-only harness
mode ends after restarts and shuts down its owned servers; the default full edit/revert matrix is
unchanged. Step 23 remains the full 24-build artificial-edit/revert proof, using its earlier binary.

| Phase (two servers) | Before s | After s | Before PSS GiB | After PSS GiB | Before CT hits A/B | After CT hits A/B |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| control-base | 40.831 | 46.741 | 4.502 | 5.590 | 0/0 | 0/0 |
| restarted-control-base | 23.475 | 23.997 | 5.780 | 5.648 | 0/0 | 0/0 |
| concurrent-fresh-cross-worktree-readers | 138.392 | 129.497 | 8.352 | 8.129 | 4750/6621 | 1092/4140 |
| warm-base | 19.144 | 4.998 | 8.602 | 8.139 | 3976/3965 | 0/0 |
| restarted-base-readers | 35.560 | 29.552 | 8.504 | 8.659 | 4501/4501 | 4501/4501 |

The preserved Java logs contain 2 invalidation-submission timeouts before and
0 after. Full log excerpts and action-count summaries are in the comparison JSON.
Final publication sets, expressed as (publications, distinct complete witness lists, total witnesses),
are before `[(4, 1, 338452)]` and after `[(4, 1, 338452)]`. Fresh readers can admit repositories
at different points and therefore obtain different hit counts; do not treat their wall-time change
as a controlled estimate of this optimization alone. These are single local runs, not randomized
trials, and filesystem-cache/VM state was not held constant. The restarted cache-off control gives
a useful reference for host variability. No general production-maturity claim follows from this gate.

An additional eight-build comparison reuses each completed pair's output bases, restarts both
servers, and then runs a warm command. Every restarted server imports exactly 4,501 configured
targets, addressing the unequal imported-node count in the first comparison. All outputs match.
The old warm pair has 2 submission timeouts and the new warm pair has
0. The raw results retain per-server invalidation logs. This is still one
trial per binary; restart-time and PSS variation preclude a broad speed or memory claim.

| Equal-target-count pair | Before s | After s | Before PSS GiB | After PSS GiB | Before CT reloads A/B | After CT reloads A/B |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| restarted | 57.703 | 38.762 | 8.677 | 8.399 | 4501/4501 | 4501/4501 |
| warm | 17.902 | 11.110 | 9.434 | 8.484 | 4501/4501 | 0/0 |

Next inspect repeated inherited-input validation during publication, then runtime candidate/index
bounds, safe collection/quotas and reuse across nearby source revisions. Keep full content checks,
all parent dependencies, and the in-RAM graph.

Evidence: [isolated before](skycache-concurrent-2026-10-20/witness-list-before.json),
[isolated after](skycache-concurrent-2026-10-20/witness-list-after.json),
[169 cases](skycache-concurrent-2026-10-20/witness-list-tests.json),
[paired comparison and logs](skycache-concurrent-2026-10-20/witness-list-comparison.json),
[full paired before](skycache-concurrent-2026-10-20/witness-list-paired-before.json),
[full paired after](skycache-concurrent-2026-10-20/witness-list-paired-after.json),
[equal-count before](skycache-concurrent-2026-10-20/witness-equal-before.json),
[equal-count after](skycache-concurrent-2026-10-20/witness-equal-after.json),
[independent reader](skycache-concurrent-2026-10-20/witness-list-cross-worktree.json).


## Step 26 — avoid repeated identical inherited checks during publication

Implementation: [4eedeff94271](https://github.com/JSGette/bazel/commit/4eedeff9427147c7ccd3b5f68dad3b2cad04fb7e).
Publication previously called include() for every used parent, rereading the same files for each
parent with an identical input list, before the final isUnchanged() pass checked the unique inputs
again. Finish now remembers up to 32 complete lists that it has included in this finish call.
It still revalidates the first copy at publication time, still checks every different list, and
still performs the final source/input validation. All used parent IDs remain in the manifest;
sharing input evidence never removes a dependency. The set holds references already owned by the
session. The schema, evaluator, in-RAM graph and durability sequence are unchanged. Reusing earlier
discovery validation or dropping final content checks was rejected.

The benchmark creates eight independent parents with the same 4,096 external 1 KiB input files.
Each round reads all parent keys and writes one novel child key before timing finish(), including
manifest serialization, sealing and durability operations. It removes only that round's newly
created manifest from its private benchmark directory afterward, retaining a fixed parent set.
Two warmups and five rounds use the same 256 MiB initial / 1 GiB maximum heap. Median publication
time falls from 139.833 to 40.309 ms; allocation falls from 77,185,616 to
19,837,304 bytes (74.30% less). This isolates repeated-parent overhead
and does not establish a whole-Datadog speedup or a process-PSS reduction.

Two regression tests pass before and after. A child using equal input lists from two parents
becomes unreadable when either parent is removed; a content change after admission but before
publication, with size and timestamp preserved, prevents publication. Restoring the inputs does
not expose that failed child's private value, and descriptors close on failure. The first fixture
compile omitted an IOException import; the correction and failure evidence are recorded. Production
code was unchanged for the successful baseline run. All twelve focused targets / 171 cases pass.
Cache&Carry is enabled for both microbenchmark source/test runs (one remote hit each) and the full
source/test run (three remote hits).

The new binary passes the independent Datadog off/writer/reader diagnostic. The reader starts with
exactly one other writer's publication, obtains 4,021 configured-target hits and
30,016 shared reads, publishes successfully, and produces the same executable
as the cache-off control. The diagnostic uses JFR and heap inspection, so its timing is not a clean
performance comparison. The most recent two-server runtime comparison is Step 25 with the prior
binary; the most recent full artificial-edit/revert matrix remains Step 23. Repeat the full gate
after the remaining resource-lifecycle changes. No production-maturity claim is made.

Next bound candidate/index discovery resources and report local cache accounting accurately, then
design reader-safe collection/quotas and dependency-scoped reuse across nearby revisions. Whole-source
partitioning still prevents analysis reuse between different feature edits, even though concurrent
publishing, isolation and identical-source reuse work.

Evidence: [before publication benchmark](skycache-concurrent-2026-10-20/inherited-validation-before.json),
[after publication benchmark](skycache-concurrent-2026-10-20/inherited-validation-after.json),
[171 cases](skycache-concurrent-2026-10-20/inherited-validation-tests.json),
[independent Datadog diagnostic](skycache-concurrent-2026-10-20/inherited-validation-cross-worktree.json),
[fixture correction](skycache-concurrent-2026-10-20/inherited-validation-fixture-correction.json).


## Step 27 — bound optional candidate discovery and retained metadata

Implementation: [9509c3bcffd8](https://github.com/JSGette/bazel/commit/9509c3bcffd84eba820d9d70a62726c24b6bbfbf).
Per-object bounds did not prevent a growing cache from opening an unbounded number of pack indexes
and descriptors, or retaining an unbounded directory listing and rejected-publication bookkeeping.
The session now applies admission budgets before opening new candidates. The defaults are 64
publications and 256 MiB of combined encoded manifests plus indexes per invocation, configurable
with --experimental_local_skycache_max_publications and --experimental_local_skycache_max_metadata_mb.
Zero disables the corresponding shared-read capacity while private writes remain available. Negative
values disable the optional cache with a warning. These settings do not limit or evict Skyframe nodes.

The byte budget counts each candidate conservatively even when identical witness lists share Java
objects. It is **not a total JVM heap or RSS limit**: decoded objects, transient copies, values,
private-write bookkeeping and the graph use additional memory. Manifest reads now use a bounded
buffer sized from the opened channel; index opening independently enforces the remaining budget
against its opened channel before allocation. A preflight file-size check alone was rejected.

Directory enumeration stops after 4,097 entries to detect the fixed 4,096-entry scan limit before
sorting or retaining filenames. An overfull publication directory admits no new candidates for
that pass, rather than selecting an arbitrary incomplete subset. Previously pinned readers remain
open for concurrent gets until finish. Deleted entries leave the inspected/rejected maps, bounding
negative bookkeeping by the directory limit. Newly opened candidates still pass the existing full
input validation and parent-closure pruning. Incomplete candidates close their descriptors; no
parent dependency is dropped to fit a budget. Skips and peak admitted metadata/counts are reported
in local-skycache.json, even after finish closes the stores.

This is a capacity/reuse tradeoff, not garbage collection. A chain whose full closure cannot fit
falls back to misses. A directory beyond the scan limit also falls back until its contents are
reduced. Private writes can continue to grow disk usage; reader-safe GC and quotas remain required.
Changing these limits cannot make a corrupt or stale publication valid.

The benchmark uses 128 independent publications with identical 256-file witness lists. Two warmups
and five measured missing-key lookups force discovery. Before the change it retains 128 pack
descriptors; after it retains 64, and every reader closes all of them. Median discovery time is
7.536 -> 4.467 ms; allocation is 22,198,096 -> 11,483,656 bytes. The new run does
less work because it admits fewer candidates: do not describe this as equal-capacity acceleration
or infer a whole-build memory reduction.

Five new regression tests cover count-limit fallback with private writes, the exact combined
manifest/index byte boundary and independent opened-index enforcement, closure larger than the
count limit, an overfull directory after a reader has pinned a publication, and zero/negative
limits. All twelve focused targets / 176 cases pass. One test initially lacked the strict direct
packed_store dependency; the BUILD correction and failure evidence are recorded. Cache&Carry was
enabled for both benchmark source/test runs (one remote hit each) and the full source/test run
(three remote hits).

The new binary passes a fresh Datadog off/writer/reader diagnostic. The reader starts with one other
worktree's publication, gets 4,324 configured-target hits and
30,596 shared store reads, publishes successfully, and produces the baseline
executable. Its peak admitted candidate count is 1 and encoded
metadata is 32,478,246 bytes, with no admission/scan-limit skips.
JFR and heap captures make this diagnostic, not a clean timing comparison. The latest paired
runtime comparison is Step 25, and the latest full artificial-edit/revert gate is Step 23; repeat
that full gate after the remaining lifecycle and invalidation work.

Evidence: [before admission benchmark](skycache-concurrent-2026-10-20/candidate-admission-before.json),
[after admission benchmark](skycache-concurrent-2026-10-20/candidate-admission-after.json),
[176 cases](skycache-concurrent-2026-10-20/candidate-admission-tests.json),
[Datadog diagnostic](skycache-concurrent-2026-10-20/candidate-admission-cross-worktree.json),
[fixture correction](skycache-concurrent-2026-10-20/candidate-admission-fixture-correction.json).


## Step 28 — persist content-addressed file and directory dependency evidence

Implementation: [2a2dc9922ce1](https://github.com/JSGette/bazel/commit/2a2dc9922ce181f7b149e864a7888895baa45ba7).
Nearby-revision reuse is a core requirement, so its validation groundwork takes priority over
traffic statistics. Previously the local backend traversed file dependencies but suppressed their
legacy path/version records: safety came from a complete source digest and publication-wide input
manifest. Removing that guard would be unsound because local versions are MINIMAL and one logical
analysis key may have different values at different revisions.

An opt-in IndependentFileDependencyProvider now supplies deterministic opaque file/listing evidence
to the existing serialization layer. The local payload contains canonical parent dependency keys
and portable path/content witnesses, including symlink resolution. It is stored under a
local-input-v1/content-fingerprint key. Existing nested dependency nodes are now persisted too,
using their existing ordering, content fingerprints and optional Zstd format. A leaf content change
therefore changes its containing tree fingerprint. Native version providers keep their original
format; older independent providers may still omit legacy records. No evaluator change is made and
the entire Skyframe graph remains in RAM.

Payload decoding enforces 1 MiB, 512 parents and 1,024 witnesses, rejects missing witnesses and
trailing/truncated data, and preserves all parent keys. Capture errors fail the upload dependency.
The local source schema advances from v8 to v9 so older entries cannot claim this evidence exists.
The full source digest, publication-wide admission checks, inherited manifest validation and final
input revalidation are **still enforced**. This step does not enable sharing across revisions.

Six new cases cover deterministic evidence across workspace roots, same-size/mtime content edits,
symlink retargeting with identical referent bytes, directory listing changes, malformed payloads,
leaf write completion/capture failure, and persisted parent/listing references whose changed leaf
changes the composite fingerprint. These behaviors are grouped across six tests. All twelve
focused targets / 182 cases pass. Two fixture mistakes (strict dependency and Mockito restubbing)
were fixed and rerun. Cache&Carry was enabled throughout: the initial full build had five remote
hits and the final successful run had three. An XML collection after a compile failure was stale;
it was not accepted as verification. The published test summary comes from the final successful run.

The Datadog off/writer/reader diagnostic passes with identical expected executables. Exactly one
other worktree's publication exists before the reader starts. The reader gets
585 configured-target hits and 11,938 shared reads,
then publishes successfully. These JFR/heap-instrumented runs are diagnostics, not a clean timing
comparison, and do not establish a speed or peak-memory improvement.
Configured-target reuse is lower than Step 27's 4,324 hits (now 585).
The retained full-publication guard also admits a larger evidence manifest. Repository materialization
and discovery timing affect fresh-reader hits; this run does not isolate their contributions.
Treat the lower reuse as an open regression signal, not harmless variance. The next per-entry reader
must be measured with repositories ready and followed by fresh/concurrent controls before any
performance conclusion. No admission budget was exhausted in this diagnostic.

The storage audit compares the Step 27 and Step 28 writer publications for the same Datadog target:
records increase from 279,741 to 443,154; pack bytes from
3,959,749,929 to 4,024,945,017; index bytes from 13,427,616 to
21,271,440. The new writer has 89,477 leaf records, occupying
50,846,689 record bytes plus their fixed-width index entries. Composite trees
also consume storage. The audit checks leaf checksums/encoding and finds no missing referenced
leaf-parent keys across the published packs. It is not a complete tree validator or equal-work
latency benchmark. This is a measured cost for future selective validation, not a speedup.

Next build a bounded reader for complete nested/leaf evidence, with malformed, missing, compressed,
cyclic and edited-input tests. Only then can candidate-specific lookup, stable repository/environment
identity and selective witness inheritance replace the global source partition. The reader must
try another candidate when one entry is stale, retain all pack dependencies, and preserve external
repository-name bindings. Evidence must correspond to evaluated FileValues, not merely a later
filesystem observation. Dropping the source guard now or treating content hashes as monotonic
versions was rejected. Statistics and reader-safe GC/quotas remain open, followed by the full
two-instance independent-edit/revert gate.

Evidence: [182 cases](skycache-concurrent-2026-10-20/dependency-evidence-tests.json),
[storage audit](skycache-concurrent-2026-10-20/dependency-evidence-storage.json),
[Datadog diagnostic](skycache-concurrent-2026-10-20/dependency-evidence-cross-worktree.json),
[fixture corrections](skycache-concurrent-2026-10-20/dependency-evidence-fixture-corrections.json).


## Step 29 — require bounded transitive dependency validation at lookup

Implementation: [ef5e03b7d60f](https://github.com/JSGette/bazel/commit/ef5e03b7d60f372004ef2b0aae8c65b57e60f4c1).
LocalAnalysisCacheClient now validates an analysis envelope's entire invalidation root before
returning its value. LocalDependencyValidator follows composite nodes, file/listing/source leaves
and every parent reference. It verifies content fingerprints before parsing, decodes the existing
optional Zstd format, and checks portable input witnesses. Missing, corrupt, malformed or mismatching
evidence produces an optional cache miss. No evaluator change or graph eviction is introduced.

Traversal uses an explicit stack, with a 4,096-depth bound, 250,000 new records and 128 MiB combined
encoded/decompressed bytes per lookup. Composite nodes allow at most 100,000 references and 8 MiB
decompressed bytes. Leaf limits remain from Step 28. Cycles, negative/excessive counts, unsupported
keys and trailing bytes fail closed. The successful-key memo is capped at 250,000 entries. These
are work/admission limits, not a total heap cap; the store can allocate its separately bounded
record before validation accounts for it. Only successful keys are memoized. Missing records can
be retried after repository materialization or another publication.

The validator and memo live for one invocation. As with the surrounding build, inputs must remain
stable while analysis uses them. Successful leaf evidence is registered for the final publication
revalidation; mutation after an accepted lookup can therefore discard publication. This does not
make arbitrary mid-build source edits an atomic filesystem snapshot. A new invocation validates
again. One synchronized traversal owns the memo to prevent concurrent partial validation from
being mistaken for success; first-look contention remains a tuning concern.

Ten new tests cover nested/file/listing/source dependencies; changed file bytes with restored
mtime; unrelated source edits; mandatory symlink-parent validation; missing-record retry; wrong
fingerprints; malformed/truncated/trailing data; compression and decompression limits; a 1,500-node
chain, depth/work/byte bounds and a forced cycle; concurrent memo use/final revalidation; and stale
evidence rejection at the actual analysis-client boundary. All thirteen focused targets / 192
cases pass. The new test visibility and @ForOverride call were corrected before that successful
run. Cache&Carry supplied three remote hits for the full run and one for the benchmark build.

The Datadog off/writer/reader diagnostic passes with matching baseline executables. The reader
starts with one other worktree's publication and imports 1,978 configured targets,
with 50,466 shared store reads. Its validator reads
81,332 dependency records, performs 117,830 witness checks
and reports zero rejected roots. It publishes successfully, with no admission-limit skips.
The reader's JFR-instrumented build takes 93.951 seconds; this is
diagnostic context, not an equal-work before/after claim. Fresh hit counts differ from Step 28
(585) and Step 27 (4,324), so their wall times cannot establish acceleration or regression size.

For isolated cost, the benchmark opens the writer's sealed pack after all build servers stop and
uses the already materialized writer workspace. It selects 512 distinct analysis dependency roots
evenly from 48,473 sorted candidates whose referenced 128-bit root exists.
Two warmups precede five measured rounds. Each round uses a new validator, then repeats the same
roots in that invocation. Input snapshot creation and final revalidation are outside the timed
region. Median first validation is 13415.963 ms and
1,949,391,544 current-thread allocated bytes. The repeated pass is
0.155 ms / 151,584 bytes and performs no additional record reads.
The first pass checks 119,969 records and 176,546 witnesses;
all sampled roots validate. This measures added validation cost and same-invocation memo behavior,
not whole-build speed or total process memory. Native decompression allocations are not represented
by the Java thread-allocation counter.

The full-source namespace and publication-wide admission/inheritance guards remain. Thus this step
still does not enable cross-revision sharing. Next separate repository/environment identity from
source contents, validate candidate analysis values individually (trying another candidate after
a mismatch), and inherit only relevant source evidence while retaining external repository bindings
and all pack parents. Keep initial/final source checking to prevent publishing across a changed
invocation. Verify edited/reverted worktrees before claiming this core requirement is complete.
Traffic statistics and reader-safe GC/quotas remain open.

Evidence: [192 cases](skycache-concurrent-2026-10-20/dependency-validator-tests.json),
[isolated cost](skycache-concurrent-2026-10-20/dependency-validator-benchmark.json),
[runtime validation](skycache-concurrent-2026-10-20/dependency-validator-runtime-checks.json),
[Datadog diagnostic](skycache-concurrent-2026-10-20/dependency-validator-cross-worktree.json).

## Step 30 — abandon evicted, not-yet-ready deserialization continuations

Implementation: [9f3aff359f26](https://github.com/JSGette/bazel/commit/9f3aff359f26d36f04d7d8d750f2fff6d7252aa3).
The native serialization state for a pending lookup continuation had no cleanup. If its compute
state was evicted before the continuation became available, its owned lookups could remain pending
and block other deserializers sharing those values. Cleanup now installs a completion listener
that abandons those owned lookups when available. Failed/cancelled fetches remain tolerated;
shared fetches are not cancelled. The Skyframe evaluator is unchanged.

The targeted regression fails twice before the fix (ready and pending continuation) and passes
after it. The full serialization suite passes all 294 cases, including four new cases. The broader
local integration/source build passes 202 cases across thirteen targets. Cache&Carry is enabled
for both source/test runs; the full suite reported two remote hits and the source build five.
These are correctness results, not an isolated performance claim.

The cross-revision feature remains uncommitted and has not passed its large acceptance gate.
The small two-worktree probe passed 18 builds, including changing a shared Starlark rule in one
worktree, importing the replacement into another server with an older cached value, and reverting.
The full Datadog run passed six pairs through first edited readers (6,206 imports each) but stalled
on warm revert after the native ten-second invalidation timeout. Local diagnostics found unresolved
shared source-artifact lookups with no owning continuation; this led to the independent regression.

With cleanup fixed, a fresh Datadog diagnostic progressed through the same edits but both warm
reverts failed with Java heap space in their 4 GiB heaps (approximately 135 and 148 seconds).
A subsequent intrusive rerun completed, but it reused prior publications and forced garbage
collection for heap capture. It is not an acceptance or performance result. Increasing the heap
was rejected as the next step because it could conceal the failure.

The captured heap contains 16,263 RepositoryMapping objects with 15,746 distinct entry maps and
6,021,175 entries. There are 3,272 maps of size 1,502; package metadata references 16,209 distinct
mapping objects. This identifies a concrete source of duplicated data to investigate. It does not
yet establish that sharing these maps alone resolves the OOM. Raw heap dumps remain local.
Next add and measure a local shared mapping codec, then repeat the clean edited/reverted gate.

Evidence: [cleanup tests](skycache-concurrent-2026-10-20/pending-continuation-regression-after-full.json),
[failure and diagnostic protocol](skycache-concurrent-2026-10-20/continuation-cleanup-protocol.json),
[mapping identities](skycache-concurrent-2026-10-20/repository-mapping-heap-before.json).

## Step 31 — share immutable repository mappings

Implementation: [8e9dbb02758a](https://github.com/JSGette/bazel/commit/8e9dbb02758a28bbbc0b05fba0e368153400f3f5).
A local-only codec now stores each RepositoryMapping through the existing shared-value adapter.
Previously, independently serialized package metadata repeatedly materialized its full map. The
codec preserves context repository, entry iteration order (which determines inverse-name
precedence), and the cached-inverse subclass. Unknown subclasses are rejected. It does not add a
global interner or alter the evaluator. The same-source format namespace advances to v10; the
unfinished cross-revision namespace separately advances to v2, isolating older codec payloads.

Five focused cases cover large-map identity across roots, independent writers with concurrent
readers, changed mappings/context separation, cached inverse/order behavior, and empty mappings.
All fourteen focused targets / 207 cases pass. Source builds/tests use Cache&Carry.

The isolated benchmark uses 1,024 separately serialized roots containing the same 1,502-entry
mapping. Each fresh JVM warms up on 64 roots; five paired rounds alternate baseline/shared order.
Both paths retain encoded roots, the store, and decoded roots. Baseline uses the original dynamic
codec; shared uses the local codec. Median encode time is 376.872 →
17.234 ms; decode time is 278.850 → 10.378 ms.
Distinct decoded mappings are 1,024 → 1, and mapping entries 1,538,048 → 1,502. Serialized root plus
shared-store payload is 60,938,240 → 27,297 bytes. Estimated live-heap increase after explicit GC is
137,864,240 → 142,248 bytes. This is an
isolated duplication benchmark, not process peak memory or a whole-build speedup. The first harness
run leaked its test executor and was discarded; the corrected harness owns and drains its executor.

The fresh two-server Datadog smoke test passes baseline publication, warm build, restarted imports
(4,501 each), and independently edited imports (6,206 each) with correct executables. Both warm
reverts then time out at 300 seconds. Neither reports OOM, but the native ten-second invalidation
timeout still invalidates all 6,214 imported keys. Thread and heap inspection after the stall finds
ten outstanding evaluation futures and 80 unfinished Skyframe lookups without live retrieval
states. The whole-build issue is not resolved and cross-revision changes remain uncommitted.

Evidence: [paired isolated benchmark](skycache-concurrent-2026-10-20/repository-mapping-benchmark-v2.json),
[207 focused cases](skycache-concurrent-2026-10-20/repository-mapping-eviction-validator-tests.json),
[failed runtime gate and decisions](skycache-concurrent-2026-10-20/mapping-and-late-cleanup-protocol.json).

## Step 32 — remember eviction across later retrieval transitions

Implementation: [4bfb5a1c8cb8](https://github.com/JSGette/bazel/commit/4bfb5a1c8cb8a0a432da406553458518fd83b492).
Eviction could close a retrieval context while its evaluator was still constructing the next
serialization state. Cleaning only the state present at close left the later continuation without
cleanup. Four deterministic cases reproduce this before the fix, covering ready/pending futures
and both state update paths. The context now remembers that it is closed, serializes close/state
updates, and cleans up any subsequently attached state. Cleanup runs outside the context lock so
future callbacks do not execute while it is held. The evaluator remains unchanged.

The targeted before run has eight cases/four failures. The full serialization suite after the fix
passes 298 cases. Cache&Carry supplied one remote hit before and two after; the final source/local
integration rebuild passes 207 cases. The causal link to the Datadog stall remains a hypothesis
until a fresh two-server run passes. Do not claim the large gate complete from unit tests.

Evidence: [298 serialization cases](skycache-concurrent-2026-10-20/late-continuation-regression-after-full.json),
[regression XML](skycache-concurrent-2026-10-20/late-continuation-regression-before-test.xml).

### Step 32 runtime follow-up — warm revert passes

A fresh two-server Datadog smoke run now passes all six phases / twelve builds without thread
or heap captures. Both independent edited readers import 6,206 configured targets and produce
their distinct expected executables. Keeping both graphs alive, the warm reverts finish in
25.740 seconds for the pair, import 4,501 configured targets each, and reproduce the
exact control baseline SHA-256. Both servers retain the original 4 GiB heap limit. The pair's
observed peak PSS in this phase is 9,505,766 KiB. This resolves the prior
stall in this diagnostic run; it does not yet accept the full cross-revision feature. The longer
28-build gate is running separately with disk action cache disabled for edited correctness
comparisons. Remote action cache is off in both diagnostics. Setup time varied substantially, so
these runtime numbers are context rather than a controlled speedup claim.

To retain disk headroom, the two completed failed probes' private SkyCache directories were
removed after verifying no process still used their output bases. Their pack footprints total
10,063,551,875 bytes. Source worktrees, output-base
logs, results, heap diagnostics and the immutable validator benchmark fixture remain. Retirement
is limited to these named temporary directories; no shared action cache was removed. Linux free
space was reclaimed; this does not imply Windows immediately shrank the WSL virtual disk.

Evidence: [passing runtime smoke](skycache-concurrent-2026-10-20/late-eviction-runtime-smoke.json),
[retired cache inventory](skycache-concurrent-2026-10-20/failed-smoke-cache-retirement.json).


## Step 33 — share validated analysis across independently edited worktrees

Implementation: [bbbb96e3bfcc](https://github.com/JSGette/bazel/commit/bbbb96e3bfcc513398f9a6cedf97675bfccd3d49).
Linked Git worktrees now share a namespace based on canonical common Git directory, exact effective
environment, byte order and local format identity. Source contents are validated per analysis entry.
The complete bounded dependency validator runs before returning each candidate, and a stale candidate
does not hide a later valid version of the same key. Candidate bytes are copied under the session
lock, validated outside it, and accepted only while the session is open. All used parent packs remain
in the publication closure. External/absolute witnesses remain conservative publication-wide guards.
The initial full-source digest is retained as provenance and rechecked before publication with all
observed/imported witnesses and the project namespace. Mid-build edits are outside Bazel's stable
input assumption; final validation prevents publishing a changed invocation. Non-Git workspaces and
nested workspaces without their own .git remain isolated by path. Cross-machine identity is future work.

Warm invalidation checks the exact dependency root of the value previously imported into RAM.
Accepting any currently valid replacement as justification for keeping the older value was rejected.
An optional RetainedAnalysisCacheClient interface lets the invalidator use this check without
changing existing remote clients. Previous roots and newly accepted roots are distinct; a bounded
250,000-entry map survives commands and clears on namespace changes. Unknown/evicted roots invalidate
conservatively. The evaluator and full in-memory graph policy are unchanged. Manifest SKS3 and
publications-v3 separate this contract from older caches.

The final source build passes 207 cases across fourteen targets, with five Cache&Carry hits. The
serialization cleanup suite from Step 32 passes 298 cases. The earlier small 18-build probe also
checks changing a shared Starlark action definition while another server holds an older imported
graph, then replacing/reverting it, independent edits and damaged chunk fallback. Its recorded binary
predates Steps 31–32; the large gate below uses the latest mapping and eviction fixes.

The Datadog cluster-agent gate passes 28 builds in fourteen paired phases at revision
0cde96beb70963d0838119c308b9d0422ee66be0, with two independent servers, each Xmx4g and jobs=2.
First edited readers start with only recorded baseline publications and empty server RAM. Each
imports 6,206 configured targets, writes new entries and produces a distinct feature executable.
Action disk caching is disabled for first edited readers, warm edited graphs and independent edited
controls. Both feature hashes exactly match their cache-off controls. Warm/restarted reverts recover
the baseline hash. Remote action caching is disabled throughout to isolate SkyCache; Cache&Carry is
used for source builds/tests, not these measurements. No heap/thread capture perturbs this run.

Protocol correction: the original runner omitted --restart-controls, so its eleven core pairs
completed first. Their exact results are archived separately with a SHA-256. The harness then resumed
and added the three missing cache-off restart pairs; existing records are unchanged. The final three
rows below therefore occurred after all core pairs. These times are observations, not an interleaved
before/after comparison or a repeated performance claim.

| Paired phase (actual order) | Wall seconds | Peak combined PSS GiB | CT hits A/B |
| --- | ---: | ---: | ---: |
| control-base | 40.070 | 6.466 | 0/0 |
| concurrent-base-writers | 53.215 | 7.563 | 0/0 |
| warm-base | 1.582 | 7.573 | 0/0 |
| restarted-base-readers | 42.838 | 7.915 | 4501/4501 |
| first-cross-revision-readers | 397.549 | 11.858 | 6206/6206 |
| reverted-warm-graphs | 49.818 | 9.001 | 6336/4501 |
| edited-warm-graphs | 352.401 | 13.043 | 4497/4501 |
| control-edits-no-disk-cache | 366.533 | 9.867 | 0/0 |
| first-restarted-edited-readers | 81.463 | 7.506 | 4501/4501 |
| restarted-edited-readers | 45.759 | 8.063 | 4501/4501 |
| reverted-restarted-readers | 42.439 | 7.652 | 4501/4501 |
| restarted-control-base | 42.170 | 6.155 | 0/0 |
| restarted-control-edited | 23.298 | 5.724 | 0/0 |
| reverted-restarted-control | 24.348 | 5.692 | 0/0 |

Selected server logs contain 4 native invalidation timeout(s). Correct output and successful
sharing do not establish a performance advantage or production maturity. First-pass validation still
costs 13.416 seconds / 1.949 GB thread allocation in the Step 29 benchmark. Next remove repeated
content checking with bounded invocation-local success reuse, measure it against the preserved
baseline, and retain final content revalidation. Traffic accounting, cache-mode/configuration lifecycle
coverage, reader-safe collection and storage quotas remain open. Do not increase invalidation deadlines
or discard the in-memory graph to conceal the cost.

Evidence: [28 builds](skycache-concurrent-2026-10-20/cross-revision-datadog-28-builds.json),
[original 22 builds](skycache-concurrent-2026-10-20/cross-revision-datadog-core-22-builds.json),
[Starlark probe](skycache-concurrent-2026-10-20/cross-revision-starlark-worktrees.json),
[runtime and order correction](skycache-concurrent-2026-10-20/cross-revision-runtime-review.json),
[207 cases](skycache-concurrent-2026-10-20/repository-mapping-eviction-validator-tests.json).


## Step 34 — validate repeated input evidence once per invocation

Implementation: [2d145e28eb75](https://github.com/JSGette/bazel/commit/2d145e28eb7568a4cfd9d165637d6e0ab8e7124b).
The validator previously hashed each witness in its precheck and again while registering final
publication evidence. Distinct dependency records also repeated identical witnesses. It now checks
and registers a witness once, then reuses successful checks keyed by exact logical path, operation
and expected digest. Resolution parents are still traversed first. Only successes are memoized;
missing/mismatching inputs can be retried after repository materialization. A stale candidate does
not mark otherwise stable invocation inputs as changed. The memo is limited to the dependency-record
capacity (250,000 by default); additional witnesses are still validated and registered, just not
memoized. It is discarded after each command. Final publication rehashes all retained evidence and
the full source snapshot without using this memo. Source formats and the evaluator are unchanged.

This uses Bazel's existing stable-input assumption within a command. Checking twice consecutively
was rejected as an atomic-snapshot guarantee; it is not one. A persistent path/mtime memo, negative
memoization and skipping parent evidence were rejected. The bounded success set adds retained Java
metadata during a command in exchange for less repeated hashing and transient allocation; it is
not a claim that process peak memory must decrease.

All 213 cases across fourteen focused targets pass. Six new cases cover duplicate evidence with
final same-size/same-mtime content rechecking, distinct expected digests and stale-candidate isolation,
late external repository materialization, bounded memo size without lost final evidence, directory
metadata versus listing operations, and changed resolution parents. The source/test build takes
18.027 seconds and reports three Cache&Carry hits. The before and after benchmark-jar builds each
report one Cache&Carry hit. Source builds/tests retain --config=efcache, --bes_backend= and
--remote_upload_local_results=false.

The unchanged Step 29 fixture supplies the same 512 dependency roots. Preserved deploy jars run in
three alternating before/after fresh-JVM pairs, each with two warmup rounds and five measured rounds,
Xms256m/Xmx2g and the host measurement lock. Medians below cover fifteen measured rounds per variant.
Snapshot construction and final content revalidation are outside the timed region in both variants.
All roots pass; each first pass reads 119,969 dependency records and reports zero rejected roots.

| Metric | Before | After |
| --- | ---: | ---: |
| First validation, ms | 13,004.608 | 5,157.238 |
| First validation, Java thread allocated bytes | 1,950,069,056 | 1,150,615,760 |
| Repeat same roots in same invocation, ms | 0.156 | 0.135 |
| Repeat allocated bytes | 151,584 | 151,584 |

First-validation wall time falls 60.3% and current-thread
Java allocation falls 41.0% in
this isolated benchmark. The old witness counter is 176,546 occurrences
(each successful occurrence also did the second registration hash); the new counter is
113,999 checks with 62,547 memo hits.
These are not whole-build speedups or RSS/native-allocation measurements. The original Step 29
measurement remains preserved; this comparison uses its own binaries, fixed heap and fresh rounds.

A fresh twelve-build Datadog smoke passes with two 4 GiB servers. All baseline/reverted executables
match the Step 33 baseline, and both feature hashes match Step 33's independent cache-off controls.
This smoke enables action disk caching and does not repeat the full no-disk-cache control matrix.
Remote action caching stays off. It has 2 native invalidation timeout(s), recorded with the
selected server diagnostics. Runtime times below are single-run context, not an isolated comparison.

| Paired phase | Wall seconds | Peak combined PSS GiB | CT hits A/B |
| --- | ---: | ---: | ---: |
| control-base | 41.946 | 6.116 | 0/0 |
| concurrent-base-writers | 62.033 | 7.867 | 0/0 |
| warm-base | 1.615 | 7.853 | 0/0 |
| restarted-base-readers | 34.416 | 7.463 | 4501/4501 |
| first-cross-revision-readers | 49.015 | 8.537 | 6206/6206 |
| reverted-warm-graphs | 16.785 | 9.503 | 4501/4501 |

The measured validation improvement is accepted. Keep the full graph in RAM. Remaining work includes
further validation allocation/latency where measured, cache-mode/configuration lifecycle coverage,
accurate traffic statistics and reader-safe collection/quotas; this is still experimental.

Evidence: [paired benchmark](skycache-concurrent-2026-10-20/dependency-validator-witness-paired.json),
[213 cases](skycache-concurrent-2026-10-20/repository-mapping-witness-validator-tests.json),
[Datadog smoke](skycache-concurrent-2026-10-20/dependency-validator-witness-runtime.json),
[runtime review](skycache-concurrent-2026-10-20/dependency-validator-witness-runtime-review.json).


### Step 34 diagnostic follow-up — separate manifest and entry validation

A second fresh two-server smoke passes all twelve builds while JFR records only the warm-revert
phase. Recording is started/stopped through jcmd after verifying each PID belongs to the named
output base. This is an instrumented diagnostic, not acceptance timing or a performance comparison.
The raw JFR files and event JSON remain local; only aggregated stack categories, sample weights,
manifest identity counts and reproduction helpers are published.

The two recordings contain 320/314 execution/native samples under manifest input checks and
320/326 under per-entry input checks. These sample counts are not wall-time percentages, but they
support investigating repeated file checking at both layers. Four manifests each contain the same
142,388 exact witnesses; they have one complete-list identity and a 142,388-witness union. Therefore
the existing complete-list memo already avoids duplicate checks between these publications. Adding
another per-discovery overlap memo is not supported by this workload. Regex construction has visible
estimated allocation weight but only 2/3 execution samples in this classification; it is a later
allocation candidate rather than the first explanation for this deadline failure.

Next measure a combined admission-plus-dependency validation workload, since the Step 34 isolated
benchmark opens a sealed pack directly and deliberately excludes manifest admission. Investigate
bounded reuse of successful admission checks during per-entry validation within one invocation.
Do not weaken global discovery freshness, resolution-parent validation, negative retries, or final
content revalidation, and do not inherit unrelated input evidence merely because a candidate was
inspected. A broad memo inside matches() would also affect discovery/final checks and is rejected.
No implementation of this follow-up optimization has been applied yet.

Evidence: [stack summary](skycache-concurrent-2026-10-20/witness-invalidation-profile-summary.json),
[manifest identities](skycache-concurrent-2026-10-20/witness-current-manifest-overlap.json),
[diagnostic command](skycache-concurrent-2026-10-20/witness-invalidation-profile-command.json).


## Step 35 — reuse current admission checks during dependency validation

Implementation: [83e8c0f84e02](https://github.com/JSGette/bazel/commit/83e8c0f84e0266a3cfb6730dbf741e292799e2e3).
The Step 34 profile showed duplicate content checks between manifest admission and per-entry
dependency validation. Complete manifest lists were already memoized within discovery, so adding
another list-level memo was rejected. This change retains up to 250,000 exact successful admission
witnesses per invocation. The dependency validator can reuse these after traversing resolution
parents, registering each used witness for final fresh revalidation. A witness includes logical path,
operation and expected content digest. Admission always checks current content, including later
discovery attempts, and removes failed checks from the hint set. Failures remain retryable.

Merely inspecting an unused candidate does not add its witnesses to publication evidence. Overflow
falls back to fresh checks. The short hint monitor performs no filesystem work or session callbacks.
Session close clears the hint set, while preserving used evidence for final content checks. The
source snapshot, final rehash, disk format and Skyframe evaluator are unchanged. This uses the same
stable-input assumption within one Bazel command as Step 34; it is not an atomic filesystem snapshot.
Persistent path/mtime memoization, weakened namespace checks and skipping parent guards were rejected.

All 220 cases across fourteen focused targets pass, including seven new cases for fresh final and
later-admission checks, exact identity, retry after failure, concurrent capacity/fallback, unused
candidate isolation, parent rejection and release at session close. The initial parent test correctly
rejected the root but assumed zero successful hints inside a rejected parent. Evidence is sorted,
so its unchanged referent can be checked before the changed symlink. The corrected assertion verifies
that no dependency record completes; no product change was made to satisfy the incidental count.
The initial failure logs remain local. The passing source/test build took 20.332 seconds with three
Cache&Carry hits. Before/after benchmark builds took 7.402/5.732 seconds and each had one Cache&Carry
hit. All source builds/tests use --config=efcache --bes_backend= --remote_upload_local_results=false.

The new benchmark includes complete admission of all four real Datadog publications and dependency
validation of 512 sampled analysis roots. It uses the existing current-format fixture and its exact
effective environment. Environment values were reconstructed privately from local evidence; neither
cache identity nor manifests were rewritten. Each round creates a fresh snapshot/session, opens the
candidate packs, validates the roots and verifies final unchanged content. Snapshot construction and
final revalidation remain outside the timed region. Publication inventories remain byte-identical.
Three alternating fresh-JVM pairs, two warmup rounds and five measured rounds per JVM use Xms256m,
Xmx2g and the exclusive host measurement lock. Fifteen measured rounds per variant give:

| Metric | Before | After |
| --- | ---: | ---: |
| Admission plus first validation, ms | 10,106.718 | 5,588.584 |
| Java current-thread allocated bytes | 1,940,295,376 | 1,574,359,840 |
| Same roots repeated within invocation, ms | 0.131 | 0.131 |
| Repeat allocated bytes | 151,584 | 151,584 |

Combined timed work falls 44.7% and current-thread Java
allocation falls 18.9%.
Every first pass reads 101,755 dependency records and rejects zero roots. Fresh per-entry witness
checks fall from 98,792 to 2,355, with
96,437 admission hint hits; fresh manifest checks remain in the
timed region. These are isolated validation results, not whole-build speedups, retained-heap reductions
or physical disk-I/O counts. The bounded set retains metadata during a command to avoid rehashing.

A fresh twelve-build smoke passes with two 4 GiB Bazel servers, separate worktrees and source edits
followed by reverts. All baseline/reverted executable hashes match Step 33; both feature hashes match
its independent cache-off, no-disk-cache controls. The smoke has disk action caching on and remote
action caching off. It records 0 native invalidation submission timeout(s). The following
single-run times are context only and do not establish a whole-build before/after improvement.

| Paired phase | Wall seconds | Peak combined PSS GiB | CT hits A/B |
| --- | ---: | ---: | ---: |
| control-base | 42.138 | 5.955 | 0/0 |
| concurrent-base-writers | 57.507 | 7.090 | 0/0 |
| warm-base | 1.555 | 7.126 | 0/0 |
| restarted-base-readers | 27.892 | 7.399 | 4501/4501 |
| first-cross-revision-readers | 42.871 | 8.509 | 6206/6206 |
| reverted-warm-graphs | 9.218 | 9.027 | 3/3 |

On this warm revert, the server diagnostics show eight configured targets invalidated per server.
Each server retains the other valid imported graph nodes, so only three new CT hits are needed;
the low fresh-hit count is not a loss of cache reuse. Step 34's fallback instead invalidated everything
after the ten-second submission timeout. This successful run is evidence that the targeted case can
finish without that fallback, not a guarantee for every workload or a controlled whole-build speedup.

Accept the measured validation improvement. Keep the full graph in RAM. Remaining work includes
the invalidation deadline where it persists, configuration/cache-mode lifecycle, accurate traffic
statistics and reader-safe collection/quotas; production maturity is not claimed.

Evidence: [paired benchmark](skycache-concurrent-2026-10-20/admission-validation-paired.json),
[220 cases](skycache-concurrent-2026-10-20/repository-mapping-admission-validator-tests.json),
[Datadog smoke](skycache-concurrent-2026-10-20/admission-validation-runtime.json),
[runtime review](skycache-concurrent-2026-10-20/admission-validation-runtime-review.json),
[fixture protocol](skycache-concurrent-2026-10-20/admission-validation-fixture-summary.json).


## Step 36 — warm cache, configuration and environment transitions

Probe: [c3f7d4bf03b1](https://github.com/JSGette/bazel/commit/c3f7d4bf03b1e1a25d6ab2b5ea02573f9e5bc9f7).
The new reproducible lifecycle probe passes 54 builds and two query commands across two cache-enabled
Git worktrees and two independent cache-off controls. It uses the unchanged Step 35 Bazel binary,
whose source/tests were built with Cache&Carry. No new Bazel source compilation was necessary for
this Python correctness harness. Disk and remote action caches are disabled in every build. Each
server uses a 1 GiB heap and two action slots, with the host measurement lock held throughout.

The fixture has six library genrules and an active app package. Output bytes depend on source files,
a Starlark action suffix, a --define value, a repository rule reading --repo_env and a shell action
reading --action_env. Every output is checked against directly constructed expected bytes. Each
transition also matches independent cache-off builds in both worktrees, except the restarted-reader
phase, whose expected baseline and preceding control hashes are already established.

The first writers publish successfully, then both cache-enabled servers restart and read seven
configured targets each from the shared cache. All later transitions preserve each server PID,
including both query commands, so these checks exercise retained graphs rather than silently using
fresh JVMs. Configuration and source reverts, cache-directory return and resumption after query
obtain fresh analysis hits where needed. Disabling the local cache produces no local-cache statistics
or analysis hits; source and Starlark edits still produce the correct new bytes. Re-enabling, switching
to an empty cache directory and returning to the original directory remain correct. Changing the
repository or action environment changes output bytes exactly as in both controls.

| Phase | Builds | Fresh CT hits A/B |
| --- | ---: | ---: |
| initial-writers | 4 | 0/0 |
| restarted-readers | 2 | 7/7 |
| configuration-change | 4 | 0/0 |
| configuration-revert | 4 | 7/7 |
| disable-and-edit | 4 | 0/0 |
| reenable-after-edit | 4 | 0/0 |
| reenabled-source-revert | 4 | 7/7 |
| switch-cache-directory | 4 | 0/0 |
| switch-back | 4 | 7/7 |
| repository-environment-change | 4 | 0/0 |
| action-environment-change | 4 | 0/0 |
| test-environment-change | 4 | 7/7 |
| build-after-query | 4 | 7/7 |
| restore-original-environment | 4 | 1/1 |

The --test_env transition is a build of non-test targets, so it checks tolerance of that option
change, not test-execution reuse. The query phase checks that the local caching session is inactive
for a non-executing command and that the subsequent build resumes correctly. These are small,
generated correctness cases; the saved per-command times are diagnostic context, not performance
claims. All owned servers shut down cleanly. No product or evaluator change was required by this
matrix. Retain the large Datadog results for workload coverage and continue with traffic statistics
and reader-safe collection/quotas.

Evidence: [complete lifecycle protocol](skycache-concurrent-2026-10-20/lifecycle-transitions.json).


## Step 37 — logical cache traffic and latency statistics

Implementation: [9e3260773cce](https://github.com/JSGette/bazel/commit/9e3260773cce325396dff159eeeb8fbd37464630).
Bazel's native Skycache log previously reported zero bytes and requests because both local adapters
returned empty statistics. The blob-store session and analysis client now expose per-invocation
counters and bounded latency histograms through the existing statistics interfaces. No evaluator,
cache format or cache-validity rule changes. Existing `writes`, `reads`, `shared_reads` and deduplication
diagnostics remain; the new `cas_*` JSON fields explicitly describe blob-store API calls.

Accounting decisions are explicit:

- Put key/value byte totals count all submitted API payloads, including rejected attempts. Accepted
  put entries include successful private and shared deduplication; newly created private records
  remain the separate `writes` counter. No accepted entry is recorded for a failed or closed-session put.
- Blob requests count one public get, including misses/errors/closed-session responses. Returned
  bytes count its successful value. Internal candidate probes and deduplication reads are excluded.
- Analysis requests count entry lookups and retained-value validity queries. Entry response bytes
  are the returned value plus invalidation fingerprint; misses return zero bytes. A validity query
  has one logical boolean response byte. Candidate envelope bytes are not counted again as blob reads.
- Each synchronous local API call is one batch. Histograms include complete call latency, including
  lock waiting, and use monotonic time independently of the discovery retry clock. The existing
  FingerprintValueService wrapper supplies its individual-call histogram; the store's batch histogram
  includes direct dependency-validator reads too. Snapshot totals are exact after calls complete;
  snapshots during concurrent calls may observe in-flight operations.

These are logical in-process payloads and requests, not SSD sectors, compressed pack sizes or network
traffic. The upstream log retains its word “uploaded”; successful storage calls still require a
separate successful publication to become discoverable. Physical-I/O and GC metrics are distinct work.

Five new regression cases fail against the previous empty counters and pass with this change. They
cover duplicate/shared writes, conflicts and rejected/closed requests, exact concurrent totals,
independent sessions and immutable snapshots, latency bucket counts, malformed/missing analysis keys,
retained-value queries, and separation of analysis responses from nested blob reads. All 225 cases
across fourteen focused targets pass. The initial test invocation hit a BUILD visibility error before
tests ran; the next invocation reproduced all five intended zero-counter failures. The final source
build took 19.153 seconds with three Cache&Carry hits. The benchmark build took 7.600 seconds with one
Cache&Carry hit. Source builds/tests retain --config=efcache --bes_backend= --remote_upload_local_results=false.

The unchanged 512-root Datadog admission/validation benchmark runs in three alternating fresh-JVM
pairs, two warmups and five measured rounds each, using Xms256m/Xmx2g and the exclusive measurement
lock. The baseline is the preserved Step 35 binary; the input cache and all publication hashes remain
unchanged. Every round validates 101,755 records; the new counters report exactly 101,755 blob requests
and hits, zero misses and zero puts. Medians over fifteen measured rounds per variant are:

| Metric | Before | After |
| --- | ---: | ---: |
| Admission/validation, ms | 5,584.778 | 5,605.440 |
| Java current-thread allocated bytes | 1,574,016,080 | 1,574,686,528 |
| Repeat within invocation, ms | 0.137 | 0.130 |

The observed timed difference is 0.37% and allocation
difference 0.04%. This passes the
chosen 5% investigation threshold; the small difference is not a precise causal overhead estimate.
This benchmark exercises blob accounting during validation, not the complete analysis-client wrapper
or whole-build cost. Snapshot construction/final revalidation stay outside the timed region.

The lifecycle matrix is repeated with the new binary: all 54 builds and two queries pass, outputs match
cache-off controls, and native upload counters exactly equal local API counters at every cached build.
The first writer reports 393 accepted requests, representing
391 new private records. A restarted reader reports
374 requests and 58.35 KB
received, with 7 CT hits. The prior native logs reported zero traffic.

To avoid another large set of Datadog output directories, the runtime recheck reuses the Step 35
worktrees/output bases, with fresh JVMs and preserved prior results/logs. The first attempt expected
reader hits too early: both builds produced the correct baseline but rejected the old publications
and published current ones. The manifest comparison shows the same namespace and a changed
`external/bazel_tools` binding to the rebuilt Bazel installation. This is conservative invalidation,
not a statistics failure or a reason to weaken the guard. That failed reader expectation is retained
separately. The retry starts from those two valid current-binary publications while keeping the older
publications present, and passes six builds: baseline reads, independent semantic edits and warm reverts.
All output hashes match Step 33's independent no-disk-cache controls; native upload accounting matches
the session counters. Sources are restored and both owned servers shut down cleanly.

| Paired phase | Wall seconds | Fresh CT hits A/B | Native read requests A/B |
| --- | ---: | ---: | ---: |
| baseline-readers | 63.888 | 4501/4501 | 317300/317300 |
| independent-edits | 38.504 | 6206/6206 | 329257/329251 |
| warm-revert | 54.380 | 15856/6426 | 362012/331635 |

The mixed-publication recheck records 4 native invalidation timeout(s).
Its timings use existing disk action outputs and a mixed cache, so they are not a fresh-cache or
controlled before/after speed comparison. Keep any remaining mixed-publication admission cost visible;
reader-safe garbage collection and quotas remain required before claiming maturity.

Evidence: [five red cases](skycache-concurrent-2026-10-20/traffic-stats-regression-before.json),
[225 passing cases](skycache-concurrent-2026-10-20/repository-mapping-traffic-validator-tests.json),
[paired overhead measurement](skycache-concurrent-2026-10-20/traffic-stats-paired.json),
[lifecycle/native counters](skycache-concurrent-2026-10-20/traffic-stats-lifecycle.json),
[initial reader expectation failure](skycache-concurrent-2026-10-20/traffic-stats-datadog-seed-attempt.json),
[guard diagnosis](skycache-concurrent-2026-10-20/traffic-stats-seed-guard-summary.json),
[Datadog retry](skycache-concurrent-2026-10-20/traffic-stats-datadog-recheck.json).


## Step 38 — reject obsolete repository bindings before file hashing

Implementation: [e59ea2b0320b](https://github.com/JSGette/bazel/commit/e59ea2b0320b24d55457e2962507c6e9257c70ef).
Step 37 exposed four invalidation submission timeouts with obsolete and current Bazel publications
sharing a cache. Its diagnosis found a changed `external/bazel_tools` binding. Admission checked
path-sorted witnesses, hashing large `absolute/...` inputs before reaching that changed binding.
The first encounter with each rejected publication repeated this work on every new invocation.

Admission now checks exact, non-listing top-level `external/<repository>` witnesses first, then
checks every remaining witness. Both passes perform fresh content checks. A matching binding does
not establish file/listing validity; an earlier mismatch simply rejects the same publication sooner.
The existing retry hint still rechecks rejected bindings, including restoration before the discovery
timer expires. Persisted ordering, checksums, identity, parent closure, final revalidation and the
in-memory Skyframe graph remain unchanged. There is no disk-format or evaluator change.

Decision: two linear passes without sorting or extra retained metadata. Rejected alternatives were
weakening the tool binding, skipping content checks, caching negative validity across commands, or
adding a second complete-witness-list memo for failures. Those alternatives are unnecessary for the
measured stale-binding case and would add correctness or retention concerns. Repository bindings
are normally cheap directory/symlink checks; a binding unexpectedly naming a file still receives the
ordinary content check, with no special validity exemption.

Three added tests cover skipping unrelated file hashing on an invalid binding, immediate retry of a
restored binding, and rejecting changed file bytes or directory membership despite a matching binding.
The first test setup mistakenly inspected binding evidence before the writer's publication step had
recorded it; two setup failures are preserved. After explicit binding capture, the pre-change run has
exactly the intended skipped-hashing assertion failure and two passing safety controls. All 228 cases
across fourteen focused targets pass after the change. Source/test build: 18.866 seconds, three
Cache&Carry hits. Benchmark build: 4.620 seconds, one Cache&Carry hit. Every source build/test uses
`--config=efcache --bes_backend= --remote_upload_local_results=false`.

The mixed-cache benchmark freezes ten publications: four obsolete and six compatible, with 512 roots
sampled from a compatible baseline publication. Every round validates 112,195 records, rejects exactly
four publications, opens six candidates, has zero rejected roots/read-limit skips, and returns exactly
112,195 blob hits. Every dependency-validation counter is identical before and after, isolating the
saved work to admission. A compatible-cache control uses the original immutable four-publication fixture,
512 roots and 101,755 records, with no rejected publications. Each experiment has three alternating
fresh-JVM pairs, two warmups and five measured rounds per JVM, Xms256m/Xmx2g and the exclusive host
measurement lock. Manifest inventories and binary hashes are checked before and after every JVM;
the benchmark source is identical. Medians over fifteen measured rounds per variant:

| Fixture and metric | Before | After |
| --- | ---: | ---: |
| Mixed admission/validation, ms | 17,357.145 | 6,011.367 |
| Mixed current-thread allocated bytes | 3,421,789,504 | 2,231,888,616 |
| Compatible admission/validation, ms | 5,721.409 | 5,713.205 |
| Compatible current-thread allocated bytes | 1,575,959,008 | 1,569,552,480 |

Mixed-cache timed work is 65.37% lower and Java allocation
is 34.77% lower. Compatible-cache
time changes by -0.14%, within the chosen 5% investigation threshold.
These measure admission plus dependency validation, excluding snapshot construction and final raw
rechecks. Allocation is current-thread Java allocation, not retained heap or peak process memory.
No whole-build speedup or universal deadline guarantee is claimed.

The runtime check reuses the owned Datadog worktrees and output bases to limit host-disk growth.
It keeps every prior publication, runs two explicit writers with the new binary, shuts both servers
down, then runs fresh readers, independent semantic edits and warm reverts. All eight builds match
the independent Step 33 cache-off/no-disk-cache output hashes. Reader/edit/revert phases report real
configured-target hits; native traffic counters match the local adapter totals. Each server retains
its PID after the planned restart. Workload disk action cache is on and remote action cache is off;
two 4 GiB heaps and two action jobs per server. These single-run timings are runtime diagnostics,
not a controlled before/after build comparison.

| Paired phase | Wall seconds | Fresh CT hits A/B | Rejected publications A/B |
| --- | ---: | ---: | ---: |
| seed-writers | 53.029 | 0/0 | 210/190 |
| baseline-readers | 31.082 | 4501/4501 | 10/10 |
| independent-edits | 30.268 | 1708/1708 | 60/50 |
| warm-revert | 10.649 | 3/3 | 20/20 |

The run records 0 native invalidation submission timeout(s).
Both servers log exactly eight configured targets invalidated on each edit/revert. Warm revert has
three fresh configured-target hits per server while retaining valid imported nodes in RAM; fewer new
hits here reflect retained graph reuse, not a failed cache lookup. Step 37's mixed-cache run instead
hit the deadline and invalidated everything. The fixtures have different publication inventories,
so the controlled microbenchmark above, not this comparison, supports the quantified speedup.
Sources are restored and owned servers shut down cleanly. Reader-safe collection and byte/age quotas
are still missing; faster rejection does not bound disk growth. The next storage step must introduce
reader/writer lifetime protection before enabling any deletion, with a separate layout for clients
that participate in leases so an older client cannot lose a live pack to the collector.

Evidence: [initial test setup](skycache-concurrent-2026-10-20/binding-first-test-setup-failure.json),
[pre-change regression](skycache-concurrent-2026-10-20/binding-first-regression-before.json),
[228 passing cases](skycache-concurrent-2026-10-20/repository-mapping-binding-validator-tests.json),
[mixed paired measurements](skycache-concurrent-2026-10-20/binding-first-paired.json),
[compatible control](skycache-concurrent-2026-10-20/binding-first-control-paired.json),
[Datadog edit/revert](skycache-concurrent-2026-10-20/binding-first-datadog-recheck.json).


## Step 39 — active-publication leases and isolated layout

Implementation: [6b999c072eb4](https://github.com/JSGette/bazel/commit/6b999c072eb416cc2b9bba94298ea84c6b7b0da6).
This step supplies lifetime protection required before collection. It does not delete cache data or
enable byte/age eviction. Graph storage remains in RAM and no Skyframe evaluator code changes.

New clients read and write under `<cache>/leased-v1/`. The existing manifest-v3 and packed-v1
encodings remain inside that directory. Legacy clients continue using the original top-level
directories; their data is neither moved nor deleted. Forward rollout starts a separate cache
population. Rollback runs the old binary against its preserved population. Isolating the protocol is
necessary because older clients never acquire leases, so a collector cannot safely infer their
reader lifetimes. Automatic legacy deletion/migration and an implicit destructive contraction are
explicitly excluded. Future managed-byte budgets must report that legacy data is outside their scope.

`LocalCacheLeaseManager` keeps one channel per canonical managed root in each JVM and reference-counts
shared locks across that JVM's sessions. One permanent `leases.lock` file has a catalog byte and 4096
publication slots, selected by a hash prefix. Collisions conservatively protect extra publications.
The lock inode must never be replaced or collected. Shared pin acquisition and exclusive collection
probes use nonblocking POSIX file locks. A busy catalog produces a retryable cache read miss, rejects
a new private upload, or skips publication. It does not wait through a build deadline. Closing one
owner releases only its references; closing the last owner closes the channel. Process death releases
that process's locks. The bundled JDK's `FileChannelImpl.tryLock` was inspected; interruption is checked
before acquisition and no blocking channel-lock call is used.

Discovery acquires a publication pin while briefly holding the shared catalog lock, releases that
catalog lock, then reads and validates the immutable manifest. A future collector must preserve every
pinned publication and its full parent closure. Rejected or incomplete candidates release pins and
descriptors; retained candidates stay pinned through session completion. Private writers acquire their
own pin before pack creation, keep parent pins, seal their pack, and hold the shared catalog lock across
the final manifest publication. Ordinary reads and existing private writes need no catalog lock.
Descriptors close before the session releases its pins. `catalog_busy_skips` makes contention visible.

Decisions: use bounded lock slots rather than unbounded per-publication lock files, and a shared catalog
transition rather than a lifetime global writer lock. Existing `FileSystemLock` is explicitly hostile
to concurrent same-path threads and opens a channel per handle; it cannot directly provide this
same-JVM reference-counted protocol. The existing disk action-cache collector deletes independent files
and cannot preserve SkyCache's publication dependency closure. Both were inspected before introducing
the local lease manager. No claim is made for arbitrary network filesystems or remote leases.

Thirteen added cases bring the focused suite to 241 cases across fifteen targets. They cover shared
same-JVM and cross-JVM pins, hash-slot collisions, catalog ownership/exclusion, process termination,
interruption, closing one owner while another remains active, private writer and parent protection,
retry after a busy catalog, failed publication, legacy isolation, symlink rejection and repeated
descriptor cleanup. Actual subprocesses test lock release on termination and prove that closing an
unrelated same-JVM owner does not expose a live pin to an external collector. Existing descriptor
tests now count pack descriptors separately; a new twenty-cycle test verifies exactly one coordination
descriptor for concurrent sessions and zero after the final session closes.

The initial full integration run passed in 19.614 seconds with three Cache&Carry hits. After adding
the external catalog-exclusion case, the final run passed in 11.044 seconds with three Cache&Carry hits;
unchanged targets used their cached results. Benchmark before/after builds took 7.529/7.887 seconds
with one Cache&Carry hit each. Every source build/test retains
`--config=efcache --bes_backend= --remote_upload_local_results=false`.

The overhead experiment uses 24 hard links to identical immutable data in an isolated dual-layout
fixture; it adds no pack bytes and never runs collection on that fixture. The original four-publication
fixture is preserved. Before and after jars use the same benchmark source and exact environment, with
three alternating fresh-JVM pairs, two warmups and five measured rounds each, Xms256m/Xmx2g and the
exclusive measurement lock. Every round opens four candidates and validates the same 101,755 records
for 512 roots. All dependency-validation counters match, with zero rejected publications or roots.
Manifest inventories and jar hashes are verified around every JVM.

| Metric | Before leases | After leases |
| --- | ---: | ---: |
| Admission plus validation, ms | 5,633.757 | 5,657.517 |
| Current-thread allocated bytes | 1,567,923,784 | 1,575,477,272 |
| Same-invocation repeat, ms | 0.146 | 0.130 |

Observed timed difference: 0.42%, within the chosen 5%
investigation threshold. The measurement includes discovery/pinning and validation but excludes snapshot
and session construction, final raw checks and session close. It is not a total lifecycle, whole-build,
retained-memory or peak-RAM estimate.

The runtime protocol performs 74 builds and two queries across nineteen phases, with disk and remote
action caches disabled. It begins with a legacy server and a lease-aware server sharing the cache path,
each checked against an independent cache-off control. It covers the prior configuration/environment/
cache-mode matrix, then rolls the legacy participant forward, performs independent edits and reverts
with two lease-aware servers, rolls one participant back, and rolls forward again. Exact expected output
bytes and control hashes match throughout. Restarted participants show real configured-target hits;
warm participants may retain their graph and need no fresh cache read. Every saved publication hash
remains present and unchanged, and all owned servers shut down cleanly.
As in the earlier matrix, the `test_env` phase builds non-test targets; it does not establish test-result reuse.

| Runtime phase | Fresh CT hits A / B |
| --- | ---: |
| restarted-readers | 7 / 7 |
| roll-forward-to-leases | 7 / 7 |
| both-leased-independent-edits | 7 / 5 |
| both-leased-revert | 7 / 7 |
| roll-back-to-legacy | 7 / 6 |
| roll-forward-again | 7 / 6 |

This is a small correctness workload plus a Datadog-derived validation benchmark. It does not rerun
the large Datadog executable with collection, which is not implemented yet. Keep that final gate for
the collector stage and preserve host-disk headroom; the WSL VHD's host volume is near its reserved
capacity. Reader-safe deletion, byte/age policy, abandoned-writer collection and interruption recovery
remain required work before a maturity claim.

Evidence: [241 passing cases](skycache-concurrent-2026-10-20/repository-mapping-lease-validator-tests.json),
[paired overhead](skycache-concurrent-2026-10-20/lease-protocol-paired.json),
[fixture protocol](skycache-concurrent-2026-10-20/lease-protocol-fixture.json),
[mixed-version lifecycle and rollback](skycache-concurrent-2026-10-20/lease-layout-lifecycle.json).


## Step 40 — reader-safe collection and idle byte/age policy

Implementation: [65b3b345b8f5](https://github.com/JSGette/bazel/commit/65b3b345b8f5c9c2092f6f6f091d9f99cf7b1ea5).
Collection is now implemented within `leased-v1`. Each server keeps its normal Skyframe graph in RAM;
the evaluator is unchanged. The collector preserves active publication slots and every transitive
parent, including parents not yet individually pinned by a reader that has only pinned a child.
It can reclaim unpublished packs once their writer has exited or released its pin.

The permanent lease file gains an independent collector-coordination byte (4097). Only collectors
use it; ordinary clients keep the Step 39 catalog/pin protocol. One collector checks immutable
manifest checksums outside exclusive catalog coordination, then briefly takes the exclusive catalog,
reconciles newly published manifests, checks parent closure and probes publication slots. A changed
previously scanned inventory aborts the pass. A new publication is included before selection; stale
planning can never omit its parents. Existing readers and private writers continue record I/O.
New catalog operations may return a retryable miss or skip during the exclusive interval.

Selection protects live/unsafe packs and their closure, then considers newer publication roots with
their complete additional closure. The size budget is soft when protected data exceeds it. An inactive
closure larger than the entire budget is eligible for eviction. Age means publication time, not last
read: reading does not rewrite metadata. Older parents remain when a retained child needs them.
This intentionally rejects an LRU scheme requiring writes on every cache read and a TTL-only reader
liveness heuristic. OS leases establish liveness; abandoned inactive packs need no time-based grace.
Publication hash collisions retain extra data conservatively.

Deletion removes child manifests before parents, syncing each namespace after each unlink, and only
then removes known pack files. A killed or interrupted collector can leave orphan packs, which the
next pass reclaims. Exact file identity/size/time is rechecked before deletion. No recursive deletion
or symlink traversal is used. Unrecognized pack contents protect the pack and its known parent closure.
Legacy layouts, permanent coordination files, format directories and unrecognized entries are preserved.
Both manifest `.pending-*.tmp` and pack `.pending-*.index` crash remnants are recognized in their
respective directories. Unsupported/corrupt/incomplete/cyclic metadata stops the pass before deletion;
automatic repair or quarantine is not part of this change.

Scanning is bounded to 16,384 publications, 131,072 enumerated entries across both inventories and pack
inspection, 2 GiB of manifest input, 1,000,000 parent edges and 2,000,000 closure visits. An individual
manifest has the existing 64 MiB payload limit; late arrivals add at most 128 MiB of reconciliation
input under catalog coordination. Limit exhaustion stops collection conservatively. These caps and
the managed-byte policy do not claim to bound total JVM memory, graph memory, filesystem metadata or
legacy populations. Streaming parsing retains dependency IDs and skips witness objects.

The existing Bazel idle-task mechanism runs collection after a command. Defaults, chosen autonomously
for this experimental backend, are 5 GiB, seven days and a 30-second idle delay. They are exposed as:

* `--experimental_local_skycache_gc_max_size=5G`
* `--experimental_local_skycache_gc_max_age=7d`
* `--experimental_local_skycache_gc_idle_delay=30s`

Zero disables an individual limit; setting both size and age to zero disables collection. Policy is
validated before a cache session opens. Starting another command interrupts the idle task and all
leases are released. A completed or busy attempt atomically replaces the output base's
`local-skycache-gc.json`; failures/interruption use the server's idle-task diagnostics and do not
replace the previous successful report. Statistics distinguish scanned manifest bytes, approximate
known/protected/retained logical bytes, busy skips, publication/pack/temp removals and catalog time.
Active files may grow while measured. Logical unlinked bytes are not physical SSD space recovered.

Twenty core tests and four idle/options tests bring the focused suite to 265 cases across seventeen
targets. Coverage includes exact quota and age boundaries, newest-root selection, pinned transitive
parents, slot collisions, dead sealed/unsealed writers, temporary files, symlink/foreign preservation,
checksum and graph failures, collector/catalog contention, publication during the outside scan,
inventory mutation, interruption, real session reads/writes and process death. One subprocess is
killed after a child's durable unlink; its parent remains discoverable and the next collector
reclaims the orphan. A real writer JVM is killed with private bytes; its pin protects those bytes
while alive and collection reclaims them after termination.

The first twenty core cases passed in 9.201 seconds with one Cache&Carry hit. Integration initially
failed during analysis because the exception class is included in the `idle_task` target rather than
a separate target; correcting that BUILD dependency required no runtime changes. The final integration
build passed all seventeen targets in 20.723 seconds with three Cache&Carry hits. Every source build
and test retains `--config=efcache --bes_backend= --remote_upload_local_results=false`. Immutable
binary and benchmark jar copies are stored on C to preserve the E-hosted WSL VHD's reserve; source,
runtime caches and fixtures remain on WSL ext4. This placement is not used to claim a timing speedup.

The measurement creates separate hard-linked fixtures from the immutable four-publication Datadog
population. The original population and the Step 39 benchmark fixture are never collected or changed.
There are 996,488,246 logical bytes, including 108,467,720 manifest bytes. Each mode runs in three
fresh JVMs with Xms64m/Xmx256m under the exclusive measurement lock. JVM startup is outside reported
collector timings; filesystem caches are not flushed. Pinned mode pins all four publications.

| Mode | Median collection ms | Median catalog ms | Logical bytes unlinked |
| --- | ---: | ---: | ---: |
| retain | 144.916 | 14.656 | 0 |
| pinned | 144.120 | 13.659 | 0 |
| evict | 154.931 | 26.107 | 996,488,246 |

All original file hashes match after the nine runs. The evicted fixtures only unlink their extra
hard links, so this is neither a cold-SSD experiment nor evidence of physical space reclaimed.
The measurements demonstrate moving the expensive checksum scan outside catalog coordination; they
do not establish a whole-build speedup or stress the maximum supported publication count.

The runtime gate runs 22 builds across six phases using two simultaneous cache-enabled Bazel servers
and two independent cache-off controls. Disk and remote action caches are disabled throughout. Both
fresh servers import seven configured targets. Then B is held inside a controlled action after fresh
analysis has pinned shared data. A finishes its own build and its real idle task collects while B's
command is still running: 2 live slots protect 117,765 bytes,
while 1 inactive publication and its pack are removed. Once B finishes,
its idle task removes 2 publications and leaves zero retained known bytes.
Both servers restart and correctly rebuild after eviction, then perform independent edits and reverts.
All outputs match exact expected bytes and cache-off control hashes. Reverts import three targets per
server. Owned servers shut down cleanly. The first runtime attempt failed only because the sandbox
blocked the test synchronization marker; the corrected harness grants write access to that explicit
gate directory and fails promptly if the gated action exits before signalling.

This completes collector implementation and focused concurrent correctness, not the final maturity
gate. Next reuse the existing substantial Datadog worktrees/output bases with this exact binary,
explicitly seed its lease-aware population, compare cache-off/writer/fresh-reader behavior, perform
independent edits/reverts and exercise collection, recording memory, output hashes and invalidation
deadlines. Preserve the host-volume preflight. Remote leases/gRPC and automatic legacy migration stay
future work; no arbitrary network-filesystem safety claim is made.

Evidence: [265 passing cases](skycache-concurrent-2026-10-20/repository-mapping-gc-validator-tests.json),
[collector measurement protocol/results](skycache-concurrent-2026-10-20/collector-measurements.json),
[binary/source hashes](skycache-concurrent-2026-10-20/collector-binary.json),
[two-server idle collection and controls](skycache-concurrent-2026-10-20/collector-lifecycle.json).


## Step 41 — interrupted final workload and verified alternative storage

Harness/guard implementation: [a431a2e9c222](https://github.com/JSGette/bazel/commit/a431a2e9c222b4daf406cf735fdfaf955e14b94e).
The collector binary remains exactly Step 40 SHA `98082ec35a77f6a5eb6356d6bc7aba16d344667225c1dbda1b9b2cb0296753e6`. No Java, Skyframe or cache
semantics changed. No Bazel rebuild was necessary; source compilation/test commands remain configured
for Cache&Carry. Sixteen direct Python checks pass: thirteen capacity/backing checks and three
mid-phase cancellation checks. Their source hashes are saved with the result.

The final large-workload run reused the four completed Datadog worktrees/output bases and placed its
new results in a separate directory. A single baseline writer followed by an independent B reader
avoids two duplicate 430 MB cold packs; later reader/edit/revert phases still run two servers at once.
This was a storage decision, not a replacement for concurrent writing tests: Step 40 already covers
simultaneous writers and collector interactions, and the full earlier Datadog gate covers concurrent
feature publication. The reused fixture has the pinned source and resolved lockfile. No prior logs or
results were overwritten. Cache-off commands now explicitly disable the local SkyCache flag.

Ten builds succeeded before cancellation, all producing the original baseline executable hash. B's
independent reader and both restarted readers imported 4,501 configured targets. The paired restarted
control took 23.731 seconds with 5.643 GiB
peak combined PSS; the restarted reader pair took 27.835 seconds with
7.382 GiB PSS. These single paired observations show working
reuse with overhead; they do not establish an overall speed or memory improvement. Samples include
the two server/client process trees and descendants, exclude filesystem page cache and the probe,
and are taken every 200 ms.

The first no-disk-cache edited phase rebuilt OpenSSL and Python/native dependencies. Host E free
space fell below the unchanged 30 GiB reserve during that phase, although the preflight had passed.
The two precisely identified owned Bazel clients were sent SIGINT; both exited with code 8. Their
partial 6,206-target hit counts are diagnostic only: neither edited executable completed, so this
phase is **not acceptance evidence**. The harness shut down all four owned servers, restored its
source edits, preserved legacy publication hashes and observed zero invalidation deadlines. The
complete attempted run is retained with `passed:false` and cancellation evidence.

The harness now also checks capacity every ten seconds while a phase runs. An unverified or
insufficient capacity observation cancels only that probe's registered live build clients and fails
the phase. Process exit during cancellation is harmless. Finished phases do not trigger cancellation.
The memory sampler remains independent. Before-phase checks and the original reserves remain in force.

To continue without growing the low-space E-hosted WSL VHD, a new task-owned 64 GiB ext4 image was
created exclusively under the C workspace and mounted at
`/home/jgett/REPOS/.work/skycache-concurrent/c-backed-measurements`. Only that new file was formatted;
the Ubuntu VHD was neither compacted nor deliberately shut down. An automatically idling WSL distro
initially lost the temporary mount, so it was remounted and a bounded task-owned helper keeps the
distro alive between tool calls. The helper's PID/start time and stop-file path are recorded locally;
it must be stopped and the temporary mount dealt with after benchmark data is preserved.

The storage checker resolves a loop filesystem through kernel `backing_file` metadata and `findmnt`,
then checks a fresh Windows capacity observation for that actual drive. Unknown/unverifiable mappings
fail closed. Ordinary Linux-backed paths still check the Ubuntu host volume. Mixed write volumes
each require their own backing check. Live evidence proves that the new C-backed ext4 volume passes
while an ordinary write path on the low-space E-backed filesystem is still rejected. Neither the
30 GiB volume floor nor the 10 GiB system-drive reserve was lowered.

The ordinary action cache was copied into the new filesystem in 72.023 seconds; its
original remains untouched. The next run will place its new worktrees, output bases, shared SkyCache,
action cache, logs and samples on this dedicated volume. The harness can copy stopped output bases
into a fresh fixture while excluding server PIDs/sockets and previous diagnostic logs. Repository
downloads can be kept out of the default repository cache. New storage placement requires a complete
matched control/writer/reader/edit/revert run on that placement; do not combine timings across mounts.
The final substantial acceptance gate is still incomplete.

Evidence: [interrupted workload](skycache-concurrent-2026-10-20/collector-datadog-interrupted.json),
[exact command and seed decision](skycache-concurrent-2026-10-20/collector-datadog-interrupted-command.json),
[owned-client cancellation](skycache-concurrent-2026-10-20/collector-datadog-storage-cancellation.json),
[sixteen guard tests](skycache-concurrent-2026-10-20/collector-storage-guard-tests.json),
[actual backing-volume verification](skycache-concurrent-2026-10-20/collector-c-volume-verification.json).


## Step 42 — host observations independent of WSL interop; C scratch reclaimed

Harness implementation: [1b96f8cd4c05](https://github.com/JSGette/bazel/commit/1b96f8cd4c05789c8f62722fc488eef26d3392b0).
The core binary and its 265-case Java evidence are unchanged. This step changes only measurement
storage tooling; source builds/tests continue to use Cache&Carry. Twenty-one direct Python tests pass.

The first C-backed attempt was cancelled during its initial cache-off control pair after
559.750 seconds. The live guard could not obtain a fresh Windows observation
after three WSL interop attempts (`UtilAcceptVsock: accept4 failed 110`). Both owned clients exited 8;
neither produced an accepted executable, no phase passed, and no SkyCache publication was created.
The image still had approximately 40 GiB free. All four servers were shut down, sources restored,
legacy hashes preserved and no invalidation deadline observed. This is a failed infrastructure
attempt, not evidence of cache correctness or performance. Its results and local logs are retained.

The optional `SKYCACHE_WINDOWS_STORAGE_SNAPSHOT` input now accepts an atomically published observation
from a native Windows watcher. The supplied PowerShell helper queries actual drive/distro capacity
every five seconds, has an explicit stop file and a twelve-hour maximum lifetime. WSL rejects files
over 64 KiB, malformed or unknown schemas, missing observations, nonfinite/non-numeric timestamps,
future timestamps, and readings older than thirty seconds. A configured but invalid snapshot does
not silently fall back or disable the guard. Without this option the original fresh interop query
and bounded retries remain. Existing volume mapping and the 30 GiB/10 GiB reserves are unchanged.
New tests exercise freshness and malformed input alongside all existing capacity/cancellation cases.

The first native watcher startup exposed Windows PowerShell's conversion of a null string argument
to an empty path in `File.Replace`; using its explicit `NullString.Value` fixes atomic replacement.
The corrected, committed helper was observed refreshing for 321.4 seconds
with empty stderr and a 0.032-second-old reading at the recorded check. The
watcher's PID/start time, script hash and stop file are recorded privately for cleanup.

D is a healthy fixed NTFS volume with approximately 931 GiB initially free. It is backed by a
TOSHIBA DT01ACA100 SATA disk; C and E are NVMe drives. This is a different storage class, so the
retry is a correctness/concurrency gate with within-run timings, not an SSD performance result. The stopped task image
was normally unmounted and its loop attachment verified absent. Native PowerShell copied only that
64 GiB image to a newly created task directory on D, then checked SHA-256 of both complete files.
Copy time was 362.747 seconds; copy plus verification took
774.923 seconds. Only the verified D copy was enlarged to
128 GiB, checked with e2fsck and resized. It retained its filesystem UUID and original Linux mount
path so existing worktrees and output bases remain valid. The historical directory name still says
`c-backed-measurements`; live kernel backing metadata now proves that it resides on D.

After the D copy mounted and its capacity guard passed, the exact unmounted C image was removed with
native PowerShell. This reclaimed its 64 GiB allocation; observed C free space increased by
64.000 GiB and was 143.261 GiB at
verification. No Ubuntu VHD compaction, WSL shutdown, unrelated file deletion or reduced reserve was
used. The native watcher and bounded WSL keeper remain task-owned and must be stopped after results
are preserved and the D scratch volume is cleaned up.

The retry uses a separate result directory and repeats the entire control/writer/reader/edit/revert/
collection matrix. It accepts zero phases from the interrupted attempt. Before starting, it verifies
the pinned source revision, resolved lockfile, no unexpected tracked edits, stopped servers, empty
SkyCache, immutable binary/jar hashes and unchanged compiled sources. Existing downloaded dependencies
and stopped output bases are retained. The original failed report is linked by path and SHA-256 in
the new protocol. Storage is now D-backed, so timings are compared within this new run only.
The substantial final gate remains incomplete until the retry finishes successfully.

Evidence: [failed C attempt](skycache-concurrent-2026-10-20/collector-c-interop-interrupted.json),
[21 guard tests](skycache-concurrent-2026-10-20/collector-host-observation-tests.json),
[live native watcher](skycache-concurrent-2026-10-20/collector-host-watcher-live.json),
[verified relocation](skycache-concurrent-2026-10-20/collector-d-image-relocation.json),
[D-backed capacity](skycache-concurrent-2026-10-20/collector-d-volume-verification.json),
[C cleanup](skycache-concurrent-2026-10-20/collector-c-image-cleanup.json),
[retry command and decisions](skycache-concurrent-2026-10-20/collector-d-retry-command.json).


## Step 43 — tolerate snapshot replacement races and preserve warm-server measurements

Harness implementation: [0706bac566a2](https://github.com/JSGette/bazel/commit/0706bac566a28b44d973f6929a11e5ea14460b7d).
The SkyCache binary and Java implementation remain unchanged. Twenty-five direct Python guard tests
pass. No Bazel source rebuild was needed; source build/test commands remain configured for Cache&Carry.

The first D-backed control pair was cancelled after 706.467 seconds because
drvfs briefly reported the native Windows snapshot path as missing during replacement. The native
watcher remained alive and the same path immediately became readable again with fresh capacity.
Both clients exited 8, no output was accepted, no phase passed and no SkyCache publication existed.
Peak combined process-tree PSS reached 4.791 GiB.
All servers stopped, sources were restored and zero invalidation timeouts were observed. The failed
attempt remains separate evidence; it is not used to claim correctness or performance.

The reader now opens the file once and reads at most 64 KiB plus one byte, avoiding a stat/read race.
Transient open/read/encoding/JSON errors get three fresh reads with 0.1/0.2-second retry delays.
Persistent failure still rejects the observation after at most 0.3 seconds of requested backoff.
No old capacity value is reused, no interop fallback is silently substituted, and byte/schema/
freshness checks remain mandatory. Tests inject a replacement gap, persistent absence and a stale
observation after a successful retry. All existing host-full, guest-full, wrong-volume and owned-
client cancellation checks still pass.

A live stress check then exposed a separate clock issue: a newly published Windows timestamp was
approximately 53 ms ahead of WSL's clock. Strict rejection of every negative age made a fresh reading
fail. The accepted timestamp window is now one second ahead through thirty seconds old; the stale
limit remains thirty seconds. Nonfinite/non-numeric timestamps, larger future offsets and unknown
schemas fail. Regression checks cover the small skew and both rejection boundaries. This is a bounded
cross-OS clock tolerance, not a waiver of freshness or disk-capacity reserves.

The corrected live stress check completed 11,255 reads across 10
native observations in 45.004 seconds. It recovered 2
actual replacement races using 0.1-second retries, with no rejected observation. The initial failed
clock-skew stress result is retained alongside the successful run.

The audit also found that a 120-second server idle timeout could expire writer A while B prepares
dependencies on the slower scratch disk, mislabelling the following phase as warm. The harness now
exposes `--server-idle-seconds` (default 3600), requires it to cover the independent build timeout,
records the policy in the protocol and checks it on resume. Each build also records whether its log
reports starting a new server. Explicit shutdowns still define fresh-reader/control phases and final
cleanup. No live run was altered in place: the complete matrix restarts in another result directory,
accepting zero phases from either interrupted attempt and retaining their reports/logs.

Resource policy clarification (2026-10-21): the user authorized use of available machine resources
for standalone invocations. WSL currently exposes 32 logical CPUs and 15.44 GiB RAM. Future standalone
source builds or unpaired workload runs can use the available CPU/memory with headroom for the JVM,
OS and other live processes. Source builds/tests continue using Cache&Carry. The running matrix
retains its recorded per-server settings: `--jobs=2`, `--local_resources=memory=3072`, and a 4 GiB JVM
maximum heap. Changing only a writer or reader midway would confound the comparison; paired phases
must use matching policies. The action-memory value is a scheduling budget, not a hard process-RAM
limit. Reported PSS includes native memory and descendants in addition to the Java heap.
See the [recorded decision](skycache-concurrent-2026-10-20/measurement-resource-policy.json).

The requirement audit verified the unchanged binary/source hashes, all 265 focused Java cases and
absence of evaluator implementation changes. It identified a runtime coverage gap for glob membership
additions/deletions/unchanged-content renames. A focused two-cache-server/two-independent-control probe
has been prepared, including repeated edits, restarts and reverts; it remains uncommitted until its
actual runtime checks pass. It will run under the measurement lock after the current Datadog gate,
without modifying the core implementation. Both validation tasks and temporary-D cleanup remain open.

Evidence: [interrupted D controls](skycache-concurrent-2026-10-20/collector-d-snapshot-interrupted.json),
[initial clock-skew failure](skycache-concurrent-2026-10-20/collector-snapshot-clock-skew-failure.json),
[live replacement stress](skycache-concurrent-2026-10-20/collector-snapshot-read-stress.json),
[25 guard tests](skycache-concurrent-2026-10-20/collector-snapshot-race-tests.json),
[full retry command](skycache-concurrent-2026-10-20/collector-d-retry2-command.json).


## Step 44 — file-membership correctness passes; copied output bases invalidate the large fixture

Probe implementation: [46fd1681846e](https://github.com/JSGette/bazel/commit/46fd1681846e7ba6a99109708b773569c89c6b36).
The SkyCache binary and compiled Java sources are unchanged. The new focused runtime probe passes
34 actual builds across two cached worktrees and two independent cache-off controls. Remote and
shared disk action caches are disabled. Every output includes both input names and bytes, making
an unchanged-content rename observable. Repeated same-file edits, additions, renames, deletions,
restarts and reverts all match independently calculated bytes and the controls' output hashes.
The two sides produce distinct outputs for every independent mutation. Each fresh baseline,
membership-edited and reverted reader reports seven configured-target hits and positive shared
reads. Warm phases retain their server PIDs. All servers stop, all source trees are clean after
restoration, final outputs equal the baseline and no invalidation timeout occurs. This small
fixture establishes correctness, not a performance improvement.

The D-backed Datadog retry completes six successful Bazel builds with one common executable hash,
but the sixth fails the stronger reuse gate: the independent reader has zero configured-target
hits and zero admitted candidates, with 182 rejected-publication observations. It publishes its
own result after ordinary evaluation. The run is therefore failed acceptance evidence, despite
correct output. All servers stop, sources restore and no invalidation deadline expires.

Comparing the writer and reader manifests finds identical project namespaces and source digests,
87,811 global witnesses apiece, and 1,111 differing witness keys/digests (241 keys present in both).
The output-base copy optimization preserved absolute symlinks into the earlier E-backed fixture.
The saved `external/_main` digests exactly equal fingerprints calculated for those old workspace
targets; copied Go SDK links still point into the old output bases. After command shutdown the
current `_main` links point to the new worktrees, but those old published bindings are correctly
rejected. This is a fixture-relocation defect; input validation must continue rejecting it.

Next correct the copied fixture preparation, prove the repair and repeat the full substantial
matrix. Preserve this failed run, its publications and the old E fixtures. No Java implementation
change or weaker validation is justified by this evidence. The first-run timings include large
toolchain extraction on a SATA HDD and setup diagnostics; they are not general speed evidence.
The large collection/rebuild gate and temporary-volume cleanup remain incomplete.

Evidence: [34-build membership gate](skycache-concurrent-2026-10-20/membership-runtime-34-builds.json),
[failed Datadog reuse gate](skycache-concurrent-2026-10-20/collector-d-copied-output-rejection.json),
[copied-link diagnosis](skycache-concurrent-2026-10-20/collector-copied-output-diagnosis.json).


## Step 45 — repair copied fixture links while preserving cache validation

Harness implementation: [66e01d60cf04](https://github.com/JSGette/bazel/commit/66e01d60cf04f254c51fded17861d94aa4e3acc0).
The copy step now walks only the copied output directory, without following directory symlinks,
and relocates absolute link targets under the known original workspace/output-base roots. Unrelated
absolute links, relative links and ordinary file bytes remain intact. Source/destination overlap,
a symlinked destination root or a server PID file rejects repair. Each link replacement is atomic.
The helper also runs after future output-base copies and records its exact changes in the protocol.

The stopped real fixtures required 225 / 171 / 579 / 579 link repairs for A / B / control-A /
control-B: 1,554 total. Their second complete scans made zero changes. Repair plus this idempotence
check took 42.664 / 33.435 / 64.875 / 54.947 seconds on the temporary HDD volume. These are preparation
costs, not a cache speedup. Both failed publications and their packed data were retained in a
separate directory; the new matrix starts with an empty SkyCache and accepts zero prior phases.
The original E-backed fixtures remain untouched.

Four focused filesystem regressions cover repository/SDK aliases, preservation of outside/source
links and plain bytes, replacement failure, temporary-name collision, and refusal to modify an
active/aliased/source directory. The collision regression first reproduced deletion of an existing
temporary path. Creation now succeeds before cleanup takes ownership of that path. All 29 Python
guard/harness cases pass. The running matrix had already repaired its fixtures without collisions;
its exact launched source is preserved and hashed separately from this later failure-path hardening.
No running process, compiled SkyCache binary, core Java source, invocation flag or resource budget
changed. Source build/test policy continues to require Cache&Carry; no source rebuild was needed.

The full retry is live. The initial controls are performing substantial disk I/O after relocation;
read-only process observations show continuing reads and heavy kernel I/O pressure without current
memory pressure. Those initial preparation timings are excluded from undisturbed comparisons.
Independent reuse, simultaneous semantic edits, reverts and collection/rebuild still must pass;
this commit does not claim that the substantial acceptance gate is complete.

Evidence: [actual repairs and source hashes](skycache-concurrent-2026-10-20/collector-copied-output-repair.json),
[29 focused checks](skycache-concurrent-2026-10-20/collector-copied-output-tests.json),
[passing test log](skycache-concurrent-2026-10-20/collector-copied-output-tests.log),
[collision regression before the fix](skycache-concurrent-2026-10-20/collector-copied-output-collision-before.log),
[complete retry command](skycache-concurrent-2026-10-20/collector-d-retry3-command.json).


## Step 46 — preserve normal native repository sharing on the guarded volume

Harness implementation: [cdf34f2ee377](https://github.com/JSGette/bazel/commit/cdf34f2ee3779683aada7da10706647a077589bb).
The repaired-link retry completes six successful builds with the same baseline executable hash,
but its independent reader again gets zero analysis hits and zero shared reads. This remains a
failed acceptance gate. Both manifests have the same namespace/source digest and 82,206 global
witnesses each. All 900 differing witness entries (100 present in both manifests) concern Python
`__pycache__` paths. The earlier copied workspace/output-root mismatches are gone. All servers stop,
sources restore and no invalidation deadline expires.

The fixture had disabled `--repository_cache` to keep large downloads away from the nearly full
E volume. In this Bazel version that also disables the native repository contents cache by default.
`rules_python` derives its default writable bytecode-cache location from the workspace path, so
independently materialized Python repositories have different absolute cache links. The original
successful E fixture used one native cached Python repository for both outputs. Conservative input
validation correctly rejects the mismatched independently materialized repositories; it must not
ignore those paths merely to produce hits.

The harness now accepts `--repository-cache PATH`, passes both `--repository_cache` and
`--repo_contents_cache=PATH/contents`, includes that location in initial and live storage checks,
and records/checks it on resume. It rejects combination with `--disable-repository-cache`. A real
negative CLI check rejects the low-capacity default E cache before creating any fixture or launching
Bazel; the explicit D cache passes capacity checks. All 29 focused Python cases pass. An initial
unit run inherited the live snapshot environment and interfered with two tests that mock interop;
the runner now removes that environment variable only for the isolated unit subprocess. Real CLI
and measurement processes retain the live observation. No production guard behavior changed.

Four generated Python repository marker files were preserved, then native Bazel fetches rebuilt
their bindings using the explicit D cache. All four outputs now point to the same native repository,
the same bytecode-cache link and identical `pathlib.py` bytes. The serial fetches took
22.273 / 27.825 / 13.023 / 12.645 seconds for control-A / control-B / A / B. They used all 32 available
CPUs with an 8 GiB action-memory scheduling budget and a 4 GiB maximum JVM heap; all servers shut down.
This prepares dependencies and is not an analysis-cache performance measurement. The normal small
Python bytecode scratch directory remains under WSL `/tmp`; the explicit D path covers repository
downloads and shared repository contents.

Chosen: normal native repository sharing, enabled equally for all four worktrees. Discarded: ignoring
Python witnesses, rewriting their fingerprints, or adding a special shared mutable bytecode-cache
override. The SkyCache binary and Java implementation are unchanged; source builds/tests continue
to use Cache&Carry. Remote action caching remains disabled for Datadog measurement isolation.

The next complete matrix has a new result directory, an empty SkyCache and zero accepted prior
phases. Both sets of failed publications and packed data are preserved. It retains the paired
jobs=2, action-memory=3072 MiB and Xmx4g policy, resolved JDK/lockfile and compiled binary. The full
32-build reuse/edit/revert/collection gate remains pending. D is a SATA HDD; compare matched phases
within this run only. Initial materialization and observation costs are not general speed evidence.

Evidence: [failed reuse gate](skycache-concurrent-2026-10-20/collector-d-python-path-rejection.json),
[900 witness differences](skycache-concurrent-2026-10-20/collector-python-path-diagnosis.json),
[native repository preparation](skycache-concurrent-2026-10-20/collector-native-repository-preparation.json),
[CLI and unit checks](skycache-concurrent-2026-10-20/collector-native-repository-cli-checks.json),
[29-case log](skycache-concurrent-2026-10-20/collector-native-repository-tests.log),
[complete retry command](skycache-concurrent-2026-10-20/collector-d-retry4-command.json).


## Step 47 — actual reuse succeeds, large concurrent edit finalization times out

The explicit shared native repository cache fixes the independent-reuse rejection: the first B
reader and both restarted readers each report 4,501 configured-target hits and produce the exact
control executable hash. Ten builds complete with verified outputs. The complete 32-build gate
does not pass. In the next pair, both independently edited builds report 6,206 analysis hits and
finish 5,704 sandboxed actions each, but remain inside final publication validation. The 1,800-second
deadline and 90-second interrupt grace expire; both clients exit -9 at about 1,890 seconds. Their
outputs are not accepted. Subsequent graceful Bazel shutdown commands also exceed their deadline.

One read-only thread dump identifies `LocalInputSnapshot.hashContents` inside `isUnchanged` and
`LocalCacheSession.finish`. Process I/O counters advance by gigabytes while threads wait on file
reads. This supports slow content revalidation, not a lock deadlock. This phase includes diagnostic
observation and is excluded from undisturbed latency claims. A separate final log scan also finds
one warm-base B invalidation submission timeout: the upstream ten-second deadline invalidates all
4,506 loaded keys. That failure is retained even though the build eventually succeeds.

| Matched restarted pair on D HDD | Wall time | Peak participating process PSS |
|---|---:|---:|
| Cache off | 25.159 s | 5.637 GiB |
| SkyCache readers, 4,501 hits each | 56.625 s | 7.227 GiB |

Resources remain jobs=2, action memory=3072 MiB and Xmx4g per server. PSS samples include the
participating server/client process trees and exclude page cache and host VM overhead. Single-reader
phase measurements exclude the other idle server; use the paired phases for matched RAM comparisons.
No SSD or general speed claim is supported.

Recovery verifies exact task output-base command arguments and process start identities, then sends
SIGTERM through pidfds to the two remaining JVMs. Both stop without SIGKILL. Only the known artificial
Go edits are restored, after preserving their bytes. The copied MODULE lockfile remains intentional.
The original failed report is not rewritten as successful, and the cache/logs remain preserved.
Chosen: investigate and reduce redundant I/O. Discarded: accepting action success as command success,
ignoring content witnesses, or merely extending the deadline to hide the failure.

Evidence: [failed large gate and recovery summary](skycache-concurrent-2026-10-20/collector-d-retry4-timeout.json).

## Step 48 — check inherited publication inputs once

Implementation: [44cb15ede3fd](https://github.com/JSGette/bazel/commit/44cb15ede3fd8c7f61636b3b33d75b270d5ae07d).
`finish` previously read each distinct used-parent witness list in `include`, then read the union
again in `isUnchanged`. Two overlapping unequal lists therefore hash common inputs three times at
publication. Registering every used parent's exact evidence before the existing fresh final pass
checks the union once. Registration does not authorize reads. Admission, dependency validation,
source-tree/namespace checks, conflict detection and all parent IDs remain in place. Final content
mismatches return false with a failure reason; no manifest is published. No evaluator code changes.

The added regressions cover both parents' unique inputs, shared input mutation with unchanged size
and mtime, directory membership, and conflicting inherited observations. All 267 Java cases across
17 targets pass; the 62-case session suite passes. Source build/tests use Cache&Carry with remote
uploads disabled, jobs=32 and a 10 GiB action-memory budget plus Xmx4g. The final build reports five
remote cache hits and completes in 15.809 seconds.

The dedicated benchmark uses two overlapping unequal parent lists, 4,098 unique 16 KiB files,
two warmups and five measured rounds per revision on the same D-backed ext4 volume. It validates
actual parent reads and successful child publication. Median publication measurements:

| Metric | Before | After |
|---|---:|---:|
| Wall time | 405.392 ms | 345.917 ms |
| Logical bytes read | 201,392,510 | 67,142,270 |
| Read syscalls | 24,587 | 8,200 |
| Measured-thread allocation | 29,197,368 B | 13,618,648 B |

All measured rounds have zero physical reads: this is a warm-filesystem publication microbenchmark,
not cold-HDD or whole-build performance. The reduction is about 67% in logical bytes, 53% in measured
allocation and 15% in median latency. No statistical confidence interval is claimed. Cold input
hashing, warm invalidation latency and substantial concurrent recovery still require investigation
and runtime proof. Native repository sharing remains enabled; remote action caching stays off in
Datadog measurements.

Evidence: [all rounds, source hashes and test cases](skycache-concurrent-2026-10-20/publication-final-validation-once.json),
[full focused-suite build log](skycache-concurrent-2026-10-20/publication-validation-full-build.log).


## Step 49 — hash large aliased files once per fresh validation pass

Implementation: [258febe36adf](https://github.com/JSGette/bazel/commit/258febe36adf692149ee7346d2d03972a460c113).
A read-only profile of the failed Datadog publication finds 84,846 witnesses, including 45,010
regular-file paths. One full pass encounters 6,877,395,176 file bytes but only 2,677,114,482 bytes
across distinct device/inode identities. Large LLVM and Python binaries account for much of the
duplication: five Clang names resolve to one 223,765,784-byte file, five linker names to one
140,517,584-byte file, with similar library and Python interpreter aliases. These counts diagnose
duplicate reads; missing-path witnesses are expected evidence too and are not discarded.

File fingerprints now incorporate a SHA-256 content digest. One validation/capture pass may reuse
that digest for a large file only after fresh Unix device/inode, ctime, mtime and size observations
match the version already read in that same pass. It checks the version again before accepting the
digest. Every path, file kind, permission, symlink target and directory listing is still evaluated.
A new admission scan, final publication check or command starts with no retained content digests.
Providers lacking Unix identity/change time always reread content. Small files are hashed directly
without extra version metadata calls. The default memo retains at most 256 digests for files at
least 1 MiB; overflow rereads bytes. Scratch buffers remain bounded to 1 MiB per process.

This keeps the existing stable-inputs-during-command assumption. It does not claim an atomic
filesystem snapshot or protection against a privileged actor forging filesystem metadata.
Chosen: a bounded memo within a single fresh pass, guarded by change time and identity. Discarded:
cross-command timestamp caches, mtime/size-only reuse, ignoring aliases or caching large file bytes
in RAM. Hash loops now honor an already-set thread interrupt and preserve that flag; this alone is
not a claim that Bazel's earlier SIGINT/shutdown behavior has been solved.

The fingerprint representation changes deliberately. The project namespace advances to
`local-project-v3-content-sha256`, and the source digest domain to `local-source-v11-content-sha256`.
Old publications remain untouched in their old namespace; mixed versions cannot consume each
other's input evidence. Rolling back selects the old namespace. A new version initially has a
cold analysis cache. The packed data format and collection lease protocol are unchanged.

Ten focused hasher regressions cover symbolic/hardlink reuse, distinct files, changed bytes with
restored size/mtime, replacement inode, symlink retargeting, fresh passes, bounded capacity, small
and empty files, missing files and interruption. All 277 Java cases across 18 targets pass. The
source build used Cache&Carry, jobs=32, 10 GiB action memory and Xmx4g, reported three remote cache
hits and took 15.762 seconds. No evaluator implementation changed.

The alias benchmark validates two distinct 32 MiB inputs plus eight mixed symbolic/hardlink aliases
through the complete final witness check. Two warmups precede five measured rounds per revision:

| Metric | Before | After |
|---|---:|---:|
| Median wall time | 137.512 ms | 30.333 ms |
| Median logical bytes read | 335,544,443 | 67,108,986 |
| Median read syscalls | 5,132 | 1,028 |

Logical reads fall about 80% and median latency about 78%. All measured rounds have zero physical
reads; these are warm-filesystem microbenchmarks without statistical confidence intervals, not a
Datadog or cold-disk speedup. The final substantial acceptance matrix and fresh runtime membership
coverage remain pending on this new binary. The old failed run and its bytes remain preserved.

Evidence: [profile, all rounds, source hashes and 277 cases](skycache-concurrent-2026-10-20/input-alias-validation.json),
[full focused-suite log](skycache-concurrent-2026-10-20/input-alias-validation-full-build.log).


## Step 50 — current content-hashing binary passes 34 runtime correctness builds

The binary from Step 49 passes a fresh complete two-worktree membership gate: initial concurrent
writers, restarted readers, two rounds of distinct semantic edits, additions, unchanged-content
renames, deletions, fresh membership readers, warm reverts and restarted reverts. All 34 outputs
match their expected bytes and independent cache-off controls. Each fresh reader pair reports
seven actual configured-target hits and positive shared reads. Warm phases preserve the recorded
server PIDs. Remote and shared disk action caches are off throughout this small correctness fixture.
There are no invalidation timeouts, publication failures or disabled-cache fallbacks. All servers
stop and source trees restore cleanly. This fixture provides correctness coverage, not a latency
claim.

The next substantial Datadog matrix uses the current immutable binary with an empty SkyCache and
zero accepted previous phases. The prior timed-out publication and private packs remain preserved
in `cache-final-validation-timeout`; the original failed report is unchanged. Ordinary repository
sharing, resolved dependencies, paired jobs=2/action-memory=3072 MiB/Xmx4g limits, 1,800-second build
deadline and actual-hit/output/collection checks remain the same. The new matrix is running; no
claim that the previous large-workload deadline failures are fixed is made yet.

Evidence: [all 34 builds and independent verification](skycache-concurrent-2026-10-20/membership-alias-runtime-34-builds.json).


## Step 51 — repeated warm invalidation timeout and explicit local validation

The next full attempt with both input hashing optimizations produced eight correct outputs, then
failed the warm-reader acceptance condition. Its first independent reader had 4,501 configured-target
hits and the exact cache-off executable hash. On the following unchanged command, B exceeded the
upstream 10-second request-submission deadline and invalidated all 4,506 imported entries. It later
rebuilt successfully with 4,501 hits again. Correct output does not make that warm reuse behavior a
passing result. The warm pair took 140.452 seconds; A took 29.229 seconds and B took 137.126 seconds.

The remaining restarted-reader clients were deliberately interrupted once the failure was established.
Both returned exit 8; these are cancelled observations, not new command-deadline failures. All four
servers stopped normally and sources restored. No thread dump was taken: the warm server had already
stopped before diagnostic identity validation, so the attempted diagnostic made no changes. The failed
report is immutable, and its publication/private data remain in `cache-invalidation-timeout`.

The source diagnosis is a contract mismatch. `LocalAnalysisCacheClient` performs real synchronous
filesystem/dependency validation before returning its future. The upstream invalidator submits those
calls in batches and waits at most ten seconds for submission. Slow local work can therefore keep
running while the caller discards and reconstructs the same in-memory graph.

Chosen: an explicit synchronous retained-validation interface, called interruptibly on the command
thread at the same invalidation stage. It checks the original imported value's evidence, uses the
existing invocation-local bounds, and includes the entire validation cost in reported latency.
The remote submission/response deadlines stay unchanged. This gives local validation command-lifetime
semantics; it is not a claim that filesystem reads became faster. Command cancellation and the
1,800-second benchmark command limit remain in force.

Discarded: increasing the remote timeout, merely moving the same work into a future, skipping content
witnesses, keeping timestamp-only validation across commands, and prevalidating in an earlier module
phase where repository/input ordering differs. The evaluator and full in-memory graph design stay
unchanged. Sequential local key fingerprinting may add CPU work and must be measured.

The candidate has three new regression cases: all three fail against the old dispatcher; all 280 Java
cases across 18 targets pass after the routing fix, including the existing remote timeout test. A fresh
34-build worktree mutation matrix also passes with exact control outputs, positive fresh-reader hits,
restored sources and stopped servers. Source builds/tests use Cache&Carry. The source fix remains
uncommitted pending the substantial warm retention/latency observation; no before/after Datadog speedup
is claimed. A new full matrix starts with an empty SkyCache and zero accepted prior phases. Its warm
reader must explicitly invalidate zero unchanged imported keys, in addition to matching outputs and
avoiding timeout warnings.

Evidence: [failed run, regression provenance and candidate checks](skycache-concurrent-2026-10-20/invalidation-timeout-retry5.json).


## Step 52 — command-lifetime local validation preserves the warm imported graph

Implementation `802d66bc21bd706a20dc5476791d305020363303` is pushed on `jsgette/skycache-concurrent`.
The binary passes 280 Java cases across 18 targets and the fresh 34-build worktree mutation gate;
source builds and tests use Cache&Carry. No evaluator implementation changes were added. The full
32-build Datadog matrix is still running, so this records one verified issue fix, not final acceptance.

The first independent reader downloaded 4,501 configured targets and produced the cache-off executable
hash. Its next unchanged command kept 4,501 of 4,506 scanned graph keys, rejected zero dependency roots,
and downloaded zero configured targets. Validation took 49.06 seconds, fully charged to the command;
the complete warm B command took 85.081 seconds. The previous binary had hit the remote submission
deadline, discarded all 4,506 keys and downloaded 4,501 targets again; that command took 137.126 seconds.
These are separate ordered HDD observations, not an isolated or statistical speedup estimate.

The strict zero-invalidation expectation introduced in Step 51 did **not** pass: five extra graph keys
were invalidated. That expectation conflated directly imported keys with every graph key holding a
deserialized value. `ConfiguredTargetFunction` can forward a value from a transitioned key without
another download. `TargetAndConfigurationProducer` passes the destination value through unchanged,
including its `DeserializedSkyValue` marker. The invalidation scan therefore sees the forwarding key,
while `LocalAnalysisCacheClient` records original roots only for direct successful lookups.

The five labels were `@@platforms//os:windows`, `//packages/agent:windows_x86_64_fips`, `//:is_release`,
`//cmd/cluster-agent:cluster-agent`, and `//:windows_x86_64`, all under the same original configuration.
The warm dependency counters exactly match the first reader: 162,322 records, 7,798 checked witnesses,
and zero rejected roots. The five reevaluations caused five misses and zero downloaded targets.
This supports the forwarding explanation, but the exact key-to-destination mappings were not captured
from the live heap. The public JSON preserves source locations, counts and this limitation.

Chosen correction: require retained directly imported values to validate against their original
evidence, zero dependency-root rejection for this unchanged fixture, zero reload hits, and exact
control output. Preserve conservative invalidation for additional keys without original evidence.
The final audit checks this run's exact five keys/log hashes and 4,501 retained entries. The original
zero expectation and its failure remain documented. Accepting unknown evidence just to reach zero
was rejected. Explicit evidence tracking for forwarding aliases is a possible later optimization,
not required for safe sharing and not introduced in the evaluator.

Both subsequently restarted readers also completed successfully, each with 4,501 hits and exact
baseline output. Their pair took 44.773 seconds and peaked at 7.644 GiB participating process PSS;
the matched cache-off restarted pair took 24.718 seconds and 5.832 GiB. This observation does not show
a whole-build performance win. Content admission and validation still cost I/O, and process PSS
excludes page cache and host VM overhead. Concurrent edited execution, reverts, forced collection,
final evidence archival and cleanup remain pending.

Evidence: [retention measurement, criterion correction and source provenance](skycache-concurrent-2026-10-20/local-retained-validation.json).


## Step 53 — concurrent edited execution succeeds, final publication exceeds the deadline

The current 280-case binary completed ten baseline/control/reader output checks, including the warm
retention result in Step 52, then failed the first edited pair's full-command limit. Both edited
builds reported 6,206 configured-target hits and completed 6,789 actions (5,704 sandboxed, 1,085
internal) with the disk action cache disabled. Bazel's execution summaries were 1,242.640 seconds
for A and 1,277.797 for B. Neither command completed publication before the 1,800-second deadline.
Both clients returned 124; both publications were skipped with `interrupted local input hashing`.
The additional post-execution waits were at least 557.360 and 522.203 seconds. Successful executable
construction does not make these passing full builds, and no independent edited-control comparison
was completed in this attempt.

All four servers stopped normally and sources restored. There were zero invalidation timeouts:
the retained-value fix remains supported by its focused and warm-runtime evidence. The full matrix
is nevertheless failed. Its immutable report, logs and cache data remain preserved. No deadline was
increased, no input checks were skipped, and no thread dump altered this measurement. Lightweight
`/proc` observations showed advancing reads, little CPU use and roughly 89–90% I/O pressure. One
observed final-validation descriptor was reading the 199.5 MB LLVM debugger library.

The bounded digest cache was investigated before editing code. The saved baseline manifest has
84,846 witnesses, 153 large-file paths and only 88 distinct large-file versions, totaling about
2.19 GB of unique large-file bytes. Source traversal adds 27 distinct large files. This inventory
does not support exhaustion of the 256-entry per-pass cache; raising the limit was rejected. The
inventory is metadata evidence, not the exact edited command's access order or an executed pass.

A bounded diagnostic read and SHA-256 hashed the same 64 MiB prefix through three paths, with file-
scoped guest-cache eviction and reversed device/buffer order. The HDD-backed ext4 image took
1.80–1.84 seconds; direct HDD files took 0.26–0.38 seconds; native WSL files took 0.03–0.07 seconds.
Changing from 64 KiB to 1 MiB reads barely affected the image path. Host cache and physical placement
were uncontrolled, so these are storage diagnostics, not cold-device or whole-build speedup claims.
No global cache drop, system tuning, C-drive image creation or WSL restart was performed.

The repository inventory exposed a material setup difference. Each original native output base
has 497 directory symlinks, while each HDD output base has only three. The original fixture occupies
about 13 GiB; the HDD fixture about 67 GiB. The earlier cache-disabled preparation materialized
repositories privately, and reenabling native cache flags did not replace still-valid directories.
The prior Python-only repair restored its shared binding but left the other private toolchains.
Thus “native repository sharing enabled” described configuration, not actual sharing of every repo.

Chosen next step: restore normal contents-cache sharing for the repositories that were shared in
the original fixture. Preserve generated marker bytes, seed exact native cache candidates where
available, and let Bazel validate/fetch them normally for all four worktrees. Do not manually rewrite
input fingerprints or Python cache paths. The original native fixture remains untouched, and the
new matrix must start with an empty SkyCache. Keep the current binary, resources, cache policies and
1,800-second deadline. The native WSL host drive has only about 28 GiB free, below the existing 30 GiB
guard, so a new native fixture or a reduced storage guard was not selected.

Evidence: [failed run and storage/sharing diagnostics](skycache-concurrent-2026-10-20/final-publication-timeout-retry6.json).


## Step 54 — native repository sharing restored; final validation restarted

The restoration passed for all four stopped output bases. Each normal Bazel fetch requested 494
repositories, accepted all 494 seeded native candidates and installed directory symlinks. Every
selected repository resolves to the identical contents-cache directory across the four outputs.
Fetches took 83.483, 74.754, 68.126 and 65.748 seconds for control A, control B, A and B. All exited
successfully, storage guards stayed clear and all servers stopped. The already shared Python
distribution and its bytecode-cache binding were independently rechecked before the next run.

The preparation first preserved and readback-verified the failed run in a private archive, including
logs, BEP, publication bytes and all 1,976 generated marker files. Exact native cache entries were
seeded under a shared Java-compatible repository GC lock. Bazel performed validation and rebinding;
no input fingerprints were rewritten. The original native fixture was unchanged. SkyCache was off
during these fetches. Guest free space rose from about 41 GiB after seeding to about 87 GiB after
private generated repositories were replaced. This is storage preparation, not a product speedup.

Retry7 starts with an empty SkyCache and repeats the complete 32-build matrix. It accepts no prior
phases or publications, retains the same committed binary and fixes the same resources, cache
policies and 1,800-second deadline. Previous failed runs remain separate. The current 280-case suite
and 34-build membership matrix still cover this unchanged binary; no redundant source build was
performed. Cache&Carry remains required for source builds/tests.

The user requested a wrap-up once there is a working implementation. The stopping point is the
large concurrent edit/revert/collection validation plus evidence archival, owned scratch cleanup
and a concise implementation/limitations handoff. Further optimization, SSD characterization and
gRPC are future work. No full retry7 result or overall speedup is claimed at this preparation step.

Evidence: [native repository preparation](skycache-concurrent-2026-10-20/native-repository-restoration.json).


## Step 55 — user caps the final large validation at 16 builds

The user explicitly requested “Shrink validation to 16 builds.” This supersedes the32-build
acceptance schedule, and already completed builds count toward the cap. The unchanged measured
Bazel binary remains the280-case implementation. Only the Python measurement harness changes:
`--validation-build-limit=16` selects the shorter schedule and checks the cumulative invocation count
before every new phase. Resumed rows are validated rather than rerun. The original32-build mode
remains available. The harness/storage checks pass31 cases, including refusing a17th invocation and
refusing a pair that would cross the limit. No Bazel rebuild is needed for this harness-only change.

The selected16 builds retain two baseline controls, two restarted baseline controls already run,
one writer, one independent reader, two warm builds, two simultaneous feature edits, two warm
reverts, two independent edited controls with the disk action cache disabled, and a writer plus
independent reader after forced collection. The additional repeated restart/re-edit checks and
post-collection concurrent reader pair are omitted. The separate34-build membership matrix was
already complete; it is neither repeated nor counted as new work under this cap.

The coordinator switched schedules after the completed warm phase's result and memory samples were
saved. All eight successful commands and their timings were preserved, and the resumed capped
harness revalidated those rows. No running Bazel build was cancelled or rerun; no omitted restart
phase had started. The old coordinator's SIGTERM reflects this operator-requested boundary, not a
Bazel command failure. The terminal audit still requires exactly the selected16 builds; it remains
pending, and no full pass is claimed.

The final-run warm audit independently verified4,501 retained graph keys out of4,506, five unknown
forwarding keys conservatively invalidated, zero dependency-root rejections and zero configured-target
reloads. Warm B took154.002seconds, including approximately129seconds of validation (Bazel displayed
2.150minutes). The audit parser was corrected to accept Bazel's minute unit; no product behavior or
acceptance condition changed. This is correct retention with significant validation overhead, not
an established performance win.

The final matched performance table will compare the concurrent edited pair with its independent
cache-off controls. A fresh restarted-baseline cache/control ratio is absent from this reduced run;
older measurements remain historical evidence. The unchanged binary, resource settings, source
checks and1,800-second per-command deadline are retained.

Evidence: [scope, phase counts and harness checks](skycache-concurrent-2026-10-20/compact-validation-scope.json).


## Step 56 — retain two failed attempts; use only six remaining builds

Retry7 stopped with **eight passing commands and two timed-out commands**. Each edited command
imported 6,206 configured targets and executed 6,789 actions. Execution summaries were 1,250.610
and 1,201.947 seconds; at least 549.507 and 598.169 further seconds elapsed before the unchanged
1,800-second command deadline interrupted final input hashing. Neither edited publication completed.
The executable files left after cancellation have distinct hashes, but they are diagnostic artifacts,
not successful full-command results. All servers stopped, sources were restored and no invalidation
timeout occurred. Native sharing restoration did not resolve this HDD-backed finalization failure.

The user cap includes these failures: **10 attempted, six remaining, never more than16**. Preserve
the original failed report and private readback-verified archive. The last six will test simultaneous
edited reads/publications, warm reverts and independent edited controls. The large post-collection
writer/reader pair is omitted to respect the cap. Existing focused lifecycle tests and earlier real
server collection evidence remain separate; do not describe the reduced run as fresh post-GC proof.

The next physical-storage intervention copies only repository contents (about11.25GiB) to a new
temporary64GiB NVMe-backed ext4 image on C. A checksum comparison precedes a bind mount at the
same logical contents-cache path; the original D directory remains underneath for restoration.
This reconsiders our earlier operator choice to avoid another C image: current free capacity allows
the smaller image while retaining the unchanged reserves. A full128GiB image would not. Both the
C child filesystem and D output filesystem must be monitored. The temporary C image is removed
at final cleanup. No binary, fingerprint, content-validation, timeout or resource change is made.

These last commands are correctness checks with timing observations. Earlier failed edited commands
already warmed their local action outputs, so comparing them against independent controls cannot
establish a fair speedup. Keep the HDD and NVMe-input cohorts separate. The best possible capped
outcome is14 passing commands plus two recorded failures, not16/16 passing. No more full-matrix
restarts are authorized within this cap. Once the scoped checks finish, preserve evidence and
publish the implementation handoff, remaining performance issues and missing validation.

Evidence: [retry7 failure and budget decision](skycache-concurrent-2026-10-20/final-publication-timeout-retry7.json).


## Step 57 — capped validation and local prototype handoff

**16 attempts: 14 passed and two timed out.** The final six correctness builds passed after
repository inputs moved to NVMe while retaining their logical paths. Both edited servers had
real shared analysis reads and published successfully; each distinct edited executable matched
its independent cache-off control. Warm reverts matched the original baseline. Raw BEP timestamps
confirm actual overlap in every successful paired phase. Sources were restored and servers stopped.

Pairs show separate A/B analysis-hit counts; single-server phases show one count.

| Storage cohort | Phase | Pair/command wall time (s) | Peak combined PSS (GiB) | Analysis hits (A/B) | Result |
|---|---|---:|---:|---|---|
| HDD | `control-base` | 316.74 | 3.847 | 0/0 | passed |
| HDD | `restarted-control-base` | 23.98 | 5.766 | 0/0 | passed |
| HDD | `single-base-writer` | 921.69 | 3.504 | 0 | passed |
| HDD | `first-independent-reader` | 568.37 | 3.141 | 4501 | passed |
| HDD | `warm-base` | 157.33 | 5.811 | 0/0 | passed |
| HDD | `first-cross-revision-readers` | 1800.14 | 11.098 | 6206/6206 | timed out |
| NVMe-inputs | `final-concurrent-edited-publication` | 1398.16 | 7.197 | 6206/6206 | passed |
| NVMe-inputs | `final-warm-reverts` | 198.77 | 7.678 | 3/3 | passed |
| NVMe-inputs | `final-independent-edited-controls` | 1182.38 | 8.028 | 0/0 | passed |

The two HDD failures remain failures: execution completed, but final input hashing exceeded
the 1,800-second deadline and publication did not complete. Native repository sharing restoration
alone had not resolved this. The final storage change does not modify product code or validation.

No matched speedup is claimed: earlier failed commands warmed local outputs, storage differs
between cohorts and these are single ordered observations. Process memory excludes page cache
and host VM overhead. The large post-GC rebuild pair was omitted to respect the cap; focused
collector tests and earlier lifecycle evidence are separate. Only repository inputs moved to
NVMe; output bases and SkyCache data stayed on the SATA HDD. Both use WSL ext4 loop images
backed by Windows files, so this does not characterize native SSD-backed ext4.
Source builds/tests used Cache&Carry.

The private raw-evidence archive preserves 2,534 files, including failed
attempts and the successful final checks. Every archived file was read back against its SHA-256.
Raw BEP/JVM/environment data remains private. Archive SHA-256: `5fb50a263da2800933bc04b880842d665ea578455d8034580f3e8e2b383d1df7`.

The four owned final Datadog worktrees were unregistered. The temporary 64 GiB C image and 128 GiB D
image were normally unmounted, detached and removed. The original native fixtures and user
repositories were preserved. The earlier full C image had already been removed. Task helpers
were verified stopped after cleanup.

This closes the requested local prototype handoff within the tested scope. It does not establish
production readiness, a general performance improvement or a passing 16/16 run. The validation
omissions and product limitations above remain explicit follow-up work.

See the [implementation summary](../skycache-implementation-summary.md), [runtime data](skycache-concurrent-2026-10-20/final-runtime-summary.json) and [scoped acceptance](skycache-concurrent-2026-10-20/final-local-acceptance.json).

## Step 58 — two full-NVMe performance observations (2026-10-21)

The user authorized one or two additional tests after the capped handoff and requested more CPU
and RAM. Exactly two additional build commands ran; the original 16-attempt record is unchanged.
No implementation code changed and no source build was needed. The current binary is the same
`0533d78f6958327217e27338ffb4d3f2b7442eb6c91c8329fd6cfddde2e77ee0` artifact built with Cache&Carry.

Windows disk/partition and WSL registry inspection identifies E: as the **WD Blue SN570 2TB NVMe**
and the backing location of Ubuntu's ext4 VHDX. Sources, both output bases, repository contents,
toolchains, action cache, SkyCache, binary and raw reports were on native WSL ext4 on that SSD.
There was no HDD or additional Windows-file-backed loop image in these build paths.

The earlier HDD placement was a capacity workaround for the larger multi-worktree matrix,
not a suitable performance baseline. E: had 28.01 GiB host space free at this follow-up. Reusing
two stopped existing output bases avoided large copies; a 15 GiB host reserve was checked every
five seconds. C: retained a 10 GiB reserve. No storage guard tripped and E: host free space did
not fall during the runs. Existing fixtures were retained; both servers were stopped afterward.

Both builds ran sequentially with **32 jobs, Xmx6g and 6144 MiB action-memory budget**. WSL exposes
32 CPUs and about 15 GiB RAM, so this leaves headroom rather than scheduling two such builds at
once. The target remains `//cmd/cluster-agent:cluster-agent` at Datadog revision
`0cde96beb70963d0838119c308b9d0422ee66be0`. Remote action caching was disabled. Existing output
artifacts and the native SSD disk action cache were warm. Each independent server started with
an empty in-memory graph. The first build used an empty SkyCache; the second read its publication.

| Metric | Fresh publication | Independent reader |
|---|---:|---:|
| Full command wall time | 33.901 s | 27.762 s |
| Bazel analysis phase | 8.211 s | 7.451 s |
| Bazel execution phase | 1.822 s | 4.201 s |
| Bazel reported CPU time | 242.840 CPU-s | 155.380 CPU-s |
| Peak process-tree PSS | 4.780 GiB | 5.139 GiB |
| Configured-target analysis hits | 0 | 4,501 |
| Newly configured targets, BEP | 57,369 | 14 |
| Post-invocation graph nodes | 320,087 | 234,712 |
| Local action-cache hits | 4,489 | 4,538 |
| Disk action-cache hits | 1,287 | 1,197 |
| Internal actions | 1,013 | 1,054 |
| Exit code | 0 | 0 |

The writer published 443,700 records. The reader made 317,295 shared reads, rejected zero
dependency roots and deduplicated all 4,477 puts, so it correctly created no redundant publication.
Both executables have the exact baseline SHA-256
`7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`.
Source diffs remained unchanged. Memory is sampled every 200 ms and excludes page cache and VM
overhead. The reader retained fewer graph nodes but had a higher sampled peak PSS; this is not a
RAM reduction result.

The original two-build harness mistakenly required the reader to publish new records even when
all writes were deduplicated. Both build commands exited successfully, but that final assertion
failed. The raw report is preserved unchanged. A separate offline audit verified BEP success,
binary and artifact hashes, memory maxima, source restoration, stopped servers and the existing
`LocalCacheSession.finish()` no-write behavior. No third build or product change was needed.

These observations are much shorter than the earlier HDD writer/reader commands (921.695 and
568.366 seconds), but storage, filesystem layering, CPU/memory limits, fixture state and local
cache hits differ. They do not isolate the SSD's effect. Likewise 27.762 seconds versus 33.901
seconds compares reading with creating a publication; it is **not** a cache-on versus cache-off
speedup. One observation per mode and unflushed filesystem caches do not establish a distribution
or sustained throughput. Most action work was cached; this is a substantial analysis workload,
not a clean compilation throughput benchmark. Warm in-memory invalidation, concurrent performance
and the omitted large post-GC cases were not retested.

The remaining performance question is whether a reader beats an equivalent restarted cache-off
server with matched action-output state and resources. That still requires a matched
comparison beyond these two builds. The two-build budget is exhausted.

Raw logs, BEP, profiles, memory samples, exact commands, hardware mapping and the original harness
are preserved privately in a readback-verified archive. Archive SHA-256:
`508707d950fbd5cfdaab1ca2c313e29360d488b1aef231dccd0d63dcf207939d`.
The [curated data and audit](skycache-concurrent-2026-10-20/nvme-two-builds-2026-10-21.json)
contain per-phase metrics and evidence hashes.

## Step 59 — two cache-off baselines on the same NVMe (2026-10-21)

The user requested two vanilla Bazel measurements and comparison with SkyCache. Exactly two
additional build commands ran, both successfully. The baseline here means **the identical fork
binary with local and remote analysis caching explicitly disabled**, not a separately built
unmodified upstream Bazel release. This holds installation/version identity constant while testing
the ordinary analysis path. BEP canonical options confirm the disabled modes. It does not measure
whether other fork changes affect the performance of a stock upstream binary.

Both baseline servers started with empty in-memory graphs, sequentially, in the existing
`control-a` and `control-b` fixtures on E: WD Blue SN570 2TB NVMe. Sources, outputs, repositories,
toolchains, disk action cache and profiles remained on native WSL ext4. Resources match Step 58:
32 jobs, Xmx6g, 6144 MiB action memory. Datadog revision, target, binary hash, resolved lockfile,
remote-cache policy and repository bindings match; only the worktree's own `_main` binding differs.
The same 15 GiB host reserve was monitored. There were no source changes, storage interruptions,
new SkyCache statistics or analysis-cache reads. Both servers were stopped afterward.

| Metric | Cache off A | Cache off B | SkyCache writer (Step 58) | SkyCache reader (Step 58) |
|---|---:|---:|---:|---:|
| Full command wall time | 20.471 s | 18.829 s | 33.901 s | 27.762 s |
| Analysis phase | 5.505 s | 4.972 s | 8.211 s | 7.451 s |
| Execution phase | 1.656 s | 1.207 s | 1.822 s | 4.201 s |
| Bazel reported CPU time | 144.680 CPU-s | 137.940 CPU-s | 242.840 CPU-s | 155.380 CPU-s |
| Peak process-tree PSS | 3.823 GiB | 2.518 GiB | 4.780 GiB | 5.139 GiB |
| Configured-target analysis hits | 0 | 0 | 0 | 4,501 |
| Newly configured targets | 57,369 | 57,369 | 57,369 | 14 |
| Local action-cache hits | 6,788 | 6,788 | 4,489 | 4,538 |
| Disk action-cache hits | 0 | 0 | 1,287 | 1,197 |
| Internal actions | 1 | 1 | 1,013 | 1,054 |

The baseline mean is **19.650 seconds**. The observed reader took **41.3% longer** and the writer
**72.5% longer**. Reader analysis took **42.2% longer** than the 5.239-second baseline mean analysis
phase. Reader CPU time was about 10.0% above the baseline mean. These are descriptive ratios of
these observations, not estimates of a general regression distribution.

All four output hashes are identical to the original baseline:
`7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`.
The reader's 4,501 configured-target hits and reduction to 14 newly configured targets are real,
but importing and validating those values did not beat recomputation here. Its sampled peak PSS
also exceeded both baselines. The 3.823/2.518 GiB variation between baseline peaks illustrates
allocation/GC sensitivity; memory is sampled process-tree PSS, not a total-system memory bound.

There is now an on/off observation at matching resources and storage, but **initial action-output
state was not matched**: both controls reused 6,788 actions locally while the reader restored
1,197 actions from the disk cache and ran 1,054 internal actions. The baseline commands ran later;
filesystem caches were not flushed. Their wall-time advantage therefore cannot be assigned wholly
to the SkyCache implementation. The separately reported analysis timings are also single ordered
observations, not a randomized repeated experiment. No clean compilation, concurrent comparison,
warm in-memory invalidation or post-GC benchmark was added.

The existing profiles show `LocalSkycacheModule.beforeCommand` taking **4.161 seconds** in the
reader and **0.002/0.001 seconds** in the two controls. That identifies measurable cache setup
cost before analysis. Together with slower analysis despite hits, this points to input capture,
validation and deserialization as the next costs to isolate. These measurements do not apportion
all remaining time between those mechanisms. No correctness checks were weakened and no product
code was changed.

The result for this measured workload is negative: **the current implementation has not shown a
speed or peak-memory advantage over the cache-off path, even with full NVMe storage**. The next
performance experiment should match initial action-output state and repeat the on/off comparison;
optimization should target measured setup/validation/import cost. This two-build request is complete.

The [curated comparison](skycache-concurrent-2026-10-20/nvme-cache-off-comparison-2026-10-21.json)
contains source/binary identity, every reported phase and evidence hashes. Raw BEP, profiles,
logs, commands, hardware mapping, memory samples and the harness remain private in a
readback-verified archive, SHA-256
`725b474357e4ae24892c59e5207eda2840308c03d9c1a25cb660a27a111a2dd0`.
Step 58 and the original capped 16-attempt reports remain unchanged.

## Step 60 — source and saved-profile performance diagnosis (2026-10-21)

No new builds, implementation edits or benchmark retries. Verified hashes of all four saved
profiles, BEP files and logs, traced the local cache source, and enumerated the current source-tree
footprint without content hashing. The full
[diagnosis](../skycache-performance-diagnosis-2026-10-21.md) distinguishes direct timings,
source-proven work and unmeasured attribution. The
[curated data](skycache-concurrent-2026-10-20/performance-diagnosis-2026-10-21.json) records those
metrics and their evidence hashes.

The clearest measured increment is 4.159 seconds in cache setup (51.3% of the observed 8.113-second
reader gap). The implementation always captures full-workspace content before it knows whether
novel writes are needed. The dependency validator serializes a 163,378-record proof and the pack
API performs small synchronous reads despite returning futures. Memo tables are working and their
limits are not hit. Writer publication repeats full source capture. The initial idle-retention
claim was retracted during Step 61: the inherited `afterCommand()` invokes `resetCommandState()`,
which already clears session/client references. No redundant cleanup was implemented.

BEP additionally identifies 2,297 writer / 2,248 reader action-cache DIGEST_MISMATCH misses, versus
zero in controls; the exact changed digest components are untraced. GC notification spans are
under one second and repository mapping time is common to both modes, so neither explains the
multi-second regression alone. Phase durations are not added as an exact causal decomposition.
Recommendations remain within the local backend/serialization layer and preserve correctness checks.


## Step 61 — metadata and packed-I/O optimization batch (2026-10-21)

Implementation: [`6f451b2dc6`](https://github.com/JSGette/bazel/commit/6f451b2dc6f783dda879ff3c7ed3fc8146d0f410).
[Full batch report](../skycache-optimization-batch-2026-10-21.md) and
[curated six-attempt evidence](skycache-concurrent-2026-10-20/batch-optimization-2026-10-21.json).

Batched small packed-record reads/writes (three loops to one; large records three to two),
combined fresh POSIX metadata observations, removed temporary digest-length buffers, and precompiled
the dependency-key regex. Formats, content guards and validation semantics remain. Deferred parallel
hashing and validator redesign to keep the requested quick batch narrow. Inherited command cleanup
already clears references; the Step 60 idle-retention finding was retracted, not fixed redundantly.

Cache&Carry build plus all 18 executed test targets passed 284 cases in 16.856 s, with three remote
cache hits. Two preparation and four measured Datadog builds completed on E: NVMe with 32 jobs,
Xmx6g and 6 GiB action memory. All outputs match; all four measured builds show 6,788 local action
hits, zero digest mismatches and one unconditional internal action. Sources are unchanged; servers
stopped. Writer published successfully and reader reused 4,501 configured targets without failure.

Controls took 19.388/19.307 s, writer 33.766 s and reader 25.285 s. Reader remains 30.7% slower than
the control mean. Reader setup is 3.723 s versus the earlier 4.161 s. Total historical improvement
cannot be isolated from preparation/action-cache differences. Near-equal analysis phases also hide
repository work deferred into execution; the report preserves exact span evidence. Peak reader PSS
is 5.004 GiB versus 2.550/3.976 GiB controls. One observation per enabled role is not a confidence
interval. No additional attempts were run after this batch.

Raw evidence is readback-verified in the private archive recorded by the curated JSON. The source
branch is separately pushed; docs/results remain on `jsgette/master`.
