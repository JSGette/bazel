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

Bounds per bundle: 64 publications, 16,384 files, 32,768 chunk references, 4 GiB of logical file
bytes, and a 1 MiB bundle manifest. Per read session/export, the client caps attempted blob
bytes at 257 MiB by default. Set `--experimental_remote_skycache_max_transfer_size=2G`, for
example, to export a larger publication or allow a larger demand read. The setting must be
positive and at most 4 GiB + 1 MiB; failed reads consume the budget too. Reads share a
cumulative RPC-time budget across discovery, candidates,
on-demand reads and any parent materialization; build computation between reads does not
consume it. Export retains one absolute deadline across its RPCs.
Downloads and cached range reads each default to **100 concurrent operations**. Override both
with `--experimental_remote_skycache_max_concurrency` (1–1000). Transport RPCs share the same
limit across services. Concurrent RPC durations consume the shared cumulative time budget;
queued and idle time do not add another charge. Attempted bytes are reserved atomically, including
failed attempts. Concurrency does not multiply either budget. Successful requests for the same
missing chunk share one download; unrelated chunks can proceed independently.
Existing local metadata/decoded-witness budgets apply independently. Exports still stream
1 MiB chunks; a larger logical publication does not allocate a correspondingly larger buffer.
Explicit parent materialization also has a cumulative 4 GiB logical-write budget, charged
before creating staged files even when an attempt fails.

These bounds do not change the version-1 wire schema. Upgrade servers before clients that
publish more than 256 MiB. Older servers reject those commits and older clients skip those
publications safely; smaller existing publications remain readable. Rollback needs no data
conversion, but publications above the older bound become cache misses.

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
At the time of this synthetic matrix, the six-target Datadog publication exceeded the remote
size bound. Its separate local-only benchmark was not used as a remote comparison.

[Full method, transfer/phase tables, raw samples, traces and provenance](measurements/skycache-local-remote-vanilla-2026-10-25/README.md)
include reproducible vanilla/local/remote harness support and the optional server warm-up.

## Larger publications: Datadog agent validation

Implementation `b7cf340bf0815086cfa213f4a8bd85f05bd35654` separates the logical publication
bound from attempted transfer bytes. The logical parent closure now permits **4 GiB**;
the actual transfer default remains **257 MiB**, with an explicit configurable budget.
Chunk, bundle, metadata and count limits remain independent. No protobuf schema change
is required. Upgrade the server before publishing above the previous 256 MiB bound.

All **573 cases across 21 test targets pass**. New coverage includes the exact 4 GiB
logical boundary, overflow/over-limit rejection, missing CAS chunks, transfer exhaustion
and reset, budgets above 2 GiB, and a 1.5 GiB logical publication read with less than
4 MiB transferred. Explicit materialization rejects excessive cumulative logical writes
before creating files, including repeated failed attempts.

The real-worker test builds these Datadog targets at
`0cde96beb70963d0838119c308b9d0422ee66be0`: `agent`, `cluster-agent`, `process-agent`,
`trace-agent`, `security-agent` and `system-probe` under `//cmd`. A private linked worktree,
resolved dependency lockfile and common embedded tools are shared by all arms. The original
agent checkout and its existing lockfile are unchanged. Local/remote use the new client;
vanilla uses upstream `a5c50fcb65841b505ec72840d83381a704e70afa`.

The first empty-cache build successfully uploads and commits a roughly **1.51 GB** publication
in **106.25 s including the build and publication**. Further preparation produces a frozen
two-publication cache containing **1,530,554,980 B** of immutable objects/manifests. The runs use:

```sh
--experimental_remote_skycache_max_transfer_size=2G \
--experimental_remote_skycache_timeout=600s
```

