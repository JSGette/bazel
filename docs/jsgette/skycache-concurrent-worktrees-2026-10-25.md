# Shared SkyCache with two concurrent Datadog worktrees — 25 October 2026

With remote transfers capped at 16 concurrent requests per server, two WSL Datadog Git worktrees successfully read and published to **one SkyCache namespace** while building different source changes concurrently. Their outputs matched independent cache-disabled controls. After swapping the edits between worktrees and running synchronous `bazel clean`, each reused the other worktree's publication and retained the correct executable digest and runtime version marker. No product changes were made for this test. Default-limit attempts encountered appliance/transport failures and are retained separately below.

## Setup and acceptance

- Datadog revision `0cde96beb70963d0838119c308b9d0422ee66be0`; the original checkout and its resolved module lockfile were preserved. A and B share its Git common directory, have separate source trees and separate output bases, and use `/home/jgett/REPOS/.work/datadog-concurrent-2026-10-25/shared-skycache` for SkyCache.
- Candidate implementation `71521fabb3420707c39d73329fa9aa9622e8c382`; executable SHA-256 `1e17c582e2e91d983210d70f9a130e1dd4cea68de62419e3b86bd91df1b9cad9`. Cache & Carry at `grpc://10.55.0.1:8080` was enabled throughout; disk action cache was disabled.
- A changes `cmd/cluster-agent/subcommands/version/command.go` to print `Cluster Agent Skycache-A`. B independently changes `pkg/cli/subcommands/version/command.go` to print `Serialization version Skycache-B`. The [source patches](measurements/skycache-concurrent-worktrees-2026-10-25/a-state-a.patch) and [B patch](measurements/skycache-concurrent-worktrees-2026-10-25/b-state-b.patch) include the established resolved lockfile.
- Each server: 4 GiB maximum JVM heap, eight jobs, 3072 MiB local action-memory budget. Both build `//cmd/cluster-agent:cluster-agent`. The passing writer/reader matrix additionally sets `--remote_max_connections=1 --remote_max_concurrency_per_connection=16` on each server, after unstable default-limit attempts. These transport limits do not serialize the two builds or the local SkyCache. Existing external repository downloads were copied and known-root symlinks relocated; local outputs/action caches were not copied.
- Before **every** build: shutdown, synchronous `clean --noasync --noexpunge`, and assertions for zero Skyframe nodes/edges, empty execroot, absent action cache, unchanged persistent publications. All concurrent pairs start at a barrier; recorded monotonic intervals prove writer/reader overlap. The recovered B control lacks its harness PID/timing record, as explained below.
- The changed cache-disabled controls run before the cache-enabled changed builds and disable remote uploads. Thus their newly compiled changed results cannot come from this experiment's SkyCache-produced results. The same candidate binary is used for controls.

## Results

`CT / execution` is the count of restored configured targets and action-execution values. Writes count accepted new local SkyCache entries. Times include cache publication but exclude separately recorded clean/JVM startup. B's recovered control uses Bazel-reported elapsed time; all other rows use the harness's monotonic timing.

| Phase | Worktree / source variant | Policy | Seconds | CT / execution | GetActionResult | New SkyCache entries |
|---|---|---|---:|---:|---:|---:|
| stabilize-b | B / base | minimal | 10.99 | 0 / 0 | 5704 | 0 |
| seed-retry | A / base | minimal | 21.28 | 0 / 0 | 5704 | 449,222 |
| cross-read-base | B / base | minimal | 10.48 | 12 / 6 | 0 | 0 |
| controls | A / a | toplevel | 148.06 | 0 / 0 | 5704 | 0 |
| controls | B / b | toplevel | 270.74 | 0 / 0 | 5706 | 0 |
| concurrent-writers-bounded | A / a | minimal | 23.99 | 4244 / 3780 | 31 | 2 |
| concurrent-writers-bounded | B / b | minimal | 159.41 | 5949 / 3778 | 33 | 9,340 |
| remote-metadata-fill | A / a | minimal | 13.13 | 12 / 6 | 0 | 0 |
| remote-metadata-fill | B / b | minimal | 15.02 | 4244 / 3782 | 29 | 1 |
| concurrent-readers | A / a | minimal | 9.94 | 12 / 6 | 0 | 0 |
| concurrent-readers | B / b | minimal | 9.89 | 12 / 6 | 0 | 0 |
| swapped-readers-minimal | A / b | minimal | 9.80 | 12 / 6 | 0 | 0 |
| swapped-readers-minimal | B / a | minimal | 9.90 | 12 / 6 | 0 | 0 |
| swapped-readers-toplevel | A / b | toplevel | 17.32 | 12 / 6 | 0 | 0 |
| swapped-readers-toplevel | B / a | toplevel | 17.21 | 12 / 6 | 0 | 0 |

Both concurrent writer builds read existing shared entries and successfully published new entries. Their build intervals overlapped for **23.99 seconds**. All prior manifests remained byte-identical. The final 5 manifests passed checksum, identity, common-namespace, parent-reference and backing-object-presence checks. Publication events and exact timestamps are in [results.json](measurements/skycache-concurrent-worktrees-2026-10-25/results.json); integrity evidence is in [publication-integrity.json](measurements/skycache-concurrent-worktrees-2026-10-25/publication-integrity.json).

| Concurrent phase | Overlap seconds | Combined peak PSS MiB |
|---|---:|---:|
| concurrent-writers-bounded | 23.99 | 10377 |
| remote-metadata-fill | 13.13 | 6291 |
| concurrent-readers | 9.89 | 4098 |
| swapped-readers-minimal | 9.80 | 3909 |
| swapped-readers-toplevel | 17.21 | 4248 |

