# M0 preparation costs — checkpoint 08

**Fresh output-base attachment is materially more expensive than the reported restarted build.** In the identical N10 controls, initial preparation consumes 554.80 CPU-seconds with the candidate cache off and 665.33 with it on, versus 286.11 and 305.99 CPU-seconds in the subsequent measured restart. These observations justify retaining first attachment as a distinct workload when M4's scope is frozen. They do not identify all that CPU as reusable analysis or establish concurrent attachment latency.

This is an offline audit of the 17 completed rows in [checkpoint 08](evidence/m0-v7b-01/matrix-progress-checkpoint-08.json), not later checkpoints or the aborted v6/v7 attempts. Coverage remains incomplete and no milestone verdict follows. There are 134 serial client preparations: 93 first uses of new output bases and 41 preparations in repeated vanilla pools.

## What “preparation” includes

For each client, the frozen harness starts a daemon, builds the real normalizer, parses evidence, checks its executable/behavior, then immediately shuts that daemon down before starting the next client. The measured phase subsequently starts all requested daemons concurrently against the prepared output bases and caches. Preparation totals include those serial shutdowns; per-client Bazel command wall times exclude the later oracle/evidence work and shutdown. The protocol is in `harness/baseline.py:443–482,682–696`.

For pool run 1, preparation includes first population of the external-repository view, repository-rule execution, initial graph evaluation, eligible SkyCache publication, ordinary action-cache/output materialization and local persistent action-cache establishment. Shared binary installation and existing repository/download/action caches are reused. Git archive/worktree creation and the original fixture capture happened before these scopes and are **not measured** here. This is not a completely cold installation or complete edit-to-output ingestion measurement.

Vanilla pool run 2 has already completed both preparation and measured builds in its reference row. Its next preparation uses another fresh daemon but retained output-base/repository/action-cache state. It is not a new-worktree attachment control. Candidate-off and SkyCache anchors both use new pools; those are the useful same-binary initial-preparation controls. Do not compare SkyCache's first attachment with vanilla's repeated pool and call the difference a cache-feature effect.

## Recorded serial preparation totals

CPU is in seconds. “JVM CPU” sums observed daemon ticks across the serial clients; “client scope range” is the minimum/maximum CPU of individual client preparation windows, not a statistical uncertainty range. Total scope CPU additionally includes shutdowns and inter-client harness work. Memory peaks are deliberately not summed.

| Row | N / pool run | Serial wall s | Total scope CPU s | Sum JVM CPU s | Client scope CPU range s |
| --- | --- | ---: | ---: | ---: | ---: |
| source-ref | 10 / new 1 | 285.71 | 542.96 | 262.25 | 51.60–55.90 |
| source-vanilla-r | 10 / repeat 2 | 163.68 | 197.55 | 191.03 | 18.30–21.60 |
| source-sky-r | 10 / new 1 | 408.42 | 661.14 | 370.65 | 63.34–68.57 |
| i1-ref | 1 / new 1 | 53.28 | 57.04 | 28.22 | 56.91 |
| i1-vanilla-r | 1 / repeat 2 | 29.68 | 21.56 | 20.70 | 21.43 |
| i1-off-r | 1 / new 1 | 54.14 | 55.08 | 26.66 | 54.94 |
| i1-sky-r | 1 / new 1 | 56.63 | 67.39 | 38.56 | 67.25 |
| i10-ref | 10 / new 1 | 308.94 | 547.49 | 267.76 | 52.37–56.76 |
| i10-vanilla-r | 10 / repeat 2 | 168.76 | 201.81 | 195.29 | 18.25–22.23 |
| i10-off-r | 10 / new 1 | 307.28 | 554.80 | 274.44 | 52.89–57.22 |
| i10-sky-r | 10 / new 1 | 414.48 | 665.33 | 374.42 | 64.39–69.39 |
| build-ref | 10 / new 1 | 314.65 | 550.52 | 270.27 | 53.23–57.42 |
| build-vanilla-r | 10 / repeat 2 | 175.97 | 197.69 | 190.91 | 18.29–21.34 |
| build-sky-r | 10 / new 1 | 424.90 | 660.59 | 369.09 | 63.77–67.66 |
| disjoint-ref | 10 / new 1 | 347.45 | 559.23 | 274.12 | 53.79–59.39 |
| disjoint-vanilla-r | 10 / repeat 2 | 217.33 | 199.46 | 191.14 | 18.80–21.99 |
| disjoint-sky-r | 10 / new 1 | 434.20 | 664.18 | 372.82 | 64.82–68.15 |

