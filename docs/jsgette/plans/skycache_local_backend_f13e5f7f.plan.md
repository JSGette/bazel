---
name: concurrent SkyCache across worktrees
overview: Independent Bazel servers retain their graphs in RAM and safely share immutable validated analysis through a local disk cache.
todos:
  - id: concurrent-storage
    content: Concurrent immutable publication and duplicate-write optimization
    status: completed
  - id: exact-snapshot-reuse
    content: Experimental exact-snapshot analysis reuse with two servers and worktree edits
    status: completed
  - id: storage-efficiency
    content: Omit unused records; measure and optimize validation and record I/O
    status: in_progress
  - id: datadog-validation
    content: Large rules_go workloads, independent worktrees, output equivalence and RAM/latency
    status: in_progress
  - id: bounded-storage
    content: Reader-safe cleanup, crash recovery, resource bounds and observable statistics
    status: pending
  - id: nearby-revisions
    content: Per-entry dependency validation and reuse across differing snapshots
    status: pending
  - id: grpc-transport
    content: A remote transport with the same immutable publication and validation contract
    status: pending
isProject: false
---
# Concurrent SkyCache

Updated 2026-10-20. This replaces the demand-paging roadmap. Keep each Bazel server's graph
in RAM. Cache reuse is optional: a miss, unsupported value or damaged record must lead to local
evaluation. Do not make disk storage authoritative for live Skyframe state, cap graph nodes by
jobs/CPU, or add Rust/IPC to the evaluator.

The selected interface is disk-cache-like: independent Bazel instances using separate output
bases can read and write the same cache directory concurrently while building worktrees of one
repository. No designated writer, shared mutable graph, five-minute snapshot timer or shared
server is required. Preserve existing Skyframe scheduling and invalidation machinery.

## Implemented and measured

Implementation is on `jsgette/skycache-concurrent`; docs/results are on `jsgette/master`.
See the [development protocol](../measurements/skycache-concurrent-development-2026-10-20.md)
for commits, exact commands, raw results, failed experiments and limitations.

- Immutable, checksummed byte records with no-replace publication and file/directory durability.
  Independent writer-JVM races, live readers, conflicting values and corrupted records are tested.
- Duplicate puts validate existing bytes before staging another file. Storage microbenchmarks
  show much less duplicate-write work; this is not a Bazel speedup claim.
- A Bazel module at the upstream serialization/service seam enables asynchronous bidirectional
  caching. Each command writes privately, then publishes an immutable manifest after successful
  completion and input validation. Multiple commands can publish simultaneously.
- Typed workspace/output paths relocate to the reader. Source package roots are restored, and
  a local codec retains transitive package metadata needed by Skymeld's execution symlink forest.
  No evaluator implementation was changed.
- Two real Git worktrees and servers pass simultaneous cold/warm builds, separate source and
  Starlark edits, reverts/restarts and deliberate shared-chunk corruption. Output bytes match.
  Restarted readers demonstrate actual configured-target hits. A separate relocation probe
  works after the writer workspace and output base become inaccessible.
- Execution-value uploads are omitted for the local backend because its reader only accepts
  analysis envelopes. Unsupported execution lookups are also bypassed. A reused test command
  initially wrote 51 records versus 149 before and issued 49 fewer reads. The subsequent
  shared-put optimization now removes those remaining duplicate writes as well.

- A full Datadog cluster-agent writer/restarted-reader sequence now succeeds with identical
  output hashes and 4,501 configured-target hits. Schemaful provider codecs preserve depset types
  across fresh/differently primed processes. Ten focused targets / 111 cases pass. The reader
  still uses more peak memory than its control and the writer remains expensive; nearby-revision reuse and resource bounds remain unfinished. The eighteen-build
  concurrent Datadog edit/restart/revert gate now passes on file and packed backends.
- Identical puts reuse validated publications while preserving immutable-key conflicts,
  read-your-writes and parent closure. The Datadog reader writes zero records instead of 4,477,
  retains all 4,501 hits and creates no empty namespace/publication. Its measured wall time
  falls from 31.2 to 22.5 seconds in the recorded comparison. A 64-lock design was rejected
  after stack samples showed write contention; 4,096 locks restore cold-writer time to the
  baseline range. These results do not establish a general speed or memory guarantee.
- Both measurement probes check Linux capacity and, on WSL, the actual Windows backing volume
  and system-drive reserve before each phase. Four regression checks and live WSL validation
  pass. This protects measurements from the earlier host-full/guest-free failure mode; runtime
  cache quotas and reader-safe garbage collection are still pending.

- Worktree identities now use effective repository/action/test environments; explicitly exposed
  values remain significant. A nine-case real Bazel matrix and the natural-environment worktree
  probe pass. Missing/regenerated repository inputs trigger a content-validated retry before
  the negative TTL expires. Large concurrent Datadog validation exposed duplicate Go archive inputs after restoring edited graphs (12 builds passed, then both restarted edited builds failed). The isolated Runfiles identity defect is now fixed and verified by four regression tests and a real-worktree diamond probe. The full Datadog gate now passes eighteen builds, including two independent semantic Go edits, exact hashes against cache-disabled controls, repeated restarted readers and reverts. Packed storage is integrated and measured with the full gate repeated; production maturity is not established.

