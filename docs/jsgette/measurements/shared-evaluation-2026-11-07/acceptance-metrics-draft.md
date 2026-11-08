# Acceptance metrics to freeze after M0

Status: proposed definitions only. M0 is running; no candidate has been implemented or measured. The final choice must be recorded in the decision log before candidate implementation. Preserve this draft when recording changes.

## Comparators and claims

M4 retains the plan's isolated SkyCache comparator and proposed thresholds: at least 50% less aggregate build-control memory, at least 30% less build-control CPU per completed build, and no more than 10% worse single-request latency. Do not replace the denominator with whichever private-daemon implementation is more expensive.

The preliminary native/private experiment uses a newly built identical binary in both arms to examine the existing fork's combined native implementation. Its same-binary result is a diagnostic of that bundle, not automatically a pass relative to isolated SkyCache. Any recommendation that it meets the later economic goals also needs an explicitly matched SkyCache systems comparison. Record the unavoidable implementation differences.

## Accounting boundary

The request starts before admitting or ingesting its input revision and ends after requested outputs are available. Count source ingestion, hashing, lookup, validation, scheduling, client work, service work, result publication and required materialization. Report prepared immutable-root requests separately from edit-to-output requests; a root-pointer swap cannot stand in for the cost of admitting new bytes.

Build-control CPU is the sum of process CPU deltas for the clients and all controller/service/metadata/validation processes. Classify actual compiler actions separately using recorded command/process ancestry and action evidence. Charge ambiguous work to build control until it is identified; do not remove repository bootstrap simply because a compiler executable performed it. Report total workload CPU alongside the split. Keep JIT, GC and other unattributed daemon CPU in build control.

Build-control memory is peak simultaneous summed PSS of those processes, deduplicated by PID and process start time. Start sampling before request admission and retain a consistent cadence. Report sample coverage and failures. Measure the quiescent retained heap separately: it answers a different question from peak PSS. Also report total cgroup peak, anonymous/file/kernel breakdown and compiler-worker memory; lower client memory alone cannot establish savings.

Measure the observer's own CPU diagnostically and qualify sampling overhead. Keep observer and behavior-oracle work outside the primary build-only timing window where possible, while preserving full verification and an end-to-end instrumented result. Do not subtract unmeasured overhead from M0 data. Trace-enabled runs are separate diagnostics.

## Lifecycle and concurrency

Compare the same input/configuration set, output requirements, resource caps and cache policy. Equalize prior successful pool cycles; generate correctness references in separate pools. Counterbalance order. Test restarted and warm lifecycles separately; retain the cold-cache row as a separate setup-cost result. No averaging across lifecycle strata.

For N=10, verify ten independently identified requests and output views, actual overlapping command intervals, and variant-specific correctness. A single daemon does not establish ten concurrent requests. Record per-request latency and completion, not just cohort wall time. Cancellation and starvation tests are independent correctness gates.

Proposed decisive measurement budget: five paired repetitions for each required performance stratum, with alternating order fixed before running. Use the ratio of arm medians for the percentage checks, publish every paired observation and range, and do not discard slow successful runs. If a measured range crosses a gate boundary, classify the evidence as inconclusive and permit one predeclared extension of five further pairs, evaluating all ten. Do not repeatedly sample until a favorable result appears. An inconclusive result after the extension is not a pass.

For the narrow end-to-end slice, apply M3's 10% CPU and latency overhead limits to the warm single-request case. For M4, require the memory/CPU benefit for different snapshots, including disjoint edits and common-dependency changes; identical snapshots alone are insufficient. Keep single-request latency checks separate from saturated N=10 cohort latency.

## Unresolved until M0 closes

- Verify that the proposed target exposes substantial addressable retained/evaluation state rather than merely executable startup costs; record which uncertainty M1 must resolve.
- Choose and record the exact ingestion boundary for the first immutable-root API, including supported dirty-edit publication and fallback behavior.
- Validate that process classification and observer overhead can support the proposed CPU denominator without assigning unknown CPU to zero.
- Record any necessary refinement to these definitions before candidate implementation, with its reason. The final gate remains fixed once candidate results are available.
