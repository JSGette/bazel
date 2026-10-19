# Rust-backed Skyframe integration and Linux comparison

Measured 19 October 2026 in WSL Ubuntu. Implementation: [d3176f5315](https://github.com/JSGette/bazel/commit/d3176f5315), on [jsgette/skystore-phase0](https://github.com/JSGette/bazel/tree/jsgette/skystore-phase0).

## Result

The optional Rust-backed **tiered_idle** mode is integrated with the real Bazel Skyframe graph. It passes the covered correctness and recovery cases, but it is **not ready to enable by default**.

On repeated Bazel self-analysis, warm wall time increased **7.9%** versus vanilla and **9.5%** versus the modified binary with tiering off. Median live Java heap fell by **1.02 MiB (0.96%)**. Total process-memory measurements do **not establish a RAM reduction**; their variation is much larger than the live-heap difference.

This is the first engine integration, not the complete architecture in docs/jsgette. Java still owns evaluation and the graph. Compact graph storage, in-command paging, cold edges, wider audited codecs, and cross-restart persistence remain unimplemented.

## What runs

The new command flags are experimental_skyframe_engine=tiered_idle and experimental_skystore_binary=/absolute/path/to/skystore. The default is inmemory.

After all command-completion hooks, the engine can detach exact GlobsValue and GlobValueWithImmutableSet values larger than 1 KiB. Other classes, metadata/error wrappers, dirty entries, nested-set globs, and edgeless entries remain resident. This workload spills **39 values / 420,325 serialized bytes**. The 32 MiB allowance is a serialized-data cap, not a heap budget.

Rust stores payloads in a private ephemeral file and holds offsets, lengths, and CRC32C checksums in RAM. Java retains SHA-256 digests for restore validation. Values restore before command modules run; node versions and edges are preserved, with no storage I/O under node-entry monitors. A missing/corrupt value or dead sidecar resets the evaluator before evaluation resumes. Normal shutdown and parent-pipe EOF stop the sidecar. No durable reuse across restarts is attempted.

## Repeated analysis measurements

Workload: the pristine fork at 4cfc05f77ba91900a2e3c6ab135861498cb2a0bf, target //src:bazel-dev, build --nobuild. Three fresh server/output-base runs per variant, two warmups and five measured warm commands per server (15 warm samples per variant). Order was rotated across rounds: vanilla/default/tiered, tiered/vanilla/default, default/tiered/vanilla. Table values are medians of per-server measurements.

| Variant | Cold wall, s | Warm wall, ms | Warm peak RSS, MiB | Live Java heap, MiB | Post-GC process PSS, MiB |
|---|---:|---:|---:|---:|---:|
| Vanilla | 5.661 | 184.01 | 1318.4 | 106.737 | 1060.9 |
| Modified, tier off | 6.335 | 181.31 | 1322.1 | 106.761 | 1088.6 |
| Rust tiered_idle | 5.739 | 198.56 | 1351.1 | 105.714 | 1081.9 |

Warm medians by round were vanilla 184.01, 185.21, 176.93 ms; tier off 182.01, 179.80, 181.31 ms; tier on 197.53, 198.56, 200.72 ms.

Median measured maintenance costs were **7.92 ms restore + 10.41 ms spill** per warm analysis command. These costs are included in client wall time. The repeated warm-analysis slowdown exceeds the design's 2% regression gate.

Post-GC process PSS ranges overlap: vanilla 1035.5–1088.5 MiB; tiered 1071.9–1087.1 MiB. Heap byte savings are not equivalent to released operating-system RAM.

## Full Bazel builds using Cache & Carry

All variants built the same frozen //src:bazel-dev workload with action execution enabled. Each fresh build used **4,703 remote cache hits**. The first modified-default run took 456.874 s while populating new action configurations; it is excluded. The default control was rerun after priming with a new server/output base.

There is one cache-warm fresh build and five warm commands per variant here. These full-build measurements have less statistical strength than the repeated analysis experiment.

| Variant | Cold wall, s | Warm wall, ms | Warm peak RSS, MiB | Live Java heap, MiB | Post-GC process PSS, MiB |
|---|---:|---:|---:|---:|---:|
| Vanilla | 19.648 | 322.14 | 1572.4 | 134.325 | 1510.6 |
| Modified, tier off | 19.515 | 346.77 | 1563.9 | 134.437 | 1460.3 |
| Rust tiered_idle | 19.690 | 365.19 | 1518.2 | 133.766 | 1514.7 |

Observed tiered warm full-build wall time was 13.4% higher than vanilla and 5.3% higher than the modified-default control. Cold builds were approximately 19.5–19.7 s. The small cold-time differences are not evidence of a speedup. Remote downloads and local filesystem work dominate this cache-warm full-build scenario; it does not compare uncached compilation speed.

All three self-built Bazel executables were byte-identical, SHA-256:

    1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588

## Correctness and failure recovery

- Existing Skyframe suite: 1,464 testcase records, 1,458 passed and six existing skips; no failures or errors.
- New tiering Java tests: seven passed. Java IPC client tests: eight passed.
- Rust formatting/Clippy checks and all 12 Rust tests passed.
- Java/Rust process interoperability checks passed.
- Real-Bazel command matrix: 20 checks each for vanilla and modified-default; 24 for tiered, including intentional failing builds and tier-specific fault injection.
- All **15 common successful build/edit cases** produced identical output hashes across the three variants.
- Covered source edits, glob additions/deletions, BUILD/.bzl edits, flag changes, keep-going failures, cycles, query, mode changes, state dropping, clean and restart.
- Killing the sidecar and corrupting its spill file triggered safe graph resets and correct outputs. Parent-pipe closure stopped the Rust child successfully.

These tests establish the covered behavior, not full production conformance for future in-command paging, concurrency, or graph representations.

## Profiling and measurement limits

The separate profiling report compares JFR CPU/allocation samples and heap evidence. All raw recordings and logs are in the profile bundle. Timing runs used no JFR and no forced-GC memory profiling. Diagnostic passes used JFR, Bazel trace/memory profiles, a post-GC class histogram, Skyframe counts, and per-function memory attribution.

RAM samples sum the launcher, JVM, and descendant processes every 50 ms, discovering children by PPID so the Rust child is counted even when spawned by a non-leader JVM thread. RSS can double-count shared pages; PSS apportions them. Sampled peaks may miss brief spikes. Post-GC PSS is a diagnostic snapshot after the recording, separate from timed peak RSS.

These are **Linux guest process metrics**, not total Windows/WSL VM memory. Kernel filesystem page cache, including spill-file cache, is outside process RSS/PSS. No Rust native CPU sampling was collected. JFR profiling includes forced GCs and startup overhead and must not be used as an ordinary-command GC latency comparison. The edit matrix has one timing observation per case; its main purpose is output equivalence and recovery, not statistical performance claims.

## Environment and reproduction

- CPU: AMD Ryzen 9 9950X3D 16-Core Processor.
- Linux: 6.6.87.2-microsoft-standard-WSL2, x86-64 WSL Ubuntu; 15.44 GiB guest RAM.
- Source-built Bazel server: OpenJDK 27+35; target compilation and jcmd tools use the repository-provided JDK 25.
- JVM heap cap: 4 GiB; jobs: four; local resource budget: 6,144 MiB.
- Fork clone: /home/jgett/REPOS/bazel. Frozen vanilla worktree: /home/jgett/REPOS/bazel-vanilla.
- Remote action cache: USB Cache & Carry at grpc://10.55.0.1:8080. BES was disabled during measurements with --bes_backend=.
- Windows shares internet to the USB adapter through ICS; WSL uses mirrored networking. The appliance health check returned HEALTHY after the tests.

Use tools/skystore/test.sh, engine_integration_test.py, and profile_bazel.py from the branch. Profile arguments, commands, binary hashes, sample CSVs and kernel information are preserved in the bundle. profile_bazel.py defaults to analysis only; add --execute for full builds. Use a fresh output directory for every run.

Baseline binary SHA-256: 1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588
Integrated binary SHA-256: 42653a7e74eb68dde9e5d3afa516c5c8157c074c2f1e2fd955986b76ab34be63
Rust binary SHA-256: 1598a2ce9fc2daac81007776fa76094a49a73f8bfbbfe0684104ea795a2f1da9

## Decision

Keep this mode opt-in. The integration supplies a tested storage/lifecycle boundary and concrete profiling evidence, but does not meet the warm-command regression gate or show a total RAM benefit on this workload. Wider high-impact codecs, a working-set policy that avoids rehydrating every spilled value on every command, and the planned compact-graph/conformance work are needed before promotion. No production performance improvement is claimed.

Related files: [CPU/heap profiles](skyframe-rust-idle-profiles-2026-10-19.md), [measurement CSV](skyframe-rust-idle-linux-2026-10-19.csv), [incremental cases](skyframe-rust-idle-incremental-2026-10-19.csv).
