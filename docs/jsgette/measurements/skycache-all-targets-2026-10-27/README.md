# Full-repository SkyCache measurements

27 October 2026; unchanged production source `c5f012f3350a9ebc7d1dd524ca65ecabb28e1d84`.

**The larger graph exposes cache limits and a restore failure that prevent a usable
four-way speed comparison.** Successful uncached baselines are below; failed setup
attempts are excluded from timing summaries.

| Mode | n | Median command s | Range s | Mean client CPU-s |
| --- | ---: | ---: | ---: | ---: |
| Pinned upstream vanilla | 3 | 37.64 | 36.27–37.85 | 772.36 |
| Current binary, SkyCache disabled | 3 | 38.07 | 36.70–48.40 | 787.93 |
| Local warm | — | No valid result: restore failed | — | — |
| Local cold | — | Not reached after warm restore failed | — | — |
| Remote warm | — | No seed: current publication/transfer limits exceeded | — | — |
| Remote cold | — | No seed: current publication/transfer limits exceeded | — | — |

The workload selects **5,658 top-level targets**, excluding only the 36 packaging targets
approved by the user. Successful builds configure **598,064 targets including aspects**,
about 2.52 times the previous 237,112-target graph, and produce 10,738 named output digests.
Bazel reports 5,536 completed and 122 skipped top-level targets; the skipped labels match
across all six primary runs and are preserved in [target outcomes](target-outcomes.json).
Every successful baseline and seed build matches those output digests and target results.
Manifest equality compares BEP digest/length metadata; minimal downloads do not fetch every
output blob. Named files without digests are recorded separately.
Three samples per baseline on one workstation support the reported ranges, not a statistical
significance claim. No local or remote speedup can be inferred from the failed cache attempts.

## Memory configuration

At the user's request, WSL's default approximately 16 GiB limit was raised to **20 GiB**.
Ubuntu reports 20,476,032 KiB total usable memory, about 19.5 GiB. Windows has
33,990,049,792 bytes of usable physical RAM. The previous `.wslconfig` was backed up and
mirrored networking preserved. The change was applied before any primary measurement.

Java's separate heap limit was initially 10 GiB. A cache restore exhausted that heap, so
the final baseline cohort and the last local restore attempt use **14 GiB**, equally.
WSL remains capped at 20 GiB. Swap deltas and Windows memory samples are retained as evidence.

## What vanilla means here

Every timed baseline starts from a fresh JVM and a verified empty Skyframe graph: zero
nodes and edges. Vanilla recomputes analysis; it does not restore an analysis cache saved
at shutdown. Ordinary action results are already cached. All cases use the same selected
targets, strict visibility checking, tool overrides, 16 jobs, 6,144 MiB local scheduling
memory, minimal output downloads and the ordinary action-cache controls below.

The external action-cache server rejected uploads during preparation with
`RESOURCE_EXHAUSTED: Not enough space to create a replica`. A private disk action cache
was then primed, external cache writes and BES uploads disabled, and output equality
rechecked. Successful warmups had 126,937 disk-cache hits and 12,569 internal actions,
without compiler execution. The external cache remains readable if needed.
This is a new matched cohort, not a before/after comparison with older remote-action-cache-only
measurements. It measures graph reconstruction and normal cache-hit build work, not a
from-source compilation.

Startup, output cleaning, seed copying and page preparation are excluded from command time.
Command time includes finalization. One warmup per baseline is excluded, followed by three
pairs in forward, reverse, forward order. Wall time uses `CLOCK_MONOTONIC_RAW`; a separate
calibration found WSL clock slewing. JVM/BEP phases retain their native clocks and cannot
be added to reconstruct the raw wall timer.

| Mode | Median time to analysis-complete log s | Median finalization tail s | Median excluded startup + clean s |
| --- | ---: | ---: | ---: |
| Pinned upstream vanilla | 18.13 | 0.06 | 9.35 |
| Current binary, SkyCache disabled | 19.61 | 0.05 | 7.37 |

The analysis-complete marker can overlap action execution in Bazel's merged mode.
CPU is the client JVM's process CPU and includes finalization. It excludes ordinary cache-server
CPU, Windows and kernel writeback. Sum of recorded swap-page deltas by primary arm:
`{"disabled": {"pswpin": 70, "pswpout": 1475}, "vanilla": {"pswpin": 7, "pswpout": 1080}}`. These are measurements on the user's workstation,
not an otherwise idle dedicated host.

## Why cached results are unavailable

**Remote: the full publication exceeds current limits.** The full-graph remote seed build
succeeded and matched all outputs, producing a 7,679,181,224-byte local publication.
Remote publication then failed with `SkyCache transfer exceeds its byte budget`.
The largest accepted transfer option is 4 GiB + 1 MiB, and the shared protocol separately
limits the complete logical publication closure to 4 GiB. No remote seed was committed.
Increasing WSL RAM or downloading the whole graph does not remove those limits.
The metadata worker was loopback; this failed preparation is not a WAN benchmark.

