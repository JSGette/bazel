# Digest/buffer reuse and cold SkyCache restore

26 October 2026; source base 869792d6b1. Follows the
[bounded-channel experiment](../skycache-channels-2026-10-26/README.md).

**The implemented change sharply reduces allocation, with a modest warm latency gain
in this small experiment.** The cold trace identifies synchronous retained
chunk storage as a larger remaining cost. Complete parallel import is promising
as a read strategy, but its current integration loses remote-parent provenance and
adds expensive publication work. It is not ready to enable as a default.

## Implemented optimization

[RemoteSkyCache](../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache.java)
now exclusively borrows a SHA-256 instance, a 32-byte result array and a reusable
range buffer from a bounded array of atomic slots. It writes block digests directly
into the trusted table or result scratch. It retains at most min(concurrency, 64)
idle buffers of at most 64 KiB: **4 MiB maximum idle range-buffer capacity**. Active
buffers remain subject to the existing concurrency and chunk-size bounds. Larger
reads remain temporary. Digest state
is reset after each block and on release, including failures. Close drains the
invocation and clears retained scratch; worker ThreadLocals are not used.

The number and strength of cryptographic checks are unchanged. Whole CAS digest
verification, every requested covering-block hash, fresh path/channel validation,
record checksums and fresh input witnesses remain. Buffers do not escape: each
chunk's requested bytes reach the caller only after its complete covering range
passes authentication. This does not introduce atomicity across a multi-chunk read. The
runtime helper is narrowly excluded from dynamic codecs in
[AutoRegistry](../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/AutoRegistry.java).
All 8,821 diagnostic registry classes and the checksum match the previous build.

Across three selected-implementation warm measurements, common range-buffer allocation averages
**21.66 MB**, versus approximately **3.569 GB** allocated for the
same cached reads by the former reader (about **99.4% less**). It creates
30–32 digest/scratch objects and reuses them
796,504–796,505 times per command. The preceding chain trace counted
about 1.23 million block digest constructions. These figures concern the remote
authentication/range path, not all allocations or all digests in Bazel.

| Warm remote, selected atomic-slot experiment | Before | After |
| --- | ---: | ---: |
| Samples | 3 | 3 |
| Median command seconds | 20.300 | 19.832 |
| Mean command seconds | 20.350 | 19.893 |
| Range, seconds | 20.271–20.480 | 19.814–20.033 |
| Mean Bazel CPU-seconds | 84.68 | 84.01 |
| Mean reported heap garbage collected, GB | 20.10 | 16.49 |

The initial synchronized scratch-pool prototype was slower in two independent warm
cohorts: six samples per reader give 20.682 s
baseline and 21.350 s prototype medians.
It was replaced with fixed atomic slots to remove global borrow/return bookkeeping.
That mechanism is a hypothesis, not a proven attribution: the separate JFR pair
recorded no long monitor-enter events on the prototype pool and cannot exclude
shorter overhead. **All accepted prototype and confirmation measurements are
retained separately, not pooled with the selected implementation.** Reported garbage
collected is a GC-dependent proxy, not a complete allocation count.
[Selected warm results](slots-warm-comparison.json), [prototype warm results](combined-warm.json),
[monitor observations](monitor-comparison.json).

The final implementation's independent cold comparison gives
28.860 s before and
28.661 s after (three samples each). Its cold
transport volume is unchanged; the timing difference is within observed variation.
[Selected cold results](slots-cold-comparison.json).

**635 tests across 23 targets pass**, including three new tests and strengthened
range-reuse assertions. Coverage includes concurrent readers, short final blocks
after large buffers, returned-byte independence, corruption followed by reuse,
oversized buffers, lifecycle cleanup and registry exclusion. Existing forged
checksum, missing/truncated/replaced files, cache-only, eviction and cancellation
tests still pass. [Selected build and tests](build-slots-v2/result.json).

## What cold means and what it costs

| Scenario | Median seconds | Samples |
| --- | ---: | ---: |
| Existing local publication, warm pages | 17.046 | 3 |
| Existing local publication, cold pages | 23.168 | 3 |
| Empty local SkyCache; recompute using ordinary action cache | 26.567 | 3 |
| Remote, empty retained cache and cold backend pages | 28.900 | 3 |
| Full import, sequential download, then local publication | 44.559 | 3 |
| Full import, 16 download workers, then local publication | 25.975 | 3 |
| Full import, 16 download workers, remote publication enabled | 31.423 | 3 |

This chain-comparison matrix used the initial digest-pool prototype. Its remote
transport, durable retention and hydration mechanisms are unchanged by the final
atomic-slot revision, which has its own before/after cold comparison above. Local
and full-import results must not be silently merged with the later cohort.

