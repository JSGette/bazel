# Shared evaluation experiment — 7 November 2026

This directory records implementation and evidence for the [gated plan](../../plans/shared-evaluation-gates-2026-11-07.md).

## Current state

- **M0 complete — continue to bounded M1 on repeated retained state.** The v7b matrix completed all 29 checked/archived rows and both N1 diagnostic sessions passed. CPU attribution remains partial under D016; no later economic or semantic-sharing gate has passed.
- **M1 in progress; preliminary N10 hosting diagnostic complete.** The [N10 assessment](native-n10-assessment.md) and [verified diagnostics](native-n10-diagnostics.md) cover the independent reference and one private/native pair after cap-ten source qualification and all 21 acceptance checks. Build-window non-harness CPU fell about 66.3%, sampled daemon PSS 24.7%, and whole recorded lifecycle CPU 20.1%. Both arms were under heavy memory/I/O pressure. These are private-graph hosting observations against this fork's private mode, not the isolated-SkyCache economic comparator or an M1/M4 pass. D028 continues to typed-trace qualification without more native warm/variant runs or heap tuning. The separate reconstructed SkyCache prototype is being prepared for source/Java-test qualification; no actual Java qualification or trace/replay smoke has passed. M2–M6 remain not started.
- The v6 root-disk failure and v7 host-memory abort remain preserved infrastructure attempts, excluded from v7b performance comparisons. No more M0 runs or N2 transfer probe are required for the current decision.
- Branch: `jsgette/shared-evaluation-gates-20261107`.
- Starting revision: `c54d33d5b40c5711f3841f50ea1c2f950c959450`.
- [Decision and execution log](decision-log.md).
- [Completed v7b matrix](evidence/m0-v7b-01/overview-partial-11.md), [separate v6 observations](evidence/m0-v6-01/overview-partial-02.md), [v7 abort](evidence/m0-v7-01/overview-aborted-01.md), and [v7b execution manifest](harness/matrix-v7b.json).
- [Measured CPU attribution limits](m0-cost-attribution-notes.md), [Bonanza source contracts](bonanza-contract-notes.md), and [M1 native comparator preflight](m1-native-preflight.md).
- [Final M0 assessment](m0-gate-assessment.md), [diagnostic analysis](m0-diagnostic-analysis.md), and [acceptance metrics](acceptance-metrics.md). Earlier [closure](m0-gate-assessment-draft.md) and [accounting](acceptance-metrics-draft.md) drafts remain as history.
- [First-attachment preparation costs](m0-preparation-costs.md), [cross-worktree publication admission](m0-cross-worktree-reuse-notes.md), and [captured witness conflicts](m0-witness-capture-findings.md) distinguish newcomer cost from prepared-worktree restart reuse and identify external-binding portability requirements.
- Preparatory implementation maps: [native/private comparison](m1-native-implementation-checklist.md), [typed query traces](m1-trace-implementation-checklist.md), and [coherent input publication](m2-capture-feasibility.md).
- Hosting evidence: [N2 diagnostics](native-n2-diagnostics.md), [cap-ten qualification](evidence/auxiliary/native-cap10-build-01/report.json), [21-check acceptance](evidence/auxiliary/native-acceptance-cap10-01/results.json), and [N10 raw diagnostic data](native-n10-diagnostics.json).

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