Each timed reader starts with a fresh JVM, synchronous clean, zero Skyframe nodes/edges,
empty execroot and no local action cache. Local readers receive the identical frozen seed;
remote readers start with empty scratch storage and the matching frozen remote catalog.
All arms use the same Cache & Carry action cache/BES, no disk cache, 16 jobs and a 6 GiB heap.
A restarted loopback backend receives one full read/idempotent-commit warmup outside each
remote timer. Client startup/clean and backend warmup are excluded; command completion and
publication are included. No WAN latency or bandwidth shaping is applied.

**One controlled observation per cell, not medians.** The planned repeated matrix was
shortened after the first-use remote read took 599.05 s. These measurements are exploratory;
action-cache/network variability and fixed arm order limit precise ratio estimates.

| Output policy | Vanilla Bazel | Local SkyCache | Remote SkyCache |
| --- | ---: | ---: | ---: |
| minimal | 13.37 s | 14.17 s | 457.71 s |
| toplevel | 26.42 s | 30.42 s | 451.52 s |

All six final binary digests match in every completed build. `toplevel` also verifies the
six downloaded files against those digests; `minimal` leaves the binaries remote. Both
SkyCache arms hit **17 configured targets and 11 execution entries**, perform no
ActionCache/GetActionResult RPCs, and create no intermediate `.a`/`.x` files. Minimal
SkyCache builds make no output ByteStream reads. There are no transfer-limit skips,
cache failures or remote transport warnings. Local readers write zero entries.

| Output policy | Remote metadata wire download | Remote metadata wire upload | Remote private writes |
| --- | ---: | ---: | ---: |
| minimal | 1,500,515,038 B | 10,514,799 B | 15 |
| toplevel | 1,500,516,037 B | 10,514,842 B | 15 |

The prepared minimal reader delivers **244,617,077 B of cached value bytes**, yet downloads
nearly the entire 1.53 GB publication. Its fresh Bazel process records **827,313,019,557 B**
of `rchar`, with **zero physical `read_bytes`**. These counters include startup/clean and
all read-like I/O; they are neither physical disk traffic nor network payload. Source
inspection of this baseline shows that every cached range read rereads and SHA-256-checks the whole chunk
under synchronization before returning a slice. This is strong evidence of repeated local
read/hash amplification, not exclusive CPU attribution from a sampling profiler.

The minimal profiles place **446.47 s** in the remote combined analysis/execution phase,
versus **12.23 s** locally and **11.71 s** in vanilla. Remote setup before the command is
only 2.05 s. Skymeld combines analysis and execution here; these are not exclusive analysis
times and overlapping spans must not be added. Client/worker memory samples are preserved
separately; independently sampled peaks must not be summed and exclude filesystem page cache.

The initial one-publication, first-use minimal pilot was 13.77 / 35.64 / 599.05 s
(vanilla/local/remote). It required a refresh publication and is kept separate from the
prepared comparison. The prepared minimal harness then stopped on an overly strict
zero-write assertion: the remote build and output checks passed, but it wrote 15 records.
An offline checksum/key/value audit proves all 15 already exist byte-identically in the
parent (568 value bytes total). This is consistent with cached-only put deduplication;
exact chunk availability at put time was not retained. The toplevel continuation records
remote writes without requiring zero. Its writes were not independently audited for duplication.
Both earlier harness outcomes remain in the raw evidence rather than being rewritten as passes.

The larger bound removes the functional blocker, but this baseline leaves remote SkyCache
well behind local and vanilla Bazel. It motivated the authenticated block-read implementation
below. Batched FindMissingBlobs can then skip existing uploads, and smaller fixed chunks can
address demand-read amplification; CDC does not by itself remove the repeated cached-read
verification demonstrated here.

[Comparison and phase/transfer counters](measurements/skycache-large-agent-2026-10-25/comparison.json),
[verification and provenance](measurements/skycache-large-agent-2026-10-25/verification.json),
[duplicate-record proof](measurements/skycache-large-agent-2026-10-25/agent_duplicate_records.json),
and [logs, profiles and test XML](measurements/skycache-large-agent-2026-10-25/logs-profiles-tests.tar.gz)
preserve the results. The evidence directory includes exact environment-specific harnesses,
all raw run reports and SHA-256 checksums. Source, frozen local seeds, catalog receipts,
original CAS and prior reports were checked after the continuation; all fixture services stopped.

