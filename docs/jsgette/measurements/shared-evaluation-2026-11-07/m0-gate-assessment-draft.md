# M0 gate assessment draft — checkpoint 06

This is decision preparation, not a verdict. [Checkpoint 06](evidence/m0-v7b-01/matrix-progress-checkpoint-06.json) contains 14 checked/archived rows: 10 performance-eligible rows and four correctness references. Disjoint is running; configuration, glob, warm-server and fresh-cache coverage is still incomplete. Do not combine the preserved v6/v7 attempts with this v7b matrix.

The [published M0 gate](../../plans/shared-evaluation-gates-2026-11-07.md#m0-establish-the-baseline-and-identify-duplicated-cost) asks whether duplicated analysis, validation **or retained state** is substantial enough to investigate. It is not a requirement that today's SkyCache implementation win, and it is not the later 30% CPU / 50% memory gate. The latter thresholds still need a recorded freeze before candidate implementation.

## What the completed evidence establishes

| Restarted row | Scope CPU s | JVM CPU s | Peak JVM PSS MiB | Post-command graph nodes |
| --- | ---: | ---: | ---: | ---: |
| identical N1, candidate off | 22.715 | 22.220 | 713.345 | 63,236 |
| identical N10, candidate off | 286.105 | 276.880 | 6,906.965 | 632,360 |
| identical N10, SkyCache on | 305.991 | 285.190 | 5,483.610 | 508,340 |
| source N10, vanilla | 280.464 | 272.830 | 7,230.569 | 632,360 |
| source N10, SkyCache | 317.841 | 292.900 | 5,883.334 | 508,340 |
| BUILD N10, vanilla | 284.657 | 276.920 | 7,561.240 | 632,495 |
| BUILD N10, SkyCache | 301.097 | 281.180 | 5,505.923 | 508,448 |

Sources: `report.json`, `measured-resources.json`, and `measured/c00` through `c09/bep.jsonl` inside the [N1 off](evidence/m0-v7b-01/i1-off-r-run.tar.gz), [N10 off](evidence/m0-v7b-01/i10-off-r-run.tar.gz), [N10 on](evidence/m0-v7b-01/i10-sky-r-run.tar.gz), [source vanilla](evidence/m0-v7b-01/source-vanilla-r-run.tar.gz), [source SkyCache](evidence/m0-v7b-01/source-sky-r-run.tar.gz), [BUILD vanilla](evidence/m0-v7b-01/build-vanilla-r-run.tar.gz), and [BUILD SkyCache](evidence/m0-v7b-01/build-sky-r-run.tar.gz) archives. N1 has only `c00`. These archive hashes and the cited members were rechecked against their adjacent `.tar.gz.json` manifests for this draft.

- **Execution gate:** each cited N10 measured row executes ten `BazelWorkspaceStatusAction`s and no compiler actions; its runner counts are internal only. Identical/source rows report 230 action-cache hits, BUILD rows 239. Thus compiler execution is not the dominant work in these prepared-cache measurements. Repository activity, action-cache checking, hashing and output handling can still consume JVM CPU. Cold-cache results may have a different cost mix.
- **Repeated build-control cost:** candidate-off N10 has ten times the graph population, 12.46 times observed JVM CPU and 9.68 times PSS of N1. These are single-trial scaling observations. They establish repeated per-engine work/state, not equivalent typed query identities, graph-retained bytes or CPU saved by sharing.
- **Reuse is real, but savings are not established:** the same-binary N10 off/on anchor records 32,720 analysis-cache hits, 19.6% fewer graph nodes and 20.6% lower sampled PSS, alongside 3.0% higher JVM CPU and 7.0% higher scope CPU. This is evidence that the feature changes evaluation, not proof that a new evaluator will pay. Source/BUILD vanilla comparisons additionally cross source/binary versions.
- **Attribution remains incomplete:** JVM CPU includes JIT, GC, class loading, repository/module work, analysis, validation, restoration and action-cache processing. BEP analysis/execution fields and profile spans are elapsed time, often overlapping; they cannot partition CPU. Scope-minus-JVM CPU is unassigned and includes the sampler and behavior oracles. The [measurement controls](m1-probe-design.md#4-accounting-controls-before-the-nativeprivate-comparison) document this precisely.
- **PSS is not retained graph memory:** N1 vanilla reference/run-2 peaks are 713.51/505.65 MiB in v7b, repeating 716.49/501.95 MiB in v6. Different pool histories and GC/heap residency are plausible contributors, not established causes. Do not convert graph-node counts or peak PSS directly into a sharing ceiling.

A separate observation archived after checkpoint 06 flags resource pressure: `measured-resources.json` in [disjoint-ref-run.tar.gz](evidence/m0-v7b-01/disjoint-ref-run.tar.gz) has 445 of 454 samples at or above 99% of the 10 GiB cap. Before any measured daemon appears, it already charges 10,140,446,720 bytes, including 9,572,712,448 file bytes. The final sample charges 10,729,058,304 bytes: 6,856,003,584 anonymous, 3,461,812,224 file and 407,433,216 kernel bytes. Lifetime peak is 10,737,938,432 bytes; wall time is 86.839 seconds. This is a cap-pressure flag, not measured reclaim stall time. Neither those resource samples nor `result.json`/`host-memory.jsonl` in [disjoint-ref-guard.zip](evidence/m0-v7b-01/disjoint-ref-guard.zip) contain `memory.events`, reclaim counters, PSI or CPU-throttling deltas. Guard completion cannot establish their absence. Do not infer evaluator CPU from the elapsed slowdown; this reference remains correctness-only.

## Smallest closure after the 29 rows

1. **Finish and validate the existing matrix; do not add another broad matrix.** Require every intended comparison's archive, source/output/behavior/glob oracle, accounting and guard checks. Assess warm, restarted and fresh-cache strata separately. Use warm N1/N10 rows to identify whether substantial cost remains without restarting engines, and cold rows to label execution/bootstrap cost. Select this normalizer as a build-control workload only; it does not represent whole-agent compilation. A correctness failure blocks advancement; infrastructure failures remain separate.

2. **Run one small, separate N1 diagnostic pair: the same candidate binary with SkyCache off and on.** Give both arms equal preparation counts and fresh, independently owned output-base histories; copy correctness evidence before diagnostics. Each session observes a restarted prepared-cache build followed by a warm no-op. Record process CPU, observer thread CPU and subprocess roles, then use the built-in CPU profiler described below. Attribute broad categories only: evaluator/repository work, validation/decode/install, JIT/GC/runtime, action/cache I/O, and unknown. Report sample-based estimates as estimates and reconcile coverage with process CPU; elapsed stack/profile spans are not a substitute. Snapshot cgroup `memory.events`, `memory.stat` (including reclaim counters), `memory.pressure`, `cpu.stat` and `cpu.pressure` before/after each command; retain cumulative deltas and distinguish preparation residency. These are diagnostic additions only, never new captures inside the frozen matrix.

   After the timed commands, collect post-GC live heap and a deduplicated whole-graph size/category accounting in both sessions. Existing private-mode diagnostics include `info used-heap-size-after-gc` and `dump --memory=full,summary`; verify their availability and accounting scope in the pinned binaries before use. Report graph payload, node/index overhead and unaccounted JVM/native memory separately where observable. Do not sum overlapping deep sizes or treat serialized bytes as retained heap. This is the minimum missing evidence for the retained-state leg of M0; it does not require per-query instrumentation or a new product build.

3. **Allow only a targeted repeat if that diagnostic changes the decision.** If the conclusion depends on the PSS/pool anomaly, repeat one matched N1/N10 baseline pair with equal pool history and reversed order. If CPU profiling is unsupported or cannot separate validation/restoration, preserve those fields as unknown: do not launch an instrumentation project inside M0. Decide explicitly whether measured retained state alone supports a limited M0 continuation, or whether attribution is still insufficient. Record that scope/deviation against the published breakdown requirement. Do not claim a complete CPU breakdown or a graph-sharing opportunity from unknowns.

## Concrete diagnostic protocol, not yet executed

Use the pinned safe-subset binary `/home/jgett/skycache-20261029/bin/bazel-skycache-safe-subset`, SHA-256 `11547ce20ac9edcaa14c48206ac14618012d898ef24d4bbf6e0ac229b44b1e25`, in **both** private-daemon arms. Bounded reads of `/home/jgett/skycache-20261029/bazel` verified HEAD `a23ae68903f03f08ce83dcf1be85cdff54bb1eeb` and no dirty changes to the five diagnostic files below; the broader checkout remains dirty. The binary hash remains authoritative.

- `profiler/CommandProfilerModule.java:39,67,93,127,158–165` provides `--experimental_command_profile=cpu`: bundled async-profiler, CPU event, 10 ms interval, JFR output at `<output_base>/cpu.jfr`. `bazel/Bazel.java:46` registers it. This needs no tooling installation. The module's SHA-256 is `0e6ada2e68cc57e6feaefe21bfe9b5f1e5288a383e58e5439c088aeae64c2da9`.
- It begins at the module's `beforeCommand` and stops at `afterCommand`, so startup and earlier command work remain outside the sampled interval. JNI/perf initialization can fail while the build continues: require a fresh nonempty JFR and successful decode, preserve profiler diagnostics, and otherwise record profiling as unavailable. Retain process CPU as the total; missing native/JIT/GC stacks do not mean zero cost. Do not substitute ordinary wall-clock JFR stack samples for CPU samples. This global profiler is for these private M0 daemons, not the native comparator.
- `runtime/commands/info/UsedHeapSizeAfterGcInfoItem.java:30,38` implements `info used-heap-size-after-gc` via `System.gc()` followed by heap usage minus a filler-array adjustment; output is rounded. `runtime/commands/DumpCommand.java:221–222` accepts `dump --memory=full,summary`. `buildtool/SkyframeMemoryDumper.java:280–309` shares one identity set across reachable SkyValues, excluding builtins. Its result is an estimated unique value-payload footprint, **not** a complete retained-heap/dominator measurement; graph maps, keys and node/index overhead are not thereby accounted. Preserve raw output and detect duplicate JSON keys before aggregating category records.

Run only after the matrix completes, through the existing guard with a fresh unique scope, verified host-budget preflight, 10240 MiB / six CPUs / no swap. Each arm gets a newly created directory below `$R/m0-diagnostics-v1/{off,on}` (refuse existing paths), including output base, evidence, local SkyCache and `tmp`/`java` directories. Reuse the prepared task repository/action caches with recorded provenance; no old pool is leased or altered. Use `fixture.clean_env()` and explicit `TMP`, `TMPDIR`, `--repo_env=TMP`, `--repo_env=TMPDIR` and `-Djava.io.tmpdir` pointing into that new arm directory. All profiler, heap-summary, log and decoder outputs stay on `$R`; host-guard evidence stays on C as usual.

Start from the exact archived `measured/c00/command.json` argv in [i1-off-r-run.tar.gz](evidence/m0-v7b-01/i1-off-r-run.tar.gz), replace only owned output/evidence/temp/cache-mode destinations, and preserve the pinned fixture/resource/cache flags. In shell-array notation, `START` is that command's startup argv and `BUILD` its build argv with per-command BEP/profile paths. The only profiling addition is explicit:

```sh
"${START[@]}" "${BUILD[@]}"                          # one unprofiled preparation
"${START[@]}" shutdown
"${START[@]}" "${BUILD[@]}" --experimental_command_profile=cpu
cp --no-clobber "$OB/cpu.jfr" "$D/restart.cpu.jfr"    # archive before the next build
"${START[@]}" "${BUILD[@]}" --experimental_command_profile=cpu
cp --no-clobber "$OB/cpu.jfr" "$D/warm.cpu.jfr"
"${START[@]}" info used-heap-size-after-gc > "$D/heap-after-gc.txt"
"${START[@]}" dump --memory=full,summary > "$D/skyvalue-memory.json"
"${START[@]}" shutdown
```

Give preparation/restart/warm distinct BEP/profile/log paths and run the existing executable digest/behavior oracle after each build. Ensure shutdown also occurs on failure; the guard is the outer task-only cleanup boundary. Decode the copied recordings using an existing compatible JDK's `jfr summary "$D/restart.cpu.jfr"` and `jfr print --json --events jdk.ExecutionSample,jdk.NativeMethodSample "$D/restart.cpu.jfr"`; inspect the summary's event inventory and retain unknown events/frames. A missing decoder/profiler is an explicit attribution gap, not permission to install tooling or change global perf settings. These two sessions require only six N1 build commands total, including preparations; their CPU/PSS values are diagnostics, not additional gate-performance trials.

## Decision to write once closure evidence exists

- **Continue to bounded M1 investigation** if substantial retained evaluator state or evaluator/validation CPU is directly evidenced, correctness is intact and the remaining opportunity has a concrete target. State which leg supports continuation and which costs remain unknown. M1 must still prove semantic overlap and charge validation/index overhead.
- **Redirect to the existing native-hosting comparator** if runtime/startup work explains the substantial cost and graph/validation opportunity is small; one JVM with private engines then tests the cheaper hypothesis. This is an explicit plan pivot, not an evaluator-sharing success.
- **Stop or redirect toward execution/bootstrap** if the completed relevant strata are dominated by those costs and the diagnostic leaves little evaluator opportunity. If the bounded closure cannot distinguish the alternatives, report M0 as inconclusive and stop for the architectural decision rather than extending research indefinitely.

No gate outcome is assigned by this draft. No builds, runtime-filesystem reads or frozen-harness edits were performed to prepare it.
