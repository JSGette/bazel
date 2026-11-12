# Shared evaluation across worktrees: gated implementation plan

Date: 2026-11-07

Status as of 2026-11-12: M0 complete; M1 inconclusive after the bounded investigation. Stopped before M2 for an architectural pivot. No later gate has passed.

Evidence is recorded in the [measurement index](../measurements/shared-evaluation-2026-11-07/README.md). Architectural choices, commands, deviations, measurements, and gate decisions belong in the [decision log](../measurements/shared-evaluation-2026-11-07/decision-log.md).

## Hypothesis and initial scope

Adding a worktree should mostly cost its changed inputs, affected computations, and active requests, rather than another complete evaluation state.

The first implementation is a small local evaluation service supporting one real rule family, using existing content-addressable storage (CAS) and action execution infrastructure. Remote placement follows demonstrated local savings. Separate snapshot, query, immutable result, invocation, and output-view identities. Keep rule semantics stable initially to isolate the effect of changing evaluation-state ownership and sharing.

The percentages below are proposed acceptance thresholds, not predicted or measured results. Finalize them after measuring the M0 baseline and before implementing the candidate. Record the frozen thresholds and their rationale in the decision log; do not revise them later to fit results.

## Execution authority and stopping rule

The user authorized saving this plan to their fork and implementing it without stopping for routine architectural choices. Make educated decisions autonomously and document exactly what was chosen, why, what alternatives were rejected, and what evidence would reverse the decision.

Continue automatically across passing milestones. A failed gate permits one timeboxed repair attempt only when a measured cause and a specific fix exist. Correctness failures block advancement. Stop and present the evidence when a milestone fails and an architectural pivot is needed. Do not expand scope or soften thresholds to rescue a failed experiment. Passing a narrow fixture does not constitute passing a broader milestone.

## Milestone status

| Milestone | State | Gate outcome |
| --- | --- | --- |
| M0: baseline and duplicated cost | Complete | Continue on repeated retained state; partial CPU-breakdown deviation recorded |
| M1: dependency-trace replay | Inconclusive — pivot required | All N1 controls and the bounded residual audit assessed; neither CPU nor memory savings ceiling established. D037 stops this route before M2 |
| M2: immutable snapshots and query inputs | Not started | Not decided |
| M3: real end-to-end evaluation slice | Not started | Not decided |
| M4: ten concurrent worktrees | Not started | Not decided |
| M5: persistence, recovery, and remote placement | Not started | Not decided |
| M6: second workload and investment decision | Not started | Not decided |

## M0: Establish the baseline and identify duplicated cost

Implement a repeatable benchmark harness comparing independent Bazel servers with the current SkyCache implementation. Start with 1 and 10 worktrees. Cover identical inputs, disjoint source edits, shared BUILD-file edits, configuration changes, and additions under globs.

Measure analysis, validation, graph restoration, action execution, memory, CPU, and wall time separately. Include warm servers, restarted servers with warm caches, and cold caches. Pin source revisions, hardware limits, configurations, and cache state. Existing measurements may guide workload selection but must remain distinguishable from new measurements.

**Continue:** duplicated analysis, validation, or retained state is a substantial cost that the proposed architecture can address.

**Stop or redirect:** execution dominates and duplicated evaluator work is too small to justify changing the architecture.

**Deliverable:** baseline results, a cost breakdown, and the chosen representative target.

**Recorded outcome (2026-11-08):** [M0 assessment](../measurements/shared-evaluation-2026-11-07/m0-gate-assessment.md) continues to bounded M1 after the 29-row matrix and two diagnostic sessions. Repeated retained state supplies the basis; CPU attribution remains partial under D016. This does not establish typed overlap, cheap first attachment or the later economic gates. No additional M0 or N2 transfer run is required for this decision.

## M1: Estimate the benefit using real dependency traces