For identical N10 off/on, individual client-window scope CPU sums are 553.51/663.88 seconds; the additional 1.29/1.44 seconds in total scope CPU cover shutdown/inter-client work. Shutdown wall sums are 5.20/5.12 seconds. Initial-preparation CPU is 1.94/2.17 times the corresponding measured-restart CPU; at N1 the ratios are 2.43/2.63. These are different lifecycle observations, not candidate savings ratios.

## Same-binary N10 newcomer detail

Each cell below belongs to a distinct output base. Command wall time is serial first-build time; the Gazelle duration is one named repository-function elapsed span, not CPU and not added to command wall. Later clients benefit from shared storage/page-cache history within that cohort; they are not independent cold-start trials.

| Client | Off wall s | On wall s | Off scope / JVM CPU s | On scope / JVM CPU s | Gazelle elapsed off / on s |
| --- | ---: | ---: | --- | --- | --- |
| c00 | 57.23 | 60.13 | 55.81 / 27.20 | 66.89 / 38.05 | 21.33 / 11.31 |
| c01 | 23.89 | 39.02 | 55.69 / 28.00 | 66.16 / 37.26 | 6.93 / 7.36 |
| c02 | 26.15 | 38.80 | 56.79 / 29.19 | 64.92 / 36.15 | 6.64 / 6.97 |
| c03 | 34.91 | 39.14 | 57.22 / 29.41 | 64.47 / 35.68 | 6.63 / 7.03 |
| c04 | 26.26 | 38.51 | 54.89 / 27.03 | 64.39 / 35.42 | 6.51 / 6.85 |
| c05 | 24.16 | 38.46 | 54.49 / 26.70 | 65.79 / 36.77 | 6.57 / 6.85 |
| c06 | 24.51 | 38.74 | 52.89 / 24.99 | 69.39 / 40.34 | 7.42 / 6.90 |
| c07 | 24.15 | 29.58 | 56.05 / 28.16 | 66.28 / 37.99 | 6.54 / 7.43 |
| c08 | 25.71 | 47.41 | 54.79 / 26.97 | 68.01 / 38.72 | 6.46 / 6.92 |
| c09 | 34.97 | 39.44 | 54.90 / 26.79 | 67.58 / 38.04 | 6.48 / 7.54 |

At N1, off/on first-build command wall is 53.61/56.10 seconds, scope CPU 54.94/67.25, JVM CPU 26.66/38.56, and Gazelle elapsed time 19.08/20.03. N1-to-N10 total preparation CPU scales by 10.07 off and 9.87 on in these single trials. Serial wall sums cannot predict ten simultaneous newcomers' latency or throughput.

All 41 SkyCache preparation clients across these rows record `local-skycache.json.restore.roots = 0`. Their initially empty stores end preparation with one publication at N1 or ten at N10; the subsequent measured restarted builds report restoration. Populating the shared store during serial preparation did not produce recorded root restoration for later first-attachment clients in this protocol. This is an observation about this implementation/fixture, not an explanation of the cache identity or a general impossibility claim.

## Ordinary actions and repository bootstrap are different evidence

| Preparation group | Local action-cache hits | BEP actions executed | Disk-cache runners | Internal runners | Sandbox runners |
| --- | ---: | ---: | ---: | ---: | ---: |
| New N1 pools, each | 0 | 24 | 16 | 8 | 0 |
| New identical/source N10 pools, each | 0 | 240 | 160 | 80 | 0 |
| New BUILD vanilla reference | 0 | 249 | 165 | 80 | 4 |
| New BUILD SkyCache | 0 | 249 | 169 | 80 | 0 |
| New disjoint vanilla reference | 0 | 240 | 150 | 80 | 10 |
| New disjoint SkyCache | 0 | 240 | 160 | 80 | 0 |
| Repeated vanilla N10 pools | 230; BUILD 239 | 10 | 0 | 10 | 0 |

