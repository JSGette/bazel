# Frozen acceptance metrics — 2026-11-08

Adopted with the M0 continuation decision, before M1 product edits or native candidate measurements. The original [draft](acceptance-metrics-draft.md) remains preserved. Thresholds, strata, input boundary and observer qualification below are fixed; implementation must qualify against them. Existing diagnostic helpers do not automatically satisfy the full acceptance boundary.

## Comparators and fixed thresholds

The M4 denominator remains **isolated SkyCache**, pinned by binary/source, effective options and lifecycle. Require at least **50% lower aggregate build-control memory**, **30% lower build-control CPU per completed build**, and **no more than 10% single-request latency regression**. Do not select whichever private implementation is more expensive.

M3 explicitly requires both CPU and latency no more than 10% worse for **N1 warm** requests in the supported end-to-end slice. Keep N1 restarted results as startup diagnostics rather than substituting them for the warm overhead gate.

The preliminary same-binary native/private comparison tests the native-mode bundle, including its unavoidable module, interning and action-sharing differences. It does not establish graph sharing or pass M4. An economic claim still requires a matched isolated-SkyCache comparison.

## Full accounting boundary

Start before admitting or ingesting the requested input revision; end when requested outputs are available. Include source ingestion, hashing, lookup, validation, scheduling, clients, services, result publication and required materialization. Define and measure a drain/tail for attributable required work that continues after the response. An early response cannot hide that CPU or memory. Report prepared immutable-root requests separately from edit-to-output requests; a pointer swap cannot replace admitting changed bytes. The supported publication contract is fixed below; raw mutable-directory capture remains an explicit fallback.

Build-control CPU includes clients and every controller/service/metadata/validation process, including JIT, GC and unattributed daemon work. Deduplicate by process identity. Use role subcgroups or exit/wait accounting for short-lived processes where supported, and reconcile with complete-scope CPU. Classify actual compiler actions from command/process ancestry plus action evidence; report compiler CPU and total workload CPU separately. Charge ambiguous/unobserved remainder to build control until identified, including repository bootstrap even when it invokes a compiler. A missing process sample is not zero; incomplete scope capture makes the performance claim ineligible.

Build-control memory is peak sampled aggregate PSS for these processes, deduplicated by PID/start time. Start before admission, include the drain, keep cadence consistent, and publish coverage, read durations and failures. Sequential reads approximate a simultaneous peak. Report quiescent post-GC heap separately, plus total cgroup peak, anonymous/file/kernel memory and compiler-worker memory. Count retained shared-service/publisher state; moving state out of the client is not a saving.

## Observer accounting and conservative CPU decisions

Keep behavior oracles outside the primary build window where possible, while preserving full verification and the instrumented end-to-end result. Measure observer CPU and qualify its perturbation in matched instrumentation-on/off runs before using corrected CPU for a gate. CPU subtraction cannot undo scheduling, cache or lock effects. Use the fixed qualification protocol below; until it passes, keep results diagnostic. Trace-enabled runs remain separate.

Bracket each scope-counter read with whole-harness process CPU readings, covering all harness threads. Derive lower/upper harness CPU bounds for exactly the measured boundary, widen for counter resolution, and preserve raw scope totals. Derive non-harness bounds conservatively, then reconcile proven compiler CPU to obtain build-control bounds; that remainder is not automatically evaluator CPU. Missing, regressed or inconsistent counters stay unknown. Do not clamp negative estimates to zero or subtract overhead not captured by the brackets. Include ingestion and drain intervals; a build-command-only interval cannot stand in for the complete gate boundary.

For each pair, use the worst supported CPU ratio: `(candidate upper bound / completed candidate builds) / (baseline lower bound / completed baseline builds)`. Require a positive, known baseline lower bound. Apply ≤0.70 for M4 and ≤1.10 for M3 N1 warm. Publish raw totals, bounds and the ratio. A threshold-crossing uncertainty interval cannot pass. Do not retroactively correct M0 with newly measured overhead.

## Required strata and repetition rule

Use matched source/configuration sets, outputs, resource caps, cache policy and successful pool histories. Generate independent references in separate pools and counterbalance order. Keep lifecycle strata separate.

| Stratum | Decision requirement |
| --- | --- |
| N1 warm | M3 CPU ≤1.10 and latency ≤1.10; retain the single-request latency gate for M4. |
| N1 restarted | Startup diagnostic; publish separately. |
| N10 restarted, identical inputs | M4 memory ≤0.50 and CPU ≤0.70. |
| N10 restarted, disjoint declared-source edits | Same M4 gates; different snapshots required. |
| N10 restarted, common-dependency edits | Same M4 gates; shared dependency changes required. |
| **N10 first attachment, disjoint declared-source edits** | Same M4 gates, including admission/ingestion/bootstrap. Fresh output views/engines, with no preparatory build in those views. |

