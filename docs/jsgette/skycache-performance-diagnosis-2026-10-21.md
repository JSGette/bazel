# SkyCache performance diagnosis — 2026-10-21

The current local cache is functionally reusing analysis, but the cost of proving that cached
values are usable and reconstructing them exceeds the saved work in the measured Datadog workload.
The strongest directly timed cost is about four seconds of mandatory cache setup before analysis.
The remaining source-proven costs are serialized dependency validation, broad publication admission,
and hundreds of thousands of small synchronous record operations. Exact inner-method wall-time
shares are not yet measured.

This is a read-only investigation of the four saved NVMe runs and implementation commit
`34688d19d6244ef3ccf633ebcca6e1376f12c87b`. No additional Bazel builds or implementation changes
were made. The measured binary is unchanged. Baselines use the identical fork with SkyCache off,
not a separately built upstream release. See the [measurement data](measurements/skycache-concurrent-2026-10-20/performance-diagnosis-2026-10-21.json)
and [comparison protocol](measurements/skycache-concurrent-development-2026-10-20.md#step-59--two-cache-off-baselines-on-the-same-nvme-2026-10-21).

## What the profiles establish

| Component | SkyCache reader | Cache-off mean | Observed difference |
|---|---:|---:|---:|
| Full command | 27.762 s | 19.650 s | +8.113 s |
| LocalSkycacheModule.beforeCommand | 4.161 s | 0.002 s | +4.159 s |
| Bazel analysis phase | 7.451 s | 5.239 s | +2.213 s |
| Bazel execution phase | 4.201 s | 1.432 s | +2.770 s |
| Bazel CPU time | 155.380 CPU-s | 141.310 CPU-s | +14.070 CPU-s |
| Recorded GC notification spans | 0.486 s | 0.212 s | +0.274 s |

The setup increment is numerically 51.3% of the observed wall-time gap. This is not a controlled
causal allocation of the whole gap. Phase events overlap or nest, and execution state differs;
adding the rows would double-count or misattribute time. Analysis time is the net cost after reuse
savings, not a dedicated validation/deserialization timer. Main repository mapping is a common
8.3–8.8-second startup cost in all runs, not the new regression.

## 1. Mandatory full-workspace content capture

**Measured: 4.161 seconds in reader setup; source-proven full scan within that stage.**

[LocalSkycacheModule.beforeCommand](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalSkycacheModule.java#L133) eagerly constructs
`LocalInputSnapshot`, whose [constructor](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalInputSnapshot.java#L72) immediately
calls `captureSource()`. The [recursive walk](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalInputSnapshot.java#L368) sorts every
directory, reads attributes and permissions, and hashes every regular file. It is serial. Ignored
and untracked files are included; only narrow Git metadata and Bazel output-link exclusions apply.

A post-measurement enumeration using those exclusions found **27,634 file visits, 4,945 directory
visits and 236,480,543 logical file bytes** (about 225.5 MiB). This is a footprint count, not a Java
microbenchmark or an exact recording of the earlier build's file accesses. It includes content
outside the selected target, such as test fixtures and release notes.

Every enabled command pays this cost before knowing whether it will publish anything. The measured
reader ultimately wrote no new records, yet paid the full initial scan. Crucially, `sourceDigest`
is not the cross-worktree reuse key: [manifest reading](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheSession.java#L889)
only validates its format. It is a whole-tree start/end mutation guard. Simply deleting it would
remove a safety check; replacement with dependency-scoped validation requires proof that relevant
files, listings, absences and symlink transitions are all witnessed.

There are no nested timers separating this scan from directory creation/fsync and lease setup.
The scan is a concrete expensive mechanism inside the measured 4.161 seconds, not proof that
hashing alone consumes the entire interval.

## 2. A serialized dependency proof before reuse

**Source-proven serialization; 163,378 dependency records processed in the reader.**

[LocalDependencyValidator.validate](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidator.java#L82) is synchronized
for the entire DFS. It keeps that monitor while retrieving records, checking parents, hashing
inputs and parsing evidence. Its [record load](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidator.java#L142)
blocks on `store.get(key).get()`. Multiple target lookups funnel through this validator, so giving
Bazel 32 jobs cannot parallelize that section.

The 4,501 configured-target hits therefore hide much more work: 163,378 dependency records,
150,062 memoized witness entries and the transitive proof linking them. Before entry validation,
[publication admission](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheSession.java#L611) serially validates the publication's
union of non-workspace witness evidence, including repository bindings. Entry validation still
has to reconstruct and traverse dependency records afterward.

This is **not missing memoization**: 372,651 dependency memo hits and 142,264 admission-witness hits
were recorded. Only 7,798 witness checks remained on the fresh-validation path. The memo limits
were not saturated. Increasing their caps is not a supported explanation or remedy for this run.
The first large dependency closure and its record parsing are still expensive despite the memo hits.

## 3. Fine-grained synchronous pack I/O and allocation

**Source-proven operation amplification; precise wall-time attribution remains open.**

The reader issued **312,794 CAS gets for 336,777,109 value bytes**, only about **1,077 bytes per get**.
[PackedFingerprintValueStore.get](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/PackedFingerprintValueStore.java#L220) hashes the key
and searches the index. [readRecord](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/PackedFingerprintValueStore.java#L312) allocates
header/key/value/checksum buffers, reads header, key and value separately, and verifies SHA-256.
That implies roughly **938,000 positional read loops for the CAS gets alone**, plus analysis-key
and duplicate-write probes. This is not a measured syscall count, and certainly not that many
physical NVMe reads; page-cache hits still require calls, copies, parsing and checksums.

The bytes are read before `immediateFuture` is returned. The async deserialization interface does
not make this local implementation asynchronous. Dependency validation then verifies its own
fingerprint and decodes evidence. This repeats work across storage integrity and graph-validation
layers, although those checks have distinct correctness purposes.

Writes have similar granularity. [Packed put](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/PackedFingerprintValueStore.java#L170)
clones the value, computes checksums and performs three positional writes. Session puts hold a
striped per-key lock across the operation and enter synchronized refresh/store helpers. This is
not one global lock over all record I/O and not a per-record fsync. Lock contention itself has not
been measured. Fine-grained writes, copies, checksum work and helper synchronization are concrete
costs worth reducing before adding more worker threads.

## 4. Writer publication costs are paid in addition to analysis

The writer stored 443,700 novel records and accepted 454,267 put requests. Its profile contains a
**3.368-second build-targets tail** after analysis/execution, versus about 0.02–0.04 seconds in
other runs. Its full command also extends **3.474 seconds beyond BEP's reported wall time**.
Those are measured intervals; neither has a detailed publication sub-timer, so they are not exact
serialization-versus-fsync-versus-final-validation allocations.

[Session finish](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheSession.java#L727) and
[input revalidation](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalInputSnapshot.java#L181) show the work that follows successful
novel writes: repeat the whole-workspace content capture, validate retained witnesses, seal the
pack and durably publish the manifest. This strongly implicates draining and publication in the
writer's additional cost. The reader had `writes=0`, so it correctly skipped redundant publication.

## 5. More temporary metadata; retained heap ownership remains unmeasured

The reader's peak PSS was 5.139 GiB against 3.823/2.518 GiB controls, despite fewer final graph
nodes (234,712 versus about 319,822). The graph node count is not the whole heap. Import creates
encoded buffers, decoded objects, futures, dependency/witness sets and candidate metadata alongside
the in-memory graph.

The index and manifest total **48,413,504 encoded bytes**; that alone cannot explain a 1.3–2.6 GiB
PSS difference. BEP reports 12.496 GiB of Eden garbage reclaimed in the reader, versus 6.891/8.129 GiB
in controls (writer 20.039 GiB). These are reclaimed-byte counters, not allocation-rate or retained
heap measurements. They support allocation churn, while under-one-second recorded GC spans do
not explain the multi-second latency gap. Existing object fingerprint caches already use weak
references and `NOT_LINKED`; enabling those is not a new fix.

**Correction after tracing inherited cleanup during the optimization batch:** the initial diagnosis
incorrectly said `Services.client` and `Services.session` survive until the next command.
`LocalSkycacheModule.afterCommand()` calls `super.afterCommand()`, and
[SerializationModule.afterCommand](https://github.com/JSGette/bazel/blob/34688d19d6244ef3ccf633ebcca6e1376f12c87b/src/main/java/com/google/devtools/build/lib/skyframe/serialization/SerializationModule.java#L55)
invokes `resetCommandState()`, clearing those references. No redundant lifecycle cleanup was added.
The peak-PSS difference remains measured; its ownership still needs an allocation or heap profile.

## 6. Part of the observed wall-time gap is unequal action reuse

BEP is more specific than merely showing differing hit counts: writer and reader had **2,297 and
2,248 `DIGEST_MISMATCH` action-cache misses**, respectively. Controls had only the unconditional
workspace-status action. The reader restored 1,197 disk-cached actions and ran 1,054 internal
actions, while both controls reused 6,788 actions locally.

This explains real additional execution work, but the saved evidence does not identify which
action-digest component changed. Fixture/output drift and implementation-induced differences remain
possibilities. The writer with zero analysis-cache hits has the same symptom, so blaming only
reader deserialization would be unsupported. Matching initial action-output state and tracing the
specific mismatches is required before assigning the entire 41.3% observed difference to SkyCache.

## Recommended order

1. **Add narrow timing and allocation instrumentation:** split source capture, directory/lease
   setup, manifest decoding/admission, dependency traversal, content hashing, record I/O,
   deserialization and publication validation. Preserve complete-command timing.
2. **Reduce guaranteed setup and I/O costs:** combine metadata reads, bound parallel hashing,
   read/write each immutable indexed record in one operation, and batch dependency records.
   Preserve key, checksum, bounds, conflict, cancellation and durable-publication checks.
3. **Reduce the amount of evidence processed:** make admission and input validation specific to
   requested dependency closures, with explicit proof for shared external bindings and mutation
   cases. Do not replace content checks with timestamps or Git status.
4. **Remove the validator-wide bottleneck carefully:** bounded parallel or shared in-flight
   validation must preserve parent ordering, cycle detection, budgets and invocation-local validity.
   Compact evidence and precompiled key validation can reduce parsing/allocation overhead.
5. **Quantify peak versus idle memory separately** with allocation/heap evidence. Inherited command
   cleanup already clears the client/session references; no new reset is needed.
6. **Repeat a matched on/off comparison** after each measured optimization, including exact action
   cache-state evidence. More CPU, more heap or graph paging is not the first response to this data.

All of these changes can remain in the local SkyCache/serialization integration. The evidence
does not require changing Skyframe's evaluator or abandoning in-memory graphs.
