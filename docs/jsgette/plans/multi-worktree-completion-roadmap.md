# Native multi-worktree Bazel: limitations and completion roadmap

**As of 31 October 2026.** Implementation reviewed at
[`38400a54086ff4c4bc3a0e26acf7f4ba8196302e`](https://github.com/JSGette/bazel/commit/38400a54086ff4c4bc3a0e26acf7f4ba8196302e),
branch `jsgette/native-multi-worktree-mvp`.

This is the consolidated current backlog. It supersedes historical limitation
statements where later native work has addressed them. The earlier Python
supervisor is a separate fallback prototype; its eviction, queueing and recovery
behavior must not be attributed to the native daemon.

“Implemented” means present in this revision. “Verified” means covered by the
linked bounded experiments, not certified for every Bazel workload. Missing tests
and open audits are qualification gaps, not assertions of known correctness bugs.
Priorities and completion checks below are proposed work, not delivery estimates.

## 1. What “complete” means

There are three distinct finish lines:

1. **Operationally complete native support:** compatible worktrees build/test
   concurrently in one daemon with explicit ownership, resource scheduling,
   lifecycle/recovery, correct diagnostics and a documented feature matrix.
   Worktree churn must not require routine restarts or cause unbounded growth.
2. **The original reuse objective:** private mutable engines reuse audited
   expensive immutable computations/payloads with a repeatable net benefit. Finish
   the original pure compilation boundary and bounded completed-result cache, or
   explicitly replace that milestone with a measured alternative. Action
   deduplication alone does not complete this goal.
3. **Full graph sharing:** common graph structure/values are stored once, with
   workspace-specific differences. This is a larger representation/invalidation
   project. It is not implemented by weak interning, a disk cache or one JVM.
   If “complete” requires one graph with changed subtrees, section 8 must also pass.

The pragmatic path is to finish 1 and 2 incrementally and measure whether/how much
of 3 is justified. A Linux-only release can be complete for an explicit Linux
contract; it must not be described as cross-platform or ordinary-Bazel parity.

## 2. Current implementation and evidence

| Area | Current result | Boundary |
| --- | --- | --- |
| Native execution | Two private `BlazeRuntime` engines in one JVM; simultaneous builds/tests | Two retained slots; commands within one engine serialize |
| Isolation | Private evaluators, modules, output bases, execroots, repository views, local action state and command lifecycles | Process, GC and fatal crashes remain shared |
| Action reuse | Equal eligible REAPI actions wait for one producer and reuse synchronously published disk-CAS results | Cacheable local spawns; producer cancellation may cause re-execution |
| Payload reuse | Weak interning of directory-entry lists and Starlark line-offset tables | Paths, ASTs, bindings, graph topology and invalidation stay private |
| Graph storage | 80,847 nodes per native engine; 161,694 for two in the Agent sample | No common graph plus overlays |
| Heap sample | Two-engine live object totals: 169.52 → 167.31 MiB; B's increment: 62.12 → 59.73 MiB | One sequential full-GC trial per candidate; whole-JVM shallow sums, not graph-retained size or RSS |
| Original timings | Three cold-action rounds: vanilla median 35.216 s; concurrent A/B medians 38.466/38.452 s | Pair about 9.23% slower than one vanilla build; small target and fixed host |
| Latest timing smoke | Vanilla 36.668 s; concurrent A/B 38.931/38.943 s | One follow-up round, not statistical qualification |
| Action-count proof | 30 requests, 15 executions, 15 cache hits; one execution per digest pair | Also 10 internal actions per engine; internal work remains duplicated |
| Validation | Latest payload change: 629 focused cases and 15 native acceptance checks pass | Earlier native revision passed 163 focused cases; these are not one full release-suite run |

The Agent target is `//pkg/util/strings:strings_test` at
`a83c9fa5161b775ed24b1df825db4817ea562a89`. Outputs matched the independent-server
control and tests passed. The latest heap run retained 1,729 directory payloads
instead of 4,249 and 698 line tables across both engines. One-engine raw heap
increased by 0.18 MiB; JVM housekeeping allocation varied. These samples establish
working reuse, not a general memory/speed guarantee.

Evidence: [native report](multi-worktree-native-mvp-2026-10-31.md),
[original timings](../measurements/multi-worktree-native-mvp-2026-10-31/duration-comparison.json),
[graph counts](../measurements/multi-worktree-native-mvp-2026-10-31/graph-ownership.json),
[payload measurements/tests](../measurements/multi-worktree-native-mvp-2026-10-31/payload-reuse.json),
[latest cold-action smoke](../measurements/multi-worktree-native-mvp-2026-10-31/payload-reuse-duration.json).

## 3. Current compatibility and feature restrictions

These are product restrictions, not merely missing benchmarks.

| Surface | Restriction today | Required follow-up |
| --- | --- | --- |
| Platforms | Linux; exercised on Linux/WSL2 x86-64 | Publish Linux/filesystem matrix; separately port/qualify macOS and Windows |
| Commands | Only `build`, `test`, `info`, `shutdown` | Audit desired `clean`, `run`, `query`, `cquery`, `aquery`, `fetch`, module/admin and other commands individually |
| Startup | Workspace required; rejects `--batch`, `exec-server`, explicit `--output_base`, `--delete_output_base_on_shutdown`, `--run_in_user_cgroup` | Define safe semantics or retain explicit exclusions |
| Admission | Two slots including closed/failed engines; third rejected; preemptible shared requests rejected | Configurable admission, safe recycling, queueing/fairness and defined preemption |
| Compatibility | Same binary/install and conservative JVM/startup environment profile | Finish semantic audit, diagnostics and fallback selection |
| Wrappers | `PWD`/`OLDPWD` excluded from JVM identity but retained as command environment; other startup environment must match | Productize Bazelisk/wrapper integration; Agent used an adapter to normalize JVM PATH and forward original PATH/PWD |
| Execution | Forced `sandboxed,local`; workers, dynamic execution and sandbox reuse disabled | Isolate lifetimes and re-enable modes individually |
| Remote services | Remote executor/cache disabled; remote downloader/output service overrides reset | Qualify remote services, credentials, async callbacks, downloads and teardown |
| Caches | Private shared disk cache forced; reads/writes on; synchronous upload; all outputs materialized | Document/support policy combinations without weakening cacheability/publication semantics |
| Resources | Two jobs, loading threads and local test jobs; estimated two CPUs/1920 MiB per engine | Daemon scheduler with explicit budgets instead of fixed splits |
| Other forced options | Full concurrent-input guard; computed-file-digest cache zero; nondeterministic memory optimizations off; mnemonic/regexp/platform strategy overrides reset | Audit overrides; restore compatibility or explain rejection and cost |
| Profiling/debugging | JSON trace, command/CPU/memory/Starlark profiles and debugger unavailable | Invocation-aware concurrent implementations |
| Metrics/pressure | Worker, process memory/network and selected package/GC metrics suppressed; pressure/suspension and GC-after-build modules filtered | Separate daemon/invocation metrics and restore safe pressure handling |
| Maintenance | Shared-mode idle-task manager disabled; no per-engine idle eviction or automatic cache/output collection | Owner-aware maintenance and bounded storage |

The module filter also excludes `PostGCMemoryUseRecorderModule`,
`AllocationTrackerModule` and the trace-profiler service. `System.out/err` is not
patched per invocation: direct process-stream writes cannot rely on ordinary
per-command redirection. BEP/execution logs work, but omitted metrics are not
measured zeros.

The policy overrides user/rc choices. Canonical BEP records expose the effective
options, but a release needs clear diagnostics and a supported-options contract.
Native rc parsing works; the older supervisor's rc restrictions do not apply.

## 4. P0 — Ownership, lifecycle and recovery

Authentication, registration checks, private namespaces, Linux process-lifetime
reservations, directory pinning and scoped shutdown exist. Background-work and
crashed-daemon lifecycle qualification is incomplete.

- [ ] **L1 — Recycle engines:** release a drained engine's slot, graph, modules,
  threads, filesystem handles and native reservations; reopen with a fresh
  registration generation. Failed initialization must unwind or quarantine only
  its own resources and have a defined retry path.
- [ ] **L2 — Account for every owner:** drain invocations, queued commands,
  callbacks, materializers, action processes, future workers and cache writes.
  RPC/command completion does not prove all engine work ended. No callback may
  touch a disposed or replacement engine.
- [ ] **L3 — Add eviction/administration:** evict only engines without live leases;
  provide inspect/list/drain/shutdown; distinguish engine and daemon shutdown;
  define idle/low-memory shutdown. Last-engine JVM shutdown already works but is
  not per-engine idle eviction.
- [ ] **L4 — Qualify recovery:** failed startup, partial state, interrupted
  initialization, cleanup errors, disk full, corrupt/missing metadata and death
  during active builds. Prior idle-daemon death tests do not qualify active-build
  recovery with surviving descendants.
- [ ] **L5 — Handle orphans:** detect/reap or fence surviving actions/workers before
  reusing outputs. JVM death releases reservations but does not prove descendants
  stopped writing. Transparent continuation of a crashed build is not promised.
- [ ] **L6 — Close filesystem lifetime gaps:** admission/cleanup fencing is not
  atomic with running commands, retries, callbacks or shutdown hooks. Specify
  delete/recreate/rename/symlink behavior, safe abandonment when original objects
  cannot be restored, and cleanup that never traverses replacement trees.
- [ ] **L7 — Qualify locks/handoff:** simultaneous startup/admission, canonical
  aliases, nested workspace discovery, stale registrations, PID reuse, endpoint
  loss and shutdown races. Require drained ownership transfer before fallback
  processes reuse an output base.
- [ ] **L8 — Finish startup identity:** the profile assumes installed executable,
  JDK and classpath resources are immutable; it does not content-certify arbitrary
  replacements behind custom paths. Audit JVM agents, filesystem providers,
  process limits, mounts and wrappers. Preserve semantic argument ordering and
  avoid exposing environment secrets in diagnostics.
- [ ] **L9 — State coordination limits:** abstract-socket reservations are Linux
  network-namespace-local. Shared outputs across namespaces/hosts, remote/NFS
  filesystems and arbitrary legacy access to private directories are unqualified.
  Use an adequate ownership mechanism or reject those layouts.

**Completion check:** repeated create/build/cancel/drain/evict/reopen leaves
bounded state; failures in A preserve B; restart never permits two output writers.
Fault injection includes active work. Fatal crashes/GC remain daemon-wide even
when this gate passes; independent processes remain the isolation fallback.

## 5. P0 — Scheduling and process-global state

- [ ] **R1 — Replace fixed splits:** daemon admission for analysis/loading and
  actions/tests, configurable engine limits, JVM headroom and pressure backoff.
  One active engine should use available capacity. Job limits are not heap limits.
- [ ] **R2 — Define scheduler semantics:** fairness, queued cancellation, impossible
  requests, active leases, starvation prevention and shutdown while waiting.
  Prevent partial-resource deadlocks; distinguish queue and service time. Avoid
  holding daemon-wide admission locks through slow engine construction; current
  creation runs inside the synchronized registry admission path.
- [ ] **R3 — Define enforcement:** current CPU/memory numbers are estimates, not
  per-build hard RSS/CPU quotas. Decide whether OS/cgroup enforcement ships or
  document estimated admission; stress parallel analysis as well as subprocesses.
- [ ] **R4 — Finish mutable-global audit:** classify immutable/daemon/engine/
  invocation ownership, including executor/RPC callbacks. Interleave initialize,
  reset, complete and shutdown. Thread-local state alone cannot cross callbacks.
- [ ] **R5 — Replace fragile feature filtering:** the current filter uses module
  class simple names. Define explicit shared-runtime capabilities and make unsafe
  additions fail clearly instead of introducing unnoticed global assumptions.
- [ ] **R6 — Restore optimizations safely:** graph-backed global interner pooling
  is disabled because two graphs cannot own one pool. Design engine-safe or
  deliberately immutable sharing and measure disablement cost. Do not blindly
  reinstall a single-graph global pool.
- [ ] **R7 — Restore observability:** scope errors, profiles, crash context,
  worker/network/memory metrics and asynchronous remote errors correctly. Expose
  engine/queue/pressure and action-wait/payload-reuse data without charging daemon
  totals to every invocation.

**Completion check:** supported services cannot reset another engine's state;
scheduler stress stays within declared budgets without starvation/deadlock;
pressure and metrics remain meaningful under concurrency. Hard containment, if
claimed, requires separate OS-level proof.

## 6. P1 — Action sharing and execution interoperability

The registry holds at most 256 immutable flight keys. It uses a monitor and
`wait/notifyAll`, without explicit FIFO scheduling, consumer-owned shared tasks,
per-flight byte budgets or saturation telemetry. Keys contain disk-cache namespace,
digest function and REAPI action digest. Coordination is within one JVM.

- [ ] **A1 — Specify reuse precisely:** cache reads and writes must both be allowed.
  Uncacheable/bust-cache actions, internal actions and different action keys do not
  have a sharing guarantee. Physical paths/context can legitimately change keys.
  Never force equality based on matching labels or Git commits.
- [ ] **A2 — Decouple production if stronger cancellation semantics are required:**
  cancelling today's producer interrupts its work; survivors retry after lease
  release. That is correct fallback, not exactly-once execution under cancellation.
  Daemon-owned tasks, consumer references and last-consumer cancellation are needed
  before making the stronger claim.
- [ ] **A3 — Qualify publication failures/races:** producer failure, arrival during
  cancellation, cache write failure, partial/corrupt/evicted blobs, disk full,
  interrupted materialization and cleanup during use. Define transient failure
  and retry behavior; never publish incomplete outputs.
- [ ] **A4 — Bound waiting/contention:** stress saturation, many equal/different
  keys, cancellation and release. Add fairness/wait visibility and any required
  wait/task limits. An entry cap does not bound waiting threads or invocation state.
- [ ] **A5 — Measure duplicate preparation:** engines still load/analyze, construct
  actions, observe inputs and prepare REAPI/Merkle data before joining a flight.
  Measure this plus synchronous CAS publication/materialization before adding
  another sharing layer.
- [ ] **A6 — Restore execution modes separately:** workers/multiplex workers,
  sandbox reuse, dynamic execution, remote execution/cache, remote downloader/
  output service and async publication all need explicit ownership and teardown.
  Honor supported user policies or reject unsupported combinations clearly.
- [ ] **A7 — Preserve outputs/test behavior:** cover tree artifacts, symlinks,
  discovered inputs, modes, large outputs, test caching/uncached tests, timeouts,
  retries and different platforms/toolchains. Shared CAS does not imply shared
  writable execroots or outputs.

**Completion check:** output/exit/diagnostic equivalence with independent controls
across the advertised matrix, cancellation safety and complete publication.
One-execution claims state their cacheability/success/cancellation conditions.
Cross-daemon coalescing is a separate optional goal.

## 7. P1 — More reuse through small immutable boundaries

Most graph memory/computation is duplicated. Labels/configured-target keys and
some strings already intern; additional key interning alone misses most values.
The new payload interners do not share source text, parsing/resolution, compiled
programs, loaded modules, packages, configured targets, artifact roots, dependency
edges or evaluator bookkeeping.

- [ ] **M1 — Attribute the next opportunity:** measure deep retained payloads,
  allocation and CPU separately from node wrappers. The Agent histogram added
  about 15.3 MiB in Starlark syntax classes for B, excluding arrays/strings. That
  is a clue for investigation, not a proven shareable total.
- [ ] **M2 — Qualify existing interners:** single-engine overhead, contention,
  dissimilar worktrees and long churn. Weak references are not explicit entry/byte
  budgets; live engines may retain all distinct payloads. Check reclamation and
  metadata growth. These interners also affect ordinary single-workspace Bazel.
- [ ] **M3 — Add independent immutable payloads incrementally:** use existing
  equality/interning where identity is complete and no ownership is retained.
  Admit one category at a time with divergence/restoration, serialization checks
  and savings exceeding hash/table overhead. Provide independent experimental/
  rollout disablement where needed.
- [ ] **M4 — Extract pure Starlark computation:** separate file/dependency
  acquisition from parse/resolve/compile and local binding/events. Audit a typed
  key containing actual immutable bytes, logical source identity, dialect/options,
  semantics, compiler/predeclared context and implementation version. The audit,
  not this illustrative list, defines all semantic inputs.
- [ ] **M5 — Decouple locations and freeze results:** compilation currently passes
  a physical path; syntax contains file locations and resolver bindings. Shared
  programs need a real immutability contract and local diagnostic rendering.
  Audit later mutation/typing and repository/predeclared context. Source digest
  alone does not make the current AST/Program safely reusable.
- [ ] **M6 — Implement bounded completed-result reuse:** publish only finished,
  deeply immutable values; bound entries/retained bytes and define eviction.
  Expose hits, avoided work, duplicate misses and retained bytes. Eviction must
  preserve live consumers; cached objects must not retain entire engines. Ordinary
  Skyframe dependency/freshness checks and caller-specific events remain local.
- [ ] **M7 — Reuse serialization infrastructure selectively:** investigate existing
  analysis-cache/serialization facilities before inventing another store. Equal
  bytes can decode differently because roots, artifact factories and context
  distinguishers are local. Shared bytes can be useful with private decoded objects;
  a global deserialization map is not automatically safe.
- [ ] **M8 — Add pure-stage single-flight only if justified:** first measure duplicate
  misses after completed caching. Coalescing requires daemon-owned computation,
  consumer lifetimes, cancellation, failure and pressure rules. The action registry
  does not automatically solve compilation-task ownership.

**Completion check:** direct/reused paths agree on results, failures and correctly
localized diagnostics; every semantic input change prevents invalid reuse; active
consumers survive eviction; retention is bounded/accounted; paired measurements
show repeatable net benefit. Keep an overly broad/unhelpful stage private and
explicitly revise the milestone rather than claiming success from cache hits.

## 8. Separate major milestone — common graph plus workspace differences

Each graph currently owns mutable nodes, direct/reverse dependencies, dirty state
and versions. The in-memory evaluator uses `IntVersion`; there is no implemented
persistent multi-workspace version model. `ConfiguredTargetKey` does not encode
an entire worktree's source state. `RootedPath` and artifacts include physical
ownership, and configured-target values can be cleared in place. Equal keys are
not sufficient to merge current values.

- [ ] **G1 — Choose a representation/correctness contract:** compare immutable
  payload sharing behind local wrappers, immutable dependency structure, and a
  persistent base graph with workspace overlays. Define sharing for keys, values,
  edges, versions, errors, events and action ownership.
- [ ] **G2 — Establish complete semantic identity:** source/configuration,
  repository mappings, toolchains and discovered dependencies, including missing
  lookups, glob membership and symlink resolution. Git commit, label, Java identity
  or one source digest is not a certificate for arbitrary evaluation.
- [ ] **G3 — Design independent invalidation:** A's edits/dirty propagation must
  not mutate B's view. Preserve dynamic dependencies, cycles, errors, retries/
  rewinding, incremental recomputation and reclamation across concurrent versions.
- [ ] **G4 — Separate ownership-bearing values:** refactor/wrap physical roots,
  artifacts, action registration, modules, loaded environments, reporters/events
  and destructively cleared values before sharing them.
- [ ] **G5 — Specify concurrency/lifetime:** atomic publication, version pinning,
  readers, overlay disposal, reference accounting/GC, cancellation, reset/clean
  and engine eviction.
- [ ] **G6 — Prove one vertical slice:** choose one audited value/edge category
  with differential invalidation tests. Measure retained memory/CPU, not just node
  counts. Expand only after divergent/reconvergent worktrees and churn pass.

**Completion check:** common subgraphs are demonstrably retained once while
changed subtrees stay independent and invalidation semantics hold. Do not implement
this as one global `ConcurrentHashMap<SkyKey, SkyValue>`, or describe the current
private-graph daemon as this milestone.

## 9. P1 — Storage, usability and release integration

- [ ] **D1 — Bound disk growth:** retention/GC for shared CAS, private outputs,
  repositories, installations and old namespaces. Lease live reads/publications/
  outputs during collection; recover from interrupted collection. Current disk
  growth is not automatically managed.
- [ ] **D2 — Audit dependency sharing:** repository views and `repo_contents_cache`
  are private because existing locks assume one owner. Reuse downloaded immutable
  bytes only through supported contracts; never share one mutable repository view.
  Measure downloads separately from materialization/storage duplication.
- [ ] **D3 — Productize launch/status:** namespace selection, Bazelisk/wrapper
  integration, engine inspection and useful incompatibility/capacity/fencing
  diagnostics. Names are explicit user choices (1–64 ASCII letters, digits, `_`
  or `-`), not automatic repository-family grouping or worktree discovery.
- [ ] **D4 — Preserve ordinary Bazel/fallback:** separate namespaces; never put a
  fallback process on a live engine's output. Test upgrades/downgrades, binary/
  profile changes and old namespace retirement. v1/v2/v3 are separate today.
- [ ] **D5 — Make scope releasable:** independently control concurrency, immutable
  reuse and action sharing for diagnosis/rollback as needed. Publish commands,
  platforms/options, format compatibility, recovery procedures and shared-process
  failure semantics. No unqualified promise of transparent fallback.
- [ ] **D6 — Integrate CI/maintenance:** reproducible native acceptance and key
  differential/lifecycle checks, raw failure evidence, fewer experiment-only
  assumptions, review/rebase against the intended upstream version. A pushed
  experimental branch is not an upstreamed release.

## 10. P0/P1 — Qualification still required

### Correctness and longevity

- [ ] Run the native candidate on the full synthetic mutation matrix and expanded
  real targets: source/BUILD/`.bzl`, module/repo mappings, toolchains, options/env,
  missing paths, glob/symlink changes and restoration. Historical independent-server
  or Python-supervisor results are not native proof.
- [ ] Cover concurrent analysis as well as action/test barriers, warm A/B/A,
  mixed targets, same-engine queues, concurrent initialization and lifecycle races.
  Stress cancellation at waiting/publication/cleanup boundaries, client disconnect/
  kill and daemon crashes during active work.
- [ ] Run churn/soak tests: heap, threads, FDs, descendants, cache bytes and ownership
  reservations must reach stable bounds rather than accumulate with every worktree.
- [ ] Qualify analysis-discarding/reset modes and desired `clean` behavior before
  enabling cleanup commands; A's reset must not clear B.
- [ ] Test advertised platforms/execution modes, protocol identities and correct
  cross-engine diagnostics/BEP attribution. Same-user experimental namespaces are
  not a security boundary for mutually untrusted tenants.

### Performance and memory

- [ ] Compare independent servers, shared daemon with optional reuse disabled,
  payload reuse enabled and later compiled-result reuse enabled. Match cache
  populations/toolchains and aggregate resources. Separate action-sharing benefit
  from payload/compilation-sharing benefit.
- [ ] Cover 1/2/4 worktrees after lifting the slot limit; use 8 only with sufficient
  resources or label oversubscription. Include cold analysis/hot actions, cold
  actions, warm no-ops, edits, divergence, different targets and churn on larger
  production-relevant graphs.
- [ ] Record wall/queue/service latency, throughput, descendant CPU, peak process-tree
  PSS/private memory, live/deep-retained heap, allocation/GC, storage and marginal
  engine cost. Keep forced-GC/profiling diagnostics separate from latency trials.
- [ ] Preserve the Phase 0 proposed budgets, or record revisions before collecting
  qualification arms: warm single-worktree median regression at most 5%, p95 at
  most 10%; concurrent throughput does not regress beyond uncertainty; reuse gives
  repeatable benefit outside noise; no output/state leaks or unbounded growth.
  These gates have not been passed by MVP smoke measurements.
- [ ] Use at least 30 interleaved paired trials with uncertainty intervals, at least
  100 observations per arm for p95 claims, and a second session reproducing claimed
  improvements. Preserve failed/excluded runs and exact warming states.

The original Phase 0 separate-server identity criterion applies to controls.
Native runs instead require one daemon identity and distinct engine registrations/
output bases. Do not require separate PIDs from the shared-process feature.

## 11. Suggested order of small changes

| Order | Reviewable increment | Evidence before expanding |
| --- | --- | --- |
| 1 | Qualify current interners under one-engine/divergent/churn workloads; add minimal scoped reuse/wait counters | Net benefit, bounded metadata, acceptable latency/CPU overhead |
| 2 | One additional owner-free immutable payload, if attribution identifies a useful candidate | Equality/serialization, divergence/restoration and marginal savings |
| 3 | Engine disposal/background leases, then slot recycling and idle eviction | Active-work faults and stable heap/thread/FD/output ownership across churn |
| 4 | Daemon resource admission and pressure handling | Fairness/cancellation/impossible-request tests; equal-budget benchmarks |
| 5 | One pure Starlark substage, initially without a production cache | Direct/reused equivalence including errors, locations and context changes |
| 6 | Bounded completed-result reuse with independent disablement | Eviction/retention stress and repeatable workload benefit |
| 7 | Required workers/remote/profiling/commands one at a time; complete recovery/storage administration | Per-feature ownership, differential and lifecycle qualification |
| 8 | Release-scale qualification and supported-contract publication | All advertised gates pass with reproducible evidence |
| Separate | Shared graph structure/overlays if still a required outcome | Section 8 design contract and measured safe vertical slice |

This is a priority/dependency guide, not a requirement to finish all runtime work
before another small immutable-payload improvement. A full graph redesign should
not be a prerequisite for useful, measured reuse.

## 12. Source and plan map

These repository-relative links identify the implementation points reviewed. Read
them at the revision above when comparing this snapshot to future changes.

- [Original staged plan](multi-worktree-implementation-plan.md): stage 0 bounded
  baseline exists; stages 1/3/4/5/7 are partially implemented/qualified; stages 2/6
  pure compilation work remain open. Action coalescing addresses part of the
  original optional stage 8 in a different domain. Stage 9 release qualification
  is open. Older single-engine status paragraphs are historical.
- [Phase 0 budgets/methodology](multi-worktree-phase0-2026-10-31.md).
- [Run instructions](../../../tools/multiworktree/NATIVE.md) and
  [native acceptance](../../../tools/multiworktree/native_integration_test.py).
- [Launcher compatibility](../../../src/main/cpp/blaze.cc),
  [startup restrictions](../../../src/main/cpp/startup_options.cc),
  [native ownership](../../../src/main/cpp/shared_server_ownership.cc).
- [Admission/routing/shutdown](../../../src/main/java/com/google/devtools/build/lib/server/CommandServer.java),
  [registrations/leases](../../../src/main/java/com/google/devtools/build/lib/server/EngineRegistry.java),
  [directory fencing](../../../src/main/java/com/google/devtools/build/lib/server/EngineDirectories.java).
- [Runtime/module exclusions](../../../src/main/java/com/google/devtools/build/lib/runtime/BlazeRuntime.java),
  [invocation policy](../../../src/main/java/com/google/devtools/build/lib/runtime/BlazeCommandDispatcher.java),
  [suppressed metrics](../../../src/main/java/com/google/devtools/build/lib/metrics/MetricsCollector.java).
- [Flight registry](../../../src/main/java/com/google/devtools/build/lib/remote/SharedActionExecutionRegistry.java),
  [cache/lease integration](../../../src/main/java/com/google/devtools/build/lib/remote/RemoteSpawnCache.java).
- [Directory reuse](../../../src/main/java/com/google/devtools/build/lib/skyframe/CompactSortedDirents.java),
  [line-table reuse](../../../src/main/java/net/starlark/java/syntax/FileLocations.java).
- [Graph ownership/pools](../../../src/main/java/com/google/devtools/build/skyframe/InMemoryGraphImpl.java),
  [evaluator versions](../../../src/main/java/com/google/devtools/build/skyframe/AbstractInMemoryMemoizingEvaluator.java),
  [mutable configured-target values](../../../src/main/java/com/google/devtools/build/lib/skyframe/AbstractConfiguredTargetValue.java),
  [physical compilation locations](../../../src/main/java/com/google/devtools/build/lib/skyframe/BzlCompileFunction.java),
  [context-sensitive serialization](../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/FingerprintValueCache.java).
