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

If new writes depend on remote parents, finish publishes a sealed child using the authenticated
parent descriptors already discovered. It uploads the new child's files and any locally backed
parents; remote parents keep their existing CAS digests. The private writer and local parent pins
remain held through commit. A scratch child manifest is never linked into the local catalog:
its parent closure is not fully present locally, so it cannot back idle graph eviction. Server
commit still checks the complete CAS closure; eviction suppresses publication without failing
the build. Input revalidation and all existing admission guards remain in force.

Put deduplication also checks already fetched remote indexes and complete records. Its cached-only
source methods cannot initiate RPCs, including when a cached file disappears between checking and
reading. They preserve ordinary lookup order and all integrity checks. An unavailable/corrupt
record causes a private write. Thus deduplication remains opportunistic: an unchanged reader can
still publish a small child when some serialized records were never fetched. Remote-only reads
and deduplicated puts conservatively disable local idle-retention backing.

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
materialized/reused publication counts. Discovery alone does not establish a cache hit.

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

## Initial demand-read verification

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

## Initial performance comparison

The initial demand-read implementation was **not an end-to-end speed improvement**. A controlled
loopback experiment on 25 October 2026 compared normal builds, the full-import client
(`c8325fda96c1`), and demand reads (`1c46b460b637`). Each binary primed its own frozen catalog
from identical source inputs, because their built-in-tool repository installation paths differ.
An initial cross-binary primer attempt was rejected by the existing `external/bazel_tools`
binding check and excluded from timing comparisons; no validation guard was relaxed.

The workload has 512 dependency packages with deterministic 32 KiB payloads, with a reader
requesting 16 packages plus an aggregation target. Priming produces about 19.4 MB of remote
metadata. The changed case edits one requested package's BUILD rule and verifies the changed
output. Both remote modes hit 17 configured targets unchanged and 16 after the edit.

Measurement conditions: warm Bazel JVMs, `bazel clean` before every measured invocation,
fresh client metadata directories, frozen per-binary primer catalogs, no action caches,
and warm OS file caches. Three repetitions per cell, rotating mode order, give 18 measured
builds; warmups are excluded. The host is WSL2 Linux 6.6.87.2 on an AMD Ryzen 9 9950X3D;
Bazel uses four jobs and a 2 GiB heap cap. A counting TCP proxy adds one local hop equally
to both remote modes, with no network shaping. Byte counts include gRPC framing.

| Input state | Normal build | Full import | Demand reads | Demand vs full |
| --- | ---: | ---: | ---: | ---: |
| Unchanged | 7.306 s | 8.243 s | 9.380 s | 13.8% slower |
| One changed subtree | 7.304 s | 9.438 s | 9.445 s | Effectively tied |

These are median complete client-command times, including publication after the build.
For unchanged inputs, full import ranged 8.114–8.404 s and demand reads 9.365–9.429 s.
For changed inputs their ranges overlap: 9.258–9.523 s and 9.429–9.568 s respectively.
This is a small synthetic experiment, not evidence about production-repository speedups,
WAN latency, or memory consumption. All 18 outputs were correct and all services stopped.

| Input state / client | Median wire download | Median wire upload | Private writes / deduplicated puts |
| --- | ---: | ---: | ---: |
| Unchanged / full import | 19,383,980 B | 7,609 B | 0 / 19 |
| Unchanged / demand reads | 19,395,382 B | 19,404,977 B | 19 / 0 |
| Changed / full import | 19,388,111 B | 19,440,115 B | 26 / 252 |
| Changed / demand reads | 19,395,753 B | 19,513,577 B | 277 / 0 |

