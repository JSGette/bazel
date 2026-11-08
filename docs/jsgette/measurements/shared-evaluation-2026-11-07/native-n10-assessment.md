# Native N10 assessment

2026-11-08. Keep bounded native hosting as a useful component, and proceed to the typed-sharing ceiling experiment. This diagnostic does not pass M1 or M4 and does not justify more native warm/variant runs or heap tuning now.

The [verified diagnostics](native-n10-diagnostics.json) cover one ordered private/native pair on identical inputs, after a separate correctness reference. Both used the same cap-ten binary, six CPU quota, 10 GiB memory, no scope swap and 500 ms sampling. Private used ten 768 MiB heaps; native used one 7,680 MiB heap. The native implementation still owns ten independent evaluator graphs.

| Observed metric | Private | Native | Native/private |
| --- | ---: | ---: | ---: |
| Build-window non-harness CPU, seconds | 273.840655–273.841326 | 92.323921–92.324538 | 0.337144–0.337147 |
| Daemon CPU, seconds | 271.830 | 90.840 | 0.334179 |
| Sampled unique-daemon peak PSS, MiB | 5,606.068 | 4,220.933 | 0.752922 |
| Measured client-cohort wall, seconds | 131.735 | 111.598 | 0.847142 |
| Whole recorded lifecycle scope CPU, seconds | 923.760430 | 737.867303 | 0.798765 |

The conservative non-harness CPU reduction is approximately **66.3%**, PSS **24.7%**, cohort wall **15.3%**, and lifecycle CPU **20.1%**. Ratios were independently recomputed from the archived endpoints. Python harness CPU was approximately 11.323 seconds private versus 5.904 seconds native; its bounded accounting does not remove sampling perturbation. Daemon CPU endpoints extend beyond the frozen scope endpoints. PSS is a sampled process metric, not retained heap or an exact simultaneous peak.

This was a pressured comparison. Both scopes reached the 10 GiB limit: build-window `memory.events.max` increased by 64,160 private and 48,097 native, with about 2.04 million versus 1.53 million pages scanned. Full I/O pressure accumulated **85.113 versus 84.989 seconds**; full memory pressure accumulated 3.513 versus 2.790 seconds. Both recorded zero OOM/OOM-kill events. CPU throttling also differed materially. Native's lower daemon PSS coexisted with a similar whole-scope memory peak and more file-backed memory; it is not evidence of a 25% whole-scope footprint reduction. Reclaim, cache residency and scheduling can strongly affect this ordered pair.

All ten clients in each arm matched the independent output digest and behavior oracle. BEP UUIDs were distinct, workspace/PID bindings passed, and every client overlapped for at least 57.708 seconds private or 23.656 seconds native. The 264/266 and 224/226 complete PSS samples respectively covered ten distinct private processes or one native process mapped to ten engines; the remaining two samples per arm were pending startup, with no invalid samples. Both final guards recorded unsignalled scope absence.

I independently inspected all **20 measured BEPs** in the hash-verified [private archive](evidence/auxiliary/native-n10-probe01-private/runtime.tar.gz) and [native archive](evidence/auxiliary/native-n10-probe01-native/runtime.tar.gz). Each reported exactly one executed `BazelWorkspaceStatusAction`, one internal runner, 23 action-cache hits and one unconditional miss. No compilation action was reported executed in the measured windows. This supports discussing build-control cost, while leaving analysis, validation, JVM startup/JIT/GC and other work unattributed.

Lifecycle figures include fixture/seed creation, preparation, verification and shutdown. Both arms shut down after each serial preparation; the native preparation BEPs show ten different JVM PIDs. The lifecycle result therefore does not demonstrate preparation amortization across ten simultaneously retained native engines.

The comparator is this native fork's private mode, not the frozen isolated-SkyCache economic denominator. One ordered pair and unqualified observer overhead establish neither repeatability nor the 50% memory/30% CPU gates; memory improvement falls short of 50% even here. Preserve the hosting result, then measure typed equivalence, dependency validation and unsupported-cost bounds on the reconstructed SkyCache baseline. Do not pool CPU measurements across those binaries or infer a shared-graph saving from this hosting result.
