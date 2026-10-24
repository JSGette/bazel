# SkyCache v2 WSL continuation — 2026-10-24

This protocol resumes the cloud handoff on the user's Windows PC in Ubuntu WSL2.
The cloud results and their incomplete runs remain archived separately. Local
results below apply only to the recorded hardware, storage layout, binaries and
fixtures. This is experimental work, not a production qualification.

## Source and evidence

Local implementation commit: `303a149a195bbd6ad770584bd34138ea749c3e9a`. The complete current source diff is
[archived with the evidence](measurements/skycache-v2-wsl-2026-10-24/implementation-current.patch).

- Implementation base: `6f451b2dc6f783dda879ff3c7ed3fc8146d0f410`.
- Local implementation branch: `jsgette/skycache-v2-local` in
  `/home/jgett/REPOS/bazel-skycache-v2`.
- Documentation base: `8f4ec5307f553018d00a2be926868c530678f5ef` on
  `jsgette/master`, checked out in `/home/jgett/REPOS/bazel-skyframe-findings`.
- [Structured local protocol](measurements/skycache-v2-wsl-2026-10-24/protocol.json)
  records commands, binary hashes, tests, storage observations and stage outcomes.
- [Original complete handoff](measurements/skycache-v2-wsl-2026-10-24/cloud-handoff/CONTINUE.md)
  preserves the original patches, design documents, checksums and raw cloud evidence.
- The separate research patch was applied to its exact documentation base. The
  implementation patch was applied to its exact source base in a new worktree.
  Every original archive checksum passed.

## Setup and preserved failures

1. Located `C:\Users\jgett\Downloads\skycache-handoff.zip`, inspected its entries,
   read its continuation instructions, and verified Ubuntu WSL2 availability.
2. Extracted it into `/home/jgett/REPOS/.work/skycache-v2-local/skycache-handoff`
   with path containment and no-overwrite checks; verified `SHA256SUMS`.
3. Created the isolated source worktree and applied the implementation patch with
   `git apply --check` first. Existing implementation worktrees were preserved.
4. The first inline PowerShell build command failed shell parsing before Python
   or Bazel ran. Subsequent commands use saved scripts to avoid nested quoting.
5. The original WSL storage preflight rejected the Ubuntu backing volume:
   `E:\WSL\Ubuntu` had 26.6 GiB free, less than the unchanged 30 GiB reserve.
   No build ran on that rejected path.
6. Created a dedicated 64 GiB ext4 image at
   `C:\Users\jgett\Documents\Codex\2026-10-24\ex\work\skycache-v2-data.ext4`
   and mounted it as `/mnt/skycache-v2-local`. This reserves approximately
   64 GiB on C:. No persistent mount configuration was changed. The original
   storage guard verified the loop device's actual C: backing; C: retained
   about 76 GiB free and the ext4 filesystem about 62 GiB before building.
7. New Bazel output bases, installation/repository caches and temporary files
   use this volume. Source checkout and old baseline/JDK files are read from
   their existing WSL locations. Timings therefore describe ext4 through a
   Windows-backed loop image, not the earlier native-ext4-on-E: measurements.
8. Fetched the remote branches, fast-forwarded the clean local `jsgette/master`
   checkout, and restored the documentation patch and complete cloud evidence.

## Implementation and harness changes

The restored production patch contains parallel dependency-subtree validation
and configurable idle graph eviction. See the
[structure decision](skycache-structure-decision-2026-10-24.md) and
[cloud prototype report](skycache-idle-retention-prototype-2026-10-24.md).

The WSL continuation adds explicit `--output-user-root` control to the repeated
idle benchmark and the worktree probe. The worktree probe also accepts `--jdk`
and `--other-bazel`; both actual binary hashes are recorded for mixed-version
tests. The cold-import probe reuses the exact startup options saved by its
fixture. These options preserve the default behavior when omitted.

## Validation protocol

Build with Bazel 9.3.0 using a separate output base, six jobs, a 5 GiB compiler
server heap and an 8 GiB action-memory resource budget:

```bash
/home/jgett/REPOS/.tools/bazel \
  --output_user_root=/mnt/skycache-v2-local/bazel-user-root \
  --output_base=/mnt/skycache-v2-local/build-output \
  --host_jvm_args=-Xmx5g \
  test //src:bazel-dev \
  //src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:all \
  --jobs=6 --local_resources=memory=8192 --test_output=errors
```

After success, copy the executable to a fresh name and stop only the build
server before timing fixtures. Verify the preserved baseline executable against
the committed `a275f62018c2e5fc25f0b5f89199aac7da7464c9696b1daeec640db52c2e0e23`
SHA-256 and record the new executable's actual hash.

The planned correctness workload is 63 fixture builds: 25 for the corrected
three-repetition idle A/B, repeated imports and same-mtime mutation control; two
for cold import; 18 for same-version concurrent worktrees; and 18 for mixed
versions. Module-lockfile preparation is separately recorded. This is an explicit
local workload plan, not the superseded cloud three-build cap.