**Local default: the 256 MiB metadata budget cannot admit the graph.** The local seed needs
313,868,680 bytes (299.33 MiB): 129,035,654 bytes of witness accounting, 184,823,424 bytes of
indexes, and 9,602 manifest bytes. A read-only probe through the unchanged production classes
rejects 256 MiB and accepts 512 MiB. A separate check finds zero mismatches among all
733,820 saved input witnesses.

The default-budget warmup recorded 72 read-limit skips, no imported analysis or execution
entries, and about 723 seconds before completing a recomputation. That run included a thread
snapshot and read-only helper probes, so its duration is diagnostic, not a primary sample.
The snapshot found cache threads waiting on `LocalCacheSession.refresh` while the owner
checked directory listings. Source inspection shows input-witness validation before the
aggregate segmented-index budget check, followed by retries of budget failures. This is a
concrete explanation for expensive repeated admission work when the publication cannot fit.

**Local with 512 MiB: restore needs more heap and then fails on generated inputs.** The
existing `--experimental_local_skycache_max_metadata_mb=512` option admits the publication.
With a 10 GiB heap, restoration failed with exit 33 and explicit BEP `OUT_OF_MEMORY`.
At 14 GiB it reached 1,494 reported analysis-cache hits, but these targets failed because
their generated template inputs were absent after clean:

- `//pkg/security/seclwin/model:strip_go_build_windows_from_accessors_windows.go`
- `//pkg/security/seclwin/model:strip_go_build_windows_from_field_handlers_windows.go`

The missing files are `accessors_windows.go` and `field_handlers_windows.go` under
`bazel-out/k8-fastbuild/bin/pkg/security/secl/model/accessors_windows/`.
Vanilla and the candidate with SkyCache disabled build these same targets successfully.
The failed restore's cleanup was stopped with SIGINT followed by SIGTERM; exit 37 records
that termination, not the original cause. The original build errors, signals and diagnostic
logs are preserved. No successful local warm timing exists, and the cold local run was not
attempted after this failure. Its behavior must not be inferred from the warm failure.

Local warmup metadata pages were explicitly read and checked with `mincore`. Cold-mode
preparation is implemented with fsync, `POSIX_FADV_DONTNEED`, and a residency check, but no
successful full-graph cold measurement was reached. Host/device caches and source-file page
residency remain uncontrolled. No validation check was bypassed to make a run pass.

## Next work, in priority order

1. Reproduce and fix generated-input materialization after execution-state restoration.
   Trace the restored `ActionExecutionValue` through input metadata and the template action's
   path resolver; compare against the normal action-cache hit path. The missing-input error
   is observed; its exact implementation cause still needs a focused reproducer.
2. Preflight the complete immutable metadata charge before expensive input admission, and
   avoid retrying an unchanged publication that still cannot fit the fixed budget. Preserve
   fresh input validation for admissible candidates. Expose the required budget to operators.
3. Investigate the restore memory expansion and state eviction seen at 10 GiB, using a
   successful bounded workload or a focused reproducer before drawing performance conclusions.
4. Raise or redesign the remote logical-size and transfer bounds together, including worker
   validation and compatibility tests. A larger client flag alone is rejected today.
5. Repeat local and remote warm/cold measurements on this same workload after those gates pass.

The current evidence does not justify a full-graph remote cache for latency. It establishes
the successful uncached cost and the blockers that must be removed before a fair comparison.

## Reproducibility and evidence

The pinned upstream commit is `a5c50fcb65841b505ec72840d83381a704e70afa`; the candidate is the
unchanged `c5f012f335` advisory-retention binary. No startup agent or publication probe is used.
Every completed group checks source state, fixture state and binary hashes.

Isolated fixture overrides remove an unused obsolete `grep_includes` reference from
`rules_rust_prost` and explicitly export the Go SDK's `ROOT` and `bin/go` under its existing
public package policy. Strict visibility checking is enabled in all final cases. Original
external repositories and the pre-existing MODULE lock modification are preserved. Required
host tools and versions are recorded in `tools/`. These are builds, not test execution or
E2E provisioning. No product source or production limits changed during measurement.

[Comparison](comparison.json), [remote limit](remote-availability.json),
[default local budget](default-local-availability.json), [metadata probe](local-admission-budget.json),
[restore OOM](local-restore-oom.json), [generated-input failure](local-restore-input-failure.json),
[approved workload](public-workload.json), [preparation history](preparation-attempts.json), and
[recording hashes](provenance.json) preserve the evidence. Raw BEP, profiles, output manifests
and cache blobs remain at `/home/jgett/REPOS/.work/skycache-all-targets-2026-10-27`; only compact evidence is committed.
