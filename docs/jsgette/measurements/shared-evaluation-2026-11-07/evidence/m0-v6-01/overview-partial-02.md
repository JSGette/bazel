# M0 matrix snapshot

Coverage: **partial**. Batch: `stopped_for_assessment`. No automatic milestone verdict.

Each comparison is one trial per arm: descriptive ratios only, no uncertainty estimate or statistical claim. Candidate on/off comparisons use the same binary and only identical/restart anchors; vanilla comparisons span different source versions. Inspect pool history, flags and environment provenance before interpreting ratios.

Scope CPU includes clients/descendants; engine CPU is sampled JVM ticks. Analysis/execution durations are elapsed time, not CPU. PSS excludes compiler workers; page-cache ownership and preparation affect scope memory.

| Row | Mode / N | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine PSS MiB | Graph nodes | Targets configured |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| i1-ref | vanilla / 1 | False | 14.315 | 23.863 | 23.320 | 716.493 | 63243 | 14709 |
| i1-vanilla-r | vanilla / 1 | True | 14.319 | 24.657 | 24.220 | 501.945 | 63243 | 14709 |
| i1-off-r | candidate-off / 1 | True | 14.115 | 24.751 | 24.230 | 507.650 | 63243 | 14709 |
| i1-sky-r | skycache / 1 | True | 14.381 | 22.723 | 22.170 | 720.335 | 50841 | 3384 |
| i10-ref | vanilla / 10 | False | 52.302 | 290.881 | 281.830 | 7402.988 | 632430 | 147090 |
| i10-vanilla-r | vanilla / 10 | True | 50.984 | 284.249 | 276.440 | 7251.579 | 632430 | 147090 |
| i10-off-r | candidate-off / 10 | True | 51.743 | 288.498 | 279.570 | 7163.109 | 632430 | 147090 |
| i10-sky-r | skycache / 10 | True | 68.620 | 300.053 | 284.300 | 5526.410 | 508410 | 33840 |

| Row | Analysis elapsed s | Execution elapsed s | Executed actions | Action-cache hits | Action-cache misses | Internal runners | Other runners | Analysis-cache hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| i1-ref | 3.469 | 0.192 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-vanilla-r | 3.597 | 0.206 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-off-r | 3.584 | 0.208 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-sky-r | 3.560 | 0.183 | 1 | 23 | 1 | 1 | 0 | 3272 |
| i10-ref | 298.651 | 10.098 | 10 | 230 | 10 | 10 | 0 | unknown |
| i10-vanilla-r | 287.659 | 11.606 | 10 | 230 | 10 | 10 | 0 | unknown |
| i10-off-r | 291.730 | 10.068 | 10 | 230 | 10 | 10 | 0 | unknown |
| i10-sky-r | 429.997 | 4.903 | 10 | 230 | 10 | 10 | 0 | 32720 |

## Same-binary on/off anchors

```json
[
  {
    "off": "i1-off-r",
    "on": "i1-sky-r",
    "clients": 1,
    "single_trial_on_over_off": {
      "scope_cpu_s": 0.9180460141020146,
      "engine_cpu_s": 0.9149814279818408,
      "engine_pss_bytes": 1.4189587445222898,
      "graph_nodes": 0.8038992457663299
    }
  },
  {
    "off": "i10-off-r",
    "on": "i10-sky-r",
    "clients": 10,
    "single_trial_on_over_off": {
      "scope_cpu_s": 1.0400522987519305,
      "engine_cpu_s": 1.0169188396466002,
      "engine_pss_bytes": 0.771509950069693,
      "graph_nodes": 0.8038992457663299
    }
  }
]
```

## N1 to N10 scaling

```json
[
  {
    "mode": "vanilla",
    "n1": "i1-vanilla-r",
    "n10": "i10-vanilla-r",
    "single_trial_n10_over_n1": {
      "engine_cpu_s": 11.413707679603634,
      "engine_pss_bytes": 14.446950536195116,
      "graph_nodes": 10.0
    }
  },
  {
    "mode": "candidate-off",
    "n1": "i1-off-r",
    "n10": "i10-off-r",
    "single_trial_n10_over_n1": {
      "engine_cpu_s": 11.538175815105241,
      "engine_pss_bytes": 14.110319832869724,
      "graph_nodes": 10.0
    }
  },
  {
    "mode": "skycache",
    "n1": "i1-sky-r",
    "n10": "i10-sky-r",
    "single_trial_n10_over_n1": {
      "engine_cpu_s": 12.823635543527288,
      "engine_pss_bytes": 7.672000466362898,
      "graph_nodes": 10.0
    }
  }
]
```

## Missing, active or failed rows

- source-ref: stopped — Guard or command failed for source-ref; preserve evidence and assess before continuing.
- source-vanilla-r: not_started
- source-sky-r: not_started
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