This is an experimental implementation. The passing Datadog gates establish tested concurrent workloads, not
production maturity. Add partial-publication identity and mixed restored/recomputed graph probes. The prior Go paging experiment is historical, with a measured
latency/memory regression; its code remains on its separate branch.

## Invariants

1. Each server owns its graph, in-flight work, invalidation state, action execution and outputs.
   Shared records are immutable optional reuse data. Do not share mutable Java identities or
   process-local version numbers as semantic identities.
2. Compatibility covers binary/codec identity, configuration, semantics, platform/toolchain
   inputs and source state. Rebase typed roots; never blindly rewrite arbitrary user strings.
3. Git HEAD and dirty filenames are insufficient validity evidence. Verify content, file
   types/modes, symlink chains, directory membership, missing paths, external repository
   bindings and relevant environment. Fail closed for unsupported cases.
4. Publish durable chunks before exposing roots. Retain explicit publication dependencies.
   Readers opened earlier must discover later publications; negative caches are bounded.
5. Corruption, incomplete writes and missing chunks cannot create successful stale reuse.
   Recovery must preserve output equivalence and avoid failing an otherwise valid build.
6. Collection cannot remove records needed by active readers, writers or live publications.
   Keep a bounded storage policy separate from the in-memory graph.
7. Respect ordinary Bazel's stable-input expectation within a command. Do not claim that
   pre/post hashing is an atomic filesystem snapshot or protection against arbitrary ABA races.

## Current correctness blocker (Step 19)

Independent partial publications exposed a Runfiles identity bug: the combined provider diamond
produced four arguments instead of three. Stable producer-key/creation-ordinal provenance now
fixes the cold-reader case (117 focused cases pass). A reader retaining its locally built left
branch in RAM still duplicates the common leaf when it imports the right branch. Fix this warm
graph composition and both arrival orders before additional performance/maturity claims. The
eighteen-build Datadog gates did not cover this case. Preserve reference semantics and reject
content-only merging of distinct producer objects. Keep the graph in RAM and avoid evaluator edits.

## Next implementation and measurement sequence

### 1. Rule and filesystem compatibility

Source directory symlinks, bounded cycle detection, external absolute links and intermediate
link-chain retargeting now have content-validation support and regression tests. Expand external
repository-change coverage and retain the large-workload results in the protocol. Audit
arbitrary host-specific strings in rule values. Add tests that require fresh execution roots,
not merely action-cache hits. Validate cache-mode transitions and different environment values.

Run the public Datadog Agent checkout at the recorded commit with substantial rules_go targets.
Keep output bases isolated, use fixed JVM/action resource limits, and hold the host measurement
lock. Record source/lockfile state, binary hash, target/configuration, graph-cold versus warm
conditions, actual analysis hits, output hashes, server/process memory and cache growth.
Compare same-binary cache-off, writer and reader behavior; do not conflate action-cache savings
with analysis reuse. Expand to two live servers and semantic edits/reverts in both worktrees.

### 2. Storage and validation cost

Establish baselines before each change. Measure source scanning, external-input validation,
candidate discovery, serialization, durable writes, reads and retained metadata separately.
Sealed packs now batch durable writes and use compact per-publication indexes, with
publication and nested-chunk fallback checks. Remaining costs include linear candidate search
and whole-source/whole-publication validation. Measure and bound retained indexes and validation work. Avoid a full per-process
in-memory index that grows with the entire shared cache.

After each issue: implement one change, run focused regression checks, repeat measurements,
save commands/raw results and explain tradeoffs, commit the implementation and docs separately,
push both branches, then proceed. Report negative or inconclusive performance results.

### 3. Bounded lifecycle

Define a byte/age budget and cleanup policy. Begin with a conservative lock/lease protocol
protecting active publications and their dependency closures. Test readers surviving collection,
concurrent publishers, interrupted writes, abandoned staging and process death. Use a single
collector coordinator if useful; never serialize all ordinary writers behind a lifetime lock.
Add meaningful hit/miss/rejection, validation-time, I/O, failure and GC statistics.

### 4. Nearby commits and dirty worktrees

Current exact source identities segregate differing trees, which is safe but loses partial reuse.
Move toward per-entry transitive dependency witnesses and multiple candidates per logical key.
Validate those witnesses before exposing a value. Git diff may narrow searches but cannot
replace content checks. Cover repeated same-file edits, globs, additions/deletions/renames,
configuration changes and independently dirty worktrees. Retain safe exact-snapshot fallback.

### 5. Future gRPC

Keep the existing FingerprintValueStore/service interface as the transport boundary. Separate
immutable bytes, candidate publication/discovery and reader lifetime protection. Before remote
reuse, define portable external-root/toolchain identity, batch requests, cancellation, deadlines,
idempotent retry and remote lease semantics. The filesystem implementation and its conformance
tests remain the reference; no network service is needed for the current milestone.
