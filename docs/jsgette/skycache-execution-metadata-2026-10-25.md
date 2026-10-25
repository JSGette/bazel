# SkyCache execution metadata and build without the bytes — 25 October 2026

The implementation now reuses validated remote execution metadata before walking the generating action's dependencies. On Datadog, all six measured SkyCache builds made **zero `GetActionResult` requests**. `minimal` also made **zero CAS reads**. `toplevel` fetched five requested output/runfile blobs, including the final executable. Missing remote blobs are recovered only when requested, through Bazel's existing action rewind path.

Implementation: `71521fabb3420707c39d73329fa9aa9622e8c382` on the local `jsgette/skycache-v2-local` branch. This documentation branch carries the [incremental implementation patch](measurements/skycache-execution-metadata-2026-10-25/implementation-incremental.patch), [complete patch since the established implementation base](measurements/skycache-execution-metadata-2026-10-25/implementation-current.patch), tests, harnesses, measurements and preserved failures. It does not merge the experimental implementation branch into the documentation checkout.

## Repeated Datadog results

Each cell is the median of three measured builds. Time excludes the separately recorded clean/JVM startup. PSS is sampled memory of the build process tree, not host RAM or filesystem cache.

| Output policy | Binary / mode | Build seconds | Peak PSS MiB | GetActionResult | CAS read MiB |
|---|---|---:|---:|---:|---:|
| minimal | Vanilla fork | 8.52 | 2037 | 5704 | 14.60 |
| minimal | Candidate, SkyCache off | 8.52 | 1963 | 5704 | 14.60 |
| minimal | SkyCache execution metadata | 7.22 | 1659 | 0 | 0.00 |
| toplevel | Vanilla fork | 12.17 | 2151 | 5704 | 130.86 |
| toplevel | Candidate, SkyCache off | 12.37 | 2171 | 5704 | 130.86 |
| toplevel | SkyCache execution metadata | 10.72 | 1732 | 0 | 116.26 |

Against the same candidate with SkyCache disabled, median time changed by -15.3% for `minimal` and -13.4% for `toplevel`. Peak PSS changed by -15.5% and -20.2%, respectively. Against the preserved vanilla fork, median time changed by -15.3% and -11.9%.

The vanilla comparator is the preserved source build at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, not the released Bazel 9.3 binary. The same-binary off/on comparison isolates this feature more closely. Three repetitions and a shared network cache do not establish statistical significance. Full ranges and individual observations are in [summary.json](measurements/skycache-execution-metadata-2026-10-25/execution-metadata/summary.json).

Every measured build used Cache & Carry at `grpc://10.55.0.1:8080`, the same Datadog workspace/output base, JDK 25, 6 GiB JVM heap, 16 jobs and 6144 MiB local action-memory budget. Disk action caching was disabled. Each build was preceded by shutdown and synchronous `bazel clean --noasync --noexpunge`; the harness verified **zero Skyframe nodes/edges**, empty execroot, absent local action cache, unchanged persistent SkyCache publications, and unchanged JVM PID between clean and build. Clean started the server; its graph was empty. One Python process kept WSL running across the entire sequence, so the boot ID was stable. OS page cache and external repository downloads were warm. Source-build and fixture servers were shut down before timing.

The sequence contains two preparation builds plus 18 measured builds, rotating three variants per policy. The first preparation populates execution metadata and is excluded from measured medians. Every build recorded gRPC traffic, BEP, a profile, output inventory and process-tree PSS/RSS samples at 100 ms. Resource/storage guards remained enabled. Source revision, lockfile hash, exact commands and binary hashes are retained in [results.json](measurements/skycache-execution-metadata-2026-10-25/execution-metadata/results.json).

## Output and network behavior

- SkyCache restored six execution values and 12 configured targets per measured hit build. Bazel ran six internal actions, compared with 5,704 remote action-cache hits and 1,085 internal actions with SkyCache disabled.
- `minimal`: no `GetActionResult`, no ByteStream reads, no final executable, and no intermediate `.a` or `.x` files. Four small internal files totaling 45,186 bytes are generated; minimal does not suppress Bazel's necessary internal metadata/manifests.
- `toplevel`: no `GetActionResult`; five ByteStream reads totaling 121,907,152 bytes for requested top-level outputs/runfiles. No intermediate `.a` or `.x` files. The final executable is 119,596,056 bytes and its SHA-256 is `7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664` in every variant.
- Both hit policies still make one `GetCapabilities` request when initializing the remote client. Zero action-result lookups does not mean zero contact with the remote service.
- Source/analysis dependency validation remains local and conservative. A hit build validates a compact dependency graph; it does not query every action result or check every referenced CAS blob. This local validation is still substantial work and is not eliminated by this change.

