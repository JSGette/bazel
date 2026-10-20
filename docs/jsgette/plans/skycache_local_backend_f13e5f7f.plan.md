---
name: concurrent SkyCache across worktrees
overview: Independent Bazel servers share immutable validated analysis records through a concurrent filesystem backend, with demand paging and a future gRPC transport.
todos:
  - id: go-measure
    content: Complete real rules_go correctness and Datadog memory/latency comparisons
    status: completed
  - id: concurrent-storage
    content: Implement and test the immutable concurrent filesystem storage foundation
    status: completed
  - id: exact-snapshot-reuse
    content: Add portable exact-snapshot analysis reuse across simultaneous worktrees
    status: pending
  - id: dependency-validation
    content: Validate cached dependency witnesses across nearby commits and dirty trees
    status: pending
  - id: bounded-residency
    content: Enforce byte budgets, batch I/O, compact storage, and measure whole-machine memory
    status: pending
  - id: grpc-transport
    content: Add gRPC using the same storage and publication conformance contract
    status: pending
isProject: false
---
# Concurrent SkyCache

> Direction updated 2026-10-20: keep the graph in RAM and minimize evaluator changes.
> Build a shared disk analysis cache for independent Bazel servers. Active development,
> decisions and measurements are recorded in the [development log](../measurements/skycache-concurrent-development-2026-10-20.md).
> The paging and bounded-residency sections below describe the earlier proposal and are
> no longer implementation requirements; the immutable-sharing and validation requirements remain.

Architecture revised 2026-10-19 after the completed rules_go correctness and Datadog benchmark
matrix. Concurrent filesystem publication is implemented and tested; portable cross-worktree
analysis integration is next.

## Objective and ownership

Independent Bazel servers in simultaneous worktrees should reuse completed analysis from the same
or nearby repository states. Each server owns its evaluation overlay: dirty/in-flight nodes,
dependency scheduling, local versions, reverse-edge mutation, action execution state and output
roots. Shared storage holds immutable portable values, shared chunks, dependency evidence and
published lookup candidates. Several servers may publish and read concurrently; there is no
exclusive lifetime writer and no requirement to wait for a five-minute snapshot.

The Rust code in this fork is a storage process, not a Rust Skyframe evaluator. Preserve Java's
evaluation semantics while improving storage and residency. A full evaluator rewrite is a separate
project and is not required for concurrent reuse.

## Evidence gates

The existing native-genrule experiment saved about 10% of post-GC heap on a synthetic 1,024-action
graph, but raised JVM-plus-Rust PSS about 24%, and added latency on disjoint and edited builds.
It does not establish a whole-machine memory win. The completed
[Go comparison](../measurements/skyframe-lazy-go-datadog-linux-2026-10-19.md) uses the same
source-built binary in both modes on Datadog's cluster-agent (57,298 configured targets,
318,018 graph nodes). It reports graph residency, process-tree PSS, live heap, latency, I/O and
matching output hashes through full-GC reload and source/Starlark/library edits and reverts.

The Go prototype is also a negative performance result: at a 256-eligible-node limit, 314,159
nodes remain pinned and 3,603 are cold. Post-warm live heap rises from 248.4 to 381.4 MiB (+54%);
warm-build median rises from 0.598 to 24.019 seconds (40x). Peak process-tree PSS is about 4 GiB in
both modes. Keep demand paging opt-in. Complete portable sharing independently, and require
compact metadata, byte accounting and removal of repeated serialization before widening residency.

The current local codec pins configurations and Starlark definitions. Its node IDs, key dictionary,
versions and identity references belong to one evaluator generation. Those bytes must never be
published as portable SkyCache records. The existing alternative graph also retains Java handles
for every node and a Rust in-memory location index. Counting evicted delegates is insufficient.

Real rules_go validation has already found three requirements absent from the native-genrule
fixture: artifact-array codecs for multi-output compile actions; explicit sharing of the transitive
GoArchive provider DAG (otherwise the store exceeds 1 GiB before analysis finishes); and preservation
of Runfiles identity inside GoArchiveData, so resident and decoded depsets do not pass duplicate
packages to the linker. The first Go codec pins Runfiles and reports that cost. These findings make
provider semantics and ownership an explicit acceptance gate for every new rule family.

## Invariants

1. Separate optional cache misses from loss of authoritative paged evaluation state. A missing or
   invalid shared cache entry causes recomputation. A missing private spill record fails the
   operation or resets the evaluator at a safe command boundary; it must not silently disappear.
2. Derive a compatibility namespace from repository identity, Bazel/codec versions, semantics,
   configuration/transitions, toolchains and platforms. Explicitly rebind workspace/output roots.
   Do not reuse local numeric Skyframe versions across JVMs.
3. Start with exact source snapshots validated by content, file type/mode, symlink target and
   relevant directory/absence witnesses. HEAD plus a list of dirty filenames is incorrect: editing
   the same dirty file twice leaves that key unchanged. Include relevant untracked/ignored files,
   external repositories and environment/toolchain inputs, or disable sharing for unsupported cases.
4. Treat commit IDs as lookup hints once dependency validation exists. Preserve multiple candidate
   versions per logical key; two worktrees must not overwrite each other's candidate. A conflicting
   payload for an identical full semantic identity signals nondeterminism or an incomplete key.
5. Publish complete immutable data before making a root/candidate visible. A successful publication
   means the referenced closure is readable and has reached the required durability boundary.
   Readers opened before publication must discover it without restarting; negative caches cannot
   hide new entries indefinitely.
