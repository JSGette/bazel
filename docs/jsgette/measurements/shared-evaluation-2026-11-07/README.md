# Shared evaluation experiment — 7 November 2026

This directory records implementation and evidence for the [gated plan](../../plans/shared-evaluation-gates-2026-11-07.md).

## Current state

- M0 remains in progress. The v6 matrix completed eight reference/anchor rows before a root-disk failure. The fresh v7 matrix stopped during preparation at the host-memory reserve. Both attempts are preserved; the full v7b retry adds a bounded wait for verified host headroom after task-cache reclamation.
- M1–M6: not started; no architecture or performance gate has passed yet.
- Branch: `jsgette/shared-evaluation-gates-20261107`.
- Starting revision: `c54d33d5b40c5711f3841f50ea1c2f950c959450`.
- [Decision and execution log](decision-log.md).
- [Current v7b completed-row overview](evidence/m0-v7b-01/overview-partial-09.md), [separate v6 observations](evidence/m0-v6-01/overview-partial-02.md), [v7 abort](evidence/m0-v7-01/overview-aborted-01.md), and [v7b execution manifest](harness/matrix-v7b.json).
- [Measured CPU attribution limits](m0-cost-attribution-notes.md), [Bonanza source contracts](bonanza-contract-notes.md), and [M1 native comparator preflight](m1-native-preflight.md).
- [M0 closure assessment draft](m0-gate-assessment-draft.md) and [proposed acceptance accounting](acceptance-metrics-draft.md); neither declares a gate outcome.
- Preparatory implementation maps: [native/private comparison](m1-native-implementation-checklist.md), [typed query traces](m1-trace-implementation-checklist.md), and [coherent input publication](m2-capture-feasibility.md).

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
