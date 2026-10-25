# Fresh WSL and Datadog comparison — 25 October 2026

The user authorized purging WSL and Qwen and restarting Datadog from scratch.

The four Qwen model shards in `E:\QWEN` (148.51 GiB) were deleted. The old Ubuntu distribution at `E:\WSL\Ubuntu` was unregistered, removing its 718.80 GiB virtual disk. A new Ubuntu 24.04.5 LTS distribution was installed at the same E: location, with user `jgett`. E: had about 881.56 GiB free immediately after the purge and about 880 GiB after initial installation.

Ten repositories, including local branches and uncommitted working files, and 6,442 experiment evidence files were backed up and verified before unregistering. The private backup is `D:\Codex\wsl-reset-2026-10-25`; it contains source/history archives, evidence, the exact benchmark binaries and JDK, and the incremental bundle for the local SkyCache commit. Access is restricted to the current Windows user, SYSTEM, and Administrators. Private archives and credentials are not uploaded to Git.

The old 64 GiB C: loopback benchmark image was also removed after archiving and verifying 143 evidence files. The scratch build caches were discarded. The Downloads handoff and unrelated Windows applications remain available.

Fresh checkouts were created for Datadog commit `0cde96beb70963d0838119c308b9d0422ee66be0`, SkyCache commit `303a149a195bbd6ad770584bd34138ea749c3e9a`, and the documentation branch. No old Datadog output bases, repository caches, action caches, or graph caches were restored. Preserved Bazel binaries and JDK 25 make the implementation/runtime comparison reproducible.

Bazelisk v1.30.0 was installed from the official GitHub release after checking its published SHA-256. The ordinary Datadog workflow resolves its pinned Bazel 9.3.0. Benchmark variants use the explicit source-built vanilla and experimental binaries recorded in the setup manifest. `~/.bazelrc` provides `--config=efcache` for Cache & Carry at `grpc://10.55.0.1:8080`.

The final Ubuntu virtual disk is 37.20 GiB, versus 718.80 GiB before reset. After all builds, E: has 844.36 GiB free and C: has 138.51 GiB free.

## Completed comparison

This real Datadog workload does not reproduce the earlier synthetic speedups. SkyCache warm builds are 8.2 times slower than vanilla at the median, and retained-validation work dominates that regression. Remote rebuild medians differ by only 1.9% with overlapping observed ranges. SkyCache also uses more foreground process memory; idle eviction lowers its own footprint substantially but leaves it slightly above vanilla's retained footprint.

All 46 Bazel builds succeeded and produced the same cluster-agent binary. Four preparation builds are excluded from comparison; 42 measured builds cover three rotated rounds of vanilla, SkyCache off, and SkyCache on, followed by three remote-cache rebuilds per enabled implementation. Nine idle cycles were recorded. The initial SkyCache publication rejection is preserved as a failed preparation validation, despite the underlying build succeeding.

The timings below include client startup and completion, including synchronous publication/validation work. Values are medians; parentheses show the full observed range. PSS is proportional resident memory of the Bazel process tree, including compiler children, sampled at 100 ms. RSS is also retained but can count shared pages more than once. These are small repeated samples on one machine, without significance claims.

| Build scenario | Implementation | n | Wall seconds, median (range) | Peak PSS MiB, median | Peak RSS MiB, median |
|---|---|---:|---:|---:|---:|
| Fresh server, existing outputs | Vanilla baseline | 3 | 9.318 (8.252–9.366) | 2060 | 2066 |
| Fresh server, existing outputs | SkyCache binary, cache off | 3 | 7.837 (7.577–8.146) | 1597 | 1604 |
| Fresh server, existing outputs | SkyCache enabled | 3 | 10.550 (10.413–14.732) | 4129 | 4135 |
| Warm unchanged-source build | Vanilla baseline | 6 | 0.556 (0.504–0.670) | 2085 | 2091 |
| Warm unchanged-source build | SkyCache binary, cache off | 6 | 0.557 (0.499–0.619) | 1756 | 1763 |
| Warm unchanged-source build | SkyCache enabled | 6 | 4.558 (3.716–12.735) | 4367 | 4373 |
| Build after 20 seconds idle | Vanilla baseline | 3 | 0.492 (0.491–0.528) | 1651 | 1657 |
| Build after 20 seconds idle | SkyCache binary, cache off | 3 | 0.477 (0.471–0.485) | 1609 | 1616 |
| Build after 20 seconds idle | SkyCache enabled | 3 | 6.553 (6.442–6.791) | 5246 | 5253 |
| Fresh server, local action cache disabled | Vanilla baseline | 3 | 149.667 (146.186–168.140) | 1964 | 1970 |
| Fresh server, local action cache disabled | SkyCache enabled | 3 | 152.573 (150.910–153.570) | 4832 | 4899 |

- Fresh server, existing outputs: SkyCache median wall time was 13.2% higher than vanilla.
- Warm unchanged-source build: SkyCache median wall time was 719.3% higher than vanilla.
- Build after 20 seconds idle: SkyCache median wall time was 1232.7% higher than vanilla.
- Fresh server, local action cache disabled: SkyCache median wall time was 1.9% higher than vanilla.

## Observed warm-build bottleneck