Every build starts with a fresh JVM, empty Skyframe graph and cleared action cache,
with the same source, tool/environment identity, six targets plus a publication
probe, and ordinary remote action/output-cache settings. Source/repository inputs
and service code are warmed by excluded warmups. This is not a completely cold
machine or an output-cache miss benchmark.

For disk-cold publication comparisons, private local publication files or private
backend CAS files are synced and evicted with POSIX_FADV_DONTNEED. mincore confirms
**zero resident Linux metadata pages** before every timed cold command. No global
cache flush is used; host/device caches are outside this control. Both sides use
the exact same two-publication seed and bytes. Cold remote additionally starts
without retained client chunks. Warm local retains its pages. Empty local starts
without any publication and therefore recomputes graph state.

The empty local case is slightly faster than cold remote here, but uses
**461.83 mean Bazel CPU-seconds**, versus **102.27** for remote. It
configures 237,113 targets, performs 16,698 ordinary remote action-cache hits and
writes a fresh graph. Restored cases configure 26 targets and hit 18 analysis / 11
execution entries. This is a latency-versus-resource tradeoff. CPU numbers for an
eager run's Bazel process omit its separate import helper and must not be presented
as the total CPU cost of that scenario.

## Cold chain attribution

Arithmetic means below add within each row. The table above uses medians. The
middle interval includes admission, validation, restoration, analysis/execution
and command overhead; it is not pure I/O time.

| Scenario | Import | Setup | Middle | Finalization | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| Local publication, pages cold | 0.000 | 0.397 | 19.966 | 2.834 | 23.196 |
| Remote, empty retained cache | 0.000 | 1.698 | 24.100 | 3.145 | 28.944 |
| Parallel import, local publication | 9.286 | 0.388 | 13.378 | 2.877 | 25.929 |
| Parallel import, remote publication | 9.737 | 0.875 | 19.467 | 7.589 | 37.668 |

Local reads already-open pack files. Remote must additionally discover authenticated
metadata, fetch whole CAS chunks, verify them, write scratch, construct trusted
4 KiB hash tables and publish retained chunks. This fixture downloads **1,613 chunks /
1,463,742,717 bytes**, then reads about **3.57 GB** of authenticated covering ranges.
The preceding trace's packed-record demand was only about 306 MB, or 406 MB including
indexes/metadata. Those demand counters are historical context from the identical
seed, not new primary instrumentation.

A separate cold diagnostic records:

| Method | Calls | Accumulated worker seconds | At least one call active, seconds |
| --- | ---: | ---: | ---: |
| Transport download, including response assembly and CAS digest | 1,613 | 18.00 | 10.57 |
| Retained chunk store, including file/directory sync | 1,613 | 16.39 | 8.20 |
| Build block authentication table | 1,614 | 0.61 | 0.56 |
| Enclosing download operation | 1,613 | 35.92 | 13.18 |

**These are nested/overlapping intervals; do not add them or interpret worker time
as critical-path wall time.** Nevertheless, retained storage is comparable to
transport in worker time. The enclosing operation averages only about 2.72 active
workers while active, despite a configured concurrency limit of 100. Increasing
that limit alone does not remove demand dependencies or synchronous stores.

[RemoteChunkCache.store](../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCache.java)
recursively ensures durable directories, forces each chunk, links it into retained
storage and forces its directory. These operations sit before the miss returns.
LocalCacheSession.durableDirectory repeats parent-directory synchronization.
This is the strongest narrow cold-path target. Keep verified scratch immediately
usable while reducing repeated durability work for the advisory retained cache;
preserve atomic visibility, next-read authentication and durable full publications.
[Diagnostic intervals](cold-trace.json).

## Download everything, then invalidate changed parts?

**Yes as a transfer/materialization strategy; it does not remove dependency
validation.** After authenticated hydration, the existing local validator already
rejects stale roots and allows unaffected entries to hit. The complete unique payload
is **1,510,678,665 bytes**, only 46,935,948
bytes (3.21%) more than lazy downloading. Lazy demand already transfers **96.89%**
of this fixture's complete payload. Bulk prefetch can remove dependency-discovery
stalls, then restore through the local reader. Fewer requested bytes alone are not
the right optimization criterion here.

The existing production hydrate() imports the complete authenticated closure.
The benchmark's 16-worker helper calls the existing verified downloader before
hydrate; queued futures retain no byte arrays. Sequential import plus local build
costs 44.559 s; parallel prefetch plus local build costs 25.975 s.
These include helper JVM startup, import, local validation/build and local publication.
The helper is **not** a new production mode.

