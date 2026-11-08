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
| i10-ref | vanilla / 10 | False | 50.582 | 283.995 | 275.320 | 7047.931 | 632360 | 147090 |
| i10-vanilla-r | vanilla / 10 | True | 51.114 | 284.468 | 276.820 | 7113.280 | 632360 | 147090 |
| i10-off-r | candidate-off / 10 | True | 53.614 | 286.105 | 276.880 | 6906.965 | 632360 | 147090 |
| i10-sky-r | skycache / 10 | True | 86.104 | 305.991 | 285.190 | 5483.610 | 508340 | 33840 |
| build-ref | vanilla / 10 | False | 59.181 | 287.000 | 276.440 | 7127.637 | 632495 | 147108 |
| build-vanilla-r | vanilla / 10 | True | 51.329 | 284.657 | 276.920 | 7561.240 | 632495 | 147108 |
| build-sky-r | skycache / 10 | True | 81.746 | 301.097 | 281.180 | 5505.923 | 508448 | 33840 |
| disjoint-ref | vanilla / 10 | False | 86.839 | 306.688 | 286.660 | 6762.444 | 632360 | 147090 |
| disjoint-vanilla-r | vanilla / 10 | True | 55.802 | 298.436 | 289.770 | 7701.184 | 632360 | 147090 |
| disjoint-sky-r | skycache / 10 | True | 87.903 | 311.800 | 290.280 | 5868.535 | 508340 | 33840 |
| config-ref | vanilla / 10 | False | 56.217 | 289.528 | 279.500 | 7083.333 | 632360 | 147090 |
| config-vanilla-r | vanilla / 10 | True | 51.065 | 285.374 | 277.610 | 7040.879 | 632360 | 147090 |
| config-sky-r | skycache / 10 | True | 108.153 | 324.301 | 296.230 | 5877.021 | 508340 | 33840 |

| Row | Analysis elapsed s | Execution elapsed s | Executed actions | Action-cache hits | Action-cache misses | Internal runners | Other runners | Analysis-cache hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| source-ref | 258.032 | 9.953 | 10 | 230 | 10 | 10 | 0 | unknown |
| source-vanilla-r | 282.463 | 9.007 | 10 | 230 | 10 | 10 | 0 | unknown |
| source-sky-r | 658.531 | 102.019 | 10 | 230 | 10 | 10 | 0 | 32720 |
| i1-ref | 3.395 | 0.203 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-vanilla-r | 3.962 | 0.216 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-off-r | 3.346 | 0.192 | 1 | 23 | 1 | 1 | 0 | unknown |
| i1-sky-r | 4.131 | 0.181 | 1 | 23 | 1 | 1 | 0 | 3272 |
| i10-ref | 281.278 | 10.214 | 10 | 230 | 10 | 10 | 0 | unknown |
| i10-vanilla-r | 289.223 | 9.402 | 10 | 230 | 10 | 10 | 0 | unknown |
| i10-off-r | 285.206 | 9.115 | 10 | 230 | 10 | 10 | 0 | unknown |
| i10-sky-r | 620.251 | 4.080 | 10 | 230 | 10 | 10 | 0 | 32720 |
| build-ref | 285.483 | 13.393 | 10 | 239 | 10 | 10 | 0 | unknown |
| build-vanilla-r | 286.755 | 10.022 | 10 | 239 | 10 | 10 | 0 | unknown |
| build-sky-r | 578.383 | 4.446 | 10 | 239 | 10 | 10 | 0 | 32720 |
| disjoint-ref | 222.484 | 258.167 | 10 | 230 | 10 | 10 | 0 | unknown |
| disjoint-vanilla-r | 301.261 | 21.462 | 10 | 230 | 10 | 10 | 0 | unknown |
| disjoint-sky-r | 611.280 | 7.980 | 10 | 230 | 10 | 10 | 0 | 32720 |
| config-ref | 284.327 | 10.049 | 10 | 230 | 10 | 10 | 0 | unknown |
| config-vanilla-r | 287.089 | 10.410 | 10 | 230 | 10 | 10 | 0 | unknown |
| config-sky-r | 750.676 | 111.776 | 10 | 230 | 10 | 10 | 0 | 32720 |

| Row | Unique build UUIDs | All-client command overlap s | Start spread s |
| --- | ---: | ---: | ---: |
| source-ref | 10 | 43.908 | 0.085 |
| source-vanilla-r | 10 | 46.342 | 0.072 |
| source-sky-r | 10 | 94.968 | 0.069 |
| i1-ref | 1 | 13.891 | 0.000 |
| i1-vanilla-r | 1 | 14.523 | 0.000 |
| i1-off-r | 1 | 13.744 | 0.000 |
| i1-sky-r | 1 | 13.940 | 0.000 |
| i10-ref | 10 | 43.215 | 0.530 |
| i10-vanilla-r | 10 | 47.004 | 0.008 |
| i10-off-r | 10 | 48.285 | 0.006 |
| i10-sky-r | 10 | 71.287 | 0.071 |
| build-ref | 10 | 50.559 | 0.640 |
| build-vanilla-r | 10 | 46.523 | 0.079 |
| build-sky-r | 10 | 73.840 | 0.076 |
| disjoint-ref | 10 | 33.257 | 0.083 |
| disjoint-vanilla-r | 10 | 46.863 | 0.088 |
| disjoint-sky-r | 10 | 65.259 | 0.082 |
| config-ref | 10 | 50.067 | 0.073 |
| config-vanilla-r | 10 | 46.328 | 0.086 |
| config-sky-r | 10 | 104.478 | 0.085 |

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
  },
  {
    "off": "i10-off-r",
    "on": "i10-sky-r",
    "clients": 10,
    "single_trial_on_over_off": {
      "scope_cpu_s": 1.06950517658012,
      "engine_cpu_s": 1.0300130020225369,
      "engine_pss_bytes": 0.7939247521325564,
      "graph_nodes": 0.8038775381112024
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
      "engine_cpu_s": 10.533485540334855,
      "engine_pss_bytes": 14.067558667946471,
      "graph_nodes": 10.0
    }
  },
  {
    "mode": "candidate-off",
    "n1": "i1-off-r",
    "n10": "i10-off-r",
    "single_trial_n10_over_n1": {
      "engine_cpu_s": 12.46084608460846,
      "engine_pss_bytes": 9.682506348695695,
      "graph_nodes": 10.0
    }
  },
  {
    "mode": "skycache",
    "n1": "i1-sky-r",
    "n10": "i10-sky-r",
    "single_trial_n10_over_n1": {
      "engine_cpu_s": 11.3531050955414,
      "engine_pss_bytes": 7.587967016929364,
      "graph_nodes": 10.0
    }
  }
]
```

## Missing, active or failed rows

- glob-ref: running
- glob-vanilla-r: not_started
- glob-sky-r: not_started
- i1-vanilla-w: not_started
- i1-sky-w: not_started
- i10-vanilla-w: not_started
- i10-sky-w: not_started
- i1-vanilla-c: not_started
- i1-sky-c: not_started

JSON preserves checked archive/report hashes, guard/accounting checks, complete-PSS sample counts, correctness oracles, caps, harness, preparation/pool history, effective flags and per-client cache counters. Reference rows remain correctness-only.
