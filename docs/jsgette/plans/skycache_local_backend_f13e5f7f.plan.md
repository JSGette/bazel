---
name: skycache local backend
overview: "Fork Bazel 9.2.0 locally and add a BlazeModule that gives Skycache a real backend: a server-lifetime RAM tier plus a sharded on-disk CAS-style store, with git-diff-driven invalidation so flipping --config=gorace restores analysis from cache instead of re-analyzing from scratch."
todos:
  - id: p0-measure
    content: "P0: measure current cost of a --config=gorace flip (full analysis time) with --profile on a representative target set, to establish the baseline the restore path must beat"
    status: pending
  - id: fork-setup
    content: "Set up local Bazel 9.2.0 build: checkout tag 9.2.0 in ~/REPOS/bazel, confirm a clean bootstrap build, and wire the resulting binary into datadog-agent via BAZEL_REAL/tools/bazel"
    status: pending
  - id: disk-store
    content: Implement DiskFingerprintValueStore (sharded hex path, temp+rename writes, size-capped LRU sweep as an IdleTask) with unit tests
    status: pending
  - id: layered-store
    content: "Implement LayeredFingerprintValueStore: bounded Caffeine RAM tier in front of the disk tier, write-through puts, promote-on-get"
    status: pending
  - id: module
    content: "Add LocalSkycacheModule extends SerializationModule plus LocalServicesSupplier: one server-lifetime FingerprintValueService over the layered store, null analysis cache client, MinimalVersionGetter installed via env.setVersionGetter in beforeCommand"
    status: pending
  - id: flags
    content: Add experimental_local_analysis_cache* options and the flag that unlocks store-only DOWNLOAD
    status: pending
  - id: patch-gates
    content: Patch Bazel.java module registration, the BuildTool DOWNLOAD gate at BuildTool.java:1331, and UPLOAD_BUILDS_MUST_BE_COLD in BuildView.java, each behind the new flag
    status: pending
  - id: p1-validate
    content: "P1 validation: cold server upload of config A with --experimental_active_directories, flip to config B, flip back with DOWNLOAD, and confirm cache-hit stats plus an analysis-time win over the P0 baseline"
    status: pending
  - id: p2-git-invalidation
    content: "P2: implement GitChangeSource feeding VersionedChanges as client changes, make VersionedChangesValidator reachable, and wire it into SkyValueRetriever so entries whose FileDependencies match are rejected"
    status: pending
  - id: p2-validate
    content: "P2 validation: edit a source file, confirm only dependent nodes are invalidated and unrelated cached analysis still restores; add a verify mode comparing restored vs freshly analyzed graphs"
    status: pending
  - id: p3-decide
    content: "P3: with P1/P2 numbers in hand, decide whether to add an HTTP FingerprintValueStore against bazel-remote (--disable_http_ac_validation, /ac/<64 hex>) for cross-machine sharing, including swapping murmur3 for SHA-256"
    status: pending
isProject: false
---

> Note: this plan cites `/Users/joseph.gette/REPOS/bazel` at tag `9.1.1`. Verified with `git diff --stat 9.1.1 9.2.0` that every file touched below is byte-identical in `9.2.0`, which is what [.bazelversion](/Users/joseph.gette/REPOS/datadog-agent/.bazelversion) pins.

# Skycache local backend: RAM + disk analysis cache

## Goal

Flipping between `--config=gorace` and default in the same server should restore the analysis graph from a cache instead of re-analyzing. One JVM, one Skyframe graph, but serialized analysis for N configs living in RAM and on disk.

## What already works vs. what is missing

Working in stock OSS 9.2.0:

- Full serialization pipeline (`FrontierSerializer`, `SelectedEntrySerializer`) and deserialization (`SkyValueRetriever`).
- Per-config keying for free: `FrontierNodeVersion` mixes `topLevelConfigChecksum` + `blazeInstallMD5` + `evaluatingVersion` + distinguisher, and the entry key is `fingerprint(version || serializedSkyKey)` (`SkyKeySerializationHelper.java:58-66`). Two configs never collide, so one store holds all of them.
- The store interface is already injectable with no patch: `FingerprintValueService` has a public constructor taking a `FingerprintValueStore`, and `SerializationModule.getAnalysisCachingServicesSupplier()` is `@ForOverride`. `BazelSkycacheIntegrationTest.java:101-104` is a working example of overriding it.
- Invalidation *matching* is implemented and unit-tested: `VersionedChanges`, `VersionedChangesValidator`, `FileDependencyDeserializer`.

