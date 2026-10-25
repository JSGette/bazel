# Remote SkyCache MVP

This branch adds a runnable remote metadata path to the local SkyCache implementation and
a persistent catalog to Bazel's existing Java REAPI worker. It uses the worker's existing
CAS and ByteStream implementations; a separate fork of bb-storage or bazel-remote is
unnecessary for this MVP.

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

The client downloads the first structurally complete candidate among at most 16 candidates.
It does not currently fetch further discovery pages or choose candidates by source revision.
The server supports signed, query-bound pagination stable across concurrent commits and
restarts. Discovery is advisory: an existing receipt does not promise that its CAS blobs
still exist.

A publication bundle names the complete transitive parent closure, in parent-first order.
Each publication contains its local manifest, sealed segmented index and packs, and
compact witness blocks. File names are a strict allowlist of basenames, never paths.
Each file is an ordered list of at-most-1-MiB SHA-256 chunks. This is complete-bundle
transfer, not selective metadata fetch or a new serialization format.

Bounds per bundle: 64 publications, 16,384 files, 32,768 chunk references, 256 MiB of file
bytes, and a 1 MiB bundle manifest. Per import/export, the client caps transferred blob
bytes at 257 MiB and shares one absolute deadline across all RPCs and candidate attempts.
Existing local metadata/decoded-witness budgets apply independently. Larger publications
are skipped; this MVP is not the large-tree performance solution.

The client stages files privately, checks CAS digests, local manifest identities/parents,
index file inventory, and witness integrity before exposing any publication. It installs
immutable objects and then manifests under the existing catalog lock and publication
leases. The ordinary local dependency validator still performs source/configuration
admission and per-entry checks. A successful remote commit does not bypass validation.
Output-file digests inside execution metadata are opaque to this transport; ordinary
remote execution/cache settings continue to control their retrieval and rewind recovery.

Publishing occurs after a successful local seal and its input revalidation. The export
pins that immutable publication and its parent closure against local collection.
Uploading does not require the live source tree to remain unchanged after the snapshot
was sealed: future readers validate the snapshot's evidence against their own inputs.

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

Use `staging-b` for the reader. Startup reports imported publication count; successful
exports report `Remote SkyCache: publication committed`. Existing `Local SkyCache`
statistics report actual admitted candidates and hits. A completed import alone does
not establish a cache hit.

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

Focused tests live in:

- `.../serialization/local:RemoteSkyCacheTest`: publication transfer and safe local installation.
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
Selective fetching, portable repository identities, explicit reader/primer policies,
production authentication and retention management remain follow-up work.