## Optimization plan and first implementation

1. **Remove repeated cached-read amplification (implemented).** After verifying a complete CAS
   response against its SHA-256 digest, derive SHA-256 hashes for 4 KiB blocks. Cached range reads
   read and authenticate only covering blocks. Retain hashes, not payloads, in an invocation-owned
   LRU with a 32 MiB weight budget including a per-entry overhead allowance. Eviction requires a
   whole-chunk digest check before rebuilding trusted hashes. File size, missing-file, symlink,
   record-key, record-checksum and index checks remain. Corruption in an unread block is detected
   when that block is requested; every returned byte remains authenticated. No wire-format or
   server change is required. Counters expose cached bytes read, hash-cache weight and evictions.
2. **Measure and reduce the remaining serialized I/O (implemented).**
   Downloads and cached range reads now each default to 100 concurrent operations. Hash-cache
   locking covers only map operations; successful concurrent requests for one missing chunk share
   the downloaded file. Transport accounting sums active RPC durations and reserves attempted
   bytes atomically. Exhaustion cancels outstanding calls, including replies that beat the budget
   timer. Tests cover configured/default limits, duplicate fetches, queued interruption, reset
   rejection, failed-transfer accounting and close/cancellation. The measurements below compare
   the earlier eight-reader/single-download implementation with the new default.
3. **Deduplicate and batch uploads.** Use bounded REAPI `FindMissingBlobs` requests before
   `BatchUpdateBlobs`, retaining complete descriptor validation and atomic catalog commit. Compare
   an unchanged repeat, a changed subtree, and an empty server. An unchanged blob should transfer
   no payload, and a missing parent or failed upload must prevent commit. This also avoids
   transferring already-present local parent chunks when publishing a child.
4. **Reduce demand-transfer amplification, then evaluate CDC.** Compare fixed 1 MiB and 256 KiB
   chunks on the same frozen publication, counting payload, RPCs, descriptor size and total wall
   time. Keep the existing 1 MiB chunk maximum and 1 MiB bundle bound; a 64 KiB experiment on a
   multi-GiB closure may need a separately designed bounded descriptor layout. Compare CDC with
   fixed chunks of the same average size only after testing realistic changed records and
   repacking. Adopt it only if saved transfer outweighs chunking CPU and extra RPC/metadata costs.

The first implementation has five new regression tests: repeated/boundary ranges, rewritten
value plus embedded checksum, deferred detection of unread-block corruption, missing/truncated/
symlinked scratch, and eviction followed by successful or rejected whole-chunk reauthentication.
The build and all 578 cases across 21 test targets pass (13 targets executed, eight cached).

Bazel's installation digest is part of `FrontierNodeVersion`, so a new binary requires its own
prepared analysis entries. The first cross-binary pilot correctly missed every old analysis key;
it is excluded from performance conclusions. The new binary's prepared local and remote arms
must share the same frozen metadata, and their six binary digests must match vanilla. Do not
disable installation identity to manufacture cache hits in a production benchmark.

### Agent repository measurements after block authentication

Product commit `cf7456d7b70a9c59d014908670bfb33ea0340ebc` was built from the exact tested tree.
Three measured repetitions per arm/policy (18 builds), plus two excluded preparation passes,
used the same six Datadog targets and repository revision as the earlier comparison. Arm order
rotated and policy order alternated. All builds and output checks passed; every cache reader
restored 17 configured-target and 11 execution entries. The 578 test cases also pass.

