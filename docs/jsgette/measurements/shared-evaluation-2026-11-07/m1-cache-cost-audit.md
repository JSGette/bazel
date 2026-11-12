# Existing SkyCache cost evidence and its boundaries

Source-only audit, 2026-11-08; reviewed 2026-11-12. Qualified source: `work/skycache-trace-source/source`, instrumented Git tree `e8aa57dfd71b302706f16f25389351ff6bb328ff`, combined patch `026010487e7a049bfbed364803053729f64d1f2be7acd970d5a04132d3512a90`. No runtime reads, builds, new experiment, or Java changes. Line references below refer to that source.

**Finding:** existing counters establish cache activity and existing timers establish elapsed latency. They do not partition cache CPU. Direct validation lacks its own typed trace event, but some of its CPU is already inside an unsupported configured-target `COMPUTE` interval. Therefore neither “all validation is outside the collector” nor “all restoration is included in captured state CPU” is correct.

## What the existing measurements mean

| Existing evidence | Actual boundary and concurrency | Limit |
|---|---|---|
| Analysis-client latency histogram | `LocalAnalysisCacheClient.lookup`: candidate discovery/read, envelope parsing, dependency validation, acceptance and payload copy, timed with `System.nanoTime`; returns an immediate future only after this synchronous work. `validatePreviouslyLoadedValue` contributes to the same histogram. | Wall latency, including lock/I/O/subtree waits. It mixes retained-root checks and lookups, including misses/rejections. It excludes the later SkyValue payload decode. Request and batch latency fields repeat the same histogram; do not add them. |
| Dependency-validation statistics | `LocalDependencyValidator.statisticsJson`: records read, memo hits, witnesses checked, rejected roots, memo entries/capacity skips, witness/admission memo hits, subtree waits. Validation runs on callers; shared pending subtrees can block other callers. | Counts, not CPU or duration. `witnesses_checked` differs from witness memo entries. A validated record can involve hashing, protobuf/Zstd decoding, filesystem evidence and waits. |
| Restore and CAS statistics | Session `restore` reports admitted root attempts, admitted serialized bytes and skips; separate CAS counters report reads/hits/misses/received bytes. Admission charges survive failed candidates. Local segmented reads are synchronous despite their immediate-future API. | Serialized volume is neither decoded heap, retained size nor CPU. Admission is not proof of a successfully materialized configured target; use retrieval hits/BEP for that. |
| Retrieval phase distributions | `SkyValueRetriever.RetrievalContext` measures wall time between state transitions and total wall since retrieval start, including Skyframe restarts. BEP reports HIT/MISS distributions by SkyFunction and phase. | `INITIAL_QUERY` includes the local synchronous lookup/validation; `WAITING_FOR_CACHE_SERVICE_RESPONSE` also performs initial payload decoding. Later phases include shared-decode futures, Skyframe lookups and scheduling. These are not pure service/deserialize CPU buckets. Concurrent per-key durations and nested TOTAL cannot be added into command CPU/wall. |
| Value-store latency | `FingerprintValueService.get` measures from call to future completion; direct-executor listener records elapsed time. Session-level get/put histograms also use elapsed time. | Future completion latency, not decoder CPU; several layers can time overlapping work. |
| `local-skycache-phases.json` | Six optional scalars: publication, final validation, witness manifest, seal, local install, close stores. Enabled from the ordinary profiler's state captured before the build. Publication includes its nested phases; excludes initial lock wait, close stores and module-side remote export. | Explicitly `inclusive_elapsed_wall_not_cpu`; disabled/missing/null is unknown. Final validation can use up to four platform workers (two under `ActiveProcessorCount=2`), so even that phase's elapsed time is not CPU. |

Source anchors:

- `LocalAnalysisCacheClient.java:110`, `validatePreviouslyLoadedValue` at 118, `lookup` at 175, `getStats` at 220.
- `LocalDependencyValidator.java:129`, `validateRoot` at 146, witness checks at 203, pending waits at 230, record/hash/decompression work at 303–354, statistics at 385.
- `LocalCacheSession.java:123`, restore budget at 123–154, statistics at 466, `get` at 631, candidate admission at 737, `finish` at 1125; `SegmentedFingerprintValueStore.java:432`.
- `SkyValueRetriever.java:159`, transition timing at 159–216 and retrieval/decoding at 358–486; `MetricsCollector.java:407` exports retrieval distributions at 438–455 and store/client traffic and latency at 458–491.
- `FingerprintValueService.java:211` defines the get timer. LocalCacheSession.java:508–577 and 632–676 define session put/get histograms; line 306 defines finalization timing.

## Which CPU can the collector already include?

