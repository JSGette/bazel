# Shared evaluation experiment — 7 November 2026

This directory records implementation and evidence for the [gated plan](../../plans/shared-evaluation-gates-2026-11-07.md).

## Current state

- M0: environment qualification and baseline harness in progress.
- M1–M6: not started; no architecture or performance gate has passed yet.
- Branch: `jsgette/shared-evaluation-gates-20261107`.
- Starting revision: `c54d33d5b40c5711f3841f50ea1c2f950c959450`.
- [Decision and execution log](decision-log.md).

Earlier measurements are background, not new passes. In particular, diagnostic Skyframe names and path-normalized graph dumps are not sound cache identities. This experiment must measure validation and representation overhead before claiming cross-snapshot reuse.

Raw evidence, commands, binary/source hashes, failed attempts, and benchmark limitations will be added here as each stage runs. No credentials or raw environment values are to be recorded.
