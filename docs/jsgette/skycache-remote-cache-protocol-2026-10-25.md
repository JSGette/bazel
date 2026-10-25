# Remote SkyCache protocol v1

**Decision: reuse unmodified REAPI CAS and ByteStream for metadata bytes, and add a
separate two-RPC SkyCache catalog for discovery and publication.** Keep source
validation, serialized-value interpretation and output recovery in Bazel. The
implementation and loopback server are complete enough to freeze this experimental
v1 contract; further performance tuning is separate work.

| Design record | Value |
| --- | --- |
| Owner | JSGette/Bazel fork |
| Last major update | 25 October 2026 |
| Status | Finalized fork proposal; implemented experimentally; not submitted or approved upstream |
| Lead reviewer / upstream discussion | Unassigned / not opened |
| Implementation reviewed | [`0f8d49f6c611`](https://github.com/JSGette/bazel/commit/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee) |
| Upstream Bazel baseline | [`a5c50fcb6584`](https://github.com/bazelbuild/bazel/commit/a5c50fcb65841b505ec72840d83381a704e70afa), verified current master on 25 October 2026 |
| Follow-on work | [Upstream extraction plan](skycache-upstreaming-2026-10-25.md) |

This replaces the original speculative proposal. The checked-in
[skycache.proto][proto] is the wire-schema authority; [SkyCacheProtocol][protocol],
[catalog][catalog], [server][server] and [transport][transport] define the implemented
profile below. The [MVP guide][mvp] contains commands, flags and historical test and
performance evidence. The limits described here are those of this implementation,
not universal REAPI limits.

## Scope and REAPI reuse

SkyCache stores serialized graph values and dependency evidence in immutable local
publications. The same logical SkyKey fingerprint can resolve to different values
in different publications. It is therefore not a CAS digest. A remote publication
must preserve its own key-to-record mappings and parent closure, then validate
source evidence before a restored value becomes a hit.

The data plane uses ordinary REAPI `Digest`,
`ContentAddressableStorage/BatchUpdateBlobs` and
`google.bytestream.ByteStream/Read`. SkyCache defines its own file,
`src/main/protobuf/skycache.proto`, in package `build.bazel.skycache.v1`, importing
the official `remote_execution.proto`. **No copy, modification or new fields in the
REAPI proto are needed.** The backend extends Bazel's existing Java remote worker
with the catalog service, reusing its CAS, ByteStream, TLS and interceptors.

| Responsibility | v1 choice | Reason |
| --- | --- | --- |
| Immutable metadata bytes | Standard REAPI CAS plus ByteStream | Established hash/size addressing and storage implementation |
| Multiple candidate publications | Custom `SkyCacheCatalog` | CAS has no listing; ActionCache has one result per action digest and no conditional append |
| Source validity and reference resolution | Existing local SkyCache formats and validator | A digest authenticates bytes, not their applicability to current inputs |
| Execution output bytes | Existing Bazel remote execution/cache path | Preserve deferred downloads and missing-output rewind |
| Durable local reuse of downloads | Verified local chunk cache | Save downloads without pretending a partial publication is a complete local backing |
| Metadata retention | Best-effort CAS availability | Standard REAPI provides no recursive pin for references hidden in SkyCache blobs |

These distinctions follow the [REAPI CAS and ActionCache contracts][reapi]. A
CAS-only exchange of an externally supplied bundle digest is possible. A restricted
ActionCache adapter could publish a manifest as a declared output of a genuine
canonical Action, whose actual serialized digest is the AC key. Neither supplies
transparent multi-candidate discovery or concurrent append. A changing candidate
list under one synthetic Action key loses updates without additional coordination.
Remote Asset naming likewise needs a separate discovery/concurrency policy. These
are alternative adapters, not implemented v1 modes.

## Identifiers and compatibility

Four identifiers have different meanings:

1. `Scope = (instance_name, namespace_id)` selects catalog entries. Namespace is
   exactly 64 lowercase hexadecimal characters. Instance is empty or slash-separated
   `[A-Za-z0-9_-]+` components, at most 128 characters. The MVP worker accepts only
   its configured instance.
2. A local publication `id` is an opaque 64-character lowercase hexadecimal ID.
   The producer currently hashes a random UUID; this ID is **not** a content digest.
3. REAPI `Digest{hash, size_bytes}` identifies exact bytes using SHA-256, lowercase
   64-character hexadecimal hash and exact byte length. There is no extra hash prefix.
4. `PublicationRef.manifest` identifies the serialized **PublicationBundle** in CAS.
   It does not identify the local publication file named `manifest`.

There is no canonical protobuf-serialization requirement in v1. Different encodings
of equivalent bundles can have different digests. Local IDs and parent descriptors
must remain consistent; the client rejects conflicting descriptions of a previously
seen publication ID.

The actual namespace includes `local-project-v4-content-sha256`, native byte order,
the real Git common-directory path (or real workspace path without Git), and sorted
effective environment values. Those include repository/action/test environments,
allowed yanked versions, and execution-cache/executor/instance settings. Download
policy is excluded so `minimal` and `toplevel` can reuse the same metadata.
[Namespace construction][snapshot] and [environment inputs][module] are authoritative.

**Independent clones and different machines are not a supported sharing boundary.**
Linked worktrees of the same repository can share with compatible configuration and
serialization. Portable repository identity, root relocation and codec compatibility
need their own audit and namespace migration; matching Git revisions are insufficient.

## Public schema and immutable bundle

The exact field numbers and types are in [skycache.proto][proto]. There are two unary RPCs:

```proto
service SkyCacheCatalog {
  rpc DiscoverPublications(DiscoverRequest) returns (DiscoverResponse);
  rpc CommitPublication(CommitRequest) returns (PublicationRef);
}
```

| Message | Fields in wire order |
| --- | --- |
| `Scope` | `string instance_name`, `string namespace_id` |
| `PublicationRef` | REAPI `Digest manifest`, `int64 catalog_sequence` |
| `DiscoverRequest` | `Scope scope`, `int32 page_size`, `bytes page_token` |
| `DiscoverResponse` | repeated `PublicationRef candidates`, `bytes next_page_token` |
| `CommitRequest` | `Scope scope`, REAPI `Digest manifest`, `string idempotency_key` |
| `PublicationBundle` | `uint32 format_version`, `string namespace_id`, `string root_id`, repeated `LocalPublication publications` |
| `LocalPublication` | `string id`, repeated `string parent_ids`, repeated `PublicationFile files` |
| `PublicationFile` | `string name`, `int64 size_bytes`, repeated REAPI `Digest chunks` |

`CatalogReceipt` is a server-private persistence message, not another public RPC.
There are no revision hints, capability negotiation, leases or logical-key lookup RPCs.

A bundle describes the complete transitive parent closure of its root, parents
before children. Every publication ID is unique; each parent's ID precedes its child
and occurs at most once in that child's parent list. Every publication must be
reachable from the root. Missing roots, cycles, missing parents and unrelated
publications are invalid.

Each publication contains unique basenames matching exactly:

```text
manifest|index|segment-[0-9]{5}\.(index|pack)|witnesses-[0-9]{5}\.block
```

Both `manifest` and `index` are mandatory. File paths and symlinks cannot be named.
Ordered chunk lengths must sum to the declared file size. Empty files with no chunks
are structurally legal; referenced chunks must have positive lengths. Repeated chunk
digests are legal. The file contents use the existing sealed local format, including
segmented indexes/packs and compact witness blocks; the envelope does not define a
new Java object serialization.

| Structural bound | v1 value |
| --- | ---: |
| `format_version` | Exactly 1 |
| Serialized bundle bytes | 1 MiB maximum |
| Publications per bundle | 1–64 |
| Files across the whole bundle | 16,384 maximum |
| Chunk references across the whole bundle | 32,768 maximum |
| Bytes per referenced chunk | 1–1,048,576 |
| Sum of logical file sizes across the whole bundle | 4 GiB maximum |

Logical sizes count repeated references, rather than only unique CAS bytes. Bundle
namespace must equal the request namespace. These are structural envelope checks:
they do not establish source validity or correctness of serialized SkyValues.
Bundles reference SkyCache metadata only. Output digests inside execution values
remain opaque; the server does not parse chunk contents to enforce this distinction.

## Discovery

`DiscoverPublications` returns distinct published bundle digests, newest first by
their original positive catalog sequence, within the requested scope. Page size zero
selects 16; explicit values must be 1–64. Oversized values are rejected, not clamped.
Discovery neither checks CAS existence nor refreshes blob retention.

The first page captures a receipt-count high-water mark. The opaque HMAC-SHA256 token
binds token version, scope, normalized page size, high-water mark and cursor. Later
commits cannot enter that pagination snapshot. Tokens survive server restart with
the same catalog and signing key. Malformed, mismatched or unauthenticated tokens
return `INVALID_ARGUMENT`. The implementation accepts token sizes greater than 32
bytes and at most 16 KiB. There is no expiry/TTL contract in this version.

The current client requests only the first page of 16 candidates. It validates
decreasing positive sequences and bounded digests, prepares candidates within its
resource budget and retains at most 64 unique local publications. The normal candidate
loop may try another prepared candidate after a miss or stale dependency proof. It
does not paginate further or rank candidates by source revision.

## Commit, retries and persistence

The writer sends a scope, bundle digest and nonempty idempotency key of at most 4096
Java string characters. The current producer uses the bundle's SHA-256 hash as the
key; the server does not require hex or ASCII keys.

For a previously unseen `(scope, key)`, the server:

1. Fetches the bounded bundle and verifies its exact byte length and SHA-256 digest.
2. Parses and validates its namespace, version, structure and bounds.
3. Checks existence and refreshes mtime for every distinct referenced chunk.
4. Durably appends the authenticated catalog receipt before replying.

The chunk checks do **not** hash all chunk contents or acquire a GC lease. They are
observations of availability, not a transaction over the CAS closure. A successful
receipt can outlive any of its blobs. Clients must still authenticate downloaded
bytes and treat missing required metadata as a miss.

The same `(scope, key)` and same manifest hash/size return the original receipt
before CAS I/O, including after eviction. Rebinding that key to a different digest
returns `ALREADY_EXISTS`. A new key for an existing manifest rechecks the closure
and consumes another receipt, but retains the original publication sequence and
does not add another discovery entry. Unknown protobuf fields do not enter this
idempotency comparison. The client retries commit once on `UNAVAILABLE` with the
identical request and deadline; other application-level retries are not implemented.

The MVP stores at most 4096 receipts globally, including aliases, with at most 64 KiB
per receipt payload plus a 32-byte signature. One process owns the catalog directory.
It requires a trusted POSIX filesystem with file locking, atomic rename and directory
fsync. Receipts are signed, fsynced and renamed, followed by directory fsync, before
in-memory indexing and reply. An uncertain persistence failure makes the live store
unusable until reopening/replay. Replay rejects missing sequence numbers, corrupt
signatures, conflicting bindings and malformed requests. The 32-byte signing key
requires mode `0600`; losing it in a nonempty catalog fails startup. Backups need the
whole catalog including that key. There is no catalog GC, TTL, replication or CAS lease.

| Condition | Catalog/server status |
| --- | --- |
| Invalid scope/instance, page/token, key/digest or bundle encoding/structure | `INVALID_ARGUMENT` |
| Existing key bound to another digest | `ALREADY_EXISTS` |
| Receipt capacity exhausted | `RESOURCE_EXHAUSTED` |
| Missing bundle or referenced chunk | `FAILED_PRECONDITION` |
| Bundle bytes disagree with declared size/digest | `DATA_LOSS` |
| Commit interrupted | `CANCELLED` |
| Other catalog/CAS I/O failure | `UNAVAILABLE` |
| Missing/wrong configured worker bearer token | `PERMISSION_DENIED` |

The client reports status codes while suppressing arbitrary remote error text.
Failures produce warnings and ordinary cache/build fallback, not an authorization to
use an unvalidated value.

## Transfer, validation and local retention

Uploads use one identity-compressed item per REAPI `BatchUpdateBlobs` call, explicitly
selecting SHA-256, at most 1 MiB per item. The client checks the item's status and its
digest when present; it accepts an omitted successful digest for compatibility with
the current test worker. Downloads use ByteStream resource
`[instance/]blobs/{hash}/{size}` with zero offset and limit. The whole chunk is fetched,
bounded and hashed. A record range selects intersecting complete chunks; it is not
an unauthenticated ByteStream subrange.

The implementation does not call `GetCapabilities`, `FindMissingBlobs`, `BatchReadBlobs`,
ByteStream Write/QueryWriteStatus, ActionCache, Execution or SplitBlob/SpliceBlob.
It assumes the configured endpoint supports the SHA-256/identity profile and payload
size. Incompatible endpoints fail safely; universal third-party server interoperability
has not been established. Negotiating capabilities and reusing Bazel's fuller remote
client facilities are follow-on integration work.

Readers initially authenticate local manifests, global witness blocks and root
indexes in private scratch storage. Segment indexes load when their bucket is searched;
pack chunks load when a record requires them. Index checksums, record keys and value
checksums remain enforced. Referenced objects resolve through the originating
publication and its declared parents, not an unrelated candidate selected to fill a
missing dependency. The local validator checks the evidence needed by each entry.
Changed workspace subtrees invalidate dependent entries while unrelated entries can
hit. External/absolute-path witnesses retain conservative publication-wide admission.

Downloaded chunks are retained under the local SkyCache directory at
`leased-v2/remote-chunks-v1/sha256`. Each invocation creates a private hardlink pin
and verifies the retained file's full digest and length before exposing it. Subsequent
range reads use invocation-local authentication for covering 4 KiB blocks. Corrupt
retained entries found during activation are discarded; ordinary reads can refetch
when activation is unavailable. Later range-authentication failures become cache
misses, and cached-only calls never fetch. The default
soft quota is 5 GiB and 65,536 entries, pruned after command close by last use. Active
or abandoned hardlink pins and concurrent writes can exceed those soft limits.
Setting `--experimental_remote_skycache_local_cache_size=0` disables retention.

This is a chunk cache, not an offline catalog or automatic promotion of partially
hydrated publications into the durable local catalog. Cached-only deduplication
cannot initiate RPCs. Remote-only backing conservatively disables local idle graph
eviction. Sealed readers release bucket/segment initialization locks before record
I/O, allowing unrelated reads to proceed concurrently.

Writers revalidate the source and inherited evidence of successful local work, then
seal before export. The immutable publication and local parents remain pinned through
upload and commit. Upload local files, then bundle, then commit. Remote parent
descriptors can be reused without downloading or reuploading their chunks; the server
still checks their availability on a new commit. A child whose parent closure is not
fully local remains private to remote export and is not inserted in the local catalog.
The source tree need not remain unchanged during upload after a valid seal: future
readers validate that sealed evidence against their own inputs. This retains the
existing stable-input assumption while building, not a transactional filesystem snapshot.

Execution-output digests stay within existing execution metadata. Ordinary remote
execution/cache settings control downloads, `--noremote_accept_cached`, and recovery.
`minimal` and `toplevel` preserve their normal output behavior. Missing required output
blobs invoke existing rewind and stale-candidate exclusion; unused missing outputs
need no recovery. SkyCache commit never checks the execution-output CAS closure.

## Client resource and deployment profile

| Client control | Current default and semantics |
| --- | --- |
| Attempted blob-byte budget | 257 MiB per read session/export; configurable from 1 byte to 4 GiB + 1 MiB |
| Read timeout | 60 seconds of aggregate active-call time, including local per-call response validation |
| Export timeout | One 60-second absolute deadline |
| Transport concurrency | 100 shared across RPC services; configurable 1–1000 |
| Download / cached-read concurrency | Separate bounds, each using that same configured value, default 100 |
| Explicit parent materialization | Additional cumulative 4 GiB logical-write limit |

Attempted upload bytes and declared download lengths are reserved atomically;
failures are not refunded. Catalog payloads do not consume the blob-byte budget.
Overlapping calls each consume active time; queued callers and intervening build
computation do not add a separate time charge. More concurrency does not multiply
the budgets. Local decoded-metadata/validation limits apply independently. Reaching
a limit is a miss/fallback, never a reason to skip validation.

The endpoint is `grpc://host:port` or `grpcs://host:port`, with an explicit port and no
path, user information, query or fragment. TLS uses normal platform roots. An optional
token file supplies a bearer token to catalog and CAS calls: at most 8192 bytes,
trimmed nonempty printable ASCII, no CR/LF. REAPI request metadata provides a generated
invocation UUID and action ID `skycache-metadata`. Credential helpers, custom client
CA/mTLS options, proxies and distinct catalog/CAS endpoints are not integrated.

This backend is a development worker for trusted producers. Its CAS is shared across
instance names; instance/namespace selection is not multi-tenant authorization.
A digest proves bytes, not producer honesty or source assertions. Production use
requires access control and resource/retention administration beyond this prototype.

## Compatibility, evidence and deferred work

Keep the v1 fields and their meanings stable. Unknown protobuf fields are not a
negotiation mechanism. Incompatible envelope semantics need a new format/service
version; changes in local serialized-value compatibility need namespace isolation.
Larger accepted bounds require compatible readers/servers. The earlier 256 MiB
server/client bound rejects/skips larger publications safely; upgrade servers first.
Rollback retains smaller readable publications and treats oversized ones as misses.

Fixed chunks of up to 1 MiB are the current writer policy. The reader already accepts
unequal positive chunk lengths, so client-side content-defined chunking could be
evaluated without changing the envelope, provided all reference/size limits hold.
CDC, compression, REAPI chunking extensions, prefetch and further lock/cache tuning
are deferred; none is required for v1 correctness.

The [retained-chunk evidence][evidence] records 612 passing test cases across 22 test
targets and 24 measured fresh-JVM builds on six Datadog agent targets. All six warm
remote runs downloaded zero blob bytes. Warm remote medians were 26.17 s (`minimal`)
and 33.78 s (`toplevel`), versus 14.27/25.22 s for local SkyCache and 13.32/25.63 s for
vanilla Bazel. These are three samples per arm/policy on loopback with a warm OS cache,
not a WAN or independent-machine result. Remote overhead remains; this proposal does
not claim a speedup over local SkyCache or vanilla Bazel.

Existing tests cover catalog replay/idempotency/pagination, corrupt or missing chunks,
partial availability, demand reads, source changes, parent descriptors, concurrent
reads, retained-file corruption and normal fallback. Finalizing this document reruns
no performance experiment and changes no runtime behavior.

Before broad deployment or upstreaming the full feature, resolve:

- Portable repository/root identity and serializer compatibility across clones/hosts.
- Capability negotiation, standard authentication integration and real cache-server
  interoperability tests beyond the Java worker.
- Production scope authorization, catalog retention/replication and any real CAS pin
  contract; a catalog row alone cannot provide a lease.
- Selective external-witness admission with equivalent dependency evidence, and
  better candidate selection/pagination when justified by measurements.
- Upstream review of the local publication/validation foundation, followed by the
  remote catalog/profile and client integration. The independent fixes in the
  [extraction plan](skycache-upstreaming-2026-10-25.md) can move separately.

[proto]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/src/main/protobuf/skycache.proto
[protocol]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote/SkyCacheProtocol.java
[transport]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote/SkyCacheTransport.java
[catalog]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/src/tools/remote/src/main/java/com/google/devtools/build/remote/worker/SkyCacheCatalogStore.java
[server]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/src/tools/remote/src/main/java/com/google/devtools/build/remote/worker/SkyCacheServer.java
[snapshot]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalInputSnapshot.java
[module]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalSkycacheModule.java
[mvp]: https://github.com/JSGette/bazel/blob/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/docs/jsgette/skycache-remote-mvp.md
[evidence]: https://github.com/JSGette/bazel/tree/0f8d49f6c611e1e0735c4c19f85f10c36dacc9ee/docs/jsgette/measurements/skycache-retained-chunks-agent-2026-10-25
[reapi]: https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/remote_execution.proto