That implementation saved **no meaningful download bandwidth** here.
The direct mechanism is visible in the counters and code: remote candidates are excluded
from put deduplication to avoid speculative reads. The unchanged demand reader consequently
writes 19 metadata records, materializes its used parent, then exports the complete closure.
The full-import reader deduplicates those 19 puts and publishes nothing. Its tiny upload count
is request traffic, not publication data. The changed case publishes a child in both modes,
so both reupload their parent closure. Selective-read unit tests prove the range-reader seam,
but the complete build lifecycle still defeats its potential bandwidth benefit.

This experiment identified the following work (the first two changes are implemented in
`91d51164d3d315dc2d79bbe3d40af874b9dae9e0`):

1. Deduplicate writes against **already fetched** remote indexes/records without triggering
   new network reads. Acceptance: unchanged readers do not publish solely because duplicate
   puts were skipped, while read-your-writes and immutable-key conflict checks remain intact.
2. Publish new children using authenticated remote parent descriptors and existing CAS digests.
   Acceptance: changed-subtree builds transfer new metadata without materializing/reuploading
   unrelated parent bytes, while eviction remains a safe miss.
3. Repeat this matrix, then test a real analysis-heavy repository and controlled network latency.
   Profile local chunk-cache I/O, hashing and memory separately before claiming improvements there.

[Raw timings and counters](measurements/skycache-remote-performance-2026-10-25/results.json),
[the excluded primer trial](measurements/skycache-remote-performance-2026-10-25/excluded-primer-trial.json),
and [its witness differences](measurements/skycache-remote-performance-2026-10-25/excluded-witness-diff.json)
are preserved. Reproduce with the committed harness:

```sh
python3 tools/skycache/remote_read_benchmark.py \
  --old-bazel /absolute/path/bazel-remote-mvp-tracing \
  --new-bazel /absolute/path/bazel-remote-demand-v2 \
  --worker /absolute/path/worker \
  --root /absolute/path/new-performance-directory
```

## Cached records and remote parent reuse

Implementation `91d51164d3d315dc2d79bbe3d40af874b9dae9e0` implements the first two
performance changes above. All **259 cases across 19 suites pass**, including tests for
cached-index/record disappearance, partial records, lookup precedence, integrity, immutable-key
conflicts and input revalidation. A mixed local/remote closure test evicts a local ancestor's CAS
pack, repairs it from the pinned local copy, reuses its remote child's exact descriptor, and opens
the resulting three-publication closure from a fresh remote reader. GC runs inside commit and
cannot reclaim pinned parents/private child; after finish it reclaims the orphan local objects.

The real-worker probe passes all six phases. Unchanged and restarted readers hit seven configured
targets, and a BUILD mutation hits six while producing the changed output. Remote-only children
commit without installing a local manifest or materializing parents. The small unchanged reader
deduplicates six of nine puts; three uncached records still become a small child. Empty CAS and
offline behavior remain safe fallbacks. This probe checks correctness, not timing.

[Tests](measurements/skycache-remote-parent-reuse-2026-10-25/tests.json) and
[real-worker probe](measurements/skycache-remote-parent-reuse-2026-10-25/probe.json) preserve
counts, output checks, transfer statistics and binary digests. The parallel optimization session
was integrating the earlier `6a380e6eeb` implementation while this change was tested separately;
the feature commit above is the additional integration point.

### Performance after parent reuse

A second 18-build matrix directly compares the previous demand reader (`1c46b460b637`)
with cached-record deduplication plus parent reuse (`91d51164d3`). It uses the same workload,
warm-JVM/clean-state protocol, per-binary primers, frozen catalogs, rotating order, three samples
per cell and loopback counting proxy described above. The normal arm uses the new binary with
SkyCache disabled. The shared `skyframe-measurements.lock` serialized this matrix after the
parallel Datadog benchmark, avoiding overlapping measurement workloads.

| Input state | Normal | Previous demand reader | Cached dedup + parent reuse | Time reduction |
| --- | ---: | ---: | ---: | ---: |
| Unchanged | 7.437 s | 9.706 s | 8.631 s | 11.1% |
| One changed subtree | 7.443 s | 9.784 s | 8.724 s | 10.8% |