**Preliminary native-hosting probe:** build and pin one fork binary with the existing two-engine cap, qualify functionality, then compare private and native N2 runs with matched effective policies, independent references and complete accounting. Native mode retains private graphs and changes other services; its result is a bundle comparison, **not a semantic-sharing pass**. Consider a ten-engine cap only after N2 correctness and measured usefulness justify the bounded extension, with admission and ownership checks. This cheaper preliminary route does not replace the trace savings ceiling required before a graph rewrite or change isolated SkyCache as the later comparator.

**Preliminary status (2026-11-08):** cap-two/N2 and cap-ten source qualification and all [21 cap-ten acceptance checks](../measurements/shared-evaluation-2026-11-07/evidence/auxiliary/native-acceptance-cap10-01/results.json) passed. The [completed N10 assessment](../measurements/shared-evaluation-2026-11-07/native-n10-assessment.md) covers an independent reference and one ordered private/native pair: build-window non-harness CPU fell about 66.3%, sampled daemon PSS 24.7%, and full recorded lifecycle CPU 20.1%, under heavy reclaim/I/O pressure. Native still retains private graphs, and the comparator is this fork's private mode. D028 preserves that useful hosting result and proceeds to typed traces without more N10 warm/variant runs or heap tuning. The separate reconstructed SkyCache prototype passed product build and all thirteen focused Java targets under D032. Its eight-build/five-trace Java interoperability smoke passed. D034 closes all twenty N1 workload sessions and ten replay checks. The [N1 assessment](../measurements/shared-evaluation-2026-11-07/m1-trace-n1-diagnostics.md) and [function coverage](../measurements/shared-evaluation-2026-11-07/m1-trace-family-coverage.md) show material tracing cost, over 99% unsupported observations and unresolved CPU/memory ceilings. This does not pass M1 or M4.

Instrument the selected target to record logical queries, dependency observations, value sizes, and computation/validation costs. Build an offline replay tool that groups equivalent results across snapshots and models retained variants.

Measure lookup and validation overhead in the replay itself. Estimate remaining savings after accounting for indexes, dependency records, and request bookkeeping. Give unsupported costs an explicit generous bound so a narrow trace subset cannot determine the gate by omission. Treat diagnostic names and normalized physical paths as diagnostic evidence, not sufficient authorization for a cache hit.

**Continue:** measured overlap leaves enough headroom to plausibly meet the later memory and CPU gates.

**Stop:** even optimistic sharing cannot meet those gates.

**Pivot:** if repeated validation consumes most of the potential savings, test a shared snapshot/validation service before building a new evaluator.

**Deliverable:** an explicit savings ceiling and evidence identifying which computations can be shared. Replay establishes potential; it does not establish evaluator correctness.

Timebox M0 and M1 together to roughly one engineer-week. This is the first investment decision, not a promise about elapsed time.

**Recorded outcome (2026-11-12):** the [final M1 assessment](../measurements/shared-evaluation-2026-11-07/m1-gate-assessment.md) is inconclusive. The one bounded residual investigation did not resolve either CPU or memory savings. Stop the current trace route and retain the useful native-hosting component. A completed immutable result-bundle experiment is proposed as a new architectural direction, not started and not an M2 pass.

## M2: Implement immutable snapshots and explicit query inputs

Implement a narrow contract such as `Evaluate(snapshot, query)`, with separate identities for snapshots, logical queries, immutable results, invocations, and output views.

Snapshot inputs must cover file contents, directory membership, missing-path observations, symlinks, relevant modes, configuration, repository bindings, and environment inputs used by the supported slice. Capture must be coherent or detect changes and retry. Unsupported ambient inputs must produce an explicit fallback.

**Continue:** mutation tests cannot admit stale or mixed input states; relocating a checkout or deleting its output view preserves valid reuse; capture overhead fits within the agreed input-processing budget.

**Stop or narrow scope:** required inputs cannot be represented reliably without changing build behavior.

**Deliverable:** the snapshot store, tracked-read interface, and correctness fixtures.

## M3: Complete one real end-to-end evaluation slice

