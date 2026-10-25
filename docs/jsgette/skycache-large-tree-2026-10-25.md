# SkyCache large-tree implementation and after-clean benchmarks

25 October 2026. Native Linux in WSL on the E: SSD. Windows work remains deferred.

The formerly rejected 237,112- and 220,062-configured-target Datadog scopes now publish reusable SkyCache data. The implementation replaces large witness manifests and single value packs, and separates bounded validation memo retention from traversal limits. Performance and RAM results below include all three repeated observations per variant and output policy; preparation and interrupted diagnostic attempts are excluded.

## Implementation

Source commit `d4971808785aaa3cd46bf6dd1419cb65afb4319e`, tree `57e373490c32f4a733263e130ba6214a2cacd633`. Final executable SHA-256 `e757f790289c786736622fbe66a5a010ba2128fba4cd5805ac5c2d76d4aad731`.

- `leased-v2/publications-v4` contains small checksummed headers, witness-block descriptors and parent publication IDs. `leased-v2/objects/segmented-v1` contains flat immutable witness blocks and pack segments. Old `leased-v1` data remains untouched and invisible to the new reader/GC; rollback uses the preserved old format.
- Witness paths use prefix compression, SHA-256 digests use 32 raw bytes, and negative witness states have explicit tags. Each block has at most 4,096 witnesses or roughly 1 MiB of encoded source payload, is independently compressed and checksummed, and is bounded before allocation. Admission charges fully expanded paths as well as encoded/decoded bytes against the existing 256 MiB metadata budget.
- Sixteen fingerprint buckets route lookups. Each mutable pack rotates at 16,384 records or roughly 32 MiB, seals its compact index and releases the mutable map. Readers open segment indexes on demand, within the aggregate admission budget. Rotation is checked before the next write; one bounded record can exceed the byte target. Explicit limits remain: 4,096 segments, 256 MiB writer index-entry budget, 64 MiB individual values, and bounded root directories.
- All files are durable before atomic publication. Reader leases, parent closure, GC, corruption fallback and final input revalidation remain in force. Global external/repository witness admission is still eager. Selective per-value repository-binding validation is a separate, unimplemented stage.
- A full success memo no longer invalidates an otherwise fully validated dependency result. It stops retaining additional successes. The default memo now holds at most 1,000,000 dependency successes and 1,000,000 witness successes. The independent per-lookup 250,000-record, 128 MiB, depth, reference and in-flight limits remain unchanged. This bounded increase prevents the repeated-traversal behavior observed with a saturated 250,000-entry memo; it is not an unlimited cache.

The [incremental patch](measurements/skycache-large-tree-2026-10-25/implementation-large-tree.patch) applies after `71521fabb3420707c39d73329fa9aa9622e8c382`. The [combined implementation patch](measurements/skycache-large-tree-2026-10-25/implementation-current.patch) applies after `6f451b2dc6f783dda879ff3c7ed3fc8146d0f410`; applying it in a temporary Git index reconstructs the exact source tree above.

## Capacity

Largest publication in each final cache:

| Configured targets | Manifest bytes | Witnesses | Blocks | Witness disk MiB | Expanded path/evidence MiB | Records | Segments | Index MiB |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 237,112 | 5598 | 418,229 | 103 | 9.61 | 66.26 | 1,664,713 | 113 | 76.22 |
| 220,062 | 5286 | 396,111 | 97 | 8.85 | 61.93 | 1,545,003 | 96 | 70.73 |

The previous six-target attempt required at least 73.95 MiB of witness manifest data and 1,664,716 records, exceeding its 64 MiB manifest and one-million-entry pack limits. Those historical counts were reconstructed from an unpublished pack; they are not byte-identical fixtures of the final format. The current layout inspection authenticates headers and indexes and records all descriptor sizes in `publication-layout.json` for each scope.

## Repeated performance

All cells are medians of three runs. `off` and `on` use the same final executable. `vanilla` is the preserved pre-SkyCache source comparator at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, SHA-256 `1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588`; it is not an official stock Bazel release.

