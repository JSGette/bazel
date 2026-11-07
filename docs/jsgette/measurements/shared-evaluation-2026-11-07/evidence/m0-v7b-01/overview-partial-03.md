# M0 matrix snapshot

Coverage: **partial**. Batch: `validating`. No automatic milestone verdict.

Each comparison is one trial per arm: descriptive ratios only, no uncertainty estimate or statistical claim. Candidate on/off comparisons use the same binary and only identical/restart anchors; vanilla comparisons span different source versions. Inspect pool history, flags and environment provenance before interpreting ratios.

Scope CPU includes clients/descendants; engine CPU is sampled JVM ticks. Analysis/execution durations are elapsed time, not CPU. PSS excludes compiler workers; page-cache ownership and preparation affect scope memory.

| Row | Mode / N | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine PSS MiB | Graph nodes | Targets configured |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| source-ref | vanilla / 10 | False | 53.300 | 287.677 | 278.410 | 6722.622 | 632360 | 147090 |
| source-vanilla-r | vanilla / 10 | True | 50.053 | 280.464 | 272.830 | 7230.569 | 632360 | 147090 |
| source-sky-r | skycache / 10 | True | 98.197 | 317.841 | 292.900 | 5883.334 | 508340 | 33840 |
| i1-ref | vanilla / 1 | False | 14.001 | 22.671 | 22.160 | 713.506 | 63236 | 14709 |
| i1-vanilla-r | vanilla / 1 | True | 14.636 | 26.739 | 26.280 | 505.651 | 63236 | 14709 |

| Row | Analysis elapsed s | Execution elapsed s | Executed actions | Action-cache hits | Action-cache misses | Internal runners | Other runners | Analysis-cache hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| source-ref | 258.032 | 9.953 | 10 | 230 | 10 | 10 | 0 | unknown |
| source-vanilla-r | 282.463 | 9.007 | 10 | 230 | 10 | 10 | 0 | unknown |
| source-sky-r | 658.531 | 102.019 | 10 | 230 | 10 | 10 | 0 | 32720 |
| i1-ref | 3.395 | 0.203 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-vanilla-r | 3.962 | 0.216 | 1 | 23 | 1 | 1 | 0 | unknown |

| Row | Unique build UUIDs | All-client command overlap s | Start spread s |
| --- | ---: | ---: | ---: |
| source-ref | 10 | 43.908 | 0.085 |
| source-vanilla-r | 10 | 46.342 | 0.072 |
| source-sky-r | 10 | 94.968 | 0.069 |
| i1-ref | 1 | 13.891 | 0.000 |
| i1-vanilla-r | 1 | 14.523 | 0.000 |

Overlap uses hash-verified measured BEP start/finish timestamps, not daemon presence. It proves concurrent in-flight commands, not simultaneous CPU-running threads.

## Same-binary on/off anchors

```json
[]
```

## N1 to N10 scaling

```json
[]
```

## Missing, active or failed rows

- i1-off-r: running
- i1-sky-r: not_started
- i10-ref: not_started
- i10-vanilla-r: not_started
- i10-off-r: not_started
- i10-sky-r: not_started
- build-ref: not_started
- build-vanilla-r: not_started
- build-sky-r: not_started
- disjoint-ref: not_started
- disjoint-vanilla-r: not_started
- disjoint-sky-r: not_started
- config-ref: not_started
- config-vanilla-r: not_started
- config-sky-r: not_started
- glob-ref: not_started
- glob-vanilla-r: not_started
- glob-sky-r: not_started
- i1-vanilla-w: not_started
- i1-sky-w: not_started
- i10-vanilla-w: not_started
- i10-sky-w: not_started
- i1-vanilla-c: not_started
- i1-sky-c: not_started

JSON preserves checked archive/report hashes, guard/accounting checks, complete-PSS sample counts, correctness oracles, caps, harness, preparation/pool history, effective flags and per-client cache counters. Reference rows remain correctness-only.
