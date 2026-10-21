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
production maturity. Partial-publication identity and mixed restored/recomputed graph probes now pass; expand beyond the tested rule/value families. The prior Go paging experiment is historical, with a measured
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

## Retained graph composition verified (Step 20)

Producer/ordinal identity now reconnects decoded Runfiles and symlink entries to compatible live
objects while retaining reference equality. Command epochs and active-producer guards avoid
reusing unfinished or old generating-action identities. Unscoped identity rejects publication.
Optional local upload failures discard the whole private publication, preserving successful
builds; cancellation/fatal failures and ordinary remote behavior remain unchanged. Eleven targets /
156 cases and cold/warm partial-publication, generated-artifact and cache-mode-transition probes
pass. The expanded 24-build Datadog gate also passes, now including matched cache-off restarts.
Repairing missing upload witnesses after cache-off warmup remains an efficiency limitation.
Keep the in-memory graph. Existing SkyframeExecutor invalidation already deletes imported values
when caching is disabled; no extra invalidation path or whole-graph flush was added.

## Fresh-worktree Datadog reuse still unproven (Step 21)

The 24-build gate's readers can reuse their own output-base publications. A fresh-output-base
diagnostic exposed a local-JDK generated error message containing that output base's absolute path,
which makes the writer manifests themselves incompatible across worktrees. Correct rejection must
remain; do not normalize arbitrary strings. Next, explicitly configure a valid common JDK and test
a new reader with access only to another worktree's publication. Keep the original failure in the
protocol. Small independent-publication fixtures already pass, but do not generalize that evidence.

Repeated rejected validation now has a completion-based interval and checks the previous mismatch
first, with full revalidation mandatory before admission. 158 focused cases and twelve small
concurrent worktree builds pass; the microbenchmark removes most repeated rejection allocation.
Full-pass hashing still allocates heavily. Heap diagnostics separate collectible allocation from
live heap, but native/PSS overhead remains unresolved.

## Independent Datadog reader now verified (Step 22)

A fresh reader with only another worktree's publication now passes with real configured-target hits
and byte-identical output under an explicit shared valid JDK. This exposed and fixed missing aspect
key/transitive-package metadata in the local codec. The generated remote codec and evaluator remain
unchanged. Twelve focused targets / 165 cases pass. The Step 21 portability diagnosis and the original
reader crash remain in the protocol. Next extend the stronger setup to two simultaneous fresh readers
and independent edited/reverted worktrees; then address measured full-validation allocation, bounds,
reader-safe collection and nearby-revision reuse. This does not yet establish production maturity.

## Two independent concurrent Datadog readers verified (Step 23)

The stronger 24-build gate passes: two fresh worktrees start with only other worktrees' publications,
both get actual hits and publish, and independent semantic edits/reverts match cache-disabled controls.
The full table/protocol is in the development report. Performance remains worse than cache-off in
important phases. Warm imported nodes are discarded after the existing invalidation submission
deadline expires. Optimize measured file-hashing allocation and repeated validation work without
skipping content checks or changing evaluator semantics. Keep candidate bounds, safe GC/quotas and
nearby-revision reuse in the remaining sequence; do not label the implementation production mature.

## Hashing allocation reduced; concurrent validation timeout remains (Step 24)

A bounded 1 MiB scratch-buffer pool removes most per-file hashing allocation without changing
digests or skipping content checks. 167 focused cases, a fresh independent Datadog reader and
old/new restart/warm checks pass. The microbenchmark allocation reduction is large, but single-run
process PSS rises and neither isolated warm run reproduces the earlier two-server timeout. Do not
claim that timeout or memory overhead is solved. Next investigate repeated publication/witness
validation under concurrent load, keeping the existing invalidation safety checks and in-RAM graph.

## Identical publication checks reused within discovery (Step 25)

A bounded per-discovery memo validates identical complete witness lists once and shares their
immutable metadata. It preserves full checks for changed lists and later passes, parent closure,
final publication validation, and the existing evaluator/deadline. 169 focused cases and twenty
paired Datadog builds pass. The isolated benchmark improves from 114.292 to 22.794 ms;
the paired logs contain 2 invalidation timeouts before and 0 after.
An additional eight builds with 4,501 imported configured targets per server confirm
2 old versus 0 new submission timeouts.
The development report records wall time, PSS, hit counts and single-run limitations. Next inspect
publication-time inherited validation, then bound runtime resources and address safe GC/quotas and
nearby-revision reuse. Step 23 remains the full artificial-edit/revert gate for the earlier binary.