Missing, and therefore our work:

- No persistent store. The default supplier is a static in-memory map with a `TODO: b/358347099 - use a persistent store` (`SerializationModule.java:82-89`).
- DOWNLOAD is gated on a gRPC analysis-cache service being present, even though the store-only path is fully implemented (`BuildTool.java:1331-1350`, and `SkyValueRetriever.java:237-245` handles `analysisCacheClient == null` by fetching straight from the store).
- `CommandEnvironment.setVersionGetter` has zero callers, so UPLOAD hits `throw new NullPointerException("missing versionGetter")` (`FrontierSerializer.java:147-152`).
- UPLOAD refuses to run on a warm server (`BuildView.java:158-268`, `UPLOAD_BUILDS_MUST_BE_COLD`) — exactly the "second config in the same server" case.
- No reader-side invalidation wiring. `VersionedChangesValidator` is never instantiated in `src/main/java`, and `lookupKeysToInvalidate` returns an empty set when there is no cache client (`BuildTool.java:1761-1769`).

## Architecture

```mermaid
flowchart TD
  subgraph server [Bazel server, one JVM]
    graph[Skyframe graph, one config live]
    svc[FingerprintValueService, server lifetime]
    fpc[FingerprintValueCache, deserialized objects]
    ram[Bounded RAM byte cache]
    disk[Sharded disk store]
  end
  gitsrc[GitChangeSource, diff vs baseline] --> vc[VersionedChanges]
  vc --> validator[VersionedChangesValidator]
  graph -->|UPLOAD, serialize frontier| svc
  graph -->|DOWNLOAD, deserialize| svc
  svc --> fpc
  svc --> ram
  ram -->|miss| disk
  validator -->|"reject stale entry"| svc
```

Two distinct RAM effects, worth keeping straight:

- `FingerprintValueCache` inside a server-lifetime `FingerprintValueService` memoizes *deserialized* shared values, so repeated config flips skip re-deserializing shared subgraphs.
- Our own bounded byte cache in front of the disk store removes I/O only.

Neither removes the cost of deserializing and inserting nodes into Skyframe. Expected win is "analysis restore" vs. "full re-analysis", not "instant".

## Patches to Bazel (keep minimal, they are the maintenance burden)

1. `src/main/java/com/google/devtools/build/lib/bazel/Bazel.java:86` — register our module instead of `SerializationModule`.
2. `src/main/java/com/google/devtools/build/lib/buildtool/BuildTool.java:1331-1350` — allow DOWNLOAD with a null analysis cache client, behind a new flag so the default stays upstream behavior.
3. `src/main/java/com/google/devtools/build/lib/analysis/BuildView.java:259-268` — relax `UPLOAD_BUILDS_MUST_BE_COLD` under the same flag so config #2 in a warm server can upload.
4. `src/main/java/com/google/devtools/build/lib/skyframe/serialization/SkyValueRetriever.java` — consult the invalidation data key (phase 2 only).

Everything else is new files under a new package, ideally `.../skyframe/serialization/analysis/local/`, so rebases stay cheap.

## New code

- `LocalSkycacheModule extends SerializationModule` — overrides `getAnalysisCachingServicesSupplier()`, and in `beforeCommand` calls `env.setVersionGetter(...)`.
- `LocalServicesSupplier implements RemoteAnalysisCachingServicesSupplier` — builds one `FingerprintValueService` per server (not per command) over the layered store; leaves `getAnalysisCacheClient()` returning null.
- `LayeredFingerprintValueStore implements FingerprintValueStore` — RAM tier (Caffeine, already vendored in Bazel) in front of a disk tier; `put` writes through, `get` promotes.
- `DiskFingerprintValueStore` — `<root>/<2 hex>/<30 hex>` from `PackedFingerprint.toBytes()` (fixed 16 bytes, `PackedFingerprint.java:42`), temp file + atomic rename, size-capped LRU sweep registered as an `IdleTask` via `env.addIdleTask`.
- `MinimalVersionGetter implements LongVersionGetter` — returns `MINIMAL` for existing paths and `CURRENT_VERSION` for nonexistent ones. This is what makes the git-diff scheme work: MTSVs become uniform, and staleness is decided entirely on the read side.
- `GitChangeSource` — `git rev-parse HEAD`, `git diff --name-only <baseline>..HEAD`, `git status --porcelain`; feeds `new VersionedChanges(clientChanges)`.

