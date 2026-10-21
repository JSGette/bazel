---
name: concurrent SkyCache across worktrees
overview: Independent Bazel servers retain their graphs in RAM and safely share immutable validated analysis through a local disk cache.
todos:
  - id: concurrent-storage
    content: Concurrent immutable publication and duplicate-write optimization
    status: completed
  - id: exact-snapshot-reuse
    content: Independent server and worktree reuse with verified outputs
    status: completed
  - id: storage-efficiency
    content: Measured validation costs; net whole-build benefit remains unproven
    status: completed
  - id: datadog-validation
    content: 'Recorded 16 attempts: 14 passed, 2 timed out; final six correctness checks passed'
    status: completed
  - id: bounded-storage
    content: Lease-aware collection tested; large post-GC rebuild deferred by cap
    status: completed
  - id: nearby-revisions
    content: Per-entry dependency validation and reuse across differing snapshots
    status: completed
  - id: grpc-transport
    content: Future transport; not required for the local filesystem milestone
    status: pending
isProject: false
---
# Concurrent SkyCache

Updated 2026-10-21 through development Step 58. The intended result is an optional shared disk
analysis cache that independent Bazel servers can read and write simultaneously while building
worktrees of the same repository. Each server retains its own full graph in RAM, private output
base, action execution and invalidation state. There is no designated writer, periodic snapshot
timer, shared mutable graph, graph paging, Rust service or evaluator residency limit.