The first-attachment stratum is required because D018 found no restored roots in audited SkyCache preparations: prepared-pool restarts do not establish cheap newcomers. Declare an equal seed-publication/cache history for both arms and keep initial seed costs visible separately. Count retained seed/service state; include all additional publication work in the measured requests. Do not erase an output-view-specific preparation from the boundary by calling it setup. Report pre-existing immutable inputs and newly admitted bytes explicitly. Fresh named caches remain separate setup-cost coverage; first attachment does not mean a globally cold machine.

For every row above, including the restarted N1 diagnostic, use **five paired repetitions**, with alternating order fixed before execution. Publish every candidate/baseline ratio, median and range. All five ratios must meet every applicable threshold; no discarding slow successful runs, adaptive extension or sampling until favorable. This is an engineering reproducibility rule, not statistical confidence. A missed ratio blocks advancement but does not alone establish architectural impossibility. Retain the plan's one bounded, evidence-based repair rule; do not move gates.

For N10 verify distinct requests/output views, unique build UUIDs, actual positive all-request interval overlap and variant correctness. Publish per-request latencies/completions alongside cohort wall time. Cancellation, starvation, incorrect reuse and lifecycle recovery are independent correctness gates. BUILD/configuration/glob/cold cases remain required coverage without an exhaustive five-pair cross-product unless explicitly justified and frozen before candidate results.

## Fixed publication, lifecycle and qualification contracts

The initial supported source API admits an immutable Git-derived base revision and transactional `Apply(baseRevision, edits) -> newRevision` updates. A transaction supplies complete file bytes and explicit add/delete/mode/symlink operations, validates its expected base generation, and atomically publishes the new root; readers pin their root. Start the edit-to-output clock before reading/admitting edit bytes. Track directory membership and missing paths as inputs. Raw dirty directories without a qualified coherent-capture adapter use explicit fallback and do not count as a successful shared-evaluator case. Repository/toolchain bindings must name immutable results with semantic configuration/environment and tracked symlink behavior; physical-path normalization alone cannot authorize reuse.

Prepared-root warm requests use one successful preparation in each measured view, then the same revision/query without restart. Restarted requests use one successful base-revision preparation per view, verified daemon shutdown, and then the requested revision; apply disjoint/common-dependency edits inside the admission boundary. Independent correctness references use separate pools. First attachment starts with exactly one successful base-revision seed build in a separate seed view per arm, followed by ten fresh views with disjoint edits and no per-view preparation. Publish and charge seed costs separately, include retained seed/service state, and count every newcomer-specific repository/setup operation. Immutable fixture/flag/seed manifests must be committed before their first candidate performance result; they cannot be selected afterward for favorable overlap. Correctness coverage must include observable source changes and expected failures; unchanged output bytes alone do not prove mutation handling.

Completion requires requested materialization and acknowledgement of all required publication work, including reachable objects and metadata at the selected durability level. Record request-response latency separately from the full accounting window. Drain the request-owned publication/service queues and descendant processes, with a fixed 60-second deadline after response; timeout makes the run ineligible. Required work cannot be reassigned to idle maintenance. Record persistent service state throughout, and retain complete raw scope accounting.

Primary PSS cadence is 500 ms, with boundary observations and raw timestamps/read durations. Before acceptance comparisons, qualify this cadence against an endpoint-only observer using three fixed paired repetitions in each system at N1 warm and N10 restarted; alternate order. The endpoint-only arm retains scope/process CPU boundaries and correctness checks but no periodic PSS walk. Require all three conservative non-harness CPU ratios and client-latency ratios (periodic/endpoint-only) to be at most 1.05, with known positive denominators. This is an observer qualification, not an architecture comparison or a statistical confidence claim. Failure blocks performance eligibility; the plan's one bounded cause-based repair rule applies without relaxing 1.05. Validate short-lived process coverage and PID ownership separately; missing required accounting is never a measured zero. Memory remains a sampled peak with its coverage limitation, not an exact instantaneous maximum.

The M1 native/private probe is diagnostic and may run before this full qualification. Its build-only counters do not satisfy ingestion/drain acceptance by themselves. Native qualification cannot substitute its private comparator for isolated SkyCache, nor turn private graphs in one JVM into demonstrated semantic sharing. M1 must still establish enough conservative savings headroom before a new evaluator is implemented.
