# Remote SkyCache MVP

This branch adds a runnable remote metadata path to the local SkyCache implementation and
a persistent catalog to Bazel's existing Java REAPI worker. It uses the worker's existing
CAS and ByteStream implementations; a separate fork of bb-storage or bazel-remote is
unnecessary for this MVP.

The implementation is based on the parallel upstream integration `1aac2aa2ec`, which
merges Bazel `a5c50fcb65841b505ec72840d83381a704e70afa`. It includes the test-configuration
key fix, upstream serialization changes, and the `local-project-v4` namespace isolation.

The client and server are experimental. The client preserves the local cache's current
identity and validation rules. In particular, sharing is between linked worktrees of the
same Git repository/common directory, with compatible effective environments and
serialization versions. **Independent clones on different machines are not yet a supported
sharing boundary.** This limitation is deliberate: replacing the Git-common-directory
identity with a portable repository identity needs its own correctness work.

## Wire protocol

The new `src/main/protobuf/skycache.proto` defines
`build.bazel.skycache.v1.SkyCacheCatalog`. It imports the official REAPI `Digest`;
it does not fork or modify `remote_execution.proto`.

| Operation | Protocol and purpose |
| --- | --- |
| DiscoverPublications | Custom scoped catalog; newest-first immutable manifest digests |
| CommitPublication | Custom idempotent publication receipt after metadata CAS closure exists |
| Upload metadata | Unchanged REAPI BatchUpdateBlobs, SHA-256, identity compression |
| Download metadata | Unchanged google.bytestream.ByteStream/Read |

The client prepares admission metadata for at most 16 catalog candidates, retaining at most
64 unique publications within the metadata budget. The ordinary candidate loop can try
other prepared candidates when an entry is missing or its dependency proof is stale.
It does not currently fetch further discovery pages or choose candidates by source revision.
The server supports signed, query-bound pagination stable across concurrent commits and
restarts. Discovery is advisory: an existing receipt does not promise that its CAS blobs
still exist.

A publication bundle names the complete transitive parent closure, in parent-first order.
Each publication contains its local manifest, sealed segmented index and packs, and
compact witness blocks. File names are a strict allowlist of basenames, never paths.
Each file is an ordered list of at-most-1-MiB SHA-256 chunks. The bundle describes a
complete closure, but readers fetch pack contents selectively using that existing format.
No REAPI or SkyCache protobuf changes are required for demand-driven reads.

Bounds per bundle: 64 publications, 16,384 files, 32,768 chunk references, 256 MiB of file
bytes, and a 1 MiB bundle manifest. Per read session/export, the client caps transferred blob
bytes at 257 MiB. Reads share a cumulative RPC-time budget across discovery, candidates,
on-demand reads and any parent materialization; build computation between reads does not
consume it. Export retains one absolute deadline across its RPCs.
Existing local metadata/decoded-witness budgets apply independently. Larger publications
are still skipped: selective reading has not yet raised the wire format's logical-size bound.

The client prepares manifests, global witness blocks and root indexes in private scratch
storage. Segment indexes are fetched only when their hash bucket is searched; pack reads
fetch only the chunks intersecting a requested record. Chunk hashes, index checksums,
record keys and value checksums are all verified. Verified chunks are reused for the rest
of the invocation, including records spanning unequal chunk lengths.

Remote candidates remain separate from the durable local catalog. The ordinary local
dependency validator performs source/configuration admission and per-entry checks through
the same store interface. Changed workspace subtrees continue to invalidate their dependent
entries while allowing unaffected entries to hit. Global external/absolute-path witnesses
retain the local implementation's conservative publication-wide admission rules. A successful
remote commit does not bypass validation.
Output-file digests inside execution metadata are opaque to this transport; ordinary
remote execution/cache settings continue to control their retrieval and rewind recovery.

Publishing occurs after a successful local seal and its input revalidation. The export
pins that immutable publication and its parent closure against local collection.
Uploading does not require the live source tree to remain unchanged after the snapshot
was sealed: future readers validate the snapshot's evidence against their own inputs.

If new writes depend on remote parents, finish first materializes the **used** remote parent
closures, validates their complete local representation, and installs objects then manifests
under the existing catalog lock. Pins remain held through child publication/export, and input
revalidation runs after this I/O. Failure suppresses the new publication, not the build.
Pure readers never perform this full import. Remote-only analysis does not qualify as durable
local backing for idle graph eviction. Put deduplication probes only local candidates, avoiding
speculative downloads caused by ordinary serialization writes.

