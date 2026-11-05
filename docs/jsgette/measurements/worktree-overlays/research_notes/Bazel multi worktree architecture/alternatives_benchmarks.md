# Alternative architectures and benchmark gates

## Which architecture alternatives merit implementation?

### Takeaway

Keep the native branch as the starting experiment, keep independent Bazel servers with equivalent shared action caches as the control and fallback, and make ownership/lifecycle plus a single safe reuse boundary the next MVP. A multi-version graph is a credible eventual design, but a scheduler transplant cannot remove workspace context from values or make mutable outputs safe.

### Cited Findings

- Investigation is pinned to `JSGette/bazel` commit `4f4b44b5a2c1780cdbb0215c4556de39d0f0a995`. Its record describes two private engines in one JVM, eligible equal-action reuse through the disk CAS, private evaluators, output bases and cancellation contexts, and disabled global profiling/worker/dynamic-execution facilities. It explicitly excludes shared Skyframe graphs and shared Starlark compilation. This is a recorded implementation/validation claim, not a benchmark rerun in this investigation. [Branch design](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-native-mvp-2026-10-31.md#L81-L117)
- Bazel's remote cache contains action-result metadata and content-addressed output files. Its documented sequence still constructs targets/actions before cache lookup. Thus ordinary action caching is a strong control for execution reuse but does not by itself eliminate loading/analysis work. [Bazel remote caching](https://bazel.build/remote/caching)
- Bazel's output layout deliberately supports simultaneous workspaces and distinct configurations; the default output base derives from workspace-root path. This existing separation remains a useful output and clean-lifecycle boundary. [Output layout](https://bazel.build/remote/output-directories)
- The Build Systems à la Carte paper separates the scheduling decision (order) from rebuilding/reuse (whether and how to rebuild). It also models dynamic dependency discovery and early cutoff. The decomposition supports changing a narrow subsystem first; the 2018 paper is a conceptual model, not a description of all current Bazel implementation details. [Peer-reviewed paper](https://www.microsoft.com/en-us/research/wp-content/uploads/2018/03/build-systems-final.pdf)
- Modern DICE uses asynchronous computations, transaction-local work sharing, and a versioned state store. Its presentation emphasizes correct key/value equality, keeping result-affecting inputs out of untracked UserData, and recording dependency order/parallel groups to avoid invalid speculative requests after input changes. [DICE presentation](https://buck2.build/assets/Modern_DICE.pdf), [official explanation](https://buck2.build/docs/insights_and_knowledge/modern_dice/)
- Pinned Buck2 documentation says equal-key requests share work and DICE uses Rust/Tokio futures. Its cancellation documentation describes retaining shared work while another subscriber needs it. These are useful semantics to emulate, not proof of Bazel integration. [Parallelism](https://github.com/meta/buck2/blob/13eb771808f0f186fffff280678d85062acf11cf/dice/dice/docs/parallelism.md), [cancellation](https://github.com/meta/buck2/blob/13eb771808f0f186fffff280678d85062acf11cf/dice/dice/docs/cancellations.md)
- The same pinned DICE docs explicitly warn that concurrent transactions in different states are unsupported; another page says concurrent different versions and multiple node versions are not yet supported. These pages may lag the implementation, but they do not establish arbitrary multi-worktree branching as an existing, usable feature. [API warning](https://github.com/meta/buck2/blob/13eb771808f0f186fffff280678d85062acf11cf/dice/dice/docs/api.md), [incrementality warning](https://github.com/meta/buck2/blob/13eb771808f0f186fffff280678d85062acf11cf/dice/dice/docs/incrementality.md)
- Existing Bazel remote-analysis infrastructure is present in the pinned branch. Factory construction includes configuration checksum, installation identity, Starlark semantics and workspace-version information. It is a potential serialization/reuse substrate, but not evidence that arbitrary live values or their invalidation edges can be shared. The Skyframe researcher independently found context-sensitive output identity and special invalidation requirements; incorporate their more detailed cited treatment. [Factory](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis/RemoteAnalysisCacheFactory.java#L134-L161)

### Inferences

The following are design judgments, not measured rankings:

1. **Independent servers plus shared CAS:** smallest integration surface, natural failure and command isolation, parallel commands already possible. Duplicated JVMs and graph state remain. Preserve this as the reference and fallback, not as a straw-man uncached baseline.
2. **One daemon with private engines and shared immutable payloads:** closest to the current branch. It can reduce process overhead and duplicate data without merging dirty bits, reverse dependencies or action ownership. Remaining graph structure remains roughly per-engine; process-global services, fairness and reclamation require explicit ownership. This is the most credible next MVP direction.
3. **Content-addressed serialized analysis or compiled-program payload store:** potentially saves cold loading/analysis work while keeping live dependency graphs local. Keys must include the complete semantic context and dependencies; deserialization must rebind workspace/output identity. Storage identity alone is not an invalidation proof. Reuse existing serialization infrastructure where it fits; first support one audited value family. Misses, unsupported types and cache eviction must safely fall back to ordinary evaluation.
4. **Frozen shared base graph with per-worktree overlays:** potentially attacks the large duplicated analysis graph directly. A shared node must be immutable and evaluated under a semantic input snapshot; dirtiness, reverse-edge changes and task state stay in an overlay or version-specific record. An overlay cannot merely replace source-file nodes in the current graph: package discovery, negative paths, glob membership, repository mappings, configuration transitions, toolchain selection and output roots can also diverge. Shared Git ancestry is only a reuse hint, never a validity certificate.
5. **New multi-version evaluator behind a narrow adapter:** justified if validated reuse requires atomic snapshot reads/branch-local invalidation that cannot be implemented economically over Skyframe's storage/evaluator contracts. Preserve Bazel rule semantics and execution backend while first dual-running a restricted pure subgraph. The adapter must preserve dynamic dependency groups, restart behavior, transient errors, cycles, cancellation and event handling. Version is a workspace state/snapshot, not just a globally increasing counter applied to every worktree.
6. **Replace Skyframe wholesale or embed DICE:** largest migration surface and no demonstrated benefit yet. A Rust async engine would require substantial Java/Rust boundaries or a port plus semantic adapters. DICE's useful design ideas are separable from taking its implementation. Neither choosing DICE nor using a different scheduler solves untracked ambient context, output materialization, global runtime ownership or source mutation during a build.

Recommended next MVP contract: preserve two concurrent engines, make an engine's ownership/lifetime explicit, permit safe retirement/reuse of a slot only after commands and shared consumers drain, and prove that closed engines become collectible. Add explicit daemon admission/accounting sufficient to keep aggregate action budgets honest. Keep existing small immutable interning. Add at most one further sharing boundary if the source audit identifies a clearly context-free payload; do not expand capacity, graph semantics, persistent storage and execution strategies simultaneously. The root agent's audit found strong engine retention through server/crash ownership and engine construction under the daemon monitor; those should be addressed or explicitly bounded before claiming scalable memory or switching behavior.

An eventual shared-program key should distinguish source bytes, parser/compiler version, semantic options and any resolution inputs that influence compilation. Source display paths, module bindings, package context and action roots should be explicit per-engine bindings rather than silently erased. A semantic value's key needs all relevant dependencies, not only that file's content hash. Store hits are only a mechanism metric; final criteria remain memory and end-to-end commands.

### Gaps

- No replacement engine prototype was compiled or benchmarked here; there is no evidence that replacing Skyframe is presently necessary or faster.
- The DICE documentation warning must be checked against current implementation and tests before claiming a hard implementation limit. It is enough to reject an unsupported assertion that DICE already solves this problem.
- The best next payload family depends on the source audit and retained-heap ownership attribution. Existing counts do not identify deep retained ownership.
- Remote-analysis service deployment availability, practical serialization hit rate and worktree-safe rebinding are not established by finding the API.

## What does the existing evidence establish?

### Takeaway

The branch has functional evidence of concurrent commands and deduplicated eligible execution, plus one small immutable-payload memory observation. It has not yet demonstrated broad memory savings, whole-graph reuse, or superiority to an equal-budget concurrent independent-server control.

### Cited Findings

- The recorded Agent experiment has 30 spawn requests, 15 local executions and 15 cache hits, with one execution per equal digest pair and matching executable/test results. Its three timing rounds compare one vanilla build (median 35.216 seconds) with a native pair (38.466 seconds batch makespan). This is not a two-independent-server versus two-native-engine comparison. [Narrative evidence](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-native-mvp-2026-10-31.md#L7-L25), [raw comparison](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/measurements/multi-worktree-native-mvp-2026-10-31/duration-comparison.json)
- Recorded node counts remain 80,847 per native engine / 161,694 combined. Directory and line-table interning reduced two-engine live shallow-object totals from 169.52 MiB to 167.31 MiB in one sequential fresh-daemon trial; the graph-node count did not change. The record correctly distinguishes this 2.21 MiB observation from graph retained size or RSS. [Payload record](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-native-mvp-2026-10-31.md#L25-L56), [raw snapshots](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/measurements/multi-worktree-native-mvp-2026-10-31/payload-reuse.json)
- Existing Phase 0 documentation already specifies matched source builds, aggregate resources, controlled cache states, output oracles, 30 paired trials, at least 100 observations for p95, and second-session replication. It also separates low-overhead timing from diagnostic profiles/GC/process sampling and labels fixed-order/JIT/cache confounders. Reuse this work rather than replacing the harness. [Protocol](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-phase0-2026-10-31.md#L29-L74), [representative runner](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/measurements/multi-worktree-phase0-2026-10-31/harness/representative.py#L164-L183)
- Bazel documents BEP, queries, execution logs and JSON traces as performance evidence. JSON profiles can expose loading/analysis, action activity and GC, but the native slice currently disables the process-global profiler; its command-scoped BEP/spawn logs remain available. [Metrics](https://bazel.build/advanced/performance/build-performance-metrics), [trace profile](https://bazel.build/advanced/performance/json-trace-profile), [native scope](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-native-mvp-2026-10-31.md#L81-L87)

### Inferences

- Do not subtract small integer node headers from total heap and call the remainder shareable. Arrays, strings, configured targets, actions and module objects have ownership relationships that class histograms do not reveal.
- Do not infer two-build speedup from native pair time divided by a single vanilla invocation, or from the fact that each eligible digest executes once. The native pair may save work but still compete for analysis CPU, graph memory and shared locks.
- Do not count lack of action execution as a warm incremental-graph benefit: primed CAS and an already warm local graph answer different questions.
- Restoring profiling is a product change with concurrency implications. For the initial benchmark use equivalent low-overhead BEP/spawn logs plus external process/cgroup sampling and JVM diagnostics, and expose only narrowly scoped counters needed to explain results. Run intrusive heap dumps/full GC separately from latency trials.

### Gaps

- Existing evidence is retained branch evidence only; this researcher did not rerun those tests.
- No direct controlled two-server shared-cache benchmark, broad target corpus, or 4/8-engine native benchmark is established.
- The memory observation does not show engine reclamation after close, sustained churn, peak process-tree pressure or marginal behavior at higher engine counts.

## What benchmark and promotion gates should follow the MVP?

### Takeaway

Finish the smallest correctness-complete slice first, then run a predeclared matrix against both stock independent servers and the original native branch. Admit a larger graph redesign only when the evidence identifies duplicate evaluation/storage as the remaining bottleneck and an isolated replacement slice clears the same correctness oracle.

### Cited Findings

- The existing project proposes at most 5% median and 10% p95 warm-single-worktree regression, no concurrent throughput regression beyond uncertainty, and zero unexplained output/lifecycle divergence. These are acceptance budgets, not measured achievements. [Existing gates](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-phase0-2026-10-31.md#L39-L46)
- The recorded host's four-job budget makes eight independent `--jobs=1` clients an oversubscribed experiment. The protocol already calls for labeling it or using a larger host. [Concurrency qualification](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-phase0-2026-10-31.md#L62-L72)
- Bazel exposes post-GC used heap and BEP memory statistics; its documented metrics distinguish workers from JVM heap. These supplement, rather than replace, OS process-tree accounting. [Bazel performance metrics](https://blog.bazel.build/2022/11/15/build-performance-metrics.html)

### Inferences

All protocol choices and numeric targets below are proposals to freeze before collecting results.

**Comparison arms**

- A: unmodified source build at upstream base `9a2ababbd5cf113c31450fbe9214b501be8c58a2`, one server/output base per worktree, common disk action/repository caches configured equivalently.
- B: current native branch `4f4b44b5a2c1780cdbb0215c4556de39d0f0a995` as-is (two engines maximum).
- C: new minimal MVP, same upstream, compiler/JDK, binary-build mode and supported feature envelope.
- D: diagnostic ablation of C with the one new reuse feature disabled, if C adds sharing. Separate ownership/lifecycle benefit from payload-reuse benefit.
- Optional independent-cache A variant quantifies cache benefit; do not substitute it for A. Optional external deduplicating execution service is a future systems comparison, not an MVP dependency.

**Resource/cold-state contract**

Record CPU affinity/quota, total RAM/cgroup limit, disk/filesystem, actual JDK/GC flags, source/dirty-file digests, binary hashes, target set, rc files, declared environment allowlist, all flags and cache manifests. Hold total host CPU and memory ceilings constant; do not give every independent JVM the native daemon's whole heap allowance. Report both total process-tree pressure and JVM caps, since equal Xmx alone is not equal memory. Keep aggregate jobs/loading concurrency honest; separate action quotas from hard CPU/memory limits. Use cloned cache snapshots per trial or reproducible prime/reset scripts so trial order does not improve later arms. State server graph, action CAS, repository download, repository-contents and OS page-cache temperature separately.

**Workloads**

1. One and two worktrees immediately; 4 and 8 only after capacity/lifecycle support exists. For the original two-engine branch record unsupported cells, never fake scaling by comparing different concurrency. Use an adequate host or explicit admission/oversubscription experiment for 8.
2. Identical sources/targets: cold daemon+cold actions, cold daemon+primed actions, warm no-op, and A-B-A switching with retained private state.
3. Slight and substantial divergence: leaf source edit; high-fanout header/source; BUILD edit; `.bzl` implementation edit; globs, missing path creation/deletion and file/directory type changes; module lock/override, repo mapping, toolchain, platform/transition, environment and command/config changes. Restore A after each mutation and verify B remains unchanged.
4. Identical action request bursts, unequal actions with coincident relative paths, conflicting output content, cache miss/hit mix, large outputs and publication delay. Measure execution dedup separately from two materializations. Include failures/cancel at publication boundary, producer/waiter cancellation and retry.
5. Short interactive command beside a long build, warm A while cold B initializes, simultaneous commands to the same engine, full capacity admission, scoped shutdown, slot reuse and worktree churn. Repeatedly close/reopen engines with different paths/configs, measuring memory after drain. Retired engines must lose owner roots, not just disappear from the admission count.
6. Small deterministic fixture for diagnosis; existing pinned DataDog target and three-test cohort for continuity; then at least one analysis-heavy corpus with diverse Starlark/config transitions and one action-heavy C++/Java corpus selected and pinned before qualification. The existing small Go target is useful but not a universal monorepo proxy.

**Correctness oracle before timing**

For every supported source/config snapshot, run isolated stock Bazel in a private output base using isolated fresh verification cache (or read-only prevalidated contents), then compare exit status, deterministic output digests, test identities/results, and relevant normalized action descriptors. Also retain fixture-specific independently computed expected output so a common cache or inherited stale state cannot mask contamination. Normalize invocation IDs/timestamps only by explicit allowlist; preserve workspace paths when testing path correctness. Non-reproducible outputs need declared semantic comparison or exclusion, not arbitrary digest suppression. Concurrent trials must produce the same per-worktree results as serial isolated execution. One mismatch fails the candidate. Deadlocks, unexpected cross-cancellation, output overwrite, falsely accepted stale snapshot, nondeterministic success/failure, and leaked engine roots are failures, not slow samples.

**Metrics and statistical unit**

- End-to-end client wall time, queue delay, active service time, cold-start/engine-initialization time, no-op time, edit-to-result time, pair/batch makespan, successful commands/sec and per-worktree p50/p95 latency.
- Total descendant user+system CPU, executed action count, action hit/dedup/materialization time, loading/analysis counts, node counts, shared payload counts/bytes and cross-engine lock/wait duration.
- Peak total cgroup/process-tree memory; PSS where available, RSS separately; live post-GC heap, allocation rate and GC pauses; retained heap/dominator ownership in diagnostic runs; marginal cost `M(N)-M(1)` and memory after engine retirement. Graph payload/store bytes must be distinguished from bytes strongly retained by engines.
- Cache/output storage bytes and churn growth; cache hit count alone never establishes user benefit.
- Preserve failures, timeouts, shutdown results and raw observations. Use at least 30 randomized/interleaved paired **batches** per main comparison, with a predeclared warmup/JIT policy. Commands in a shared batch are correlated; confidence intervals/resampling should use paired batches, not treat each worktree as an independent replicate. At least 100 observations per arm before a p95 claim, and sufficient batches plus intervals for credibility. Repeat the main result in a second session.

**Stage gates**

1. MVP exit: isolated oracle and concurrency/lifecycle suite pass; unsupported features reject predictably; instrumentation can attribute scoped work; two engines can progress without cross-contamination; reclamation is demonstrated if slot reuse is claimed. Stop implementation here and run benchmarks.
2. Keep or simplify: preserve the existing proposed 5% median / 10% p95 single-worktree budgets. For promotion require a preregistered meaningful benefit: suggested default at least 10% lower end-to-end batch time or at least 15% lower total retained process footprint on the chosen primary workload, with confidence interval excluding no improvement and no material throughput/peak-memory regression. These new percentage targets are negotiable engineering thresholds, not facts. If memory is the priority, use memory as primary rather than choosing the best-looking metric afterward.
3. Extend shared immutable/serialized state when heap ownership or profiles show this can hit the primary objective and a complete semantic identity can be stated. If engine retention/global locks dominate, repair those instead; a new evaluator will not fix them.
4. Authorize a replacement spike only when a narrow current-Skyframe implementation is demonstrably blocked by its graph/version contracts or misses the performance objective after measured low-risk options, and the blocking mechanism is shown in profiles/ownership evidence. Dual-run one pure subgraph with adversarial snapshots and differential traces first. Require identical results and a material measured advantage before migrating more SkyFunctions.
5. Polish only after the chosen architecture wins: resource tuning, more engines, diagnostics, process failure handling, eviction, persistent cache formats/GC, workers/remote/dynamic execution, platform coverage and compatibility. Every newly enabled execution strategy gets its own correctness cases; do not hide all of these inside the MVP.

### Gaps

- User priority among memory, switching latency and concurrent throughput is not yet fixed; choose and state one primary metric before running qualification, with the others as guardrails.
- No new timings, tests or performance claims were produced by this research. The protocol is ready to implement after the design's MVP slice is selected.