| Output policy | Vanilla, median | Local, median | Remote before, one sample | Remote after, median | Before / after |
| --- | ---: | ---: | ---: | ---: | ---: |
| minimal | 13.37 s | 14.27 s | 457.71 s | 93.68 s | 4.89x |
| toplevel | 25.42 s | 23.92 s | 451.52 s | 97.73 s | 4.62x |

Remote minimal samples span 92.99–94.43 seconds;
toplevel samples span 97.53–98.48 seconds.
The old binary has only one prepared sample per policy. Each binary has its own prepared seed
because installation identity versions analysis keys; these are equivalent workload comparisons,
not identical cache-file comparisons. The new local/remote arms share identical frozen metadata
within this matrix. Their binary outputs match the pinned vanilla outputs byte-for-byte where
downloaded and by BEP digest in both policies. Build wall time includes publication and excludes
startup/clean and backend warmup. The metadata backend is loopback; these results do not establish
performance over a production network.

Minimal remote process `rchar` fell from **827,313,019,557 B** to a median **20,218,565,730 B**
(97.56% lower). This is the same cumulative process counter before/after,
including startup/clean and all read-like I/O, not physical disk or network traffic. Separately,
the new cached-read counter records a median **3,543,395,561 B** across remote samples.
Hash-cache weight reaches **11,523,808 B**, below the 32 MiB budget, with **zero evictions**.
Minimal remote client process-tree PSS has median **2.38 GiB**;
backend PSS is recorded separately and filesystem page cache is excluded.

The remaining cost is still material: each fresh remote reader downloads **1,448,193,238 B**
of metadata/chunk payload, despite returning about 244.6 MB of shared values. Remote reads retain
the global lock and 1 MiB wire chunks. All six remote samples reuse both parent descriptors and
materialize zero publications, but still write 16 private records; cached-only put deduplication
is unchanged. The excluded first refresh uploads **1,521,873,109 B**, confirming the
value of `FindMissingBlobs` before uploading local parents already present in CAS. Profile and
reduce serialized reads, deduplicate uploads, then compare fixed 256 KiB chunks before adopting CDC.

The first cross-binary pilot and an initial preparation run stopped on harness assertions and
remain explicitly failed in the evidence. The latter completed two correct preparation builds;
its first pass had 1,708 analysis hits, while the stable second pass had 17 and zero writes. The
corrected harness gates the final stable pass, and reran preparation in a new directory before
the 18 successful measurements. No input-validation or output-verification checks were disabled;
neither failed run enters the timing table. All original source/seed/CAS/catalog checks passed;
client and server services
were stopped at completion.

[Per-sample comparison](measurements/skycache-block-reads-agent-2026-10-25/comparison.json),
[raw matrix](measurements/skycache-block-reads-agent-2026-10-25/matrix.json),
[verification/provenance](measurements/skycache-block-reads-agent-2026-10-25/verification.json),
and [logs, profiles and test XML](measurements/skycache-block-reads-agent-2026-10-25/logs-profiles-tests.tar.gz)
include the exact harnesses and checksums. Catalog signing keys are excluded.

### Overlapping cached reads with downloads

The block-read baseline spends 81.894 seconds in `ParallelEvaluator.eval`, compared with 12.536
seconds locally in the first minimal sample. Its profile shows low CPU utilization during much
of that interval, but has no per-RPC or monitor-wait events to separate network latency from lock
contention. Source inspection establishes that the global chunk-reader monitor blocks cached
disk reads behind unrelated downloads. Uploads occur after evaluation and cannot explain that gap.

The second implementation removes that coupling while preserving the transport's one-at-a-time
RPC contract and cumulative time/byte budgets. A semaphore bounds cached range reads to eight;
the separate download lock coalesces successful missing-chunk fetches. Completed chunk files are
exposed with an atomic, non-replacing hard link, so cached-only reads never observe partial writes
or initiate a transfer. Hashes remain bounded by the same 32 MiB LRU weight budget, with full-digest
verification after eviction and authentication of every returned byte.

