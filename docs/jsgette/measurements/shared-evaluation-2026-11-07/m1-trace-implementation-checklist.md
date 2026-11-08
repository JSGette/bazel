# M1 trace implementation checklist

Date: 2026-11-08. Status: M0 passed its repeated-state continuation gate under [D019](decision-log.md); M1 is in progress. This checklist remains a preparatory source audit and proposed trace implementation. Follow the [plan](../../plans/shared-evaluation-gates-2026-11-07.md) and the native/private qualification in [m1-probe-design.md](m1-probe-design.md) before starting this slice. Source locations below describe the audited Windows checkout, not a tested binary.

## Minimum useful implementation

Capture per-command evaluator costs and actual dependency edges, with exact projections for a bounded subset of `FILE_STATE` and `DIRECTORY_LISTING_STATE`. Record `BZL_LOAD` keys and transitive digests as input evidence only. Build a small offline replay over these records. Do not add a cache, change scheduling, serialize modules, or instrument every SkyFunction.

This can establish supported overlap and expose where further measurement is necessary. It cannot automatically establish the whole M1 savings ceiling: unsupported computation and retained heap must remain visible in the decision.

### 1. Attach an instance-local collector

- Add `M1TraceCollector` and `M1TraceModule`, with a disabled-by-default proposed command option `--experimental_shared_eval_trace=<absolute-file>`. Keep collector state per command and module state per engine. Native engine creation constructs fresh modules (`runtime/BlazeRuntime.java:1457`); no static registry or global `Profiler` is necessary.
- Add a dedicated `EvaluationProgressReceiver` diagnostic delegate and setter to `SkyframeExecutor`, defaulting to a no-op receiver. The existing `executionProgressReceiver` is explicitly execution-only (`skyframe/SkyframeExecutor.java:3598`) and must not carry this analysis trace.
- Forward `dirtied`, `deleted`, `enqueueing`, `stateStarting`, `stateEnding`, and `evaluated` from `SkyframeProgressReceiver` (`:3589–3645`). Put diagnostic forwarding before `ignoreInvalidations` returns and before heuristic node removal in `evaluated`; preserve existing forwarding and removal behavior. Forward `changePruned` through a new override if its distinction is used by replay. No low-level evaluator changes are needed for the first version.
- In `beforeCommand`, read options, capture `CommandEnvironment.getCommandId()`, workspace binding and evaluator graph, then attach the collector. In `afterCommand`, project remaining supported done nodes, close the trace, and detach in `finally`. `BlazeRuntime.afterCommand` calls modules before its optional evaluator reset (`runtime/BlazeRuntime.java:864–907`); earlier dropping can still remove nodes and must be reported.
- Register the module in `bazel/Bazel.java:42` and its `:main` BUILD dependencies. Add a small collector target and a separate module target in `skyframe/BUILD`; use `:skymeld_module` as the module dependency pattern. Keep `skyframe_core` depending only on the existing receiver interface, avoiding a core/module dependency cycle.
- Bound node IDs, edge rows, projected bytes and writer buffering. Emit immutable scalar/byte records; never enqueue `SkyValue` or Starlark module objects. Any bounded `SkyKey` map retains keys only until flush and is charged as diagnostic overhead. Disabled tracing must cause no graph walk. Overflow, writer failure or unmatched intervals marks the trace incomplete, without changing build semantics.

Shortened `runtime/`, `bazel/`, `actions/`, `buildtool/` and `skyframe/` paths in this checklist are below `src/main/java/com/google/devtools/build/lib/`. Low-level evaluator APIs (`EvaluationProgressReceiver`, `GroupedDeps`, `InMemoryGraph`, `InMemoryNodeEntry` and `WorkerSkyKeyComputeState`) are instead in `src/main/java/com/google/devtools/build/skyframe/`.

### 2. Capture dependencies that were actually observed

`EvaluationProgressReceiver.evaluated` supplies grouped direct dependencies for newly built nodes; its `newValue` is populated only for a successful version-changing result (`src/main/java/com/google/devtools/build/skyframe/EvaluationProgressReceiver.java:137`). A null value/dependency argument does not mean no value/dependencies.