## Running

Build on Linux/WSL with the repository's supported Bazel/JDK toolchain:

```sh
bazel build //src:bazel-dev //src/tools/remote:worker
```

Start a local cache-only server; no execution work path is needed:

```sh
bazel-bin/src/tools/remote/worker \
  --listen_address=127.0.0.1 --listen_port=8091 \
  --cas_path=/absolute/path/skycache-server/cas \
  --skycache_catalog_path=/absolute/path/skycache-server/catalog \
  --skycache_instance_name=skycache-mvp
```

Use the resulting Bazel binary in two linked Git worktrees, with **different local staging
directories** outside either workspace:

```sh
/path/to/bazel-dev build //app:all \
  --experimental_strict_repo_env \
  --experimental_local_skycache=/absolute/path/staging-a \
  --experimental_remote_skycache=grpc://127.0.0.1:8091 \
  --experimental_remote_skycache_instance_name=skycache-mvp \
  --experimental_remote_skycache_timeout=60s
```

Use `staging-b` for the reader. Startup reports discovered publication count; successful
exports report `Remote SkyCache: publication committed`. Existing `Local SkyCache`
statistics report actual admitted candidates and hits. `Remote SkyCache transfers` and
`remote-skycache.json` in the output base report verified downloaded bytes/chunks and
materialized publication counts. Discovery alone does not establish a cache hit.

For TLS, use `grpcs://host:port`; the client uses normal platform trust roots. An optional
`--experimental_remote_skycache_token_file=/path/token` supplies a bearer token to all
catalog and CAS calls. The worker's existing TLS and test bearer-token options also apply
to the new service. The MVP does not integrate credential helpers, custom client CA/mTLS
options, proxies, or separate catalog/CAS endpoints.

Only trusted producers should have write access: serialized analysis metadata is trusted
build data. The worker remains a development server, not a production multi-tenant service.
Its CAS is shared across instance names; the new catalog accepts only the configured
instance. Distinct namespaces isolate discovery, not authorization or CAS storage.

## Server persistence and failure behavior

The catalog requires a trusted local POSIX filesystem supporting file locks, atomic rename,
and directory fsync. One process owns a catalog directory. Commit writes a durable,
authenticated receipt before replying. An identical scope/key/manifest returns its original
sequence even after CAS eviction; reusing the key for a different manifest is rejected.
Aliases of an already-published manifest retain its original sequence.

The server stores at most 4096 receipts, including aliases. It intentionally has no catalog
GC, TTL, leases on CAS blobs, replication, or quota administration. Back up the whole catalog,
including its owner-only signing key. Losing the key or corrupting receipts makes startup
fail closed. Use a new catalog directory for a fresh experiment.

Missing/corrupt metadata, unsupported services, timeouts, and backend outages cause warnings
and normal local SkyCache/build fallback. This implementation does not provide an offline
guarantee that every published remote candidate remains retrievable.

## Verification

Demand-reading verification on 25 October 2026 at implementation `1c46b460b637`:
both binaries build, and **248 test cases
across 19 test targets pass**. New loopback tests prove that preparation fetches no segment
indexes or pack chunks; lookup fetches the relevant segment index and first 1 MiB chunk
of a pack larger than 2 MiB, then repeated lookup makes no additional network read. Missing
unused chunks do not prevent a hit, missing/corrupt required chunks become misses, unequal
chunk boundaries work, and changed global witnesses still reject candidates. Successful
read-only finish installs no durable publication. Child-publication tests cover complete
used-parent materialization, unrelated-parent exclusion, local-only reopen and safe failure.
Already-local parents remain publishable after their remote CAS copy has been evicted.

The real-worker probe also passes with action caches disabled:

| Phase | Discovered publications | Configured-target hits | Downloaded bytes | Materialized publications |
| --- | ---: | ---: | ---: | ---: |
| Writer | 0 | 0 | 0 | 0 |
| Fresh reader | 1 | 7 | 87,559 | 1 |
| Restarted backend, fresh reader | 2 | 7 | 93,951 | 1 |
| Dependency BUILD mutation | 3 | 6 | 100,967 | 1 |
| Catalog retained, CAS emptied | 0 | 0 | 0 | 0 |
| Backend offline | 0 | 0 | 0 | 0 |