Lifecycle locks prevent scratch deletion while reads are active. Closing first cancels the channel
without waiting for the blocking RPC monitor, then waits for readers and removes scratch. Export
and admission hold the exclusive lifecycle lock so they cannot reset budgets during active reads.
Four latch-controlled tests cover cached reads during a blocked download, prompt cached-only
misses, duplicate fetches, close/cancellation and interrupted download waiters. All 582 test cases
across 21 targets pass (14 targets executed, seven cached).

New `peak_cached_reads`, `cached_read_nanos` and `download_rpc_nanos` counters distinguish bounded
cached-read overlap from time in the serialized download calls. Cached-read duration includes I/O
and hashing but excludes waiting for a reader permit. Durations sum across operations and can
overlap; they must not be added as exclusive wall-clock phases. The hash budget is a retained-cache
weight, not a total heap bound; up to eight cached operations and one download also own temporary
buffers, while callers own their result buffers.

### Agent repository measurements with overlapping cached reads

Product commit `d1289931bc63f7b6bf6786ec1002bfd182269ba1` was built from tested tree
`7685da9520dbaaa6184800686fc1aec8f5964736`; all **582 test cases** passed.
The comparison uses the same six Datadog targets, revision, output base, embedded tools,
output digests, and pinned vanilla binary as the preceding block-authentication matrix.
Both matrices contain three measured samples per arm/policy. The new run completed
18 measured builds after one deliberate cross-binary prime and two excluded stabilization
passes. The prime succeeded with zero old-seed hits and a committed new publication;
the final stabilization wrote zero records. Every measured cache reader restored
17 configured-target and 11 execution entries, with clean graph/action state and no
cache failures, transfer skips or scan/read-limit failures.

| Output policy | Arm | Previous median (range) | Overlapping reads median (range) | Previous / current |
| --- | --- | ---: | ---: | ---: |
| minimal | vanilla | 13.37 s (12.97–13.57 s) | 13.22 s (13.17–13.77 s) | 1.01x |
| minimal | local | 14.27 s (14.17–14.62 s) | 15.00 s (14.22–15.97 s) | 0.95x |
| minimal | remote | 93.68 s (92.99–94.43 s) | 85.08 s (84.78–102.53 s) | 1.10x |
| toplevel | vanilla | 25.42 s (25.12–25.53 s) | 25.52 s (25.48–26.12 s) | 1.00x |
| toplevel | local | 23.92 s (23.72–24.00 s) | 24.57 s (23.82–27.77 s) | 0.97x |
| toplevel | remote | 97.73 s (97.53–98.48 s) | 92.64 s (91.18–105.23 s) | 1.06x |

Ratios above 1 indicate lower current wall time; these small samples describe this run
and do not establish statistical significance. Each binary uses its own prepared seed
because installation identity versions analysis keys. Within the new matrix, local and
remote readers use identical frozen publication metadata. All six binary digests match
the pinned vanilla outputs in BEP; toplevel downloads also match by actual SHA-256.
Arm order rotates and policy order alternates. Each sample has a fresh JVM and client
metadata cache, with startup/clean and one backend warmup round excluded from build
wall time. Publication work is included. The metadata backend is loopback with warmed
CAS and OS caches, so production-network performance remains unmeasured.

The new counters separate accumulated cached-read work from accumulated download-RPC
duration. They are **not exclusive wall-time components**: cached operations can overlap
each other and the RPC stream, so their sums must not be added into a wall-time breakdown
or interpreted as CPU time. Cached-read timing starts after acquiring one of eight slots;
the RPC counter measures the transport download call, not all metadata RPCs or queue waits.

