# Datadog SSD benchmark preparation and E: storage audit — 25 October 2026

The repeated Datadog performance comparison is **not complete**. Both vanilla preparation attempts stopped at safety guards before a successful build. No speed or RAM improvement is established by these attempts. Cache & Carry TCP connectivity was verified; action-cache hits have not yet been established in this run.

## Storage findings

E: is the WD Blue SN570 2 TB NVMe SSD; C: is the other NVMe SSD. D: is a Toshiba SATA hard disk. The new benchmark binaries, output base, graph-cache destination, sources, JDK, and repository caches use the E-backed WSL filesystem. The older 64 GiB C: image remains intact as historical evidence; it is not the new benchmark output location.

| E: consumer | GiB |
|---|---:|
| `E:\SteamLibrary` | 946.19 |
| `E:\WSL` | 718.80 |
| `E:\QWEN` | 148.51 |
| `E:\GOGLibrary` | 28.51 |
| `E:\Projects` | 5.21 |
| `E:\Epic Games` | 1.15 |

Inside WSL, `/home/jgett/REPOS/.work` accounts for **633.93 GiB**, including **575.92 GiB** of historical SkyCache experiments. The Bazel home caches total another **44.66 GiB**, including the new 6.07 GiB cache described below. Source repositories outside `.work` total about 7.02 GiB; Docker uses about 0.23 GiB.

At least **242.19 GiB** of the old experiment total is in directories explicitly named `cache` or ending in `-cache`. These experiments retained separate analysis-object stores, output bases and worktrees. This retention, repeated across many probes, is the main development storage consumer. Parent totals and child caches overlap and must not be added together.

| Largest historical experiment | Total GiB |
|---|---:|
| `datadog-concurrent-runfiles-review` | 41.72 |
| `datadog-concurrent-environment-review` | 40.42 |
| `datadog-concurrent-packed-review2` | 32.39 |
| `datadog-independent-aspect-review` | 30.85 |
| `datadog-concurrent-live-identity-review` | 27.98 |
| `datadog-crossrevision-retained-review` | 25.39 |
| `datadog-witness-lists-before` | 23.65 |
| `datadog-witness-lists-after` | 23.63 |
| `shared-put-writer-repeats` | 18.64 |
| `datadog-crossrevision-review` | 16.96 |
| `datadog-concurrent-packed-review` | 16.81 |
| `datadog-crossrevision-admission-smoke` | 12.42 |

## Growth during this attempt

The WSL VHD file grew from 758,619,570,176 to 771,806,461,952 bytes, or **12.28 GiB**. Host E: free space fell from 26.53 GiB to roughly 14.25 GiB. Another Bazel task was also present, so the full host delta is not assigned exclusively to this benchmark.

The initial harness mistakenly used `--output_user_root=/home/jgett/.cache/bazel` instead of the established `/home/jgett/.cache/bazel/_bazel_jgett`. This created a separate repository cache occupying **6.07 GiB**, plus a 0.22 GiB installation. A same-relative-path SHA-256 audit verified **2.45 GiB / 595 files** as exact duplicates of the existing cache. Other new entries are not proven duplicates by that audit. The corrected reproduction harness reuses the established cache root.

The new benchmark directory occupies **2.35 GiB**, including about 2.01 GiB of unpacked dependencies and 0.34 GiB of copied Bazel binaries. These are setup/dependency bytes, not compiled Datadog artifacts from a successful build.

`fsutil sparse queryflag` reports that the VHD is not sparse. A root `fstrim -v /` reported 306.3 GiB trimmed but did not increase Windows free space. Free capacity shown inside Linux therefore does not mean the Windows SSD has that capacity available. Deleting old caches inside WSL may need an offline VHD compaction step to return their blocks to E:. No compaction or deletion was performed.

## Preserved attempts

| Preparation | Exit | Wall seconds | Cause |
|---|---:|---:|---|
| `prepare-vanilla` | 8 | 113.526 | Cannot check WSL host storage after 3 attempts: <3>WSL (21908 - ) ERROR: UtilAcceptVsock:271: accept4 failed 110 |
| `prepare-vanilla-retry1` | 8 | 121.986 | WSL backing volume at E:\WSL\Ubuntu has 14.3 GiB free; probe requires 15.0 GiB |

The first attempt lost WSL-to-Windows interop while checking free space. It was cancelled, recorded, and replaced by a Windows-side snapshot watcher with freshness checks. The retry then crossed the explicit 15 GiB E: reserve and was cancelled. Neither attempt is included in any performance comparison. The baseline process-tree sampled peaks are setup-only and cannot answer the requested comparison.

## Planned controlled comparison

Datadog commit `0cde96beb70963d0838119c308b9d0422ee66be0`, target `//cmd/cluster-agent:cluster-agent`; unchanged fixture and resolved lockfile. Three rotated rounds of vanilla, new binary with SkyCache disabled, and new binary with SkyCache enabled. Each cycle has a fresh-server build, two warm builds, a 20-second idle period, and recovery. The enabled variant evicts at 15 seconds. Preparation is excluded.

Vanilla is the recorded pre-SkyCache source build at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, SHA-256 `1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588`. This fork baseline includes documentation and codec-visibility changes; it is not the official Bazel 9.3.0 release. Candidate source is `303a149a195bbd6ad770584bd34138ea749c3e9a`, binary SHA-256 `28026d467ef10667c0fcfb1741495fbefe4fee9dfed43d3d2d9733726b5cb159`. Both use the same JDK 25, 6 GiB heap, 16 jobs and 6,144 MiB action-memory budget.

All builds explicitly use `grpc://10.55.0.1:8080` (Cache & Carry), an empty instance name, remote reads/uploads, full output downloads and no local disk action cache. BES is disabled to isolate cache/build performance. The monitor samples launcher/server/descendant RSS and PSS every 100 ms; client time uses blocking waitpid. Guards monitor Windows and guest storage. RSS can double-count shared pages; PSS excludes filesystem page cache and Windows/WSL VM overhead.

Before resuming: restore sufficient E: capacity, ensure other build work is idle, start the Windows storage watcher, run the corrected harness with `resume_prepare`, inspect remote-hit/output checks, then run `measure`. Preserve existing attempts. No further build was started after the storage guard fired.

## Reclamation options, not executed

Historical experiment cache/output directories are the primary development cleanup candidates. Preserve source worktrees, binary/source provenance, protocols, logs and measurement artifacts; verify exact cache/output paths before removal or archival. Offline VHD compaction requires stopping WSL and appropriate Windows privileges, so coordinate with active tasks.

Moving the four Qwen model files from `E:\QWEN` to D: would free about 148.51 GiB directly on E: without touching WSL, but model paths and loading performance would change. Steam library relocation is another option: Battlefield 6 is about 161.90 GiB and Baldur’s Gate 3 about 144.80 GiB. These are alternatives for user selection, not authorizations to move unrelated data.

Raw protocol, corrected harness, samples, disk inventories and SHA-256 manifest: [skycache-v2-datadog-ssd-2026-10-25/](measurements/skycache-v2-datadog-ssd-2026-10-25/). Raw BEP files and profiles remain local because BEP can contain environment values; their hashes are recorded. No credentials, compiled binaries or cache payloads are committed.

## Fresh WSL follow-up

[Completed Datadog comparison on the E: SSD](skycache-v2-fresh-wsl-2026-10-25.md) records the authorized Ubuntu/Qwen purge, verified backups, fresh setup and 42 measured builds using Cache & Carry. The real Datadog target shows slower warm builds and higher foreground memory with SkyCache; the earlier synthetic gains do not generalize to this workload.