6. GC cannot delete authoritative records, active reader pins or in-progress writer data. First
   implement no deletion, then introduce fenced ownership/leases and a tested collector. A single
   GC coordinator is compatible with many writers. Remote lease expiry requires a separate design.

## Delivery sequence

### A. Measure and bound the local Go experiment

Audit ordinary rules_go library Starlark actions, providers, argument mapping, resource callbacks,
configuration and artifact identity. Leave input discovery/pruning, mutable enhanced actions,
unsupported action classes and in-flight values pinned. Exercise actual large Datadog builds,
warm runs, full-GC reloads, source edits/reverts and Starlark edits/reverts. Preserve generated output
hashes and commands. Do not turn paging on by default based on node-count reduction alone.

Completed: exact command/action/provider identity tests; the full large-workload matrix; and an
additional real Go-library source/BUILD compile-option edit matrix. Outputs match the in-memory
control, including every revert. The result supports the codec experiment's tested correctness,
not a production memory or latency improvement.

### B. Concurrent filesystem storage foundation

Implement an immutable, namespaced FingerprintValueStore at the existing serialization boundary.
Use SHA-256 names/content checks, bounded record lengths, owner-private staging, atomic no-replace
publication and file/directory durability. Independent processes can publish unrelated entries and
idempotently publish identical entries; conflicting values are rejected rather than silently won
by the last writer. Read directly from the filesystem to avoid per-process full indexes and stale
startup-only discovery. No garbage collection or production module activation in this first slice.

Acceptance: a reader opened before writers sees live publications; simultaneous processes can
publish identical and distinct keys; conflicting writers cannot replace a completed entry; partial
staging, truncation and corruption are never returned as valid values; incompatible namespaces miss.

Implemented checkpoint: `ConcurrentFileFingerprintValueStore` at
`src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/` passes these checks,
including four independent live writer JVMs and a two-process conflicting publication race.
The backend requires an existing durable root on a same-user local POSIX filesystem. It publishes
with an atomic hard link (no replacement), checks the stored key and SHA-256 payload digest, and
completes writes only after file and directory fsync. It is not registered as a Bazel module yet;
cross-worktree analysis reuse depends on phase C, not merely on this byte-store implementation.

The first format may use one file per entry for a simple publication proof. The older packed
prototype already demonstrated that this layout is expensive at analysis-cache scale. Benchmark it
before activation; sealed packs and sparse disk indexes are the next storage optimization, not an
excuse to omit concurrent publication correctness.

### C. Portable exact-snapshot analysis reuse

Port the existing LocalSkycacheModule/serialization integration after auditing its guards. Supply
exact snapshot identity and root rebinding; initially reject unsupported inputs. Use completed
serialization write futures as the dependency-closure barrier, then publish an immutable candidate
manifest. Do not relax upstream warm-upload gates without tests comparing cold and warm frontiers.
Test two worktrees, two independent servers and different output bases building at the same time.
Verify outputs and action keys against uncached analysis, including edits during a publication.

### D. Reuse across nearby commits

Wire dependency validation into retrieval, with trustworthy witnesses for contents, globs, missing
paths, symlinks, external repositories, flags and environment. Git diff accelerates candidate
selection but is not the correctness oracle. Check repeated edits of the same file, additions,
deletions, renames and branch changes. Validate candidates before exposing portable values to the
local evaluator. Report rejected candidates and why they were rejected.

### E. Memory reduction and I/O scheduling

Use a byte budget covering decoded values, pinned/active state, handles/indexes, caches and I/O
buffers. Jobs/CPU times a factor controls admission and bounded prefetch, not a claim that the total
graph has only that many nodes: waiting parents, dependency fanout and arbitrary SkyFunction-held
references can exceed execution concurrency. Reserve progress capacity so reads required to finish
work cannot deadlock behind the residency budget. An oversize or unsupported value has an explicit
pin reason and measured cost.

Audit global consumers as well as the evaluator: action-conflict checking, analysis-result
collection and depset expansion can touch far more than the currently executing jobs. A lazy
node map alone does not make these consumers streaming. Define which metadata is always resident,
which references callers may retain, and how each global operation avoids repeated hydration.

Batch requests and move I/O away from globally serialized eviction paths. Avoid repeated whole
nested-set flattening and encode/decode work for still-retained values. Add private-store compaction
and a sparse on-disk shared index. Measure JVM + Rust PSS and filesystem-cache residency, with guest
page-cache and host/device-cache limitations stated separately. Require a repeatable memory benefit
at acceptable latency before widening the value allowlist or changing defaults.

### F. gRPC transport

Keep filesystem paths out of semantic records. The existing FingerprintValueStore remains the byte
transport seam; add batch get/put, immutable candidate publication/discovery and pin/release contracts
where required rather than forcing these semantics into arbitrary-key overwrite. A later gRPC
backend implements the same conformance tests with cancellation, deadlines, retries and idempotent
publication. Remote storage is optional reuse; each evaluator retains a reliable private authority
until remote durability and lease guarantees can safely serve that role.

## Existing prototype audit

`origin/jsgette/skycache-local` at `4d3d1534be` already has a module, bounded RAM tier and packed
fingerprint store. Its per-writer pack files are useful precedent, but startup-only indexes miss
live publications and sweeping protects only the local writer's current pack. Do not point several
servers at it as a supported concurrent cache. Its GitChangeSource validation is still roadmap work.
The previous local plan's dirty-filename key and blanket rejection of gRPC are superseded.

The shared-server proposal remains an alternative to evaluate separately. It is not the selected
architecture for this request, which requires independent simultaneous Bazel instances/worktrees.
