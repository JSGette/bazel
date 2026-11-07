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
| i1-off-r | candidate-off / 1 | True | 13.852 | 22.715 | 22.220 | 713.345 | 63236 | 14709 |
| i1-sky-r | skycache / 1 | True | 14.595 | 25.645 | 25.120 | 722.672 | 50834 | 3384 |

| Row | Analysis elapsed s | Execution elapsed s | Executed actions | Action-cache hits | Action-cache misses | Internal runners | Other runners | Analysis-cache hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| source-ref | 258.032 | 9.953 | 10 | 230 | 10 | 10 | 0 | unknown |
| source-vanilla-r | 282.463 | 9.007 | 10 | 230 | 10 | 10 | 0 | unknown |
| source-sky-r | 658.531 | 102.019 | 10 | 230 | 10 | 10 | 0 | 32720 |
| i1-ref | 3.395 | 0.203 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-vanilla-r | 3.962 | 0.216 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-off-r | 3.346 | 0.192 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-sky-r | 4.131 | 0.181 | 1 | 23 | 1 | 1 | 0 | 3272 |

| Row | Unique build UUIDs | All-client command overlap s | Start spread s |
| --- | ---: | ---: | ---: |
| source-ref | 10 | 43.908 | 0.085 |
| source-vanilla-r | 10 | 46.342 | 0.072 |
| source-sky-r | 10 | 94.968 | 0.069 |
| i1-ref | 1 | 13.891 | 0.000 |
| i1-vanilla-r | 1 | 14.523 | 0.000 |
| i1-off-r | 1 | 13.744 | 0.000 |
| i1-sky-r | 1 | 13.940 | 0.000 |

Overlap uses hash-verified measured BEP start/finish timestamps, not daemon presence. It proves concurrent in-flight commands, not simultaneous CPU-running threads.

## Same-binary on/off anchors

```json
[
  {
    "off": "i1-off-r",
    "on": "i1-sky-r",
    "clients": 1,
    "single_trial_on_over_off": {
      "scope_cpu_s": 1.1290081060536257,
      "engine_cpu_s": 1.1305130513051307,
      "engine_pss_bytes": 1.0130752328995913,
      "graph_nodes": 0.8038775381112024
    }
  }
]
```

## N1 to N10 scaling

```json
[]
```

## Missing, active or failed rows

- i10-ref: running
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
