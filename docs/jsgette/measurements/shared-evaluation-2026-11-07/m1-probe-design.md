# M1 probe design: test the existing native mode before shared evaluation

Status: M0 passed its repeated-state continuation gate under [D019](decision-log.md); M1 is in progress. The experiments below remain a design, with execution and results recorded separately.
The unmeasured hypothesis is that much of restarted-cohort CPU comes from JVM startup, class loading and JIT compilation. One JVM hosting private engines might meet the gates without sharing their graphs. Measure the existing native mode as a cheaper alternative first; do not infer its benefit from current timings. The [source preflight](m1-native-preflight.md) establishes that native mode also changes modules, interner policy, metrics and action coalescing. This is a comparison of that combined implementation with private daemons, not an isolated measurement of JVM hosting.

## 1. Comparator and bounded admission experiment

Use one newly built, digest-pinned binary from this fork for **both private daemons and native hosting**. The M0 safe-subset binary is a different source tree (`a23ae689` plus dirty patches); comparing it directly with this fork would mix implementation changes with hosting effects. Record source commit, patch hash, binary hash, fixture tree, effective flags and caches for each arm.

Current native entry point is `--experimental_shared_server=<name>`; each client runs from its own worktree with the same startup settings. The launcher derives distinct engine output bases; do not pass `--output_base` in native mode. The existing limit is `sharedEngines.size() + failedEngines.size() >= 2` in `src/main/java/com/google/devtools/build/lib/server/CommandServer.java:304`. Failed and closed slots are retained until daemon restart.

First qualify two engines without changing that limit. Example invocation, launched concurrently from both fixture worktrees:

```sh
cd "$WORKTREE"
"$B" --ignore_all_rc_files --output_user_root="$R/native-user" \
  --experimental_shared_server=m1n2 \
  --host_jvm_args=-Xmx1536m --host_jvm_args=-XX:ActiveProcessorCount=2 \
  build //pkg/networkdevice/profile/profiledefinition/normalize_cmd:normalize_cmd \
  --repository_cache="$REPO_CACHE" --build_event_json_file="$BEP"
```

`B`, `R`, `WORKTREE`, `REPO_CACHE` and `BEP` are explicit task-owned or pinned paths supplied by the runner. Reuse those startup arguments with `info server_pid` and `info output_base`: require one PID and two distinct output bases. Keep normalized launcher environment identical; do not persist raw environment values. Native startup restrictions are in `src/main/cpp/startup_options.cc:798`; usage is in `tools/multiworktree/NATIVE.md`.

Only after N2 correctness and measured usefulness justify the bounded extension, consider a deliberately experimental cap-ten patch: replace the admission literal with one named limit of ten in `CommandServer.java`, update its diagnostic and `BlazeServerStartupOptions.java:151` help, and parameterize the acceptance harness's expected admission boundary (`tools/multiworktree/native_integration_test.py:214`). Keep the ownership, failed-slot accounting, shutdown and command restrictions unchanged. This is not a new eviction or scheduling design.

Use private `N × -Xmx768m` versus native `-Xmx(N*768)m`: 1536 MiB at N=2, 7680 MiB at N=10. Maximum aggregate heap is matched, not assumed resident memory. Keep `ActiveProcessorCount=2` per JVM, and record that JVM-wide JIT/GC pools therefore differ by design. Both arms use the frozen M0 outer CPUQuota=600%, MemoryMax=10 GiB (10240 MiB), MemorySwapMax=0 and Windows reserves; do not enlarge the native budget to obtain a pass. At N=10, the maximum heap leaves only 2560 MiB (2.5 GiB) for native allocations and charged file memory; enforce the actual cgroup cap and host guard. D012 records why the earlier 12 GiB setting was abandoned.

The native invocation policy at `BlazeCommandDispatcher.java:950` forces two jobs, two loading threads, two estimated CPUs and 1920 MiB action memory **per engine**. Each command creates its own `ResourceManager` (`BlazeWorkspace.java:348`), configured in `ExecutionTool.java:989`; these managers do not enforce a shared aggregate admission budget. Ten engines could admit twenty actions and 19,200 MiB of estimated action memory. The outer cgroup bounds real use; it does not make those estimates a fair shared scheduler. Begin with analysis-only qualification and prepared action caches. A resource abort is an infrastructure result, not a graph-sharing result.