| Remote sample | Build wall | Cached-read accumulated duration | Download-RPC accumulated duration | Peak cached readers |
| --- | ---: | ---: | ---: | ---: |
| r1-minimal-remote | 102.53 s | 17.84 s | 57.71 s | 8 |
| r1-toplevel-remote | 92.64 s | 8.73 s | 57.89 s | 8 |
| r2-toplevel-remote | 91.18 s | 8.25 s | 57.46 s | 8 |
| r2-minimal-remote | 84.78 s | 7.77 s | 58.15 s | 8 |
| r3-minimal-remote | 85.08 s | 8.03 s | 56.71 s | 8 |
| r3-toplevel-remote | 105.23 s | 12.22 s | 58.81 s | 8 |

Observed cached-reader peaks span **8–8**, within the limit of eight.
Authentication-cache weight reaches **11,811,744 B**, within 32 MiB; recorded evictions
span **0–0** per sample. Fresh remote readers download
**1,484,230,964–1,484,230,964 B** of metadata/chunk payload and record a median
**3,565,280,220 B** of cached reads. Remote sample writes span
**15–16**; 6 of six samples report a publication commit.
Materialized-publication counts span 0–0 and reused-publication
counts span 2–2. These observed counts are recorded without a fixed
payload or remote-write assumption.

Remote client process-tree PSS medians are **2.77 GiB**
for minimal and **2.85 GiB** for toplevel; worker
peaks are recorded separately and filesystem page cache is excluded. Corresponding median
process `rchar` values are **20,293,201,670 B** and
**20,230,976,701 B**. These are cumulative fresh-JVM read-like counters,
including startup/clean, and are neither physical disk bytes nor network traffic.

Remaining limits include one download-RPC stream, 1 MiB wire chunks, repeated uploads
when a new publication is exported, and work required to read and authenticate local
chunk ranges. The measurements isolate overlapping cached I/O; they do not evaluate
upload deduplication, smaller chunks, CDC, or a production-network deployment. All
source/seed/CAS/catalog preservation and cleanup checks passed. Exact raw reports,
the prior report hashes, binary/runtime JAR provenance, harnesses, build scripts,
legacy helpers, logs/profiles and test XML are frozen with SHA-256 checksums; catalog
directories and signing keys are excluded from the archive.

[Six-cell and per-sample comparison](measurements/skycache-parallel-reads-agent-2026-10-25/comparison.json),
[successful prime](measurements/skycache-parallel-reads-agent-2026-10-25/prime.json),
[raw matrix](measurements/skycache-parallel-reads-agent-2026-10-25/matrix.json),
[verification/provenance](measurements/skycache-parallel-reads-agent-2026-10-25/verification.json),
and [logs, profiles and test XML](measurements/skycache-parallel-reads-agent-2026-10-25/logs-profiles-tests.tar.gz).

### Concurrent downloads and cached reads

The third implementation sets separate download and cached-read permits to 100 by default,
controlled by `--experimental_remote_skycache_max_concurrency` (1–1000). Fixed digest-lock stripes
coalesce successful downloads without retaining a coordination entry for every fetched chunk.
Private temporary files and atomic hard-link exposure preserve cached-only read behavior and
authentication; the 32 MiB retained hash-cache weight remains independent of temporary buffers.
The limit bounds active operations, not total process memory or caller-owned result buffers.

The transport admits RPCs under a short accounting monitor, then performs them outside it. It
reserves attempted bytes before dispatch and charges elapsed time multiplied by the number of
active RPCs. One timer cancels exhausted calls; completion also checks strict aggregate overspend
so a reply cannot escape enforcement by beating the timer. Queueing does not consume an extra
RPC-time charge. Reset rejects active or queued requests; shutdown cancels active calls and wakes
waiters. Export retains its existing absolute deadline. These changes require no server or proto
changes, and do not introduce parallel upload scheduling.

All **592 test cases across 21 targets pass** (14 targets executed, seven cached). The new tests
cover independent chunk progress, the default and configurable limits, shared admission across
RPC services, atomic failed-attempt accounting, queued interruption, reset and shutdown, aggregate
timer cancellation, and successful replies arriving after aggregate budget overspend.
`peak_chunk_downloads` and `max_concurrency` join the existing read counters. Both duration counters
sum operation time across concurrent calls; neither is an exclusive wall-time phase.