| Configured targets | Policy | Variant | Build s | Range s | Analysis s | Clean + build s | Peak PSS MiB | Peak RSS MiB | GetActionResult | CAS read MiB |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 237,112 | minimal | vanilla | 13.87 | 13.73–13.93 | 5.29 | 15.16 | 3592 | 3598 | 16697 | 15.13 |
| 237,112 | minimal | off | 13.38 | 13.22–14.52 | 5.29 | 14.91 | 3877 | 3883 | 16697 | 15.13 |
| 237,112 | minimal | on | 14.42 | 14.32–14.83 | 12.21 | 15.96 | 2999 | 3006 | 0 | 0.00 |
| 237,112 | toplevel | vanilla | 26.17 | 26.08–27.78 | 5.51 | 27.45 | 3547 | 3553 | 16697 | 418.15 |
| 237,112 | toplevel | off | 26.43 | 26.17–26.48 | 5.45 | 27.96 | 3469 | 3475 | 16697 | 418.15 |
| 237,112 | toplevel | on | 22.89 | 22.73–24.02 | 13.00 | 24.46 | 2855 | 2861 | 0 | 403.02 |
| 220,062 | minimal | vanilla | 12.37 | 11.77–12.67 | 5.07 | 13.85 | 3485 | 3491 | 12986 | 15.12 |
| 220,062 | minimal | off | 12.32 | 12.12–12.52 | 5.21 | 13.79 | 3414 | 3420 | 12986 | 15.12 |
| 220,062 | minimal | on | 14.93 | 14.83–14.98 | 12.64 | 16.41 | 2571 | 2578 | 0 | 0.00 |
| 220,062 | toplevel | vanilla | 20.68 | 20.42–20.83 | 4.98 | 21.91 | 3373 | 3379 | 12986 | 299.19 |
| 220,062 | toplevel | off | 21.38 | 20.63–21.84 | 5.49 | 22.90 | 3409 | 3415 | 12986 | 299.19 |
| 220,062 | toplevel | on | 19.02 | 19.01–19.17 | 13.33 | 20.50 | 3088 | 3094 | 0 | 284.06 |

Positive values mean reductions; negative values mean regressions:

| Configured targets | Policy | Baseline | Build time reduction | Peak PSS reduction |
|---:|---|---|---:|---:|
| 237,112 | minimal | vanilla | -4.0% | 16.5% |
| 237,112 | minimal | off | -7.8% | 22.6% |
| 237,112 | toplevel | vanilla | 12.5% | 19.5% |
| 237,112 | toplevel | off | 13.4% | 17.7% |
| 220,062 | minimal | vanilla | -20.6% | 26.2% |
| 220,062 | minimal | off | -21.2% | 24.7% |
| 220,062 | toplevel | vanilla | 8.0% | 8.5% |
| 220,062 | toplevel | off | 11.0% | 9.4% |

Capacity is fixed; build-time performance remains mixed. Minimal is 4.0% slower at 237k targets and 20.6% slower at 220k, while toplevel is 12.5% and 8.0% faster, respectively, against the preserved vanilla comparator. Median peak PSS falls by 8.5–26.2%. At 237,112 configured targets, median minimal-mode analysis takes 12.21 s with SkyCache versus 5.29 s for vanilla. At 220,062 configured targets, median minimal-mode analysis takes 12.64 s with SkyCache versus 5.07 s for vanilla. Avoiding action-result traffic does not eliminate the cost of eager input admission and dependency validation.

These are small samples on a shared remote service with warm OS and external-repository caches. They do not establish statistical significance. PSS/RSS are sampled process-tree memory, not total host RAM or filesystem page cache, and may miss short peaks. Individual values, ranges, clean timings, RPC counts and all commands are in the [measurement directory](measurements/skycache-large-tree-2026-10-25).

First minimal run per scope/variant:

| Scope | Variant | Targets configured | Analysis hits | Execution hits | Local files | Local bytes | .a / .x | Validation records read | Rejected roots |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 237,112 | off | 237112 | 0 | 0 | 1211 | 13390119 | 0 / 0 | 0 | 0 |
| 237,112 | on | 24 | 17 | 11 | 14 | 184572 | 0 / 0 | 608611 | 1 |
| 237,112 | vanilla | 237112 | 0 | 0 | 1211 | 13390119 | 0 / 0 | 0 | 0 |
| 220,062 | off | 220062 | 0 | 0 | 1108 | 10097561 | 0 / 0 | 0 | 0 |
| 220,062 | on | 23 | 16 | 10 | 12 | 136649 | 0 / 0 | 580054 | 1 |
| 220,062 | vanilla | 220062 | 0 | 0 | 1108 | 10097561 | 0 / 0 | 0 | 0 |

