# M0 cost attribution: what the archived v6 evidence measures

The six completed v6 performance anchors are mostly JVM CPU, but their traces do not separate evaluator computation, validation, restoration, startup, GC and JIT CPU. These are single trials from the incomplete v6 matrix, kept separate from v7; no milestone verdict follows.

Only C-backed archives were read. All 33 measured profiles were checked against archive/member hashes and processed with the existing `summarize.profile_summary`. The [profile inventory](evidence/m0-v6-01/profile-attribution-01.json) preserves hashes, exact categories/names, per-client span summaries and raw SkyCache phase sidecars; the [verified overview](evidence/m0-v6-01/overview-partial-02.md) supplies cohort accounting and BEP metrics.

| Restarted cohort | Scope CPU s | Sampled engine CPU s | Scope minus engine s |
| --- | ---: | ---: | ---: |
| N1 vanilla | 24.657 | 24.220 | 0.437 |
| N1 candidate-off | 24.751 | 24.230 | 0.521 |
| N1 SkyCache | 22.723 | 22.170 | 0.553 |
| N10 vanilla | 284.249 | 276.440 | 7.809 |
| N10 candidate-off | 288.498 | 279.570 | 8.928 |
| N10 SkyCache | 300.053 | 284.300 | 15.753 |

Engine ticks account for approximately 94.7–98.2% of scope CPU. The residual includes other scoped processes and sampling/window differences; it is not a compiler-CPU measurement. JVM CPU includes runtime overhead and does not establish how much is reusable graph work.

| Requested breakdown | Observed evidence | Attribution limit |
| --- | --- | --- |
| Analysis/evaluation | `skyframe evaluator` / `Parallel Evaluator evaluation`; `general information` names `ParallelEvaluator.eval`, `evaluateTargetPatterns`, `skyframeExecutor.evaluateBuildDriverKeys`, `runAnalysisAndExecutionPhase`; BEP analysis duration | Nested elapsed intervals and a combined analysis/execution scope, not query CPU or a disjoint phase partition. |
| Validation | `applyInvalidation`, `resolveWithTimeout: analysis cache invalidator`; sidecar `records_read`, `witnesses_checked`, memo hits and subtree waits | Counts establish performed validation, not its CPU cost. `validateOptions` is not an input-witness validation timer. |
| Restoration | `initAnalysisCacheClient`, `getDeserializedKeys`, `resolveWithTimeout: analysis cache client`; restored-root/serialized-byte counters | No complete per-key lookup/read/decode/install CPU breakdown. `getDeserializedKeys` alone does not measure total restoration. |
| Action execution | `action dependency checking`, `action processing`, `complete action execution`, `critical path component`; `Action.execute`, `actuallyCompleteAction`; BEP execution durations/cache statistics | Every client reports 23 action-cache hits and one executed internal workspace-status action. No `userTime` or `systemTime` occurs in the 33 clients' action summaries; compiler CPU cannot be inferred from cached-action names. |
| Runtime overhead | `gc notification` / `minor GC`; sampled `CPU usage (Bazel)` and `CPU usage (total)` counters | GC notification durations are elapsed observations. No JIT breakdown or per-event thread CPU fields (`tts`/`tdur`) were found; no GC/JIT CPU subtraction is justified. |

The remaining observed categories are `bazel module processing`, `Fetching repository`, `package creation`, `Starlark Parser`, `Starlark builtin function call`, `Starlark user function call`, `Starlark repository function call`, `Starlark thread context`, `Conflict checking`, and `build phase marker`. Markers include `Launch Blaze`, `Initialize command`, `Evaluate target patterns`, `Load, analyze dependencies and build artifacts`, and `Complete build`. They locate activity; they do not provide a non-overlapping CPU budget.

All span sums are elapsed time and can overlap across threads/clients or nest. For example, N1 candidate-off has 436.174 s of `bazel module processing` spans during a 14.115 s cohort: treating that sum as CPU would be invalid. SkyCache `ph=C` counters such as `Skycache: SkyValues: Waiting for value bytes` and `Skycache: Invalidation: Nodes: Pending` are sampled quantities, not measured wait durations; `profile_summary` deliberately does not aggregate them as spans.

The 11 SkyCache clients also expose `local-skycache-phases.json`, explicitly labelled `inclusive_elapsed_wall_not_cpu` in nanoseconds at `boundary_command_end`. Only `publication` and `close_stores` have values; `final_validation`, `witness_manifest`, `seal` and `local_install` are null in every client. Missing fields remain unknown, not zero.

For M1, add task-correlated timing around actual lookup, dependency validation, payload decoding, value installation and evaluator compute attempts. Record thread CPU and elapsed time separately; split asynchronous work and waits, retain retry/parent identifiers, and avoid double-counting nested scopes. Couple costs to dependency observations, typed query/input identities, outcome and value/metadata bytes; diagnostic names alone cannot authorize reuse. Keep process/cgroup accounting as the reconciliation total, explicitly leave GC/JIT unattributed unless separately observed, and measure tracing overhead with an otherwise matched disabled-tracing run. Existing aggregates locate the instrumentation gaps; they cannot yet support a query-sharing savings replay.
