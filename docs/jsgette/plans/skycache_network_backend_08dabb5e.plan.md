---
name: SkyCache gRPC transport
overview: Add a transport-compatible gRPC backend after concurrent filesystem sharing, portable analysis reuse and validation are proved locally.
todos:
  - id: local-conformance
    content: Complete concurrent local publication and portable exact-snapshot analysis reuse first
    status: pending
  - id: transport-contract
    content: Specify namespace, immutable batch get/put, closure publication, candidate discovery and reader pin contracts
    status: pending
  - id: grpc-store
    content: Implement asynchronous bounded gRPC byte transport with cancellation, deadlines and idempotent retries
    status: pending
  - id: candidate-service
    content: Add multiversion candidate publication/discovery without last-writer-wins graph state
    status: pending
  - id: remote-lifecycle
    content: Define durable acknowledgement, reader leases, fenced GC, authentication and compatibility negotiation
    status: pending
  - id: remote-validation
    content: Run shared conformance and simultaneous two-machine worktree tests, including disconnection and lease expiry
    status: pending
isProject: false
---

# SkyCache gRPC transport

Revised 2026-10-19. This is phase F of the
[concurrent SkyCache roadmap](skycache_local_backend_f13e5f7f.plan.md), not a separate first
implementation. Filesystem sharing, portable values and source validation come first. The earlier
standalone KV service proposal is superseded; its original text remains in Git history.

## Preserve one semantic contract

The existing `FingerprintValueStore` is the byte-transport seam. The new
`ConcurrentFileFingerprintValueStore` establishes immutable local publication and live discovery
of bytes. gRPC must preserve that contract, with explicit bounded batches and asynchronous I/O.
Semantic keys and serialized records must not contain a local filesystem path or evaluator-local ID.

- A compatibility namespace covers repository identity, format/Bazel/codec versions, semantics,
  configuration, toolchains and platform. Reject unsupported or mismatched namespaces.
- `put` of the same key and bytes is idempotent. Different bytes for the same full identity are a
  conflict, not an overwrite. Commit IDs alone are not semantic cache keys.
- `get` distinguishes a validated value from missing or corrupt data. Optional reuse falls back to
  recomputation. A network cache failure must not silently lose authoritative evaluator state.
- Bound key/value sizes, batch count, total request bytes, outstanding RPCs and decoded resident
  bytes. Carry cancellation and deadlines through serialization and lookup tasks.
- A successful root/candidate publication means its referenced closure reached the required durable
  visibility boundary. Uploading arbitrary blobs is not enough to publish usable analysis.

Candidate metadata needs a separate publication/discovery abstraction where the byte interface is
insufficient. Keep multiple candidate versions per logical node. Readers must discover publications
made after their process starts, and validate dependency evidence before exposing a value locally.
Do not encode mutable Skyframe versions, reverse-edge ownership or command state in a shared KV row.

## Storage lifecycle

The initial local foundation intentionally does not delete entries. Remote reclamation requires
explicit reader pins/leases, publication ownership, fencing and crash recovery. A single fenced
collector is compatible with multiple writers; a single lifetime writer is not required.
Specify what happens if a reader's lease expires during a network partition before permitting GC.
Do not use uncoordinated LRU eviction for entries required by active readers or in-progress closures.

Local files, sealed packs or a remote object store may implement the service's durable layer later.
Redis/S3/GCS adapters are not prerequisites or part of the first filesystem milestone. Authentication,
authorization and namespace isolation are requirements before sharing across machines/users.

## Acceptance

Reuse the local backend's tests for distinct/identical concurrent publishers, conflicts, reader
visibility, corruption, partial publication and incompatible namespaces. Add process/server crash,
lost acknowledgement with retried put, disconnect during closure publication, cancellation, bounded
backpressure and lease-expiry cases.

Finally run two independent Bazel servers in separate worktrees on separate machines. Verify output
hashes and action keys against uncached analysis for same snapshots, repeated dirty-file edits,
nearby commits and mismatched configurations. Report rejected candidates, total memory, network
traffic and latency. Default enablement remains gated on correctness and measured benefit.