1. **Caller-side restoration can be inside COMPUTE.** `ConfiguredTargetFunction.compute:275` calls retrieval inside `AbstractParallelEvaluator.java:473`'s COMPUTE bracket. Local lookup/validation is synchronous. This caller-thread CPU can be charged to an unsupported configured-target interval without separately identifying validation. No supported leaf projection earns that CPU merely because the validator checked a file.
2. **Shared decoding crosses threads.** Initial decoding is inline, but `SharedValueDeserializationContext.readValueForFingerprint:375` schedules `SharedBytesProcessor` through the fingerprint service's executor. `LocalSkycacheModule.Services:418` uses the common pool; `SafeExecutorOwner:91` dispatches there. That worker CPU is not inherited by a waiting requesting thread's CPU delta. Continuation setters also use direct callbacks, so not every decode operation has a single thread category.
3. **Lifetime is wider than recorded state intervals.** `Bazel.java:88` registers the cache before the last trace module. Setup precedes attachment; cache `afterCommand` runs before trace finish, because `BlazeRuntime.java:830` calls modules in order. `M1TraceModule.java:133` declares this window and direct validation unobserved; finish/detach occurs at 190–195. Being attached does not generate a state event for final validation/publication or observer export.
4. **Final validation is separately parallel.** `LocalInputSnapshot.isUnchanged:239` uses one thread below 16,384 witnesses, otherwise a bounded `skycache-final-validation-*` platform pool, and joins it before returning (273–305). Namespace/source checks also run on the caller. Named-worker CPU alone would omit those caller checks, publication and shared decoding.

## Smallest useful evidence path after the frozen matrix

**Use existing evidence first:** inspect the archived BEP HIT/MISS phase distributions and cache-client/store latency histograms alongside the four cache statistics/ownership sidecars and optional finalization scalars. Preserve their different boundaries and missing fields. They can show whether expensive elapsed regions coincide with validation/restoration, not identify their CPU. The already archived M0 JFRs offer a cheaper coarse check before another capture: the `M0 analysis` decoded 1,982 on/restart CPU events but used only leaf categories and at most five decoded frames. A bounded deeper decode of the preserved raw recording, if deeper stacks were recorded, could classify cache callers versus utility/native leaves. This is historical diagnostic evidence from a different binary/protocol, not current M1 CPU or a savings denominator; truncated/missing stacks remain unknown.

**If coarse attribution still changes the decision:** the existing `CommandProfilerModule.java:67` supports `--experimental_command_profile=cpu`, bundled async-profiler CPU events at 10 ms, writing `<output_base>/cpu.jfr` (39, 93–138, 157–165). It requires no Java patch. Any later authorized diagnostic should retain whole-process/scope CPU and classify full sampled stacks into disjoint validation, store/read/decompression, value reconstruction, other Bazel, JVM/runtime and unknown buckets; use inclusive caller views only as a separate view. Do not multiply sample shares by off-arm CPU, or add inclusive stack categories. Successful build alone does not establish profiler availability: verify fresh output, event type, decoded stacks and profiler errors.

**Important cutoff:** that profiler is registered early at `Bazel.java:46` and stops in its own `afterCommand`, *before* the cache module's finalization and trace module's export. It can distinguish sampled request-time validation/decoding, but cannot resolve their complete command-lifecycle CPU. A separately bounded whole-process recording or synchronized process/thread CPU boundaries would be needed for that tail. Thread-name polling alone can miss short-lived workers and cannot assign common-pool CPU to methods; ordinary wall-stack samples are not CPU evidence. No such capture is authorized or run by this audit.

The first pair's roughly 25.8 s off-request CPU, 7.17 s on-trace state CPU and 26.6% trace-on/off request difference are incompatible attribution boundaries, not an arithmetic decomposition. The 175 MB trace and approximately 99.2% unsupported observations do not select the next semantic family or justify scaling the supported share. Keep the combined unattributed work and semantic ceilings unknown until evidence separates cost and demonstrates reusable typed results; count, payload size and stack frequency alone establish neither.

Review clarification (2026-11-12): the small measured post-response drain does not bound the profiler omission. Cache finalization occurs after the built-in profiler stops and before the client returns; that unprofiled segment is inside the build window. Keep it distinct from the measured post-response tail.

Source reconstruction and qualification are pinned by the [candidate proof](evidence/auxiliary/m1-trace-source-build-03/candidate-verification.json) and [v3 source preparation](evidence/skycache-trace-build-preparation-v3/). These line references describe that exact source, not a different branch revision.

## Final follow-up status (2026-11-12)

The [ten-row measured sidecar audit](m1-residual-sidecars.json) completed. The
two historical restart CPU recordings were not decoded at greater depth:
host-budget admission and then task mount inspection stopped the fixed attempts
before launching the tool. Prepared analysis code has synthetic test coverage
only. See the [M1 assessment](m1-gate-assessment.md).

The original JFR print command omitted stack-depth, whose documented default is
five. A startup async-profiler agent can avoid the built-in module's early stop;
its VMInit/VMDeath boundaries still leave lifecycle residuals. A Java heap dump
can describe retention at capture time. None supplies semantic equivalence or
peak-PSS reclaimability. These are feasible alternatives, not experiments run:
[JFR reference](https://docs.oracle.com/en/java/javase/25/docs/specs/man/jfr.html),
[async-profiler 4.5 integration](https://github.com/async-profiler/async-profiler/blob/v4.5/docs/IntegratingAsyncProfiler.md),
[profiler lifecycle callbacks](https://github.com/async-profiler/async-profiler/blob/v4.5/src/vmEntry.cpp),
[heap-dump semantics](https://docs.oracle.com/en/java/javase/25/docs/specs/man/jcmd.html).