These are median complete command times, including publication. Previous/current ranges are
9.695–10.052 / 8.404–8.676 s unchanged, and 9.613–10.060 / 8.690–8.881 s changed. All 18 outputs
matched; both remote versions hit 17 configured targets unchanged and 16 after the edit.
All fixture services stopped successfully. **Normal builds remain faster on this cheap-analysis
synthetic workload**; this demonstrates lower remote overhead, not a general build speedup.

| Input state | Wire download before / after | Wire upload before / after |
| --- | ---: | ---: |
| Unchanged | 19,401,341 / 16,711,850 B | 19,411,184 / 15,807 B |
| One changed subtree | 19,401,530 / 16,712,780 B | 19,519,816 / 67,830 B |

Downloads fall about **13.9%** and uploads by **99.9% unchanged / 99.7% changed**. Parent
materialization falls to zero; each current run reuses one remote parent descriptor. The remaining
private writes fall from 19 to 3 unchanged and from 277 to 29 after the subtree edit. The remaining
16.7 MB download illustrates fixed-chunk read amplification: useful serialized data is much
smaller, but selected records touch many 1 MiB chunks. Smaller fixed chunks and RPC batching
are a more immediate experiment than CDC for this access pattern. Neither WAN behavior nor
peak-memory improvements were measured here.

[Raw timings, counters, binary hashes and cleanup results](measurements/skycache-remote-parent-reuse-2026-10-25/performance.json)
preserve all samples. The harness's historical internal mode keys `full` and `demand` mean
reference and current respectively; `mode_labels` records their actual implementations. Reproduce:

```sh
flock /path/to/shared-measurement.lock python3 tools/skycache/remote_read_benchmark.py \
  --old-bazel /absolute/path/bazel-remote-demand-v2 \
  --new-bazel /absolute/path/bazel-remote-parent-reuse-v1 \
  --old-label demand-before-parent-reuse \
  --new-label cached-dedup-and-parent-reuse \
  --worker /absolute/path/worker \
  --root /absolute/path/new-performance-directory
```

## Direct comparison with local SkyCache and upstream Bazel

The 2026-10-25 follow-up uses the integrated client `6931630c83d9` for both local and
remote SkyCache and a separate upstream `a5c50fcb6584` binary for vanilla. Both use the
same embedded tools, and local/remote share the exact same prepopulated metadata.
The server's read paths are explicitly warmed outside the timer; new-publication writes
remain inside the measured command. Median complete command times, three samples each:

| Packages read / input | Vanilla Bazel | Local SkyCache | Remote SkyCache | Remote vs local |
| --- | ---: | ---: | ---: | ---: |
| 16 / unchanged | 7.351 s | 7.461 s | 8.499 s | +13.9% |
| 16 / one subtree changed | 7.380 s | 7.458 s | 8.540 s | +14.5% |
| 512 / unchanged | 8.136 s | 8.091 s | 12.209 s | +50.9% |
| 512 / one subtree changed | 8.193 s | 8.427 s | 13.148 s | +56.0% |

Local SkyCache is close to vanilla on this cheap-analysis synthetic workload; remote is
14–56% slower than local. The common repository-mapping phase takes about 7.1 seconds.
For the 512-package reader, combined analysis/execution is 0.859 / 0.890 s in vanilla,
0.841 / 0.974 s locally and 4.885 / 5.564 s remotely (unchanged / changed). These are
Skymeld combined phases, not exclusive analysis timings; most extra remote time occurs
before the first recorded genrule.

All 72 measured builds across cold-server and warmed-read-path matrices produced the
expected outputs and cache hits. The narrow remote reader still downloads about 16.7 MB
from a 19.4 MB primer, while changed-subtree uploads remain small. Backend startup alone
does not explain the gap. Loopback, synthetic metadata, registry access and three samples
limit generalization; no WAN, peak-memory or production-build speedup is established.
The six-target Datadog publication exceeds the current remote size bound, so its separate
local-only benchmark is not used as a remote comparison.