Keeping remote publication enabled gives 31.423 s median. It reveals a real
integration problem: hydrated parents are admitted as local candidates, so
publishPending receives localParents and records **reused_publications=0**, rather
than reusing both authenticated remote descriptors. uploadLocal then rereads,
rehashes and checks parent chunks for upload. Existing CAS blobs need not be sent
again, but their processing still costs time. Preserve remote provenance through
materialization before treating the local-only prototype as an end-to-end win.
The confirmation cohort includes a slow 51.40 s run with a long analysis interval;
it remains in the results. Its extra delay has not been independently attributed.

Materialized files total 1.520 GB, in addition to approximately 1.511 GB of retained
chunks in this prototype. Eager import therefore has disk-capacity and extra-write
costs as well as latency costs. It must obey closure, transfer and concurrency bounds.

The serialized publication is not a complete live Skyframe heap with reverse edges.
Current roots carry independent dependency fingerprints, and the validator checks
fresh file, listing/glob, symlink, repository and configuration evidence before
accepting them. Downloaded CAS hashes prove byte identity, not current-input
freshness. Restoring every live node and then doing native fine-grained bottom-up
invalidation would require a different graph/lifecycle design.

## Next path and research interpretation

1. Reduce synchronous retained-chunk durability on cache misses. Measure file and
   directory sync separately, with crash/interruption and corrupt-retention tests.
2. Prototype bounded parallel import that preserves authenticated parent provenance
   and avoids retaining duplicate chunk/pack forms unnecessarily. Keep existing
   validation and fall back safely when a full closure exceeds the budget.
3. Compare it with lazy retrieval and recomputation on smaller target sets, changed
   inputs, WAN latency and constrained CPU. Select eager or observed-working-set
   prefetch only when avoided stalls exceed extra transfer/materialization cost.

A useful decision model is:

`eager wins when extra_bytes / bandwidth + materialization_cost < removed_fetch_stalls + removed_remote_read_work`

CPU, I/O and transfer overlap; this is a selection model, not an additive measured
decomposition. This fixture's high coverage favors prefetch; it is not universal.

[Oracle Java 25](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/security/MessageDigest.html)
documents digest reset and caller-supplied output buffers, supporting the reuse
mechanism. [Bazel's Skyframe reference](https://bazel.build/reference/skyframe)
distinguishes bottom-up invalidation of changed inputs' reverse closure from
top-down validation of a requested subgraph; neither makes freshness proof optional.

[FlacIO, FAST 2025](https://www.usenix.org/conference/fast25/presentation/liu-yubo)
studies I/O amplification and collective service-oriented loading.
[SnapShare, October 2026 preprint](https://arxiv.org/abs/2610.13318) combines eager
working-set retrieval with lazy fallback. These support evaluating layout,
granularity and demand-aware prefetch; their container/snapshot performance numbers
are not predictions for SkyCache, and the latter is a preprint.

## Controls, limits and reproduction

The primary experiment uses the same frozen installation and exact publication
seed for every arm. A startup-only Java agent loads the exact tested reader/helper
class bytes; all arms share the runtime-class registry exclusion. It changes no
installation checksum, namespace or validation guard. New ordinary Bazel binaries
still require compatible entries for their installation identity. No per-read
instrumentation is present in primary timings. Diagnostic JFR/probes are separate.

**48 measured primary commands and four diagnostics** pass output, publication and
expected hit-count checks. Restored runs preserve the same 103 witness blocks;
empty-local runs correctly have zero SkyCache hits. Source and binaries stay fixed
within each cohort; measurement jobs do not overlap. Worker startup, seed copying,
backend warmups and cache eviction are outside command timing. Server startup plus
clean is recorded separately, rather than mislabeled as pure JVM startup.

These are loopback, single-host, small-sample measurements. Three samples per reader
in the selected implementation and six per reader in the earlier prototype establish
neither tail latency nor statistical
significance. The initial pre-timing harness failure is archived and excluded.

- [Warm](warm-comparison.json), [cold](cold-comparison.json), [empty local](empty-comparison.json),
  [eager import](eager-comparison.json), [confirmation](confirm-comparison.json),
  [diagnostic](diagnostic-comparison.json), [selected warm](slots-warm-comparison.json),
  [selected cold](slots-cold-comparison.json), with complete compressed records/timelines in each folder.
- [Prototype agent provenance](agent-provenance.json), [selected agent provenance](agent-slots-provenance.json), [registry equality](registry-comparison.json),
  [source/profile provenance](provenance.json), [verification](verification.json),
  [evidence index](evidence-index.json).
- Helper sources and harnesses are archived here. They depend on the private Datadog
  fixture, exact seed, JDK 25, Python gRPC environment and reachable action cache.
  Large binaries, profiles and JFR recordings remain at the native paths in provenance.