Implementation: [`jsgette/skycache-concurrent`](https://github.com/JSGette/bazel/tree/jsgette/skycache-concurrent),
currently `34688d19d6244ef3ccf633ebcca6e1376f12c87b`. The measured Java binary remains the
`802d66bc21bd706a20dc5476791d305020363303` implementation; the later commit changes only the harness. Documentation and results remain on
`jsgette/master`. The [development protocol](../measurements/skycache-concurrent-development-2026-10-20.md)
contains the chronological measurements and decisions. The
[previous roadmap](skycache_local_backend_f13e5f7f.history-through-step49.md) is preserved for history;
its old pending-work statements do not describe current implementation status.

## Local prototype handoff; qualification remains open

The current binary passes 280 Java cases across 18 targets and a 34-build concurrent membership
matrix. The capped Datadog run contains 16 attempts: 14 passed and two timeouts. The last six passed
after repository inputs moved to NVMe while preserving logical paths: simultaneous edited
reads/publications, exact warm reverts and independent edited controls. Both prior failed commands
remain failures. No full 16/16 success, general speedup or production maturity is claimed.

A separately authorized two-build follow-up on full E: NVMe storage passed with 32 jobs,
Xmx6g and 6 GiB action memory: 33.901 seconds for fresh publication and 27.762 seconds for an
independent reader with 4,501 analysis hits. Both outputs match the baseline. These are single
observations with warm action outputs; cache-off speedup remains unmeasured. See development
Step 58 for the preserved harness assertion correction, raw-evidence hashes and qualifications.

The [implementation summary](../skycache-implementation-summary.md) describes behavior, measurements,
shortcomings and missing work. Raw evidence was archived and readback verified; four owned final
worktrees and both temporary images were removed. Source builds/tests used Cache&Carry. Every
server retains its graph, and the Skyframe evaluator implementation remains unchanged.

The last six timings are correctness observations: earlier failed attempts warmed local outputs.
Large post-GC writer/reader and repeated restart checks were omitted under the cap. Current focused
collector tests and earlier real-server lifecycle evidence remain separate. Net performance benefit,
broader compatibility, longer histories and remote transport remain open.

## Implemented design

- The local module uses existing analysis serialization and cache-service interfaces. It preserves
  ordinary Skyframe scheduling and in-memory evaluation; the evaluator implementation is unchanged.
- Each command writes private immutable packed records and publishes a checksummed manifest only
  after successful completion and fresh input validation. Durable chunks precede discoverable roots.
  Simultaneous publishers and previously opened readers are supported.
- Validated identical puts reuse existing records. Conflicting immutable values, corruption,
  incomplete records, missing parents and unsupported data cause conservative misses or discard
  the optional publication. They must not produce stale successful reuse.
- Compatibility includes the actual Bazel installation identity, configuration, Starlark semantics,
  backend identity and stamp mode. The local project namespace includes the Git common directory,
  relevant effective environments and input-fingerprint version. Separate clones and remote machines
  need a future portable project identity; linked worktrees already share the common directory.
- Only typed workspace/output paths are relocated. Provider/depset schema, Runfiles/symlink producer
  identity, transitive packages, aspects and repository mappings have explicit serialization support.
  Arbitrary user strings are not rewritten. Unsupported rule/value families remain optional misses.
- Per-entry dependency evidence permits reuse across different source snapshots. Retained imported
  values are validated against their original dependency roots, not replacement entries for a key.
  Whole external repository bindings remain conservative guards.
- Input evidence includes content, types, modes, full symlink chains, directory membership, missing
  paths and relevant environments. Git HEAD or a list of dirty files never replaces content checks.
  Untracked/ignored source files are included, except Git administration and Bazel-owned output links.
- Large-file digest reuse is bounded to one fresh validation/capture pass and guarded by Unix
  device/inode, change time, modification time and size. Every path's metadata and link evidence is
  checked. No digest memo crosses passes or commands. Unsupported identity providers reread bytes.
  Defaults retain at most 256 digests for files of at least 1 MiB, with 1 MiB of pooled scratch space.
- Candidate discovery is bounded by publication count, encoded metadata and scan limits. Success
  memoization is bounded and invocation-local. Late repository materialization can trigger a fresh
  content check; stale timestamps cannot turn a failed witness into a hit.

The current input fingerprint domains are `local-project-v3-content-sha256` and
`local-source-v11-content-sha256`. Older data remains in its previous namespace; mixed versions
cannot consume each other's evidence. Rollback selects the previous namespace. No in-place data
rewrite is required, and packed storage/collection lease protocols are unchanged.

## Storage lifecycle and observability

OS-held publication leases protect active writers, readers and their transitive parent closure.
Process exit releases leases. Ordinary record operations do not hold a global writer-lifetime lock.
A collector coordinator and short catalog lock protect discovery/deletion transitions; expensive
scanning/checksum work occurs outside the catalog lock. Manifest deletion is ordered child before
parent and synced before owned data removal. Corrupt closures, cycles, limit violations and unknown
layouts fail conservatively before unsafe deletion. Unrecognized files and symlinks are preserved.

The default soft collection policy is 5 GiB or seven days, attempted after 30 idle seconds. Active
data may exceed those limits. A zero dimension disables that dimension; both zero disables automatic
collection. Focused tests and prior real-server lifecycle probes cover live-reader protection,
concurrent publication, crash/process death, interruption, corruption and recovery. The large
post-collection rebuild pair was omitted under the 16-attempt cap; focused and prior lifecycle evidence remain separate.

Per-command JSON reports reads, shared reads, writes, deduplication, rejected candidates,
publication failures, bounded-discovery counters and logical cache traffic. Dependency-validation
and idle-collection reports are separate. Logical request bytes are not physical disk I/O, and
encoded metadata budgets do not bound decoded objects or the whole graph. See the
[usage guide](../skycache-local-usage.md) for flags and report locations.

## Invariants

1. Each server owns its graph, in-flight work, invalidation state, action execution and outputs.
   Shared records are immutable optional reuse data; Java identity is not semantic identity.
2. Compatibility covers binary/codec identity, configuration, semantics, platform/toolchain
   inputs and source state. Rebase typed roots; never blindly rewrite arbitrary user strings.
3. Verify content, types/modes, symlink chains, directory membership, missing paths, external
   repository bindings and relevant environments. Fail closed for unsupported cases.
4. Publish durable chunks before roots. Retain explicit parent dependencies and support readers
   discovering later publications. Bound negative discovery state and retained metadata.
5. Corruption, incomplete writes and missing chunks cannot create successful stale reuse.
   Optional upload failures discard the publication without failing an otherwise valid build.
6. Collection cannot remove records needed by active readers, writers or retained publications.
   Keep disk policy separate from the in-memory graph and preserve unknown/legacy data.
7. Respect Bazel's stable-inputs-during-command expectation. Pre/post checks and per-pass digest
   memoization are not an atomic filesystem snapshot or protection against privileged metadata
   forgery or arbitrary input ABA races.

## Follow-up priorities

1. Run fair repeated comparisons on native SSD-backed ext4 with identical starting outputs and
   randomized order. Report full command time, process memory and physical I/O; hits are not speedup.
2. Profile hashing, dependency validation and metadata before changing code. Preserve content
   checks, original retained-value evidence and conservative handling of unknown forwarding keys.
3. Complete the omitted large post-GC/restart checks; broaden rules, platforms, server counts and
   long-running history coverage. Existing focused tests do not establish every production scenario.
4. Keep graphs in RAM and retain the evaluator. Future gRPC and portable identities are separate.

All source builds/tests used Cache&Carry:
`--config=efcache --bes_backend= --remote_upload_local_results=false`.
Concurrent Datadog commands each used jobs=2/action memory 3072 MiB/Xmx4g. Remote action caching was
off; edited builds and independent controls also disabled disk action caching. The unchanged
1,800-second full-command deadline counted both prior failures.

## Future gRPC boundary

Keep `FingerprintValueStore` and the existing service-supplier boundary. Separate immutable bytes,
candidate publication/discovery, validation and reader lifetime protection. Before cross-machine
reuse, define portable project/external-root/toolchain identities, request batching, cancellation,
deadlines, idempotent retries and remote lease semantics. The filesystem implementation and its
conformance tests are the reference. A network service is outside the current local milestone.