Run validator and packed-store scaling in fresh JVMs at 1/2/4/8 threads, with the
harness's warmups and repeated samples. Preserve all attempts and do not pool
incomplete or failed conditions. Independent output checks, actual eviction
receipts, same-PID checks, publication guards and corruption fallback remain
enabled. No machine-wide filesystem-cache flushing is performed.

## Local results

Host: AMD Ryzen 9 9950X3D, 16 cores / 32 logical processors; WSL had 15 GiB
RAM and 4 GiB swap. The run image is on the WDC WD BLACK 512 GB NVMe C: drive.

The rebuilt executable and all **190 cases in 12 test targets passed**. The
fresh build completed in 478.174 seconds; this is setup cost, not a SkyCache
performance measurement. All **63 fixture builds passed**, with independent
output checks, same-JVM recovery, repeated imported-only cycles, same-mtime
content invalidation, concurrent worktree edits, retained-value invalidation,
and deliberately corrupted-chunk fallback. The cold-import probe confirmed
zero novel writes in both its cold import and same-PID post-idle recovery.

[Machine-readable summary](measurements/skycache-v2-wsl-2026-10-24/summary.json)
contains every aggregate and links through the protocol to commands and logs.

| Condition | Median full-client recovery | Median warm command |
| --- | ---: | ---: |
| skycache | 0.228 s | 0.199 s |
| off | 0.365 s | 0.255 s |

The median cache-off/SkyCache recovery ratio was 1.60× in this synthetic fixture.
The warm command always runs with SkyCache enabled so that both conditions have
the same durable backing and eviction setup; the off row switches it off only
for the measured recovery. Three samples do not support a tail-latency claim.
The recovery result is not a representative real-build speedup.

| Threads | Serial median, JVM 1 / 2 (ms) | Parallel median, JVM 1 / 2 (ms) |
| ---: | ---: | ---: |
| 1 | 35.36 / 34.41 | 35.23 / 35.83 |
| 2 | 37.87 / 37.77 | 20.85 / 20.55 |
| 4 | 39.02 / 37.62 | 22.93 / 21.96 |
| 8 | 37.61 / 37.96 | 17.98 / 18.28 |

Idle eviction released a median 130.8 MiB of heap in the SkyCache cycles.
The separately sampled median PSS drop was 292.9 MiB; these are fixture
observations and are not guaranteed OS-memory reductions.

Every measured validator round read exactly 8,832 records and checked 8,192
witnesses, with zero rejected roots. Packed-store results retain separate fresh
JVMs and sample sizes in the JSON; durable writes include sealing/fsync and all
reads are byte-checked. Storage timings are specific to the C:-backed loop volume.

Packed reads scaled more strongly than durable writes on this volume:

| Workload | Threads | Durable write median, JVM 1 / 2 (ms) | Read median, JVM 1 / 2 (ms) |
| --- | ---: | ---: | ---: |
| 20,000 × 256 B | 1 | 236.29 / 246.05 | 13.89 / 13.07 |
| 20,000 × 256 B | 4 | 226.60 / 241.84 | 5.36 / 5.54 |
| 8,000 × 4 KiB | 1 | 1087.65 / 1079.45 | 22.66 / 22.64 |
| 8,000 × 4 KiB | 4 | 1075.20 / 1059.42 | 7.67 / 7.53 |

Durable-write gains were small; these measurements do not reproduce the cloud
write scaling on the local loop-backed storage path. Retain this negative result
alongside the read and validator improvements.

The old/new worktree probe used the verified original base executable in B and
the new executable in A. Both the same-version and mixed-version probes passed
all ten phases (18 builds each). This covers the tested fixtures and format
compatibility; it is not a general production compatibility certification.

## Report-generation checks and resuming

The evidence preserves two aggregation setup failures: an early attempt rejected
an unfinished packed-store sample file, and a later attempt found the temporary
mount absent after the benchmark processes ended. No partial summary was
published. The existing volume was remounted, every stage/sample count was
rechecked, and aggregation passed. The mount-loss cause was not established.
The [aggregation checks](measurements/skycache-v2-wsl-2026-10-24/aggregation-checks.json)
and [resume-helper checks](measurements/skycache-v2-wsl-2026-10-24/resume-helper-checks.json)
include the failed attempts and corrections.

After a WSL restart, run the saved `Resume-Skycache.ps1` helper to verify/remount
the existing image. Add `-OpenShell` to open the source checkout in WSL. The
helper does not format or rebuild the volume. Its initial Windows CRLF pipe
issue was corrected and its already-mounted path was tested successfully.

## Remaining scope and interpretation

Representative real-target timing, production qualification, proof-block/batch
I/O redesign, observation-bound provenance, selective publication and a global
in-flight byte budget are separate work. A mechanism benchmark speedup does not
establish an end-to-end build improvement. An idle-recovery comparison does not
establish a benefit for a resident warm analysis graph.

Full fixture directories and profiles remain in `/mnt/skycache-v2-local`.
Public evidence contains command/result logs, XML test reports and profile hashes;
compiled binaries, generated repositories, packs and raw environment dumps are
excluded. The original handoff preserves failed and interrupted cloud runs.