Every output matched its expected hash, including the changed BUILD rule. The empty-CAS
case fell back normally and published new metadata; the offline case built normally.
These are one-shot correctness measurements, not controlled speedup measurements.
The unchanged fresh reader still wrote nine small metadata entries and therefore imported
its used parent before publishing. This is direct evidence for prioritizing remote parent
descriptor reuse; demand-driven analysis reads alone do not eliminate finish-time downloads.

All fixture services stopped successfully. [Demand-read results](measurements/skycache-remote-demand-2026-10-25/results.json)
include binary/output digests; [test counts](measurements/skycache-remote-demand-2026-10-25/tests.json)
record each suite. A separate [failed restart-hit attempt](measurements/skycache-remote-demand-2026-10-25/restart-unavailable.json)
is retained: discovery returned `UNAVAILABLE` immediately after restart, the build correctly
fell back and published, and the probe stopped because it required a hit. Discovery currently
uses fail-fast RPC behavior; bounded wait-for-ready/retry is a follow-up reliability improvement.
The next complete run of the identical binary passed all six phases; no code or fixture
changes were made between the failed attempt and that rerun.
Full logs remain under
`/home/jgett/REPOS/.work/skycache-remote-mvp/` on the test host.
The [original full-import MVP results](measurements/skycache-remote-mvp-2026-10-25/results.json)
remain available separately (238 cases at implementation `c8325fda96c1`).

Focused tests live in:

- `.../serialization/local:RemoteSkyCacheTest`: selective reads, integrity, admission, complete
  publication transfer and safe parent installation.
- `.../serialization/remote:SkyCacheTransportTest`: actual gRPC transport, integrity, bounds,
  authentication, deadlines, and uncertain commit retry.
- `.../remote/worker:SkyCacheCatalogStoreTest` and `SkyCacheServerTest`: persistence,
  pagination, commit semantics, scope checks, CAS eviction and corruption.

`tools/skycache/remote_mvp_probe.py` runs actual builds with separate writer/reader
staging caches and a real worker, preserving logs and a JSON result:

```sh
python3 tools/skycache/remote_mvp_probe.py \
  --bazel /absolute/path/bazel-dev \
  --worker /absolute/path/bazel-bin/src/tools/remote/worker \
  --root /absolute/path/new-probe-directory
```

The probe and tests establish MVP correctness; no remote-cache speedup is claimed.
Portable repository identities, explicit reader/primer policies, production authentication
and retention management remain follow-up work.

## Remaining limitations and next implementation choices

1. **Publication after remote reuse still imports used parents.** The next useful optimization
   is exporting a child with authenticated remote parent descriptors, reusing existing CAS
   digests instead of downloading and reuploading those parents. That child needs either a
   separate remote publication path or a durable local representation that explicitly tracks
   remote backing. Writing a normal local manifest with missing local parents would break
   recovery and garbage collection. Acceptance: changed-subtree build publishes its new values
   without reading unrelated parent pack chunks, with CAS eviction remaining a safe miss.
2. **Logical size and transfer bounds remain coupled in version 1.** Separate a larger logical
   closure bound from the actual downloaded-byte budget, then support bounded streamed export
   and reuse existing CAS chunks. Keep independent metadata/count limits. Acceptance: a
   publication larger than 256 MiB serves a small dependency closure below the read budget;
   oversized metadata, actual byte exhaustion and malicious ranges still fail safely.
3. **Discovery is one bounded page.** Add lazy pagination on candidate exhaustion, bounded by
   invocation metadata/scan/network budgets, and order roots using target/configuration hints.
   Acceptance: a valid older candidate on the next page is found after newer candidates fail
   per-entry validation, without walking an unbounded catalog.
4. **Independent clones need more than a namespace override.** Introduce an explicit stable
   repository identity while retaining serializer/configuration/environment isolation; audit
   external repository bindings, typed paths and opaque absolute-path strings for relocation.
   Acceptance: different clone/output paths share unchanged analysis, while different repository
   contents or incompatible environments cannot produce false hits.
5. **External evidence remains conservative.** Move relocatable external witnesses into the
   per-entry dependency proof only after repository identity is stable. Preserve current global
   guards until tests prove changed external subtrees invalidate every affected entry.

The first item is the most direct next step for changed-subtree workloads. Protocol size and
independent-clone portability are separate compatibility changes, not reasons to weaken the
existing per-entry validator. The development server is sufficient for these correctness and
controlled bandwidth/latency experiments; production catalog retention and access control remain
separate server work.