## Inherited publication validation deduplicated (Step 26)

Finish validates each identical complete inherited input list once per finish call, with a bounded
32-list set, and retains all parent IDs and the final complete validation. 171 focused cases and a
fresh independent Datadog reader pass. The eight-parent publication microbenchmark improves from
139.833 to 40.309 ms; this is not a whole-build or PSS claim. Next bound runtime discovery
resources and improve accounting, then design reader-safe GC/quotas and nearby-revision reuse.
The latter still requires dependency-scoped validation; do not simply remove the whole-source guard.

## Candidate discovery admission is bounded (Step 27)

Default per-invocation caps are 64 shared publications and 256 MiB combined encoded manifest/index
bytes, with two experimental tuning flags. A fixed 4,096-entry directory limit bounds listing and
negative bookkeeping. These are optional-cache admission limits, not a total JVM/graph memory cap.
Closure and content checks remain mandatory; pinned readers survive later discovery limits and
private writes remain available. 176 focused cases, a 128-publication descriptor benchmark and a
fresh independent Datadog reader pass. Overfull directories and dependency closures safely miss;
disk growth is not yet bounded. Next improve accounting and design reader-safe GC/quotas and
dependency-scoped nearby-revision reuse, followed by the full concurrent edit/revert matrix.

## Dependency evidence publication is implemented (Step 28)

The local backend now persists content-addressed file/listing evidence with parent references and
the existing composite dependency tree. All 182 focused cases and a fresh Datadog off/writer/reader
diagnostic pass; storage overhead is measured. Full-source partitioning and manifest validation
remain mandatory, so nearby-revision reuse is still incomplete. Prioritize a bounded transitive
reader, then candidate-specific validation and stable repository/environment identity. Preserve
external bindings, final revalidation and the evaluated-input relationship before relaxing guards.
Do not reinterpret content hashes as monotonic versions. Keep the graph in RAM and evaluator
unchanged. Statistics and reader-safe GC/quotas follow; rerun the full two-server edit/revert matrix.

## Transitive lookup validation is implemented (Step 29)

Every accepted local analysis entry now passes bounded iterative dependency validation, including
content fingerprints, compressed composite nodes, all leaf parents and input witnesses. Successes
are memoized only for the invocation and retained for final revalidation. 192 focused cases, a
512-root cost benchmark and a fresh Datadog independent reader pass. Full-source partitioning and
publication-wide guards remain: cross-revision reuse is still incomplete. Next implement stable
repository/environment identity, per-candidate analysis lookup and selective source witness
inheritance while preserving external bindings, pack closure and invocation final validation.
Then run independent edits/reverts in two simultaneous servers. Statistics and safe GC/quotas remain.

## Cross-revision acceptance remains open (Step 30)

The working implementation separates project identity from source contents and validates both
candidate entries and the exact dependency roots of values already imported into RAM. The small
18-build edit/revert probe passes. Large Datadog warm reverts exposed a pending-continuation
cleanup bug (now committed, 294 serialization cases pass), then heap exhaustion in two 4 GiB
servers. Cross-revision changes remain uncommitted. Investigate repeated repository-mapping
deserialization, measure a local codec improvement, then rerun the full clean two-server gate.
Keep the graph in RAM and retain the heap limit. A GC-instrumented diagnostic pass is not acceptance.

## Mapping sharing measured; late eviction race fixed (Steps 31–32)

The local mapping codec removes repeated mapping materialization in the isolated benchmark and
passes five codec cases / 207 focused cases. Large warm revert still timed out after analysis.
A second independent cleanup regression proves that states created after compute-state eviction
were not cleaned up. The context now remembers closure; 298 serialization cases pass. Next verify
the runtime effect in a fresh two-server run before accepting cross-revision sharing. Native
ten-second invalidation timeouts persist; optimize validation after correctness is established.
Keep heap limits, full graph in RAM, and no evaluator rewrite. Traffic stats and safe GC remain.

## Late-eviction runtime diagnostic passes

The fresh six-phase/twelve-build Datadog smoke now passes warm revert in both 4 GiB servers,
including exact baseline output equivalence. The full 28-build edited/reverted gate is running;
cross-revision changes remain uncommitted until it passes. The smoke uses action disk cache and
does not substitute for the full edited correctness comparison.

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