Configured-target hits count restored frontier values, not the transitive graph avoided. `scope-and-output-details.json` preserves each run's validation and admission counters. Any remaining GetActionResult requests correspond to work not restored through SkyCache; the implementation adds no all-action validation sweep. CAS bytes and materialized outputs are measured rather than inferred from the policy. `bes-profile-checks.json` distinguishes FindMissingBlobs profile uploads from output validation.

All twelve final measured SkyCache builds make zero GetActionResult requests. All six minimal builds make zero CAS reads. Each six-target toplevel run reads ten blobs totaling 422,596,080 bytes; each five-target toplevel run reads nine totaling 297,860,368 bytes. Neither policy materializes intermediate `.a` or `.x` files on a hit. The last toplevel run in each scope additionally maps every downloaded digest to an actual materialized output, retained in `top-output-read-verification.json`.

## Protocol and correctness

Six targets: `//cmd/agent:agent`, `//cmd/cluster-agent:cluster-agent`, `//cmd/process-agent:process-agent`, `//cmd/trace-agent:trace-agent`, `//cmd/security-agent:security-agent`, `//cmd/system-probe:system-probe`. The five-target scope removes only core Agent. Datadog revision `0cde96beb70963d0838119c308b9d0422ee66be0`; frozen resolved lock SHA-256 `0bb43dc2960626918c20c350491be5cdf6fd18730bb34a956d4608a1e1aa98fc`.

Every build follows server shutdown and `clean --noasync --noexpunge`. The harness verifies zero Skyframe nodes and edges, empty execroot, absent local action cache, unchanged persistent publication hashes, and the same new server PID throughout the build. Each variant uses the same workspace/output base, JDK 25, 6 GiB JVM, 16 jobs and 6144 MiB action-memory budget. Disk caching is disabled. Cache & Carry remote cache and BES are enabled at `grpc://10.55.0.1:8080`, with one connection and sixteen requests per connection. Explicit clean clears the graph; idle eviction and GC are disabled for measurement.

Each scope has three excluded preparation builds followed by eighteen measured builds. Variant order rotates off/on/vanilla, on/vanilla/off, vanilla/off/on; output-policy order also alternates. Build wall time includes synchronous publication and final validation but excludes separately timed clean. PSS/RSS is sampled every 100 ms; host and WSL storage checks remain active.

All requested executable digests match vanilla preparation. Toplevel also hashes actual executable files; measured minimal runs require final executables to remain absent. File inventories record intermediate archives and exports. All **201 test cases in 15 targets pass**, including witness size/corruption/budget handling, concurrent segment rotation, leases/GC, repository changes, old-format isolation, full memo behavior and unchanged traversal limits. This is the SkyCache package suite plus the specified Datadog builds, not a full Datadog unit/E2E test run or Windows qualification.

The original Datadog lockfile is restored after each scope (SHA-256 `1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8`), tracked workspace changes are checked, and owned build servers are shut down.

## Diagnostic attempts retained

The initial segmented-format candidate passed the test package and published the large tree. A complete comparison round exposed the original 250,000-entry success memo rejecting valid roots: six-target minimal on took 33.68 s versus vanilla 14.22 s, and toplevel on 51.24 s versus vanilla 26.83 s. The second round was deliberately interrupted to address this; the interrupted row is excluded.

The next candidate stopped rejecting successes when the memo filled, but its excluded reuse preparation performed 82,389,346 record reads and was stopped after 133.90 s. This demonstrates that dropping retention alone can cause severe repeated traversal. The final candidate separates a bounded one-million-entry memo from the unchanged traversal limits. Both interrupted attempts, source hashes, commands, output digests, samples and stop reasons are retained, not blended into final medians.

Two development test failures are retained: an assertion assumed eager pack opening, and a new fixture modified its workspace after taking the stability snapshot. The former now verifies the descriptor bound under lazy opening; the latter creates fixtures before the snapshot. Subsequent full test runs pass.

Public evidence includes patches, scripts, logs, metrics, output hashes, samples, XML test results, and checksums. Raw BEP, gRPC, profiles, binaries and cache files are in the verified private archive recorded in [private-backup.json](measurements/skycache-large-tree-2026-10-25/private-backup.json). Raw content may contain environment details, so it is not committed to the public branch. All historical caches and failed attempts remain preserved.
