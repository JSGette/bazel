# N1 trace cost and coverage — completed diagnostics

Measurements: 2026-11-08. Verification and assessment: 2026-11-12.

**Outcome:** all twenty fixed sessions and ten separate replay checks passed. Tracing has material cost and leaves most semantic observations unsupported. This does not pass M1 or establish that shared evaluation cannot work.

The same qualified SkyCache binary ran in both arms, with tracing disabled for preparation. Each session had independent state; restarted and warm lifecycles remained separate. The independent reference, source/behavior oracles, effective policies, complete raw accounting, close receipts and final unsignalled cleanup were verified. All forty build outputs matched the reference digest. [Complete data](m1-trace-n1-diagnostics.json), [frozen protocol](evidence/m1-real-trace-preparation-v1/helpers/n1/m1-real-trace-protocol.md), [completed coordinator and analysis tools](evidence/m1-trace-n1-completion/).

## Every prepared-request pair

Ratios are tracing on / off. CPU uses the conservative non-harness upper/lower ratio. This remainder includes clients and any compiler descendants; it is not automatically evaluator CPU. The prepared request includes descriptor/context checks, build, materialization and required drain, while initial preparation and subsequent behavior checks remain in the separate lifecycle window.

| Lifecycle | Pair | CPU ratio | Wall ratio | Off wall, s | On wall, s |
| --- | ---: | ---: | ---: | ---: | ---: |
| restart | 1 | 1.2664 | 1.2869 | 15.3612 | 19.7676 |
| restart | 2 | 1.2752 | 1.1943 | 15.9614 | 19.0634 |
| restart | 3 | 1.2616 | 1.1709 | 16.0081 | 18.7439 |
| restart | 4 | 1.1095 | 1.1140 | 15.8173 | 17.6202 |
| restart | 5 | 1.2641 | 1.1243 | 16.0529 | 18.0485 |
| warm | 1 | 2.1543 | 1.9678 | 0.4800 | 0.9445 |
| warm | 2 | 2.0101 | 2.1701 | 0.4480 | 0.9722 |
| warm | 3 | 2.3570 | 2.2001 | 0.4569 | 1.0052 |
| warm | 4 | 1.4434 | 1.7426 | 0.5208 | 0.9075 |
| warm | 5 | 1.8216 | 1.9360 | 0.4817 | 0.9326 |

## Median and full range

| Lifecycle | Window | CPU ratio median [range] | Wall ratio median [range] |
| --- | --- | --- | --- |
| restart | build | 1.2649 [1.1099, 1.2761] | 1.1727 [1.1152, 1.2899] |
| restart | lifecycle | 1.0510 [0.9957, 1.0583] | 0.9950 [0.9741, 1.0698] |
| restart | prepared_request | 1.2641 [1.1095, 1.2752] | 1.1709 [1.1140, 1.2869] |
| restart | tail | 1.1381 [0.8102, 1.7015] | 0.9164 [0.6666, 1.5089] |
| warm | build | 2.1111 [1.4625, 2.4587] | 2.3556 [2.0287, 2.8144] |
| warm | lifecycle | 1.0206 [1.0085, 1.0298] | 1.0257 [0.9386, 1.0721] |
| warm | prepared_request | 2.0101 [1.4434, 2.3570] | 1.9678 [1.7426, 2.2001] |
| warm | tail | unknown | 1.0796 [0.7880, 1.5353] |

Whole lifecycle includes fixture/seed work, preparation, checks and shutdown; its smaller ratios do not remove the measured request overhead. Warm pair 3 has an unknown non-harness CPU bound in its short post-response tail, preserved as unknown. Its complete request/build/lifecycle CPU bounds remain known. No trace overhead is subtracted from captured function costs.

## Actual trace coverage

| Lifecycle | Nodes per trace | Observations per trace | Unsupported key/value observations | Observed / retained-only nodes | Trace bytes, range |
| --- | ---: | ---: | ---: | --- | --- |
| restart | 50,951 | 102,680 | 101,869 (99.21%) | 50,894 / 57 | 175,050,715–175,104,770 |
| warm | 69,287 | 69,305 | 69,036 (99.61%) | 17 / 69,270 | 37,082,456–37,090,396 |

All ten captures stayed within the frozen bounds and had verified context/close receipts. Each replay retained 269 complete variants. Every whole-graph savings ceiling remains unknown. Single-trace replay deliberately earns zero cross-engine credit; these repeated N1 captures are not a concurrent sharing experiment. Retained-only nodes earn no current-command CPU credit. Value bytes, Python allocations and JVM/process memory are different units.

The separately guarded parser/table processing ran after each measured session. With Python tracemalloc enabled, restarted traces required about 34.8–35.1 CPU-seconds to parse and 3.70–3.77 CPU-seconds for table processing; warm traces required 6.64–6.84 and 1.24–1.25 CPU-seconds respectively. These are costs of this diagnostic JSON/parser implementation, not a production cache lookup or avoided evaluator work.

## Memory, work and limits

All twenty measured BEPs reported one internal action; restarted builds also reported 23 action-cache hits. No compiler action was reported executed in the measured requests. The 415 measured sampling attempts comprise 395 complete and 20 startup-pending observations, with no invalid samples. No measured-build OOM or reclaim scan was recorded. Throttling and I/O pressure are retained in the data.

| Lifecycle | Sampled daemon-PSS ratio median [range] |
| --- | --- |
| restart | 1.1288 [1.0402, 1.1849] |
| warm | 0.9999 [0.9943, 1.0553] |

Daemon PSS is partial, sampled process memory. Client/harness/other-role memory and a full ownership partition are missing; cgroup peaks include preparation and cannot be subtracted into request peaks. The separate 500-ms PSS versus endpoint-only observer qualification is still pending. These are diagnostic ratios with no M3/M4 threshold verdict.

## Next decision

Do not scale this observer to an N2/N10 sharing claim or multiply its function costs into trace-off totals. The [completed computation-family summary](m1-trace-family-coverage.md) leaves a combined unattributed cost envelope. Select exactly one bounded investigation of the largest measured missing cost question; an undifferentiated residual remains explicitly undifferentiated. The investigation must address the CPU and memory uncertainty relevant to the decision without treating allocation, serialization size, or sampled stack frequency as retained PSS savings. If that one investigation cannot resolve the whole-architecture ceiling, record M1 inconclusive and present the pivot. The frozen 50% memory, 30% CPU and 10% latency goals remain unchanged.
