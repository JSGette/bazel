# Datadog builds after `bazel clean` — 25 October 2026

This experiment answers the explicit request to compare SkyCache with no SkyCache after an actual `bazel clean`. It uses the same experimental binary with the feature disabled/enabled, the same Datadog workspace, output base, machine, resource limits and Cache & Carry endpoint. It does not compare the earlier warm no-op measurements.

Across three runs per condition, enabling SkyCache changed median post-clean build time by +12.2%, analysis phase elapsed time by +327.3%, and peak process PSS by +88.9%. The table gives medians and full observed ranges; three samples do not establish statistical significance, particularly for remote-transfer time.

The first SkyCache run rejected an older snapshot because Python toolchain symlink referents were missing. It published a replacement; the subsequent runs restored 4,501 configured targets each. Both cases are retained below. The prior **4.56-second** result was a warm build and does not measure this clean-build scenario.

| Measurement | SkyCache off | SkyCache on |
|---|---:|---:|
| Build after clean | 144.70 (144.08–154.35) s | 162.34 (146.91–166.46) s |
| Analysis phase (BEP) | 3.26 (3.22–3.67) s | 13.93 (5.32–21.51) s |
| Execution phase (BEP) | 139.47 (139.31–149.34) s | 139.60 (139.25–140.33) s |
| Clean, including JVM startup | 1.73 (1.73–3.57) s | 1.73 (1.72–3.54) s |
| Clean + build | 146.43 (145.81–157.92) s | 164.06 (148.64–169.99) s |
| Peak process PSS | 2513.21 (2096.44–2517.79) MiB | 4746.88 (2953.66–5202.68) MiB |
| Peak process RSS | 2519.72 (2102.91–2524.33) MiB | 4753.45 (2960.14–5209.19) MiB |

## What was cleaned and verified

Before **each** timed build, the harness ran synchronous `bazel clean --noasync --noexpunge`. After clean, every run verified:

- `bazel dump --skyframe=summary` reported **zero nodes and zero edges**.
- The `execroot` had no children, including no previous binary or generated outputs.
- The local `action_cache` directory was absent.
- All persisted SkyCache publication hashes were unchanged. Its store is outside the output base, so standard clean preserves it.

The clean and graph-dump logs are included in the public evidence. The code paths examined were `CleanCommand.actuallyClean` (calls `clearCaches`, `resetEvaluator`, and removes execroot contents) and `BlazeWorkspace.clearCaches` (clears both in-memory and on-disk action caches).

Each run shut down the previous server before clean, which started a fresh JVM. The subsequent build used that **same PID**. This equalizes JVM history between conditions and ensures no retained in-process graph or SkyCache helper state crosses runs. It is a post-clean comparison with fresh JVM history, not a measurement of clean inside an already JIT-warmed developer JVM. Clean/startup time is separate from build time; graph verification and shutdown are not included in either timer.

## Runs and cache use

The order was off/on, on/off, off/on. All six builds succeeded and produced the same binary. Cache & Carry was enabled throughout at `grpc://10.55.0.1:8080`; no disk action cache was configured. Unlike the earlier forced-cache experiment, these commands did **not** use `--nouse_action_cache`: standard clean actually removed local action state and outputs before each run.

| Run | Clean s | Build s | Analysis s | Peak PSS MiB | SkyCache configured-target hits | Remote action hits | Local actions |
|---|---:|---:|---:|---:|---:|---:|---:|
| r1-off | 3.57 | 154.35 | 3.259 | 2513 | 0 | 5704 | 0 |
| r1-on | 1.72 | 162.34 | 13.925 | 2954 | 0 | 5704 | 0 |
| r2-on | 3.54 | 166.46 | 21.511 | 4747 | 4501 | 5704 | 0 |
| r2-off | 1.73 | 144.70 | 3.666 | 2096 | 0 | 5704 | 0 |
| r3-off | 1.73 | 144.08 | 3.216 | 2518 | 0 | 5704 | 0 |
| r3-on | 1.73 | 146.91 | 5.319 | 5203 | 4501 | 5704 | 0 |

All six runs passed the remote-warning, output-equivalence, storage and unchanged-source checks. The analysis and execution timers come from Bazel's BEP. With interleaved analysis and execution they can overlap, so they should not be summed or treated as pure CPU time. End-to-end build time includes the client and synchronous completion work.

## Persisted snapshot behavior

