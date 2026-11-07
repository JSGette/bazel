# M0 matrix snapshot

Coverage: **partial**. Batch: `stopped_for_assessment`. No automatic milestone verdict.

Each comparison is one trial per arm: descriptive ratios only, no uncertainty estimate or statistical claim. Candidate on/off comparisons use the same binary and only identical/restart anchors; vanilla comparisons span different source versions. Inspect pool history, flags and environment provenance before interpreting ratios.

Scope CPU includes clients/descendants; engine CPU is sampled JVM ticks. Analysis/execution durations are elapsed time, not CPU. PSS excludes compiler workers; page-cache ownership and preparation affect scope memory.

| Row | Mode / N | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine PSS MiB | Graph nodes | Targets configured |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |

| Row | Analysis elapsed s | Execution elapsed s | Executed actions | Action-cache hits | Action-cache misses | Internal runners | Other runners | Analysis-cache hits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |

## Same-binary on/off anchors

```json
[]
```

## N1 to N10 scaling

```json
[]
```

## Missing, active or failed rows

- source-ref: stopped — Guard or command failed for source-ref; preserve evidence and assess before continuing.
- source-vanilla-r: not_started
- source-sky-r: not_started
- i1-ref: not_started
- i1-vanilla-r: not_started
- i1-off-r: not_started
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