Memory is sampled combined server process-tree PSS every 200 ms, including action descendants. It excludes the Python probe, client launchers, unrelated processes and OS page cache. Other unrelated Bazel servers were present on the machine and were left untouched. These observations establish functional overlap; they are not a controlled speedup benchmark or a statistical contention study.

## Output isolation and build without bytes

Independent control output:

```text
Cluster Agent Skycache-A 7.86.0-localbuild - Commit: dev - Serialization version: v5.0.213 - Go version: go1.26.9
```
```text
Cluster Agent 7.86.0-localbuild - Commit: dev - Serialization version Skycache-B: v5.0.213 - Go version: go1.26.9
```

| Source state | Executable SHA-256 |
|---|---|
| Baseline | `7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664` |
| A only | `48f0ea130496f45d02e683ac8e0f73373335e017cd1cbb004424b20e733dc90b` |
| B only | `111ef3e3f3aff797a5b388617c07b6e4e503df5b1c69b84e73db4ce21e2abcbd` |

The three hashes are distinct. Every successful later build's BEP output digest matched the correct control. Final `toplevel` builds also verified the actual file bytes and executed `cluster-agent version`, requiring the expected marker and rejecting the other marker. Swapping source variants forces A to consume B's cached variant and B to consume A's, ruling out mere reuse of each worktree's own prior state.

The strict clean hit phases require **zero `GetActionResult`**. `minimal` additionally requires zero CAS reads, no final executable and no intermediate `.a`/`.x` files. Final `toplevel` builds fetch only requested output/runfile blobs and match control bytes, with no intermediate `.a`/`.x` files. Newly executed local actions may materialize files during a cache miss; the no-materialization assertions apply to fully cached minimal builds. A separate `remote-metadata-fill` pass allows newly compiled/uploaded local outputs to be observed as remote-backed metadata before the strict hit checks.

Hit builds still issue one remote `GetCapabilities` request. Three final hit builds also made one `FindMissingBlobs` request each for their BES profile: the queried hash and size exactly match that run's `.profile.gz`, as verified in [bes-profile-checks.json](measurements/skycache-concurrent-worktrees-2026-10-25/bes-profile-checks.json). These were profile-upload checks, not action-result or build-output validation. Each final toplevel build read five requested output/runfile blobs totaling 121,907,152 bytes.

## Preserved setup failure and scope

The initial seed build itself succeeded and produced the baseline digest, but publication was safely rejected with `inputs changed during the invocation`. This occurred on the first build after relocating external repositories. The same source and executable published successfully on a clean retry after repository setup stabilized. The protocol preserves the original failure and harness; it does not count that attempt as a successful publication. No product change or cache validation bypass was used.

The B control also completed successfully despite many Cache & Carry transient errors. Its gRPC log exposed an instrumentation parser bug: synthetic terminal retry-summary records have `rpc_id` and field 9 but no method field. The corrected parser correlates these records with their attempts and reports them separately, retaining all error counts. B's successful BEP, executable, runtime marker, digest and RPC log were recovered without repeating the build. The phase memory samples and B's exact monotonic timing were lost on that parser exception and are explicitly unavailable. A recovery-script assumption about an omitted protobuf default exit-code field was corrected before any further build.

The first concurrent writer pair then failed with Cache & Carry `GetCapabilities` connection refused, and neither published an incomplete build. The separate user-authorized LAN/BES task reported an appliance restart/licensing connectivity outage. It also enabled BES publishing in future harness launches; that setting was preserved. After `/healthz` returned `HEALTHY`, the writer pair was retried against the same cache and source states. All attempts are retained. BES traffic from the writer retry onward is included in timings/RPC evidence; these runs are not directly comparable to the earlier BES-disabled performance series.

That first retry still encountered repeated transport resets. A completed with the correct output and published but returned exit 38 for failed BES delivery; B failed before producing its output. These are not counted as a passing pair. Its logs also contained valid non-retrying RPC entries without `rpc_id`; the parser now counts those explicitly rather than assuming every entry has an ID. Exact build records were recovered, but that phase's memory samples were unavailable. The subsequent passing matrix capped remote transfer concurrency at 16 requests per server. The evidence does not establish whether connection pressure, appliance recovery, background BES uploads, or their combination caused the earlier transport failures, nor does it prove success at the default remote limits.

Both bounded writers exited zero and published. The harness then incorrectly asserted that every minimal build must leave the final executable absent. That assertion was narrowed to the strict cache-hit phases: locally executed actions legitimately produce output files. The completed writers were revalidated against their independent hashes and runtime markers, without rerunning them. Their saved memory samples and monotonic overlap are intact.

This checks two processes sharing one local SSD cache, divergent source content, clean readers/writers, cross-worktree variants and output policies. It does not prove arbitrary process counts, crash recovery, GC contention or network-filesystem semantics. Garbage collection was disabled to isolate the requested read/write test.

## Evidence

The [evidence directory](measurements/skycache-concurrent-worktrees-2026-10-25) contains the reproducible harnesses, commands, build/clean/graph logs, patches, RPC counts, memory/storage samples, failed attempts and SHA-256 checksums. Raw BEP, gRPC logs and profiles remain in a verified private archive on D: because they can include environment values or request headers. The archive checksum and location are in [private-backup.json](measurements/skycache-concurrent-worktrees-2026-10-25/private-backup.json).