New flags, all `experimental_` and defaulting to off: `--experimental_local_analysis_cache` (path), `--experimental_local_analysis_cache_ram_bytes`, `--experimental_local_analysis_cache_max_bytes`, and one flag to unlock the store-only DOWNLOAD path.

## Phasing

**P0 — measure the prize.** Time a `--config=gorace` flip today on `//pkg/ebpf/...` and on the target set from terminal 3, with `--profile`. If restore has to beat, say, 90s of analysis, that number decides whether P2 is worth it.

**P1 — pinned baseline, no incrementality.** Patches 1-3, the module, both store tiers, `MinimalVersionGetter`. Correctness comes from keying: put `git rev-parse HEAD` plus a digest of the dirty-file list into the cache key, reusing the existing `--experimental_analysis_cache_key_distinguisher_for_testing` mechanism (`RemoteAnalysisCachingOptions.java:213-219`) at first, then a properly named flag. Any source edit changes the key, so you get a clean full miss rather than a stale hit. UPLOAD also needs `--experimental_active_directories` since this repo has no `PROJECT.scl`. Workflow to validate: cold server, build config A with UPLOAD, flip to config B, flip back to A with DOWNLOAD, confirm restore.

**P2 — git-diff invalidation.** Patch 4 plus `GitChangeSource`. Drop the source state out of the cache key, register changed files as client changes, and reject any entry whose `FileDependencies` match. This is what makes the cache survive editing code, and it is the difference between "useful when the tree is clean" and "useful all day". The pieces are all there; the work is the `SkyValueRetriever` call site and making `VersionedChangesValidator` reachable.

**P3 — decide on externalization.** Only after P1/P2 numbers exist.

## Option B: fully external backend

Analyzed, and the honest conclusion is that **bazel-remote already is this**, so writing a server is the wrong first move.

The Skycache store is a flat `16-byte key -> opaque bytes` KV. It is *not* a CAS: top-level entries are keyed by `fingerprint(version || key)`, i.e. by key, not by content (`SkyKeySerializationHelper.java:58-66`). Only shared values inside an entry are content-addressed. So a strict REAPI CAS cannot host it — you need arbitrary-key KV.

bazel-remote's `/ac/<64 hex>` endpoint over plain HTTP/1.1 PUT/GET is exactly that, once `--disable_http_ac_validation` turns off ActionResult parsing. It brings disk storage, an LRU with `--max_size`, optional zstd, and S3/GCS/Azure proxy backends for free. Our 16-byte fingerprint hex-encodes to 32 chars, so pad to the required 64.

Tradeoffs:

- In favor: zero server code; sharing across machines and CI becomes a config change; battle-tested eviction.
- Against: no gRPC needed but you do need an HTTP `FingerprintValueStore` (~150 lines on `java.net.http`), and every get becomes a loopback round trip where an in-process map was free. bazel-remote keeps its index in RAM but data on disk, so the fast tier still has to live inside Bazel.
- Correctness caveat for sharing: `NONPROD_FINGERPRINTER` is murmur3_128 (`FingerprintValueService.java:39-40`). Fine for one dev's disk, not something to point at a shared multi-user store without swapping in SHA-256 first.

Recommendation: build P1 against the local disk store, keep `FingerprintValueStore` the only seam, and add an HTTP-backed implementation later if sharing turns out to matter. No gRPC at any point.

## Risks

- Rebase cost on every Bazel bump. Mitigated by four small patches plus an isolated package.
- `UPLOAD_BUILDS_MUST_BE_COLD` exists for a reason; relaxing it may serialize a frontier computed over a graph that already holds another config's nodes. Needs explicit validation that a warm-server upload produces the same bytes as a cold-server one for the same config.
- Inputs outside git (external repos under `external/`, env vars, generated files) are invisible to `GitChangeSource`. Mitigate by mixing `MODULE.bazel.lock` into the key and treating non-workspace paths conservatively.
- Skycache is upstream-experimental. Serialization bugs surface as analysis-time crashes or, worse, silent incorrectness. Keep it opt-in per developer, never on in CI, and add a "verify" mode that re-analyzes and compares against the restored graph on demand.

## Delivery

Local only, per your choice: build from `~/REPOS/bazel`, opt in via `BAZEL_REAL` / the existing [tools/bazel](/Users/joseph.gette/REPOS/datadog-agent/tools/bazel) wrapper. No change to `.bazelversion` and nothing that affects other developers or CI until P0/P1 numbers justify it.