- Copy `GroupedDeps.getDepGroup(i)` with group boundaries and order. Do not reconstruct edges from labels, names or source paths. Preserve evaluation status, attempts, error/dirty/deleted state and dependency coverage independently of value coverage.
- After the command, visit the done graph. Require `InMemoryNodeEntry.isDone()` and `keepsEdges()` before `getCompressedDirectDepsForDoneEntry()`, then use `GroupedDeps.decompress()`. Project supported values immediately. `InMemoryGraph.getValues()` includes dirty values and is unsuitable as a complete done snapshot; error, edgeless, removed and unsupported entries stay explicit.
- Before the existing FILE/DIRECTORY_LISTING parent callback removes its state child, inspect its actual direct dependencies and project any supported done state values. This also covers injected file states that may never produce a compute callback. If lookup fails or the value is unavailable, record missing evidence; do not rehash the filesystem or force reevaluation to fill it.
- Tag `observed_this_command` separately from `retained_only`. A warm command can leave many nodes untouched. A whole retained graph is not necessarily the current target's demand closure; initial scope is retained-state overlap plus observed costs, without instrumenting every `evaluate()` call to reconstruct all roots.

### 3. Define the supported identities narrowly

Use a schema/semantic epoch, explicit logical repository/root binding, function tag and root-relative path as the source query identity. Begin with the fixture's verified main-source-root mapping. Physical paths and canonical repository names alone do not establish equivalent repository bindings. External, generated, absolute and unbound roots are unsupported until an explicit binding is implemented. Record snapshot provenance separately; putting the entire changing snapshot root into every logical query key would hide reuse across unrelated edits.

| Value | First supported projection | Unknown or excluded |
| --- | --- | --- |
| `FILE_STATE` | Switch on `FileStateValue.getType()`. For regular files initially support only `RegularFileStateValueWithDigest`; require its existing non-null digest and encode concrete class/kind, size, digest algorithm and digest. Encode directory/nonexistent kind, or exact unexpanded symlink target for a symlink. | Contents-proxy and `RegularFileStateValueWithMetadata` instances remain unknown even if metadata exposes a digest; also exclude special files and unsupported roots. Never assume `getValueFingerprint()` is a portable content hash. This projection does not add executable-mode semantics absent from this value, nor prove a symlink's resolved closure. |
| `DIRECTORY_LISTING_STATE` | Encode every `getDirents()` entry as exact name and type, using canonical ordering and unambiguous length-prefixed fields. | Oversized projection, unresolved root binding and unsupported entry types. A directory listing proves membership/type observations, not child contents. |
| `BZL_LOAD` | Encode explicit key variant, structured label, prelude/builtins/dialect flags and verified repository/semantics bindings; record `getTransitiveDigest()` separately as input evidence. | No supported module-value fingerprint or module reuse claim. Recorded repository mappings/visibility are additional evidence, not a complete serialized module contract. |

Relevant APIs: `actions/FileStateValue.java:213–286,374–467`; `skyframe/DirectoryListingStateValue.java:49,89`; `skyframe/BzlLoadValue.java:107–173`. The reconstructed safe-subset source confirms `RegularFileStateValueWithMetadata.equals()` delegates to the full `FileArtifactValue` (`FileStateValue.java:479–489`); digest plus size does not establish exact represented-value equality for that class. The digest-backed class is the initial supported regular-file subtype. The Bzl key explicitly returns `valueIsShareable() == false`, citing equality limitations after deserialization. That is a portability warning, not proof that a future evaluator can never share a module.

`DirectoryListingStateFunction.compute` invokes external-file handling before reading (`:51–66`), so preserve its actual dependencies rather than declaring every listing dependency-free. `BzlLoadFunction` has an inlining path that bypasses BZL_LOAD Skyframe events (`:103,275–348`). Record whether this mode is active and its coverage limitation; do not attribute all Starlark cost to visible BZL_LOAD events. Its transitive digest accumulates compilation, loaded-module and builtins digests (`:838–860`), which still does not make it a module-result fingerprint.

Exact projection equality is evidence of equal represented values. It is not permission to skip their input validation in a live evaluator; that requires M2's tracked-input contract. Configuration, environment and repository inputs outside this subset remain unknown, not an empty dependency set.

### 4. Measure costs without inventing missing CPU

Record monotonic wall intervals and supported current-thread `ThreadMXBean` CPU deltas for CHECK_DIRTY, INITIALIZING_ENVIRONMENT, COMPUTE and COMMIT. The evaluator already brackets compute attempts, including restarts, with symmetric callbacks (`src/main/java/com/google/devtools/build/skyframe/AbstractParallelEvaluator.java:430–476`). Use per-thread interval stacks and attempt IDs; avoid a key-only active-interval map. Report exclusive/union CPU so nested intervals cannot be summed twice.