SkyCache's no-change warm builds repeatedly validated 163,619 dependency records and read 52,882,761 bytes of CAS values (about 50.4 MiB), despite configuring no targets. The first warm run logged 10.09 seconds in invalidation lookup; the second round's first warm run logged 3.380 seconds. These observed validation costs explain a substantial part of the warm-build regression. Per-run timings and counters are in `summary.json` under `warm_validation_overhead`, with profile observations alongside the evidence. The next optimization target is avoiding repeated dependency validation and CAS reads when the retained graph and its input evidence remain valid. The benchmark does not modify or disable validation to obtain a faster number.

## Idle memory

SkyCache alone uses a 15-second analysis retention deadline; it evicts the graph and requests GC. Vanilla and the disabled control retain their normal behavior. The 12-to-20-second change is the median paired change within each cycle, so it need not equal the difference of the two displayed medians. Eviction receipts are included in `results.json`.

| Implementation | PSS at 12 s, MiB | PSS at 20 s, MiB | Paired PSS reduction, MiB |
|---|---:|---:|---:|
| Vanilla baseline | 1603 | 1603 | 0 |
| SkyCache binary, cache off | 1540 | 1540 | 0 |
| SkyCache enabled | 4404 | 1712 | 2692 |

## Cache & Carry and preparation

Every invocation explicitly used `grpc://10.55.0.1:8080`, with no disk action cache. The repository wrapper's automatic cache selection was disabled to select this endpoint explicitly. Preparation filled fresh E: output bases; measurements reused the resolved dependencies and outputs. The remote rebuilds started a fresh server and set `--nouse_action_cache`, forcing action execution/cache lookup while retaining existing filesystem contents. They are not empty-output or cold-OS-cache download tests.

- Vanilla baseline Cache & Carry hits across the three forced rebuilds: [5704, 5704, 5704]; local sandbox actions: [0, 0, 0].
- SkyCache enabled Cache & Carry hits across the three forced rebuilds: [5690, 5704, 5704]; local sandbox actions: [14, 0, 0].

Where remote-hit and local-action counts differ, the measured wall difference includes that action mix and network variability. It cannot be attributed solely to analysis-cache performance. Internal action counts are recorded separately in the raw metrics.

The first vanilla build ran 5,704 local sandbox actions. Six asynchronous remote uploads exceeded the original 120-second RPC deadline; the build itself succeeded. The first SkyCache build obtained 5,697 remote hits and ran seven local sandbox actions. Its output matched vanilla, but analysis snapshot publication was rejected with `inputs changed during the invocation`. Exact changed witnesses were not reported; fresh external-repository materialization is a hypothesis, not a proven cause. Retrying with the populated output bases and the module lockfile frozen succeeded. Both measured implementations used a 600-second remote timeout, and every measured run passed the no-remote-warning check. The original attempts, hashes, logs and diagnosis were retained.

| Preparation attempt | Bazel exit | Wall seconds (not comparative) |
|---|---:|---:|
| prepare-vanilla | 0 | 793.150 |
| prepare-skycache | 0 | 204.660 |
| prepare-vanilla-retry1 | 0 | 8.815 |
| prepare-skycache-retry1 | 0 | 25.757 |

## Reproduction and limits

- Host: AMD Ryzen 9 9950X3D, 16 cores / 32 threads, about 32 GiB installed RAM. WSL exposes approximately 15.4 GiB RAM. Storage: Linux ext4 inside `E:\WSL\Ubuntu\ext4.vhdx`, on the WD Blue SN570 2 TB NVMe SSD. C: is a separate WD Black NVMe drive.
- Datadog revision: `0cde96beb70963d0838119c308b9d0422ee66be0`; target: `//cmd/cluster-agent:cluster-agent`. This measures the Datadog cluster-agent target, not all targets in the repository.
- SkyCache: `303a149a195bbd6ad770584bd34138ea749c3e9a`. Vanilla: `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, the source-built pre-SkyCache fork baseline, not the official Bazel 9.3.0 release binary. An additional control uses the SkyCache binary with the feature disabled.
- Same JDK 25, 6 GiB JVM heap cap, 16 action jobs, 6,144 MiB action-memory budget. At least 30 GiB E: and 10 GiB C: reserves were enforced. Exact command arrays and binary hashes are in `results.json`.
- Sources were unchanged during measurement; the resolved `MODULE.bazel.lock` is represented by its hash and patch. All measured invocations used `--lockfile_mode=error`. The initial module resolution is not a measurement.
- OS caches were warm. Timed runs are unchanged-source builds and forced remote-cache rebuilds; this does not measure incremental source edits or compare cold compilation throughput. The initial 793-second and 205-second setup times have different cache conditions and must not be interpreted as a SkyCache speedup.
- PSS/RSS exclude Windows host overhead and Linux filesystem page cache. Peaks are sampled, not an exact continuous maximum. Other Windows workloads were not controlled.
- Output SHA-256 for every successful build: `7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`.

Full raw BEP, profiles, logs and samples are retained in `/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25` and a verified private archive on D:. Public evidence includes the exact harness versions, filtered metrics, compact sample series, storage protocols, source-lock patch and hashes of raw evidence. Arbitrary BEP client environment values are not published.

Evidence: [reset, setup, benchmark and validation artifacts](measurements/skycache-v2-fresh-wsl-2026-10-25/).

## Actual clean comparison

A separate [six-build comparison after verified `bazel clean`](skycache-v2-datadog-clean-2026-10-25.md) tests SkyCache enabled versus disabled using the same binary, with zero graph nodes and empty local outputs before every build. The warm-build figures above do not describe that scenario.