Repeated vanilla N1 preparation reports 23 local action-cache hits and one internal execution. BEP “actions executed” includes actions served by the disk-cache runner: seeing `GoCompilePkg`/`GoLink` in that list is not evidence that the compiler ran. The BUILD/disjoint references also prime some ordinary-action results for later rows, so those cross-binary first-attachment arms do not have identical initial action-cache populations.

Separately, all **93 new-output-base preparations** contain exactly one complete `Starlark repository function call` span named `gazelle++non_module_deps+bazel_gazelle_go_repository_tools`; none of the **41 repeated-pool preparations** contain that span. The identical N10 off/on sums are 81.51/75.16 elapsed seconds. These sums contain only that named parent event, excluding nested process/fetch spans. Repository commands do not follow ordinary build-action spawn-cache accounting; the existing [bootstrap audit](m0-repository-bootstrap-notes.md) explains the Gazelle/cache path mechanism and its remaining causal uncertainty.

The sum of client scope CPU minus observed JVM CPU is 279.07 seconds off and 289.46 on for identical N10. Repository subprocesses are a plausible contributor, but the residual also includes clients, observer/oracle work and counter boundaries. No per-repository-process CPU attribution was recorded. It must not be relabeled “Go compilation CPU,” “reusable analysis,” or guaranteed savings.

## Resource and correctness qualifications

All 17 guard archives report completion. All 134 preparation windows identify one expected output base and a live JVM in the owned scope, with successful builds and behavior checks; source/tree guards and every preparation shutdown pass. The 93 non-reference preparations have matching independent vanilla output digests. The 41 reference preparations have behavior checks but no independent digest oracle; their costs remain descriptive evidence, not performance-gate trials.

Every row records six cgroup CPUs, 10240 MiB memory, zero cgroup swap, two Bazel jobs/loading threads, 768 MiB JVM maximum heap and 2048 MiB estimated action memory. CPUQuota/MemoryMax are limits, not proof that work was unthrottled or free from reclaim. Preparation accumulates charged file cache within the scope; the archived sampler lacks reclaim/PSI/throttling deltas. Do not sum sequential PSS/charged-memory peaks or extrapolate an uncapped first-attachment latency from these runs.

## Consequence for the M4 freeze

Include **one N10 fresh-attachment stratum using the existing disjoint-source fixture**, alongside prepared/restarted results. Start each request before input admission/output-view attachment and end after its first required outputs/publication are available. Fix shared download/action-cache populations and any service/SkyCache priming policy before comparison; retain priming cost separately and charge repository bootstrap as build control even when it invokes a compiler. Compare against isolated SkyCache, not whichever vanilla pool has the larger setup cost.

This recommendation does not require another M0 matrix or a new rule family. The existing preparation evidence already shows why excluding first attachment would omit a major cost of adding worktrees. If M4's initial claim is deliberately restricted to previously attached worktrees, say so explicitly and leave fresh attachment unqualified rather than treating a restarted daemon as a substitute.

## Evidence protocol

For every completed row listed by checkpoint 08, this audit verified the run archive SHA-256 against both checkpoint and adjacent `.tar.gz.json` manifest, then verified `report.json`, `output-base-binding.json`, every `preparation-cNN-resources.json` and every `preparation/cNN/profile.json.gz` used above against member hashes. The per-client metrics/oracles/action counts are in `report.json.preparation`; `serial-preparation.json` is its standalone counterpart. Guard ZIP hashes, `policy.json` caps and `result.json` completion were checked separately. Exact input/output commands remain in each archive's `preparation/cNN/command.json`.

Primary same-binary archives: [N1 off](evidence/m0-v7b-01/i1-off-r-run.tar.gz), [N1 on](evidence/m0-v7b-01/i1-sky-r-run.tar.gz), [N10 off](evidence/m0-v7b-01/i10-off-r-run.tar.gz), [N10 on](evidence/m0-v7b-01/i10-sky-r-run.tar.gz). The [checkpoint](evidence/m0-v7b-01/matrix-progress-checkpoint-08.json) pins all other row archive names/hashes. Only published C-backed evidence and existing notes were read; no builds, runtime scans, frozen-harness edits, cache changes or product changes were performed.