The initial manifest and its replacement have identical project namespaces and source digests. Their 900 witness differences all involve the Python toolchain's `__pycache__` paths. For the first changed symlink, the old fingerprint exactly matches an existing directory referent; its current target under `/tmp/rules_python_pycache/` is absent, and the current fingerprint matches the replacement manifest. The read-only decoder verifies manifest checksums and mirrors `LocalInputSnapshot` fingerprinting. It reproduced the old snapshot's first mismatching witness and verified all 142,366 replacement witnesses. `LocalCacheSession.matchesForAdmission` rejects a publication when a global witness differs. An initial decoder-only root-relative-path normalization error was corrected; its output remains in the private raw evidence and is identified in the protocol.

The deletion event itself was not captured, so this evidence does not attribute the missing temporary directories to clean. Clean preserved the persisted publication bytes. Subsequent clean builds restored from the replacement without changing the implementation, cache settings, repository sources, or missing Python symlink targets. The old publication remained present and was rejected while the replacement was admitted.

The first attempt's harness incorrectly asserted that every enabled build must have positive analysis hits, and stopped after the successful `r1-on` build. The protocol was corrected to accept a cache miss as an observed outcome; all other validation and build commands remained unchanged. Original harness/results, the failure, and every completed trial were preserved. The remaining four builds resumed in their planned order.

For a secondary comparison restricted to the paired rounds with successful snapshot restoration (2, 3; 2 samples per condition), the medians and ranges are:

| Measurement | SkyCache off | SkyCache on |
|---|---:|---:|
| Build after clean | 144.39 (144.08–144.70) s | 156.68 (146.91–166.46) s |
| Analysis phase | 3.44 (3.22–3.67) s | 13.41 (5.32–21.51) s |
| Peak process PSS | 2307.12 (2096.44–2517.79) MiB | 4974.78 (4746.88–5202.68) MiB |

This subset is selected by observed cache behavior and is descriptive; it does not replace the complete three-pair comparison. Each restored run reads 163,219 dependency records and 336,656,848 bytes from the local analysis store. Those are local SkyCache reads, separate from Cache & Carry action hits. Fewer newly configured targets did not translate to a faster observed analysis phase. The counters do not isolate the CPU cost of validation versus deserialization.

OS page cache and WSL runtime history were not controlled. The observed WSL boot preceded resumed `r2-on` by approximately 56 seconds. Its analysis took 21.511 seconds, versus 5.319 seconds in `r3-on`; the profile's `LocalSkycacheModule.beforeCommand` span likewise fell from 4.021 to 0.393 seconds. The last paired comparison was 144.081 seconds off versus 146.913 seconds on, a much smaller difference than the aggregate median. This variability limits attribution of the full median difference to the feature. Even that final restored build showed no speedup and had higher peak PSS (5,203 versus 2,518 MiB).

## Reproduction

- Datadog revision `0cde96beb70963d0838119c308b9d0422ee66be0`, target `//cmd/cluster-agent:cluster-agent`.
- SkyCache revision `303a149a195bbd6ad770584bd34138ea749c3e9a`. Both conditions use binary SHA-256 `28026d467ef10667c0fcfb1741495fbefe4fee9dfed43d3d2d9733726b5cb159`; the changed flag is `--experimental_local_skycache` (empty versus the persisted store). Shared analysis-related options are unchanged.
- Local SkyCache was primed by the [preceding fresh-WSL experiment](skycache-v2-fresh-wsl-2026-10-25.md). Initial publication hashes, exact command arrays and per-run clean checks are recorded in `results.json`.
- Ubuntu on the E: WD Blue SN570 NVMe SSD; AMD Ryzen 9 9950X3D, 32 logical CPUs. Same JDK 25, 6 GiB heap limit, 16 action jobs and 6,144 MiB action-memory budget.
- `--remote_timeout=600`, `--remote_retries=1`, `--remote_download_outputs=all`, remote uploads enabled and no remote-error fallback. Analysis idle retention and SkyCache idle garbage collection are disabled.
- MODULE lockfile SHA-256 `1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8`; `--lockfile_mode=error`; no source changes during measurement.
- Output SHA-256 `7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664` for all six builds, also matching the preceding experiment.
- Process-tree PSS/RSS is sampled with a 100 ms wait between readings; actual timestamps are included. Memory excludes filesystem page cache and Windows/WSL host overhead. Existing external-repository downloads are retained; neither Cache & Carry nor the persisted analysis store is cleared. OS cache state, other Windows activity and network transfer variability are uncontrolled.

Public [evidence and scripts](measurements/skycache-v2-datadog-clean-2026-10-25/) include cleanup proofs, filtered BEP metrics, memory samples and raw-file hashes. Complete logs, BEP and profiles remain at `/home/jgett/REPOS/.work/datadog-clean-2026-10-25` and in the verified private archive `/mnt/d/Codex/wsl-reset-2026-10-25/datadog-clean-benchmark.tar`. Raw BEP client environment values are not published.