### Agent repository measurements with concurrent downloads

Product commit `43e062697f8fa8be38bc7f6d006940a8cc4735d9` was built from tested tree
`d1894f553f4387399e3c2e0bf52fb8e644efd129`; all **592 test cases** passed.
The comparison uses the same six Datadog targets, revision, output base, embedded tools,
output digests, and pinned vanilla binary as the preceding matrix with overlapping cached
reads and serialized downloads. That historical baseline remains unchanged.
Both matrices contain three measured samples per arm/policy. The new run completed
18 measured builds after one deliberate cross-binary prime and two excluded stabilization
passes. The prime succeeded with zero old-seed hits and a committed new publication;
the final stabilization wrote zero records. Every measured cache reader restored
17 configured-target and 11 execution entries, with clean graph/action state and no
cache failures, transfer skips or scan/read-limit failures.

| Output policy | Arm | Previous median (range) | Concurrent downloads median (range) | Previous / current |
| --- | --- | ---: | ---: | ---: |
| minimal | vanilla | 13.22 s (13.17–13.77 s) | 13.22 s (13.07–13.48 s) | 1.00x |
| minimal | local | 15.00 s (14.22–15.97 s) | 14.47 s (14.28–14.47 s) | 1.04x |
| minimal | remote | 85.08 s (84.78–102.53 s) | 56.78 s (56.33–59.28 s) | 1.50x |
| toplevel | vanilla | 25.52 s (25.48–26.12 s) | 25.57 s (24.47–25.88 s) | 1.00x |
| toplevel | local | 24.57 s (23.82–27.77 s) | 23.77 s (23.57–24.00 s) | 1.03x |
| toplevel | remote | 92.64 s (91.18–105.23 s) | 68.08 s (62.93–68.88 s) | 1.36x |

Ratios above 1 indicate lower current wall time; these small samples describe this run
and do not establish statistical significance. Each binary uses its own prepared seed
because installation identity versions analysis keys. Within the new matrix, local and
remote readers use identical frozen publication metadata. All six binary digests match
the pinned vanilla outputs in BEP; toplevel downloads also match by actual SHA-256.
Arm order rotates and policy order alternates. Each sample has a fresh JVM and client
metadata cache, with startup/clean and one backend warmup round excluded from build
wall time. Publication work is included. The metadata backend is loopback with warmed
CAS and OS caches, so production-network performance remains unmeasured.
The SkyCache flag is omitted to exercise its new default of 100. Ordinary action-cache
connections retain the common benchmark override of 16 requests per connection across all arms.

The counters report accumulated cached-read work and accumulated download-RPC duration.
They are **not exclusive wall-time components**: this implementation permits parallel
download calls, so the RPC counter sums their overlapping durations and can exceed build
wall time. Cached reads can also overlap one another and downloads. Neither counter, nor
their sum, is a wall-time breakdown or CPU time. Timing begins after acquiring the relevant
client slot; the RPC counter covers transport download calls, including any transport admission
delay. It excludes waiting for the client download permit and does not cover all metadata RPCs.

| Remote sample | Build wall | Cached-read accumulated duration | Download-RPC accumulated duration | Peak cached readers | Peak chunk downloads |
| --- | ---: | ---: | ---: | ---: | ---: |
| r1-minimal-remote | 56.33 s | 7.17 s | 39.93 s | 13 | 7 |
| r1-toplevel-remote | 62.93 s | 8.33 s | 40.79 s | 13 | 7 |
| r2-toplevel-remote | 68.08 s | 7.41 s | 49.14 s | 14 | 6 |
| r2-minimal-remote | 59.28 s | 7.55 s | 43.61 s | 14 | 7 |
| r3-minimal-remote | 56.78 s | 7.37 s | 39.67 s | 13 | 6 |
| r3-toplevel-remote | 68.88 s | 8.24 s | 48.35 s | 15 | 6 |