Implement a single-process evaluator with candidate lookup, recorded dependency observations, multiple result variants, lazy validation, and recomputation. Connect one representative rule family to actual compiler actions and output materialization. Include transitive dependencies, toolchain/configuration inputs, and one generated-source path.

Reuse existing infrastructure where compatible. Verify portability of existing serialized values per type. Initially keep unfinished evaluations specific to their snapshots; share completed results after validation.

**Continue:** supported cases match isolated clean evaluation, including expected failures; A-to-B-to-A reuses earlier variants; unrelated edits preserve expected reuse; warm single-request latency and CPU remain within 10% of the SkyCache baseline.

**Stop or pivot:** validation still traverses most of the graph on every warm request, adapter costs erase the benefit, or compatibility work expands beyond the selected slice.

**Deliverable:** a working vertical slice with output-digest comparisons and measured overhead. Builds handled entirely by fallback do not count as evidence of success.

## M4: Demonstrate the economics of ten concurrent worktrees

Implement shared pending computations where equivalence is established, subscriber-based cancellation, request-local diagnostics, and fair scheduling. Begin with identical snapshots and fully specified actions. Extend analysis sharing only where its input equivalence can be proven; an equal target label alone is insufficient.

Run ten simultaneous source variants, including disjoint edits and changes to common dependencies.

Proposed continue gates, relative to isolated SkyCache builds:

- At least 50% lower aggregate build-control memory.
- At least 30% lower build-control CPU per completed build.
- No more than 10% regression in single-request latency.
- No incorrect reuse, cancellation interference, or starvation in the supported test suite.
- Benefits persist across different source snapshots.

Count clients, evaluator, metadata service, and validation processes. Report compiler-worker costs and total system peaks alongside these numbers. Moving costs from clients to a server does not establish savings.

**Stop the live-sharing direction:** savings depend on serializing different snapshots, or apply only to identical workspaces. Retain completed-result sharing if it independently pays.

**Deliverable:** the decisive architecture benchmark.

## M5: Add persistence, recovery, and remote placement

Implement durable result records, format compatibility, bounded candidate searches, leases, garbage collection, restart recovery, and recovery from missing CAS objects. Then run the same service remotely.

Test worker crashes, interrupted publication, cancellation storms, garbage collection during reads, and client disconnects. Scale to 100 source variants within a fixed resource budget.

**Continue:** correctness survives the failure suite; retained memory returns to its expected level after requests finish; candidate search and validation remain bounded; remote network/storage overhead preserves the agreed economic benefit.

**Stop or pivot:** retention, recovery, or network costs consume the savings, or memory growth approaches one complete graph per worktree. If local sharing works but remote placement does not, retain the local service as a valid outcome and record the failed remote-placement gate.

**Deliverable:** a recoverable service, resource limits, fault-test results, and a local-versus-remote comparison.

## M6: Validate a second workload and decide the investment

Implement a second materially different rule family and one user-relevant large target. Run the same benchmark and correctness suite, including less-overlapping edits and cold starts. Record unsupported features and fallback frequency.

**Continue toward broader adoption:** both workloads meet the agreed gates, and the next compatibility increment has a bounded implementation scope.

**Pivot to a smaller product:** shared snapshots, validation, immutable graph fragments, or materialization provide value independently.

**Stop the evaluator rewrite:** gains remain confined to synthetic or unusually overlapping workloads, or compatibility costs exceed the expected benefit.

**Deliverable:** a decision record containing measured gains, operating costs, compatibility gaps, migration/fallback strategy, and the scope of the next investment.

## Separate follow-up experiments

Phase removal, execution-dependent analysis, and compiled recipe intermediate representations receive separate experiments only after M4 succeeds. Keeping rule semantics stable initially makes the central result interpretable: whether changing ownership and sharing of evaluation state makes concurrent worktrees cheaper.

The first concrete deliverable is the M0-M1 baseline and trace-replay report, ending with a recommendation to stop, pursue shared validation only, or build the narrow evaluator.