Record CPU capability/enabled state, thread type, negative counters and unbalanced intervals. `WorkerSkyKeyComputeState.java:159` uses virtual-thread workers; work can occur outside the caller's CPU interval. Unsupported CPU is unknown, never zero. Separate action/execution functions from analysis and validation; interleaved SkyFunction activity is not all analysis. Reconcile measured trace CPU with the separate whole-JVM/build-only counters, leaving JIT, GC, workers and instrumentation as an explicit residual.

Callbacks and projection/writer work perturb the measured run, including COMMIT timing. Run one matched trace-off/trace-on qualification and report observer CPU/memory and coverage. Preserve the unique-PID sampling, build-only boundary, preparation-history and native restrictions in the probe design. Traces are diagnostic runs, not M4 performance evidence.

### 5. Replay and interpret a bounded result

- Use append-only records: header with source/binary/schema/namespace bindings and limits; node projections; attempt/state costs; grouped edges and dependency observations; final coverage/overflow/CPU-capability summary. Only a successfully closed trace is complete. Missing footer or missing required evidence makes it ineligible for equivalence claims.
- For typed supported values, group equivalent query/projection records across worktrees, preserving dependency evidence and A-to-B-to-A variants. Charge the lookup and actual implemented validation work. Any simplified candidate validation remains a model, not a live cache correctness result.
- Measure replay CPU, wall and process memory, and count retained index entries, nodes, edges, observations and variants. Trace payload bytes are storage measurements, not JVM heap. Do not sum per-value deep sizes across shared reachable objects. Separate quiescent attribution can reuse `buildtool/SkyframeMemoryDumper.java:56`/`MemoryAccountant` in a supported single-engine run; native `dump` remains unavailable.
- Publish supported coverage and potential separately from an optimistic ceiling conditional on this workload, lifecycle and representation. A name-based grouping is a hypothetical scenario, not semantic equivalence and not a universal upper bound on alternative architectures.
- A stop claim must generously include all unknown potentially removable CPU/state in its optimistic bound. A small measured leaf saving is not a ceiling for the evaluator. If unknowns dominate, M1 is inconclusive: investigate only the highest-cost missing family within the timebox or report the unresolved bound. Do not turn absent CPU/heap attribution into zero, relax the gate, or count replay bytes as retained-heap savings.

## Checks and proposed commands after M0

First add focused collector tests, then an evaluator integration test. Essential cases are restart aggregation; unchanged/new-equal values; cancellation/failure; dropped/injected state children; actual grouped dependencies; warm retained-only nodes; sequential commands and two concurrent engines; bounded overflow/writer failure; unavailable CPU; and exact missing/present, same-size content, symlink, directory membership and repository-binding distinctions. Include unsupported digest-less files, metadata-backed regular files with equal exposed digest/size but distinct metadata, and Bzl modules to prove they remain unknown. Check output equivalence with tracing off/on. Replay fixtures must independently verify duplicate grouping, A-to-B-to-A retention and charged dependency/index/variant costs.

The following are proposed commands, not executed results. Run them only inside the separately guarded build scope from [m1-native-preflight.md](m1-native-preflight.md), with a task-owned isolated output base, using the same pinned toolchain. The two M1 targets must first be added.

```sh
bazel --output_base="$BUILD_OB" test \
  //src/test/java/com/google/devtools/build/lib/skyframe:M1TraceCollectorTest \
  //src/test/java/com/google/devtools/build/lib/skyframe:M1TraceModuleTest \
  //src/test/java/com/google/devtools/build/lib/skyframe:SequencedSkyframeExecutorTest \
  //src/test/java/com/google/devtools/build/lib/skyframe:FileStateValueTest \
  //src/test/java/com/google/devtools/build/lib/skyframe:CompactSortedDirentsTest \
  //src/test/java/com/google/devtools/build/lib/skyframe:BzlLoadFunctionTest
bazel --output_base="$BUILD_OB" build //src:bazel-dev
```

Then use the digest-pinned built binary for N=1 trace-off/on and N=2 identical plus distinct-source/BUILD/glob/configuration variants, with a unique trace destination per engine/command. Apply the probe design's native startup arguments; do not pass a native output base. Only scale tracing after correctness, completeness and bounded overhead are demonstrated. No comprehensive serializer, global profiling change or extra rule family belongs in this initial patch.
