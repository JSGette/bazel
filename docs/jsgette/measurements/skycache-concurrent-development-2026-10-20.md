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
