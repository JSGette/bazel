# Shared evaluation M0 evidence summary

No milestone verdict is inferred. Eligibility denotes complete performance evidence checks, not an architecture gate.

## Measured cohorts

| Run | Mode | N | Scenario | Cache class | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine peak PSS MiB |
| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| m0-v7b-01-build-ref | vanilla | 10 | build | restarted-engine; prepared output-base/cache state retained; repo=populated, action=populated, analysis=disabled | False | 59.181 | 287.000 | 276.440 | 7127.637 |

## Client duration and work counts

Sums are across clients. Analysis/execution durations can overlap and are not CPU. Unknown means incomplete or absent reporting.

| Run | Analysis elapsed sum s | Execution elapsed sum s | Graph nodes sum | Targets configured sum | Actions executed sum | Reported action user CPU s | Reported action system CPU s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| m0-v7b-01-build-ref | 285.483 | 13.393 | 632495 | 147108 | 10 | unknown | unknown |

## Recorded cache counters by client

Counters are copied from the named invocation statistics. Missing fields remain unknown; no cache hit is inferred from build success.

| Run/client | Variant / configuration | BEP hit counters | Shared reads | Validation records | Witnesses checked | Rejected roots | Published / failed |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| m0-v7b-01-build-ref/0 | base / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/1 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/2 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/3 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/4 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/5 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/6 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/7 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/8 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |
| m0-v7b-01-build-ref/9 | build / fastbuild | unknown | unknown | unknown | unknown | unknown | unknown / unknown |

## Evidence qualifications

- **m0-v7b-01-build-ref**: digest_oracle_missing_or_failed:0; digest_oracle_missing_or_failed:1; digest_oracle_missing_or_failed:2; digest_oracle_missing_or_failed:3; digest_oracle_missing_or_failed:4; digest_oracle_missing_or_failed:5; digest_oracle_missing_or_failed:6; digest_oracle_missing_or_failed:7; digest_oracle_missing_or_failed:8; digest_oracle_missing_or_failed:9. Report SHA-256: `a0e0059b6150e6675581726e7d5d490733a4ecb9b0b2fc44d272045de1492d8a`.

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
