# M0 assessment — continue to bounded M1

2026-11-08. **M0 is complete: continue on the repeated-retained-state branch of the gate.** This authorizes a bounded investigation, not an evaluator rewrite or a claimed memory/CPU saving. The [29-row matrix](evidence/m0-v7b-01/overview-partial-11.md) and both N1 diagnostic sessions passed their correctness/accounting checks. No further M0 runs are needed. Earlier v6/v7 infrastructure failures remain preserved and excluded from these comparisons.

The representative workload is the pinned Datadog normalizer, `//pkg/networkdevice/profile/profiledefinition/normalize_cmd:normalize_cmd`, at source revision `85ad968bca856fa3d4e07b4d8e11961dabe6866f`. It is a build-control workload, not a proxy for compiling the whole Agent.

| Gate question | Assessment |
| --- | --- |
| Declared coverage and correctness | Complete: seven references and twenty-two performance measurements cover N1/N10, source/BUILD/disjoint/configuration/glob variants, restart/warm/fresh-cache lifecycles. Independent oracles, provenance, cgroup ownership, hashes and command overlap passed. |
| Substantial repeated state | Sufficient to investigate: graph populations scale with independent engines; substantial warm daemon residency remains after requests become cheap. Post-GC/value diagnostics confirm persistent state, without measuring its shareable fraction. |
| Does execution dominate? | Prepared-cache measured builds execute internal workspace-status actions and no compiler actions. Cold builds execute real actions. CPU attribution remains partial; continuation rests on state, not a proven evaluator CPU fraction. |
| Is another worktree cheap? | Unproven. First attachment remains a distinct acceptance stratum; captured witness conflicts identify a concrete portability problem. |
| Have later economics passed? | No. M1 must establish the savings ceiling; later gates retain isolated SkyCache as denominator. |

## Evidence supporting the decision

| Identical workload | Wall s | Scope CPU s | JVM CPU s | Peak daemon PSS MiB | Graph nodes |
| --- | ---: | ---: | ---: | ---: | ---: |
| N1 restart, candidate off | 13.852 | 22.715 | 22.220 | 713.345 | 63,236 |
| N10 restart, candidate off | 53.614 | 286.105 | 276.880 | 6,906.965 | 632,360 |
| N10 restart, SkyCache on | 86.104 | 305.991 | 285.190 | 5,483.610 | 508,340 |
| N10 warm, vanilla | 1.858 | 11.016 | 9.980 | 6,108.826 | 632,360 |
| N10 warm, SkyCache | 4.472 | 13.872 | 12.050 | 7,787.117 | 691,040 |
| N1 fresh caches, vanilla | 98.226 | 112.634 | 43.140 | 663.762 | 63,307 |
| N1 fresh caches, SkyCache | 99.278 | 123.409 | 53.390 | 772.947 | 69,175 |

The same-binary N10 restart comparison records 32,720 SkyCache hits, 19.6% fewer graph nodes and 20.6% lower PSS, alongside 3.0% more JVM CPU and 7.0% more scope CPU. Candidate-off N10 has 10× N1's nodes and 9.68× PSS. Warm requests are cheap but retain 5.97–7.60 GiB of daemon PSS across ten engines. These observations justify measuring alternatives; they do not prove equivalent typed contents or a sharing ceiling. Warm vanilla/SkyCache comparisons cross binary versions and lack a warm candidate-off control.

Each fresh-cache row executed 24 actions, with reported BEP action user-plus-system CPU of 37.386/37.567 seconds. Full scope totals also include repository/network/setup work; action CPU is not a complete partition. Fresh named caches do not imply a cold installed binary or OS page cache.

The [diagnostic analysis](m0-diagnostic-analysis.md) reports rounded post-GC heaps of **94 MB off / 113 MB on**, and estimated unique reachable value payloads of **42.23 / 44.71 MiB**. Parsing preserves seven printed-name collision groups in each dump. These values exclude important graph/JVM structures and are not dominator-retained sizes. Peak PSS was sampled before the forced-GC diagnostics; subtracting heap or payload from it cannot establish a native-memory fraction or potential savings.

## Explicit attribution deviation and newcomer limitation

Under D016, M0 closes with a **partial CPU breakdown**, rather than the plan's complete analysis/validation/restoration/execution partition. CPU events decoded successfully, but exposed stacks have at most five frames; leaf-name heuristics are not logical evaluator ownership. JVM/native categories mix runtime, JIT and GC, and utility frames can serve graph work. Samples are not CPU seconds or complete coverage of startup. Unknown work remains unknown.

Scope CPU includes observers, clients and descendants; JVM counters include repository/module work. Profile/BEP spans overlap and measure elapsed time. PSS depends on pool/GC history; missing pressure counters limit slowdown attribution. Every matrix comparison is a single trial under six CPUs, 10 GiB and no swap. The diagnostics include instrumentation overhead. None supplies a variance estimate or retroactive overhead correction.

All 41 audited SkyCache preparations restored zero analysis roots. [Captured witnesses](m0-witness-capture-findings.md) now show twelve conflicting fingerprints at matching external-input keys, including ten repository bindings, between two publications with the same recorded source digest. Their global guards are incompatible under the recorded states. Capture ordering does not prove client identity or the historical first rejection; one-sided physical-path records alone do not explain rejection.

The resulting requirement is portable external-repository bindings and explicit admission granularity that preserve content, environment, permission and symlink semantics. Restart hits may reuse each worktree's own publication. No N2 transfer probe is needed now: the metadata already guides that requirement and does not change the native-hosting uncertainty. Do not remove guards or normalize dependencies away.

## Bounded M1 next step

Build and pin **one fork binary**, retain its **two-engine cap**, and qualify functionality before a matched private/native N2 diagnostic. Match canonical effective policy and preparation histories, use independent references/pools, verify distinct output views and overlapping requests, and deduplicate PID/start-time accounting. Native mode retains private graphs and changes modules, interning and action coalescing: this tests the bundle, not graph sharing. Expand the cap to ten only if N2 correctness and measured usefulness justify it, with admission/ownership qualification.

Before any graph rewrite, typed dependency traces and replay must still establish a savings ceiling including validation, indexes and retained variants. Give unsupported costs a generous explicit bound; do not reject or pass a direction from diagnostic names or a narrow trace subset. Follow the unchanged 50% memory / 30% CPU / ≤10% latency thresholds and full boundaries in [acceptance metrics](acceptance-metrics.md). Native results cannot replace the isolated-SkyCache denominator. M2–M6 remain unstarted.