Mirror native effective policy in the same-fork private control, including sandbox/local strategies, disabled workers, digest-cache settings and synchronous disk-cache publication. Native forces a daemon-local action CAS and **private per-engine repository-contents caches** (`BlazeCommandDispatcher.java:962–983`); a shared repository-contents cache in the private arm would be a separate experiment. Prepare equivalent independent cache populations and verify canonical BEP command lines. Eligible in-flight action sharing already exists in native mode: record executed actions/cache hits so its benefit is not mislabeled JVM/JIT savings.

Run restarted engines with prepared caches and warm persistent engines separately. Retain executable hashes and behavior oracles for every worktree. Measure cohort CPU, daemon PSS and wall time with the existing outer accounting. Native mode disables global JSON profiles (`BlazeCommandDispatcher.java:984–1008`): explicitly mark them unavailable and use BEP plus external counters in both arms. Adapt harness PID accounting to expect one native daemon, without accepting missing engines; verify every engine's PID/output-base mapping.

**Decision:** if the native implementation meets the later CPU/RAM targets with output isolation intact, prioritize that smaller implementation and its resource scheduling. Report the remaining policy differences rather than attributing the gain solely to JVM hosting. Otherwise proceed to traces to identify the remaining evaluator opportunity. Neither outcome is established yet.

## 2. Opt-in, per-engine evaluator traces

Add a small diagnostic collector and command option, registered through a `BlazeModule` (`BlazeModule.java:204,215,335`; `Bazel.java:42`). Wire its no-op-by-default callbacks through `SkyframeExecutor.SkyframeProgressReceiver` (`SkyframeExecutor.java:3589,3631,3638,3645`). Native engine creation already instantiates separate modules/runtimes (`BlazeRuntime.java:1457`); keep collector state instance-local, never in the global profiler.

Bound product edits to the collector/module/options and their BUILD target, module registration, and forwarding calls in `SkyframeExecutor`. No LocalAnalysisCacheClient import, cache admission, evaluator scheduling change, or graph serialization protocol is needed. The public `SkyKeyStateReceiver` alone lacks result/dependency and CHECK_DIRTY callbacks, so it is insufficient for this probe.

Record command/engine IDs, typed query description, every compute attempt, CHECK_DIRTY and COMMIT intervals, evaluation status, grouped direct dependencies and supported result projections. Existing symmetric callbacks bracket `SkyFunction.compute` including restarts (`AbstractParallelEvaluator.java:473`); `EvaluationProgressReceiver.java:137` supplies newly evaluated values/dependencies. Capture unchanged nodes from the retained graph after the command; never interpret a null `newValue` as a missing result. Bound buffers and mark overflow/incomplete traces explicitly. Stream immutable rows, not retained SkyValue references.

Use `ThreadMXBean` CPU deltas for supported platform threads and monotonic wall intervals. `WorkerSkyKeyComputeState.java:160` creates virtual-thread workers; their CPU can be unavailable or occur outside the caller's interval. Preserve unknown CPU, account restarted attempts, and reconcile summed trace CPU against whole-daemon CPU. JIT, GC, workers and uninstrumented work remain an explicit residual; negative/unavailable counters are not zero-cost queries. Keep trace overhead outside the baseline cohorts and measure a small on/off pair.

Supported first-pass identities: file kind plus content digest when present (`actions/FileStateValue.java:286`), exact sorted directory entries and types (`DirectoryListingStateValue.java:89`), glob matches (`GlobValue.java:32`), and the Starlark transitive input digest (`BzlLoadValue.java:84`). Include typed query inputs, configuration and repository bindings. A metadata-only file proxy, unsupported ambient input, or unsupported value stays **unknown**. A Starlark input digest is input evidence, not by itself a complete output fingerprint.

Configured-target diagnostic names, normalized physical paths, object identity and `toString()` are not semantic result identities. Replay may report an explicitly optimistic name-based ceiling, alongside the supported typed subset; never authorize reuse or claim a proven lower bound from that ceiling. Account for dependency records, indexes, retained variants and measured replay validation overhead before comparing potential savings with the gates.

