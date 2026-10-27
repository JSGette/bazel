# SkyCache generated-input restoration and 8 GiB limit — 2026-10-27

The missing generated-template inputs are fixed. The client and worker now permit an
8 GiB logical publication and an opt-in transfer budget through 8 GiB + 1 MiB
(`--experimental_remote_skycache_max_transfer_size=8193M`). The real full-graph publication
committed successfully. A full local restore still fails with a **14 GiB Java heap OOM**;
this change does not establish usable full-graph cache performance.

## Generated-input failure

Restored `ActionExecutionValue` metadata bypasses its producer action, so the output's parent
directories may be absent. `RemoteActionFileSystem.isRemote` previously canonicalized the
path before looking up that metadata. Missing parents made it report a nonremote file;
`getInputStream` then opened the nonexistent local path without invoking the downloader.

The fix consults declared remote input metadata first, leaving materialization to the normal
fetcher. It does not create directories or download files merely to inspect metadata.
The regression test reproduces the original `FileNotFoundException` before the fix and
passes afterward. Existing filesystem and path-canonicalization tests also pass.

Both original `//pkg/security/seclwin/model:strip_go_build_windows_from_*` targets now succeed
from a clean output tree and fresh JVM against the real full-graph seed:

| Restore path | Analysis hits | Execution hits | Outputs |
| --- | ---: | ---: | --- |
| Local, warm metadata pages | 3 | 1 | Both digests match the full-build baseline |
| Remote, empty retained cache and cold server pages | 3 | 1 | Both digests match the full-build baseline |

The remote check downloaded 715,124,591 metadata bytes. The metadata worker
is loopback, separate from the existing ordinary action cache. Its excluded readiness pass
also successfully read all 7,679,782,112 publication/CAS bytes and retried the existing commit.

## Larger remote publication

The shared protocol cap, client budget validation, and option help now agree on 8 GiB.
The 1 MiB chunk and bundle limits, 64-publication, 16,384-file and 32,768-chunk bounds remain.
The default attempted transfer budget remains 257 MiB; larger transfers are explicit.
Upgrade both client and catalog worker for publications above the previous 4 GiB cap.

The full `//...` workload retains the previously approved 36 packaging exclusions:
5,658 selected targets, 598,064 configured targets/aspects, 5,536 completed targets and
122 Bazel-skipped targets. The fresh publication contains **7,679,187,524 logical bytes**;
all 10,738 named output digests match the baseline and the remote commit succeeds.

Boundary coverage includes exactly 8 GiB, one byte above it, integer overflow, missing chunks,
cumulative materialization limits, and demand reads from a 6 GiB logical file while fetching
less than 4 MiB. **821 cases across 24 test targets pass.**

## Remaining full-graph failure

With WSL capped at 20 GiB, a 14 GiB Java heap, and the existing 512 MiB admission option,
the full local consumer reaches **89,515 analysis hits** before exit 33 with
`OutOfMemoryError: Java heap space`. The two previously missing inputs and both template
outputs exist with the expected contents. The input-download defect is therefore distinct
from the remaining heap-capacity failure. The final BEP metrics are incomplete after the crash.

A heap histogram could not be collected before the JVM exited. The allocation failure's
stack location alone does not identify which objects retain the heap. Next investigation:
capture a live heap histogram/retention profile before saturation, then distinguish persistent
restored graph cost from concurrent deserialization state. Larger metadata transfer limits do
not solve this JVM-memory requirement. The separate default 256 MiB admission-budget issue
documented in the prior full-graph report is also unchanged.

## Controls and exclusions

These are correctness checks, not repeated performance samples. Startup/clean, worker readiness,
seed generation, and failed attempts are excluded from timing comparisons. Native binaries,
source/fixture state, output digests, empty graphs and cache hits are recorded.

The first attempted old-seed restore had an accidental tools-directory `PATH` alias and was
excluded. After correcting it, the old seed still had no hits because the new native binary has
a different installation checksum in `FrontierNodeVersion`. No checksum guard was bypassed:
the successful fresh remote producer supplied a new seed for both consumers. The old-seed
recompute created a parent-linked publication; it was not used for validation.

See [verification.json](verification.json), compressed per-run results/logs, the red/green test
records, and the archived harness. Larger raw recordings and cache blobs remain under
`/home/jgett/REPOS/.work/skycache-restore-limits-2026-10-27`. No fixture files were changed and nothing was pushed.