The reported concurrency setting is **100**. Observed cached-reader peaks span
**13 to 15** and chunk-download peaks span
**6 to 7**, each within that bound. These are observed
peaks, not a claim that 100 operations remain active throughout a build.
Authentication-cache weight reaches **11,992,864 B**, within 32 MiB, with no evictions.
Each fresh remote reader downloads **1,507,148,778 B** of metadata/chunk payload;
cached reads have a median of **3,544,413,091 B**. Every remote sample writes 16 records,
commits a publication, reuses both parent descriptors and materializes no publications.

Remote client process-tree PSS medians are **3.07 GiB**
for minimal and **3.13 GiB** for toplevel; worker
peaks are recorded separately and filesystem page cache is excluded. Corresponding median
process `rchar` values are **20,207,489,848 B** and
**20,240,827,772 B**. These are cumulative fresh-JVM read-like counters,
including startup/clean, and are neither physical disk bytes nor network traffic.

The observed peaks stay well below 100, so these measurements do not identify the configured
cap as the remaining bottleneck. Remote wall-time medians fell by 33.3% for minimal and 26.5%
for toplevel, while client PSS medians increased from 2.77/2.85 GiB to 3.07/3.13 GiB.
Remaining work includes reducing overfetch from 1 MiB chunks, avoiding repeated uploads when
a new publication is exported, and reducing the work needed to read and authenticate local
chunk ranges. This comparison changes both download overlap and the
cached-reader bound, so it does not isolate their individual effects. It does not evaluate
upload deduplication, smaller chunks, CDC, or a production-network deployment. All
source/seed/CAS/catalog preservation and cleanup checks passed. Exact raw reports,
the prior report hashes, binary/runtime JAR provenance, harnesses, build scripts,
legacy helpers, logs/profiles and test XML are frozen with SHA-256 checksums; catalog
directories and signing keys are excluded from the archive.

[Six-cell and per-sample comparison](measurements/skycache-concurrent-downloads-agent-2026-10-25/comparison.json),
[successful prime](measurements/skycache-concurrent-downloads-agent-2026-10-25/prime.json),
[raw matrix](measurements/skycache-concurrent-downloads-agent-2026-10-25/matrix.json),
[verification/provenance](measurements/skycache-concurrent-downloads-agent-2026-10-25/verification.json),
and [logs, profiles and test XML](measurements/skycache-concurrent-downloads-agent-2026-10-25/logs-profiles-tests.tar.gz).

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

1. **Serialized downloads and uncached/local uploads still do avoidable work.** Authenticated
   block reads now remove repeated whole-chunk reads/hashes while preserving returned-byte
   integrity. Cached range reads can overlap, but the transport still issues one RPC at a time
   and retains 1 MiB wire chunks.
   Remote-parent descriptor reuse removes repeated parent transfer, but new local files
   still use singleton BatchUpdateBlobs calls.
   Batch FindMissingBlobs and skip existing chunks before considering CDC; measure RPC overhead
   and use bounded batches. Compare smaller fixed chunks for demand-read amplification.
2. **Larger publications still have independent limits.** Logical closure and transfer bounds
   are now separate: 4 GiB logical, configurable actual transfer, unchanged metadata/count
   limits. Tests admit a 1.5 GiB logical publication while fetching less than 4 MiB, preserve
   the default transfer limit and reject over-limit materialization before staging files.
   Increasing the logical bound further needs its own measurements; it must not implicitly
   increase memory, request or metadata budgets.
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

Use the optimization sequence above to measure concurrent reads, upload deduplication and smaller chunks.
Further size increases and independent-clone portability need separate compatibility work
that preserves the existing per-entry validator. The development server is sufficient for these
correctness and controlled bandwidth/latency experiments; production catalog retention and
access control remain separate server work.
