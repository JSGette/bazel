# Shared evaluation experiment — 7 November 2026

This directory records implementation and evidence for the [gated plan](../../plans/shared-evaluation-gates-2026-11-07.md).

## Current state

**Stopped at the planned pivot: [M1 is inconclusive](m1-gate-assessment.md).** The final bounded residual investigation leaves CPU and memory savings unresolved. M2–M6 were not started. The frozen economic goals are unchanged; the evidence and task runtime image are preserved.

- **M0 complete:** all 29 matrix rows and two diagnostic sessions passed. The [M0 assessment](m0-gate-assessment.md) justified bounded M1 on repeated retained state; [CPU attribution remains partial](m0-cost-attribution-notes.md).
- **Native hosting retained as a useful component:** cap-ten qualification and all 21 acceptance checks passed. The [N10 diagnostic](native-n10-assessment.md) reduced build-window CPU about 66.3%, sampled daemon PSS 24.7%, and whole recorded lifecycle CPU 20.1%, with private graphs and heavy reclaim/I/O pressure. This is not semantic sharing or an isolated-SkyCache M4 pass.
- **Typed trace controls complete:** product/focused Java tests and the real interoperability smoke passed. All twenty N1 sessions and ten wire replays completed. The [N1 report](m1-trace-n1-diagnostics.md) and [function coverage](m1-trace-family-coverage.md) preserve material observer overhead and unsupported observations.
- **Final residual investigation:** [ten restarted cache/BEP evidence sets](m1-residual-sidecars.json) verified. The [source audit](m1-cache-cost-audit.md) records cost boundaries. Two pre-launch decoder failures remain preserved; no deep-stack result is claimed. [Final packet](evidence/m1-residual-investigation/).
- **Proposed pivot, not started:** completed immutable result bundles with explicit dependency closure and separate output views; see the [M1 assessment](m1-gate-assessment.md) and [Bonanza source contracts](bonanza-contract-notes.md).
- Branch: jsgette/shared-evaluation-gates-20261107; starting revision: c54d33d5b40c5711f3841f50ea1c2f950c959450.
- [Decision and execution log](decision-log.md), [frozen acceptance metrics](acceptance-metrics.md), [completed M0 matrix](evidence/m0-v7b-01/overview-partial-11.md), [M0 diagnostic analysis](m0-diagnostic-analysis.md).
- [First-attachment costs](m0-preparation-costs.md), [cross-worktree publication admission](m0-cross-worktree-reuse-notes.md), [captured witness conflicts](m0-witness-capture-findings.md), and [coherent-input feasibility](m2-capture-feasibility.md).

Earlier measurements are background, not new passes. In particular, diagnostic Skyframe names and path-normalized graph dumps are not sound cache identities. This experiment must measure validation and representation overhead before claiming cross-snapshot reuse.

Raw evidence, commands, binary/source hashes, failed attempts, and benchmark limitations will be added here as each stage runs. No credentials or raw environment values are to be recorded.

## Preserved attempts

| Attempt | Recorded outcome | Evidence |
| --- | --- | --- |
| Vanilla restarted daemon, one client, harness v2 | Completed qualification; single trial, not a milestone pass | [Archive](evidence/qualification-vanilla-restart-v2.tar.gz), [hash manifest](evidence/qualification-vanilla-restart-v2.tar.gz.json) |
| SkyCache restarted daemon, one client, harness v2 | Completed qualification with actual analysis reuse; single trial, not a milestone pass | [Archive](evidence/qualification-skycache-restart-v2.tar.gz), [hash manifest](evidence/qualification-skycache-restart-v2.tar.gz.json) |
| Vanilla restarted-daemon protocol, ten clients, harness v3 | Deliberately aborted during preparation; no measured phase or completed `report.json` | [Abort metadata](evidence/aborted-vanilla-n10-restart-v3.json), [archive](evidence/aborted-vanilla-n10-restart-v3.tar.gz), [hash manifest](evidence/aborted-vanilla-n10-restart-v3.tar.gz.json) |

The aborted archive preserves all 40 preparation files, provenance, the explicit abort record, and bounded JVM diagnostics. It contains no fabricated successful build report. Every archived member was checked against its recorded size and SHA-256. Original runtime files remain preserved.

## Infrastructure repair boundary

The ten-client scope was stopped with SIGTERM while Windows available physical and virtual memory were low. Its observed cgroup memory-event counters were all zero; no cgroup OOM event was observed. The subsequent Linux memory inspection showed substantial cache residency after Java processes were stopped. These observations do not establish a unique cause of the pressure.

The coordinator enabled direct I/O on the task-owned loop device (`DIO=1`), then unmounted/remounted only the task ext4 filesystem to release its retained caches and re-enabled direct I/O. A rejected attempt to pass a `direct-io` mount option is recorded in [D009](decision-log.md#d009--preserve-the-aborted-ten-client-attempt-and-repair-storage-2026-11-07). No global cache purge or user-global configuration change was performed. Next matched arms must use the same repaired storage setup and fresh run IDs. Pre-repair qualification and post-repair timings must not be pooled automatically.