[Full method, transfer/phase tables, raw samples, traces and provenance](measurements/skycache-local-remote-vanilla-2026-10-25/README.md)
include reproducible vanilla/local/remote harness support and the optional server warm-up.

## Content-defined chunking

CDC is compatible with the current ordered, variable-length chunk descriptors; no proto change
is needed. The standard REAPI FastCDC profile uses normalization 2, minimum average/4 and maximum
average*4. Thus our 1 MiB hard chunk limit permits an average of at most 256 KiB; REAPI's recommended
512 KiB average would need a larger maximum. Optional REAPI chunk-mapping RPCs are unnecessary
for client-selected SkyCache chunks stored as ordinary CAS blobs.
[REAPI profile and services](https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/remote_execution.proto)

An offline experiment compared fixed 1 MiB/256 KiB/64 KiB chunks with FastCDC2020 averages of
256 KiB and 64 KiB over **108 distinct actual packs, 38,361,013 bytes**, from the initial benchmark.
The translated boundary routine passed both official REAPI test-vector seeds. Every algorithm
required exactly the same new pack bytes relative to its primer, in all three repetitions:

| Initial client / input | New pack bytes, all five algorithms |
| --- | ---: |
| Full import / unchanged | 0 B |
| Full import / changed subtree | 43,850 B |
| Demand reads / unchanged | 1,651 B |
| Demand reads / changed subtree | 96,096 B |

These are simulated unique payload bytes, not measured network transfer. Immutable parents were
identical and child packs were already small, leaving CDC nothing extra to deduplicate. Warm-memory
chunking plus SHA-256 took a median 14.60 ms with fixed 1 MiB chunks versus 24.82 ms with FastCDC
256 KiB over the entire corpus; these C/Python harness timings do not predict Java build latency.
A deliberately synthetic one-byte insertion reduced new bytes from 1,498,290 (fixed 1 MiB) to
319,696 (FastCDC 256 KiB), demonstrating resynchronization but not an observed build benefit.

**Keep CDC out of the production path for now.** First skip CAS blobs that already exist, then
measure smaller fixed chunks, realistic large-record changes and compaction/repacking. Compare
CDC with fixed chunks of the same nominal average so smaller transfer units are not mistaken
for a content-boundary benefit. Stable serialization/order, more CAS requests, larger descriptors,
chunking CPU and compression order all matter. Localized insertions are a good CDC use case;
arbitrary record reordering is much less favorable.
[FastCDC paper](https://www.usenix.org/conference/atc16/technical-sessions/presentation/xia)

[Detailed findings](measurements/skycache-remote-parent-reuse-2026-10-25/cdc/findings.md),
[raw metrics](measurements/skycache-remote-parent-reuse-2026-10-25/cdc/results.json), and
[reproduction instructions](measurements/skycache-remote-parent-reuse-2026-10-25/cdc/README.md)
include pinned primary sources, input hashes, synthetic-control limitations and the licensed
algorithm translation. No CDC production code or wire-format change was introduced.

## Remaining limitations and next implementation choices

1. **Uncached/local uploads still send every chunk.** Remote-parent descriptor reuse removes
   repeated parent transfer, but new local files still use singleton BatchUpdateBlobs calls.
   Batch FindMissingBlobs and skip existing chunks before considering CDC; measure RPC overhead
   and use bounded batches. Compare smaller fixed chunks for demand-read amplification. Cached
   remote chunks are reread/hashed from disk, so profile that path before adding a memory cache.
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

The first item is the next transfer optimization to measure. Protocol size and
independent-clone portability are separate compatibility changes, not reasons to weaken the
existing per-entry validator. The development server is sufficient for these correctness and
controlled bandwidth/latency experiments; production catalog retention and access control remain
separate server work.
