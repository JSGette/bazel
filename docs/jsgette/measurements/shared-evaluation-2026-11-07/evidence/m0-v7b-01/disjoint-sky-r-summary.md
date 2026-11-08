# Shared evaluation M0 evidence summary

No milestone verdict is inferred. Eligibility denotes complete performance evidence checks, not an architecture gate.

## Measured cohorts

| Run | Mode | N | Scenario | Cache class | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine peak PSS MiB |
| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: |
| m0-v7b-01-disjoint-sky-r | skycache | 10 | disjoint | restarted-engine; prepared output-base/cache state retained; repo=populated, action=populated, analysis=populated | True | 87.903 | 311.800 | 290.280 | 5868.535 |

## Client duration and work counts

Sums are across clients. Analysis/execution durations can overlap and are not CPU. Unknown means incomplete or absent reporting.

| Run | Analysis elapsed sum s | Execution elapsed sum s | Graph nodes sum | Targets configured sum | Actions executed sum | Reported action user CPU s | Reported action system CPU s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| m0-v7b-01-disjoint-sky-r | 611.280 | 7.980 | 508340 | 33840 | 10 | unknown | unknown |

## Recorded cache counters by client

Counters are copied from the named invocation statistics. Missing fields remain unknown; no cache hit is inferred from build success.

| Run/client | Variant / configuration | BEP hit counters | Shared reads | Validation records | Witnesses checked | Rejected roots | Published / failed |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| m0-v7b-01-disjoint-sky-r/0 | disjoint-00 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/1 | disjoint-01 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/2 | disjoint-02 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/3 | disjoint-03 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/4 | disjoint-04 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/5 | disjoint-05 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/6 | disjoint-06 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/7 | disjoint-07 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/8 | disjoint-08 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |
| m0-v7b-01-disjoint-sky-r/9 | disjoint-09 / fastbuild | cacheHits=3272 | 62404 | 34369 | 818 | 0 | False / False |

## Evidence qualifications

- **m0-v7b-01-disjoint-sky-r**: complete recorded checks; no milestone verdict. Report SHA-256: `4f04091ae7a0303746bdaf19810f524c7f599cba994ac2f2aca551b2b5de0984`.

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
