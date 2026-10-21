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
    content: Measured storage and validation optimizations; final large-workload latency gate
    status: in_progress
  - id: datadog-validation
    content: Complete current 32-build Datadog reuse/edit/revert/collection matrix
    status: in_progress
  - id: bounded-storage
    content: Lease-aware collection implemented; current substantial recovery gate pending
    status: in_progress
  - id: nearby-revisions
    content: Per-entry dependency validation and reuse across differing snapshots
    status: completed
  - id: grpc-transport
    content: Future transport; not required for the local filesystem milestone
    status: pending
isProject: false
---
# Concurrent SkyCache

Updated 2026-10-21 through development Step 50. The intended result is an optional shared disk
analysis cache that independent Bazel servers can read and write simultaneously while building
worktrees of the same repository. Each server retains its own full graph in RAM, private output
base, action execution and invalidation state. There is no designated writer, periodic snapshot
timer, shared mutable graph, graph paging, Rust service or evaluator residency limit.

Implementation: [`jsgette/skycache-concurrent`](https://github.com/JSGette/bazel/tree/jsgette/skycache-concurrent),
currently `258febe36adf692149ee7346d2d03972a460c113`. Documentation and results remain on
`jsgette/master`. The [development protocol](../measurements/skycache-concurrent-development-2026-10-20.md)
contains the chronological measurements and decisions. The
[previous roadmap](skycache_local_backend_f13e5f7f.history-through-step49.md) is preserved for history;
its old pending-work statements do not describe current implementation status.

## Current evidence and open acceptance

The current binary passes 277 Java cases across 18 targets and a fresh 34-build runtime matrix
with two cached worktrees and independent cache-off controls. That matrix checks repeated semantic
edits, file additions, unchanged-content renames, deletion, warm reverts and restarted readers.
Every output matches expected/control bytes; fresh pairs have actual analysis hits. Source trees
restore cleanly and servers stop. The unchanged measurement harness has 29 passing Python checks.

Earlier substantial Datadog matrices passed independent reuse and semantic edit/revert checks.
The latest completed large attempt remains a failure: ten verified outputs and real independent
analysis hits were followed by two concurrent edit commands exceeding their deadline during final
input validation. One earlier warm invalidation deadline also expired. The exact failed report,
logs and cache data remain preserved. Cache-enabled restarted builds were slower and used more
process memory than matched cache-off controls on the temporary D SATA HDD.

Two subsequent fixes are measured, tested and pushed: inherited publication evidence is checked
once in the final pass, and large aliased files share one content digest within each fresh pass.
Their benchmarks reduce logical reads by about 67% and 80%, respectively. These are warm-filesystem
microbenchmarks, not claims of Datadog or cold-disk speedup. The new 32-build Datadog matrix is running
with a fresh cache, the current binary and unchanged output/hit/deadline checks. Final acceptance
is still open. Do not call this production mature or infer whole-build gains from microbenchmarks.

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
concurrent publication, crash/process death, interruption, corruption and recovery. The current
substantial forced-collection/rebuild gate must still complete.

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

## Remaining local acceptance sequence

1. Finish the current full Datadog matrix: matched cache-off controls, one writer and independent
   reader, concurrent warm/restarted readers, distinct semantic edits in both worktrees, warm
   reverts/re-edits, independent edited controls, repeated restarted edits/reverts, forced collection,
   then rebuild and independent/concurrent reuse. Require actual configured-target/shared reads,
   exact executable hashes and feature markers, successful publication where expected, no disabled
   cache fallback, no deadline failures, stopped servers and restored sources.
2. Inspect raw logs, samples and collector records against the report. Check zero active collection
   slots, removal of inactive recognized data and recovery after collection. Recheck binary/source
   hashes, pinned Datadog revision/lockfile, environment and resource-policy identity. Do not accept
   a successful action summary when command finalization failed.
3. If a gate fails, preserve the result and diagnose before editing. For each performance change,
   capture a baseline, implement the narrow fix, run meaningful regressions, repeat measurements,
   record decisions/rejected alternatives and commit/push implementation and docs separately before
   moving to the next issue. A longer timeout or ignored witness is not evidence of a solved defect.
4. Publish matched end-to-end latency, participating-process RSS/PSS, real hit counts and storage
   behavior, including negative/inconclusive results. The D device is a SATA HDD; compare within-run
   phases only and state page-cache/host-memory exclusions. Do not make an SSD or general speed claim.
5. Complete the requirement audit against current code and actual evidence. Preserve private raw
   logs/BEP, failed publication bytes, current evidence and source provenance in a verified archive.
   Unregister only the four owned final Datadog worktrees, normally unmount the exact temporary D
   image, stop task helpers and remove that image. Preserve the original E fixtures and user repos.
   Final docs/results go to `jsgette/master`; implementation stays on its separately pushed branch.

All source builds/tests use Cache&Carry:
`--config=efcache --bes_backend= --remote_upload_local_results=false`.
Standalone source work uses available CPUs with memory headroom. The matched Datadog matrix keeps
jobs=2, action memory=3072 MiB and Xmx4g per server. Remote action caching is off to isolate analysis
reuse; ordinary native repository sharing is on for all worktrees. Edited correctness phases and
their controls disable shared disk action caching. Do not change one side of a comparison mid-run.

## Future gRPC boundary

Keep `FingerprintValueStore` and the existing service-supplier boundary. Separate immutable bytes,
candidate publication/discovery, validation and reader lifetime protection. Before cross-machine
reuse, define portable project/external-root/toolchain identities, request batching, cancellation,
deadlines, idempotent retries and remote lease semantics. The filesystem implementation and its
conformance tests are the reference. A network service is outside the current local milestone.