Collect retained-size evidence in a separate quiescent run. Use whole-JVM live-heap diagnostics for native/private totals; reuse `SkyframeMemoryDumper.java:56` and `MemoryAccountant` for bounded single-engine value attribution. Native command admission excludes `dump`; do not silently enable it or turn on global profiling. Serialized bytes are not retained heap, and per-value deep sizes must not be summed across shared reachable objects.

## 3. Minimum checks before interpreting results

- Native cap: N engines attach to one PID with distinct outputs; N+1 rejects; failed/closed slots do not bypass the bound; last shutdown exits. Preserve cancellation and directory-ownership tests.
- Native semantics: identical, distinct source, BUILD, configuration and glob variants match the same-fork private oracle. A two-engine barrier proves overlap and engine isolation before N=10.
- Trace lifecycle: restarted attempts aggregate correctly; exception/cancellation paths close intervals; unchanged values are recovered; concurrent engines cannot mix records; a later command starts clean; bounded overflow rejects a complete-trace claim.
- Identity: missing/present path, symlink/type, directory membership, same-size content, configuration and repository-binding changes alter supported evidence; unsupported values remain unknown.
- Accounting: unavailable thread CPU stays unknown, trace CPU reconciles with process totals, and tracing does not change outputs. Separate size runs and diagnostic overhead from performance gates.
- Replay: independent small fixtures verify duplicate grouping, variant retention and validation/index charges; report both exact supported coverage and optimistic ceiling. Stop if even the optimistic ceiling cannot meet the gates.

## 4. Accounting controls before the native/private comparison

The frozen v7 sampler measures an instrumented cohort, not pure Bazel analysis. `harness/baseline.py:178–229` brackets scope CPU around initial, background and final PSS reads; `:365–407` also performs BEP/sidecar parsing, executable hashing and the behavior-oracle subprocess inside that window. Per-client wall time ends at Bazel exit, whereas cohort wall includes postprocessing and the final resource report (`:421–436`). The initial sample is inside scope CPU but before cohort wall starts. Shutdown and final source guards are outside both measured windows. Preserve these meanings for M0; do not retroactively subtract an estimated observer cost.

Static `evidence/m0-v7b-01/source-{vanilla,sky}-r-run.tar.gz` records scope-minus-daemon CPU of 7.634 versus 24.941 seconds. This residual includes harness, clients, compiler/oracle children and counter-boundary differences; its sampler share is unknown. There are 216 versus 593 samples, with median intervals of 193 versus 141 ms: the loop waits 100 ms **after** each variable-cost sample. Cohort wall exceeds the longest client wall by only 42 versus 56 ms, but earlier clients' postprocessing can overlap later builds, so that difference does not bound total observer/oracle CPU.

For M1, verify every engine's output-base/PID mapping, then sample each unique `(PID, process-start-time)` once. The current per-base PSS sum would count one native JVM ten times. Measure `time.thread_time_ns()` around every sample on its calling thread, including initial/final main-thread samples; record sample duration, count and read failures. Keep whole-scope and daemon CPU as separate raw measurements and report sampler CPU diagnostically. A small sparse-sampling/on-off qualification should check induced overhead; subtracting measured reader CPU alone cannot undo scheduling or kernel-lock effects.

End a separate build-only measurement window when all Bazel clients exit; run required digest/behavior/glob verification afterward and reject the row on any failure. Preserve the full instrumented end-to-end result as a distinct metric. Use equal sampling cadence and matched private/native preparation history, with oracle generation in a separate pool or the same number of prior successful preparation/measurement cycles in both arms. Counterbalance arm order; do not equate a stopped daemon with an empty output base.

M0's vanilla reference is pool run 1, its subsequent measured row is run 2, and the SkyCache row is run 1; every restart row performs another preparation/shutdown before measurement (`baseline.py:682–696`). At N=1, vanilla reference-to-run-2 peak PSS changes 716.49 to 501.95 MiB in v6 and 713.51 to 505.65 MiB in v7b. Candidate-off changes 507.65 to 713.34 MiB across those protocols. These observations flag lifecycle/GC/cache sensitivity, without identifying its cause; sampled PSS is not post-GC retained heap. Successful M0 rows still establish the recorded correctness checks and observed costs under their documented protocol, not a causal sharing benefit or a gate decision.