## Implementation and correctness

The local reader accepts execution-node envelopes (type 5) as well as analysis nodes (type 4), validating their existing owner dependency root including source witnesses. The backend's upload policy admits only remote-backed execution outputs. Local/internal outputs, rich action data and absolute resolved-symlink metadata stay on normal evaluation. The namespace includes remote cache/executor/instance identity; output download policy is deliberately excluded so minimal and toplevel can share metadata. `--noremote_accept_cached` disables this execution reuse.

The existing `ActionExecutionFunction`/`ArtifactFunction` retrieval paths return metadata before traversing the action closure. Existing rewind handling prevents retrieving the same stale value again. A missing-final-output regression exposed the rewind synchronizer's assumption that input owners were already loaded. The fallback now requests only the owners/template expansion needed by actions actually being executed, including discovered inputs and runfiles. Cached execution hits still bypass this work. This setup also covers a concurrent action triggering rewind after another action has begun its fallback.

Datadog then exposed symlink validation depending on a remote input's parent directories already existing. `RemoteActionFileSystem` now supplies those parents in memory for a symlink to known remote metadata, without downloading data or creating target directories on disk. Its regression test checks both metadata resolution and absence of materialization.

The [private HTTP-cache fixture](measurements/skycache-execution-metadata-2026-10-25/local_execution_probe.py) passed 12 clean builds on the exact measured candidate. It checks zero-request minimal hits, final-only toplevel fetch, symlinks to remote files with absent parents, absent unused intermediates, required missing intermediates, missing final output, remote acceptance disabled, internal output regeneration after clean, and source-change invalidation. Deleting the required intermediate caused a CAS 404 and successful regeneration with **zero action-cache GETs**. Deleting the final blob also recovered correctly. All deletions were confined to this fixture's in-memory cache; the shared Cache & Carry server was not modified for fault injection.

The 13 local-backend test targets passed; the final portability restriction has its focused regression test. The filtered remote-filesystem suite passed 154 executed cases with zero errors/failures. [Test XML](measurements/skycache-execution-metadata-2026-10-25/test-xml) and [test summary](measurements/skycache-execution-metadata-2026-10-25/test-summary.json) preserve the exact outcomes. This verifies the exercised paths; it is not a complete qualification of all Bazel rule types or concurrent distributed-cache failure scenarios.

## Baseline and preserved development failures

Before this change, a separate 14-build series (two preparations, 12 measured builds) verified that output policy alone did not remove action traversal. Both policies still made 5,704 `GetActionResult` calls per build:

| Policy | Analysis-only SkyCache | Build seconds | Peak PSS MiB | GetActionResult |
|---|---|---:|---:|---:|
| minimal | off | 8.42 | 1948 | 5,704 |
| minimal | on | 11.63 | 4125 | 5,704 |
| toplevel | off | 12.22 | 1997 | 5,704 |
| toplevel | on | 14.87 | 4541 | 5,704 |

This baseline is a separate series, so its cross-series timing comparison is descriptive. It downloaded 15,304,814 CAS bytes in minimal and 137,211,966 in toplevel, including ancillary cache data even when outputs were not materialized. [Baseline evidence](measurements/skycache-execution-metadata-2026-10-25/analysis-only-baseline/results.json) preserves all observations.

Development failures were retained, not counted as successful measurements: an incorrect BUILD dependency name; a test using an inaccessible artifact constructor; fixture shell-string quoting; the missing-owner rewind crash; a failed attempt to overwrite a read-only copied binary followed by an accidental old-binary rerun; Datadog publication rejection of absolute generated symlink metadata; Datadog symlink validation with absent input parents; and an initially incorrect remote-test target label. The corrective changes and reruns are in the build logs, fixture directories and Datadog preparation-failure evidence. The final series used a new cache directory and a distinct immutable binary path.

## Evidence retention

Public evidence includes source patches, commands, measurements, summarized RPC counts, samples, test XML and checksums. Raw gRPC/BEP/profile files remain local because RPC metadata can contain headers. The raw archive on D: and preserved candidate binary were verified by SHA-256; [private-backup.json](measurements/skycache-execution-metadata-2026-10-25/private-backup.json) records their paths. [SHA256SUMS](measurements/skycache-execution-metadata-2026-10-25/SHA256SUMS) covers every public evidence file.
