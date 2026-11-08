# Shared evaluation M0 evidence summary

No milestone verdict is inferred. Eligibility denotes complete performance evidence checks, not an architecture gate.

## Measured cohorts

| Run | Mode | N | Scenario | Cache class | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine peak PSS MiB |
| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| m0-v7b-01-build-vanilla-r | vanilla | 10 | build | restarted-engine; prepared output-base/cache state retained; repo=populated, action=populated, analysis=disabled | True | 51.329 | 284.657 | 276.920 | 7561.240 |

## Client duration and work counts

Sums are across clients. Analysis/execution durations can overlap and are not CPU. Unknown means incomplete or absent reporting.

| Run | Analysis elapsed sum s | Execution elapsed sum s | Graph nodes sum | Targets configured sum | Actions executed sum | Reported action user CPU s | Reported action system CPU s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| m0-v7b-01-build-vanilla-r | 286.755 | 10.022 | 632495 | 147108 | 10 | unknown | unknown |

## Recorded cache counters by client

Counters are copied from the named invocation statistics. Missing fields remain unknown; no cache hit is inferred from build success.

| Run/client | Variant / configuration | BEP hit counters | Shared reads | Validation records | Witnesses checked | Rejected roots | Published / failed |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| m0-v7b-01-build-vanilla-r/0 | base / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/1 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/2 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/3 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/4 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/5 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/6 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/7 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/8 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-vanilla-r/9 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |

## Evidence qualifications

- **m0-v7b-01-build-vanilla-r**: complete recorded checks; no milestone verdict. Report SHA-256: `9800d0ba75798608a91653db7bdfb8f89d79845abb384659a1a9dd7aa3a35c5b`.

## Interpretation limits

- This summary does not decide or pass M0 or any later milestone.
- Cold-engine means new output bases; repository, action, and SkyCache contents may remain warm.
- Warm uses a prior preparation build; restart stops its daemons but retains that output-base state.
- BEP analysis/execution sums are sums of per-client elapsed durations, not CPU; phases may overlap.
- Action CPU includes only explicitly reported user/system durations and is not whole-build CPU.
- Cgroup CPU includes harness, clients, daemons, and descendants; engine CPU is sampled daemon ticks.
- Engine PSS excludes clients and compiler workers; sampled peaks may miss short-lived maxima.
- Scope memory is charged bytes including page cache; first-touch ownership can bias run order.
- Scope lifetime memory peak includes preparation; component peaks are non-simultaneous and must not be added.
- Profile span sums can overlap and nest across threads; they are not CPU or critical-path estimates.
- No cross-run timing averages, speedups, or conclusions are computed. Review provenance before comparison.

Full per-client counters, profile spans, hashes, command references, and provenance strata are in the accompanying JSON.
