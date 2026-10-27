# Make Skycache validate only demanded evidence

**Proposed design, 27 October 2026. No runtime changes or new benchmarks accompany this document.**

Skycache should separate **which bytes it transfers, which values it validates, and which objects it reconstructs**. For the measured large workload, bulk prefetch is credible because demand loading already transfers almost the entire publication. Its first implementation should preserve remote parent provenance and feed the existing validator through the existing virtual file representation. The deeper opportunity is to reduce broad publication admission and internal proof traversal while validating every cached value before consumption. Validation only before action execution is insufficient: cached analysis changes dependency discovery, and cached execution results can skip execution altogether. The current implementation already performs per-entry validation, shares dependency work, authenticates reads, and retains chunks; these are foundations to preserve. None of the proposed changes has yet demonstrated an end-to-end latency advantage over vanilla.

## 1. The audited pipeline already contains lazy reuse

### Scope and source provenance

The implementation reviewed is the clean local development checkout `/home/jgett/REPOS/bazel-skycache-remote-mvp` at **`c5f012f3350a9ebc7d1dd524ca65ecabb28e1d84`**. The documentation destination was `origin/jsgette/master` at **`cdd1b0ba2e7f69cce973239c9ec828ac6ac57187`**. These are different histories: the destination does not contain the reviewed `serialization/local/` implementation, and the implementation revision was not contained in fetched remote branches at review time. Publishing this document does not merge that implementation.

Code references below are reproducible **repository-relative paths and line ranges at the audited local revision**, not public links to unavailable source. The following aliases make those references shorter:

| Alias | Repository-relative directory |
| --- | --- |
| `L/` | `src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/` |
| `R/` | `src/main/java/com/google/devtools/build/lib/skyframe/serialization/remote/` |
| `S/` | `src/main/java/com/google/devtools/build/lib/skyframe/serialization/` |
| `F/` | `src/main/java/com/google/devtools/build/lib/skyframe/` |
| `T/` | `src/test/java/com/google/devtools/build/lib/skyframe/serialization/` |

The measurement references in section 2 also identify local evidence. Essential results are reproduced here so that readers of the documentation branch can assess the proposal without those files. Existing recorded test passes are historical evidence; this review did not rerun them.

### Three validation boundaries and two reconstruction boundaries

Startup constructs `LocalInputSnapshot` before remote preparation. It captures namespace and recursively hashes the workspace, including untracked/ignored inputs, modes, directory membership and followed directory symlinks, with explicit exclusions for Git administration and matching Bazel convenience links. This **whole-source scan precedes any demanded-entry shortcut**. The namespace includes a format salt, native byte order, canonical Git common directory or workspace, and effective environment. The current identity is not a portable identity for arbitrary independent clones. See `L/LocalSkycacheModule.java:145–150,212–233` and `L/LocalInputSnapshot.java:36–83,120–156,473–524`.

Remote discovery then loads authenticated bundles, manifests, every declared witness block and root segment metadata. Segment indexes and record payloads remain lazy. Publication admission validates repository bindings first and then the publication's global witnesses. That global list excludes `workspace/` witnesses but includes external and absolute observations. Thus unrelated workspace changes can already preserve a hit, while unrelated external changes can still reject an entire publication. See `L/RemoteSkyCache.java:1019–1118`, `L/LocalCacheSession.java:678–705,734–761,803–829,1011–1026` and `L/LocalInputSnapshot.java:94–97`.

Entry lookup subsequently reads its envelope and validates its owning dependency root before accepting value bytes. Both analysis and execution envelopes follow this rule. This work is synchronous even though `lookup()` returns a future: the returned future is already complete. The validator is iterative and bounded, with successful-root and exact-witness memoization, shared in-flight subtrees, cycle detection and conservative budget failures. Resolution-parent evidence is checked before witness reuse. Failures are not permanently memoized because repositories and evidence can become available later. See `L/LocalAnalysisCacheClient.java:139–181` and `L/LocalDependencyValidator.java:37–99,124–220,230–365`.

After validation, retrieval deserializes demanded values. `ConfiguredTargetFunction` tries retrieval before normal target/configuration analysis; `ActionExecutionFunction` tries it before loading the generating action. A valid execution-value hit already bypasses more work than an ordinary action-cache lookup. Finally, publication freshly checks namespace, the source digest and registered witnesses, then seals and publishes. Fresh final validation already uses up to four workers and shares digests within that pass. Its freshness epoch must remain distinct from earlier lookup checks. See `F/ConfiguredTargetFunction.java:273–301`, `F/ActionExecutionFunction.java:175–219`, `L/LocalCacheSession.java:851–955` and `L/LocalInputSnapshot.java:179–281`.

The resulting pipeline is:

```text
source snapshot → publication discovery/admission → demanded root validation
                → demanded value deserialization → ordinary evaluation/execution
                → fresh final validation → publication
```

**Fetching a publication does not reconstruct a live Skyframe graph.** The publication provides serialized values, indexes and dependency evidence. Whole-file hydration additionally reconstructs local pack/index files. Neither operation recreates every evaluator node, reverse edge or runtime lifecycle. A full live-graph snapshot would be a separate project. Skyframe's distinction between immutable values, functions, graph relationships and dynamic evaluation explains why these boundaries matter. ([Skyframe model](https://bazel.build/reference/skyframe))

Several proposed “optimizations” are already present: independent chunk downloads, single-flight coordination through 1,024 striped locks, full-chunk authentication, authenticated covering 4 KiB ranges, bounded channel and scratch reuse, advisory persistent chunks without fsync, remote parent descriptor reuse and reuse of identical inherited witness blocks. Keep these mechanisms while measuring their remaining costs. See `L/RemoteSkyCache.java:54–90,109–383,529–792,1264–1322`, `L/RemoteChunkCache.java:112–151` and `L/LocalCacheSession.java:949–960`.

## 2. Existing measurements justify an experiment, not a speedup claim

### Keep measurement cohorts separate

The historical 25 October experiment used product revision `b368beb0ddde51434ce20c063546047ecccb2a88`, six Datadog targets, fresh JVM/graph state and three observations per arm. With minimal output downloads, median command times were **13.32 s vanilla, 14.27 s local, 45.97 s remote with empty retention, and 26.17 s remote with retained chunks**. It restored 17 configured-target and 11 execution entries. These results establish the earlier problem, but are not current matched baselines: subsequent experiments changed implementation and added a publication probe. Source: `docs/jsgette/skycache-remote-mvp.md:850–899` in the audited checkout.

The 26 October import experiment used base `869792d6b1` plus documented compiled prototype overrides. Its broad comparison used the initial digest prototype, before the final atomic-slot pool and advisory-retention change. The following are medians of three samples, including helper startup/import where applicable:

| Earlier import cohort | Median command time |
| --- | ---: |
| Existing local publication, warm pages | 17.046 s |
| Existing local publication, cold pages | 23.168 s |
| Remote demand retrieval, cold | 28.900 s |
| Sequential full import, then local publication | 44.559 s |
| 16-worker full import, then local publication | 25.975 s |
| 16-worker full import, remote publication enabled | 31.423 s |

Demand retrieval transferred **96.89% of the full unique payload**: full import downloaded 1,510,678,665 bytes, only 46,935,948 more bytes. However, hydration classified imported parents as local, so remote publication reused zero remote parents and repeated parent processing. Import also stored about **1.520 GB of reconstructed files plus 1.511 GB of retained chunks**. A slow 51.40 s run remains in the evidence. The local-publication arm is a useful decomposition, not proof that bulk import wins with identical remote publication behavior. Sources: `docs/jsgette/measurements/skycache-digests-cold-2026-10-26/README.md:75–112,160–195` and its `provenance.json`.

The latest completed retention cohort used base `f94779009f` plus exact compiled `RemoteChunkCache` overrides subsequently committed in `c5f012f335`. All arms shared a frozen install/registry, namespace and two-publication seed. Again, each median has three samples:

| Latest matched retention cohort | Median command time |
| --- | ---: |
| Local cold | 23.718 s |
| Previous remote cold | 30.388 s |
| Advisory-retention remote cold | 29.139 s |
| Previous remote warm | 20.213 s |
| Advisory-retention remote warm | 20.485 s |

The new cold arm downloaded **1,463,742,717 bytes in 1,613 chunks**, reused two remote parents, restored 18 analysis and 11 execution hits, retained the same 103 witness blocks and published a child. Mean client CPU was 79.06 CPU-seconds locally, 107.16 for new remote cold and 83.37 for new remote warm. All six binary digests and the publication probe matched. The recorded test run passed 637 cases across 23 selected targets. These observations support a narrow cold-retention improvement; they do not establish statistical significance, tails, server-inclusive savings or a current vanilla comparison. Sources: `docs/jsgette/measurements/skycache-retention-2026-10-27/README.md:39–68,101–148` and its `provenance.json`.

“Cold” in that cohort means a fresh JVM/empty graph and less than 0.1% resident Linux publication/backend metadata pages. Source/repository inputs and ordinary action/output-cache services were warm; host/device caches were not flushed. Setup outside timing included seed preparation, server startup/clean and excluded warmups; finalization/pruning was inside timing. These are single-host WSL/loopback measurements. The large-corpus experiments used raised limits and do not establish default-limit usability.

At review time, the independent `//...` experiment had preparation and action-prime data but no finalized matrix. Its intended workload excludes blocked private packaging dependencies and must be reported with those exclusions. No result is inferred here. Current matched vanilla, WAN behavior, small-subset transfer coverage and peak memory remain open.

### Optimize critical-path work, not a counter in isolation

Let `B` be full publication bytes, `fB` the demanded bytes after chunk amplification, `r` effective throughput, and `S` demand-discovery/RPC stalls. A useful hypothesis is:

```text
bulk helps when avoided S + avoided remote-read work
  > (1 − f)B/r + extra authentication/materialization/publication/GC work
```

This is a selection heuristic, not an additive reconstruction of elapsed time. Transfer, CPU and filesystem work overlap. A retained-cache run can download zero payload bytes while remaining expensive. An all-hit build has little execution beneath which to hide validation. A single requested root can demand almost every witness, so counting “entries hit” does not measure proof size.

The latest mean setup/middle/finalization partitions were 0.406/20.521/2.894 s locally and 1.309/24.036/3.324 s for new remote cold. Neither “middle” nor “finalization” is pure validation. An earlier final-validation replay observed 418,229 witnesses and 5.05 GB hashed, but that is an older experiment, not a new current-command counter. Record source capture, admission, demanded validation and final publication as separate spans. Worker-seconds measure resource work; only dependency-aware spans reveal the wall-clock critical path. Sources: retention README cited above and `docs/jsgette/measurements/skycache-validation-2026-10-26/README.md:14–36,48–70`.

## 3. Separate storage availability from semantic validity

### The acceptance invariant

For a demanded entry `e`, return a cached value only after compatible identity, authenticated bytes, complete entry-specific guards and current dependency observations have been established, with lifecycle protection still held. Cached edges may guide bounded prefetch before this point, but cannot define authoritative reachability or justify omitting new edges. Adding a file to a glob, changing a repository binding, or creating a formerly missing file can change the dependency graph itself.

This invariant does not require all output blobs to be present before returning execution metadata. The current implementation separately recovers missing output CAS blobs through action rewinding. Preserve that distinction. It also does not promise transactional isolation against arbitrary concurrent filesystem mutation; retain the existing stable-input assumptions and explicit final-publication freshness boundary. ([Skyframe dependency recording](https://bazel.build/reference/skyframe); `L/LocalAnalysisCacheClient.java:139–169`)

Use separate state for publication storage and entry validity:

```text
Publication identity: namespace + publication ID
Remote origin: verified descriptor closure + endpoint/instance scope
Byte backing: remote source / verified retained chunks / complete local closure
Entry validity: unvalidated → checking → valid(epoch, proof)
                                      ↘ miss or retryable-unavailable
```

Local availability must not erase origin, imply semantic validity, or imply a complete durable closure. Missing storage and mismatched inputs are different failures. A failed candidate should not hide a later revision; missing repository data remains retryable. Completion must check that the session remains open. Previously loaded values must continue validating their recorded `previousRoots`, not a newer candidate's proof. See `L/LocalAnalysisCacheClient.java:88–125,139–181`.

### Preserve parent provenance during prefetch and hydration

Today `candidate.remote()` influences both export strategy and eligibility for local child installation. These are independent questions. Replace that overloaded classification with optional authenticated origin plus an independently leased complete-local-closure capability. At finalization, choose remote descriptor reuse whenever the origin matches the export scope, even if its bytes are local. Independently permit local child installation only when every required local parent is complete, durable and pinned. Refactor `publishPending(localParents, remoteParents)` into explicit export sources, or derive disjoint export routes from richer state. See `L/LocalCacheSession.java:875–916` and `L/RemoteSkyCache.java:1215–1375`.

Initially preserve provenance within one invocation. Across restart, rediscover and verify descriptor bindings. An arbitrary same-ID sidecar is not authoritative; a durable provenance format would need authenticated binding, identity and GC rules. Matching paths on two machines do not establish repository identity. Descriptor reuse also does not establish that remote CAS still retains every parent chunk.

If a parent disappears before commit, use bounded repair only when the protocol supplies an explicit repair contract and complete authenticated local backing exists. Otherwise preserve optional-publication failure behavior. Do not publish an incomplete local closure, silently merge conflicting identities, or invalidate an otherwise correct build because optional publication failed. Current installation checks collisions before exposure, forces durable data and exposes manifests parent-first; preserve those guarantees. See `L/RemoteSkyCache.java:1459–1521` and existing parent-reuse tests at `T/local/RemoteSkyCacheTest.java:1378–1624`.

Prefetch must also avoid declaring every downloaded parent semantically used. `readCandidates()` currently adds parents to `usedParents`; speculative transfer must use an availability-only path. Track references actually consumed by accepted values or the new serialized child separately, preserving every parent the child references. Otherwise prefetch can inflate inherited evidence and make final validation slower. See `L/LocalCacheSession.java:523–563,851–955`.

### Prefetch a selected closure while demand proceeds

Start with the existing virtual `FileSource` over retained chunks. Prioritize actual demanded ranges and their indexes/evidence, then likely next chunks from one preferred publication closure. Do not eagerly download every historical candidate in a discovery page. Keep ordinary revision fallback and allow demand to promote queued prefetch without a duplicate request. Reserve transfer slots, queue capacity and attempted-byte/RPC-time headroom for demand.

The first experiment should compare current demand loading with bounded prefetch into the same representation. A second comparison can add durable hydration. This isolates scheduling from pack reconstruction, duplicate disk storage and changed local-reader behavior. Store verified chunks promptly; queued futures should retain small descriptors rather than all payload arrays. Bound decoded-record bytes separately from network concurrency.

**Do not add workers inside the existing write-locked `materializeExclusive()` path.** `hydrate()`/`materialize()` currently holds the lifecycle write lock while sequentially downloading and reconstructing files; worker calls to `download()` request the lifecycle read lock. A coordinator waiting for those workers while holding the write lock can deadlock. Register the operation and acquire immutable descriptor/pin handles under a short lock, perform bounded I/O without that lock, then publish the backing transition under a short exclusive section. Shutdown must cancel and join registered operations before releasing pins/channels or deleting stages. See `L/RemoteSkyCache.java:1121–1199,1540–1586`.

Protocol maxima are independent resource controls: up to 1 MiB chunks, a 1 MiB encoded bundle, 4 GiB logical closure, 64 publications, 16,384 files and 32,768 chunk references in v1. The transport defaults to 100 concurrent RPCs, but read sessions charge aggregate active RPC time, including overlapping requests. More concurrency can exhaust that budget sooner. Charge failed attempts and speculative work; do not reserve correctness for raised-budget benchmarks. Smaller chunks also increase descriptor count: a 4 GiB closure at 64 KiB already needs 65,536 references before bundle overhead. See `R/SkyCacheProtocol.java:17–21,53–121` and `R/SkyCacheTransport.java:46–55,236–249,325–397,474–529`.

## 4. Reduce proof work before expanding execution speculation

### Admit entries using complete local guards

Split structural publication discovery from semantic entry admission. Structural discovery still enforces format/namespace compatibility, integrity, metadata bounds and lifecycle pins. Entry acceptance then validates the external/repository observations required by that entry and its decoding. Add a versioned guard root to the entry envelope and a capability asserting complete guard coverage. Legacy or unsupported envelopes retain broad admission.

Before removing a global guard, enumerate the writer, codecs, repository resolution and deserialization assumptions it protects. Absolute file evidence alone does not identify the repository bound to a logical name: the old physical tree can remain present even after the binding changes. An intermediate design can partition guards by repository and record each entry's required repositories, while leaving unattributed absolute guards broad. Global guard removal requires a coverage argument across every entry kind, not merely passing file-leaf tests. Relevant seams are `L/LocalCacheSession.java:595–829`, `L/LocalInputSnapshot.java:94–97,376–424` and repository-binding regression `T/local/LocalCacheSessionTest.java:1384`.

This also enables witness-block loading on demand, but only after metadata provides an authenticated mapping from entry guard roots to required blocks. Downloading all witness blocks during `prepare()` and then checking fewer witnesses would remove observation work while retaining metadata-transfer overhead. Measure both separately.

### Store exact certificates for expensive reusable roots

The current content-addressed proof DAG is already correct and shared. An alternate representation should skip internal metadata records while retaining complete terminal observations. Bind each certificate to format, namespace, entry key, entry-value digest, dependency root, guard root, dictionary digest and witness-block descriptors. Cryptographic binding prevents mismatched records; it does not prove that the writer enumerated every semantic dependency. The producer must construct certificates from the same complete typed evidence as the existing validator, and its coverage must be reviewed and differentially tested.

Use a publication dictionary of exact witness identities: logical path, operation and expected digest, together with explicit repository/path-resolution prerequisites. A path-only set loses listing, missing-path, mode and link semantics. Authenticate dictionaries before using compact IDs, and qualify IDs by dictionary identity. Validate prerequisites before allowing memoized leaf results to satisfy a proof.

Do not flatten the transitive witness set of every graph node. That can create quadratic storage and writer work. Select only reusable analysis/execution roots whose proof traversal is expensive in traces; encode sorted ID runs or compressed bitmaps in canonical shared blocks. Cap selected roots, construction work, dictionary size, per-root summary bytes and total publication overhead. Intermediate nodes and oversized summaries retain the existing DAG. Sharing writer union results can help, but a reader that still follows the original union DAG has not eliminated traversal.

For selected roots, the target reader cost is approximately **certificate blocks plus distinct demanded witness observations**, rather than all internal proof records. This does not make fresh validation proportional to changed files. Without a complete trusted change oracle, each independent relevant observation can matter in the worst case. Account for writer CPU, storage amplification and publication time before promoting the representation.

The analogy is deep constructive traces in *Build Systems à la Carte*, which separate rebuilding decisions from scheduling and can validate terminal inputs while skipping intermediates. The paper also explains determinism requirements and the loss of intermediate early cutoff. Preserve a hybrid: failed certificates fall back to ordinary validation/recomputation and change pruning, since changed inputs can still produce an unchanged intermediate value. The paper motivates this design distinction; it does not establish equivalence to Skycache's typed evidence or predict a speedup. ([Mokhov, Mitchell and Peyton Jones, JFP 2020, sections 5.4 and 6](https://simon.peytonjones.org/assets/pdfs/build-systems-jfp.pdf))

### Use the evaluator's existing asynchronous contract

`SkyValueRetriever` already stores lookup futures, registers unfinished work through `dependOnFuture`, restarts and deserializes only after completion. Make fresh `LocalAnalysisCacheClient.lookup()` return a real pending future from bounded validation/I/O work. Reuse this machinery rather than adding an execution scheduler. Worker threads must not call Skyframe environment APIs outside their evaluator contract. See `S/SkyValueRetriever.java:308–451` and tests beginning at `T/SkyValueRetrieverTest.java:226,453,641,798`.

Coalesce by entry revision/proof and invocation epoch. Maintain existing subtree ownership and cycle handling; avoid allocating a future for every proof node. Bound task count, retained completions and bytes as well as worker count, and separate filesystem observation, decode CPU and transport concurrency. Queue saturation must backpressure or fall back conservatively. Canceling one waiter must not cancel shared work still required by another. Session close joins owned work before releasing leases. Preserve the separate synchronous retained-value invalidation contract until that upstream interface is deliberately redesigned.

Asynchrony reduces blocking and exposes parallel roots, but one broad top-level proof remains broad. Skymeld is already enabled with 100% allowed overlap in this snapshot. `BuildDriverFunction` still waits for top-level analysis, conflicts, compatibility and post-analysis dependencies before execution. Executing below an unfinished top-level target needs proven artifact demand and conflict readiness; simply enqueueing every completed subtarget can do unnecessary or unsafe work. Keep that extension separate from cache optimization. See `src/main/java/com/google/devtools/build/lib/buildtool/BuildRequestOptions.java:516–540` and `F/BuildDriverFunction.java:174–318`. ([Bazel 7 Skymeld announcement](https://blog.bazel.build/2023/12/11/bazel-7-release.html))

### Replace source capture only after observation coverage is complete

The whole-source scan is a distinct architectural target. Overlapping capture with transport is a smaller experiment, provided no dependent consumption crosses required invariants. Merely moving the scan to finalization loses the start-of-command comparison. A stronger replacement needs a complete observation/read set for every result that can be published, including observations controlling dependency discovery, with explicit before/after stability or a trusted snapshot/generation protocol. BUILD, `.bzl`, module/configuration files, repository-rule inputs, glob listings, negative observations, symlinks and modes all require coverage. Keep the existing scan for uncovered domains.

A trusted complete change journal could later reuse a previous proof when its relevant observation set has no intersection with the complete delta. This requires uninterrupted coverage, stable identity and appropriate handling of structural changes. Watcher overflow, unknown checkpoints, external roots, rename/link ambiguity and restarts without a trusted generation force re-observation. Git diff, size and mtime are insufficient. A probabilistic filter may conservatively request extra checks; it must never turn incomplete coverage into validity. Fresh final-publication validation remains until a separate consistency argument replaces it. Detaching finalization after command return would also require a new lifetime/freshness protocol and is outside this plan.

### Remove concrete read amplification before changing the format

`FileSource.isCached()` and `read()` currently scan chunk descriptors from the beginning. Build checked cumulative `long` offsets once and binary-search the first intersecting chunk: per-range descriptor work changes from `O(C)` to `O(log C + k)` for `C` chunks and `k` intersections, with bounded `O(C)` metadata. Charge the array against admission budgets and preserve unequal chunk lengths, zero-length and EOF semantics. This is a narrow independently measurable change; its command-level importance is unknown. See `L/RemoteSkyCache.java:424–467` and `T/local/RemoteSkyCacheTest.java:1327`.

If profiles show repeated covering-block work, compare bounded adjacent-record batching with a small cache of copied, authenticated immutable block bytes. Never trust a mutable mapping, mtime or open descriptor as authentication. If newer-segment probes dominate, test authenticated routing metadata before broad compaction. A membership synopsis may skip a segment only with a valid no-false-negative guarantee; positives require normal lookup. Missing indexes must not silently expose older values. See `L/SegmentedFingerprintValueStore.java:300–373` and `T/local/SegmentedFingerprintValueStoreTest.java:103–128,255–285`.

Optional target/configuration prefetch plans can record historically demanded chunk IDs while retaining demand fallback. They are scheduling hints, not validity proofs. Replay traces before changing layout or protocol. FlacIO's collective loading of workload-specific container images supports this experiment and warns against indiscriminate expanded prefetch, but its kernel mechanisms and performance numbers do not transfer to Java Skycache. Parent-chain compaction and shared durable chunk formats should follow measured index/closure amplification; both need new immutable identities and pin-aware GC. ([FlacIO, FAST 2025](https://www.usenix.org/system/files/fast25-liu-yubo.pdf))

## 5. Promote each change through a matched experiment

### Implementation order and rollback boundaries

| Stage | Concrete change | Evidence required before promotion | Conservative fallback |
| --- | --- | --- | --- |
| P0 | Add critical-path attribution and freeze matched baselines | Source, binary, seed and output provenance; current vanilla and cache-disabled arms | Existing behavior |
| P1 | Separate origin/backing; bounded priority prefetch with unchanged validator | Parent descriptor reuse survives hydration; total command improvement with identical publication | Current demand retrieval |
| P2 | Indexed chunk ranges; separately test pending fresh lookup | Fewer scans/blocking without memory, contention or shutdown regressions | Linear range lookup or synchronous fresh validation |
| P3 | Versioned per-entry guards and selective admission | Complete codec/repository guard coverage; shadow/differential agreement | Legacy broad admission |
| P4 | Capped exact certificates for expensive roots | Reader gains exceed writer/storage cost; complete typed-evidence equivalence | Existing proof DAG |
| P5 | Trace-derived bundles, batching/routing/compaction as justified | Measured read amplification identifies the specific mechanism | Existing format and virtual source |
| P6 | Complete observation capture or trusted journal | Coverage and epoch proof, including dependency discovery and final publication | Existing whole-source scan |

Keep formats additive and capability gated. Unknown/absent certificates, guard maps or hints must not authorize hits. Preserve existing immutable publications and their leases; a rollout should be reversible by disabling the fast path, without deleting valid data or rewriting parents in place. A full live-graph snapshot and finer Skymeld scheduling remain separate research tracks.

### Benchmark factors and accounting

Use a staged factorial design rather than combining every change in one winning binary. First hold validation constant and compare demand retrieval, prefetch over virtual files and prefetch plus durable hydration. Hold remote publication behavior constant in the primary arms; a publication-disabled arm is diagnostic only. Then independently vary asynchronous lookup, selective admission and certificates, followed by a combined interaction experiment.

| Factor | Required cases |
| --- | --- |
| Baseline | Upstream vanilla; same modified binary with Skycache disabled; local; current remote; candidate remote |
| Demand | Small subset; historical six binaries; `//...` with declared exclusions; changed requested roots; one broad root |
| Cache state | Fresh JVM/empty graph with cold metadata; fresh JVM with retained metadata; live-server incremental |
| Inputs | Unchanged; source edit/revert; comment-only cutoff; BUILD/config/toolchain changes; glob/missing-path/link/repository changes |
| Deployment | Loopback; measured RTT/bandwidth; CPU/memory constrained; default and raised limits separately |
| Completion | Read hit; required child publication; missing output CAS blob and rewind; cancellation/failure |

Measure complete command exit, first valid demanded value, build completion and first useful execution. Include initialization/import and final publication. Report client/helper/service CPU separately and together for throughput claims; capture peak RSS, live decoded bytes, task/pin/channel counts and duplicate disk occupancy. Attribute source-capture bytes/time, broad and demanded witness counts, proof records, unique input bytes hashed, chunk coverage, unused downloads, descriptor probes, index opens, repeated block authentication, queue waits, deserialization allocation and parent reread/rehash/upload work. Do not substitute total worker durations for the critical path.

Rotate paired arms against identical frozen seeds with no overlapping benchmark jobs. Archive all accepted samples, source/binary hashes, override manifests and output digests; declare exclusions beforehand. Three samples indicate direction only. Predeclare practical improvement and regression budgets, then run enough independent pairs for a confidence interval. For example, a 5% median command-time target is an engineering choice that must exceed measured noise, not a predicted result. Tail claims need substantially more samples and representative conditions.

### Correctness and failure gates

Preserve the current tests for previous-root invalidation, stale-candidate fallback, content changes preserving metadata, resolution parents, deep/cyclic concurrent proofs, late repository availability, cancellation and late-session acceptance. Relevant seams include `T/local/LocalDependencyValidatorTest.java:237–283,338–413,462–596,616–780,785–1059` and `T/local/LocalCacheSessionTest.java:1384,1565–1649,1818–1838`.

New differential fixtures must create a new glob member or formerly missing path; change BUILD/configuration/toolchain/environment dependencies; and retarget a repository while keeping its old physical tree present. Selective admission must let an independent repository-A entry hit after repository B changes only when A's complete guard coverage is established. Compare accepted values, action/dependency behavior and final outputs with an uncached reference under each mutation.

Reader fixtures must mix entry/root revisions, forge or reuse IDs across dictionaries, omit structurally required guard references, truncate or corrupt blocks, and exceed budgets; these must reject or fall back. Separately, producer mutation tests must deliberately omit semantic dependencies and require the differential oracle to detect disagreement with the existing validator before promotion. A consistently encoded but incomplete certificate is not inherently detectable by the fast reader. Test fallback on unsupported formats and writer limits.

Storage/lifecycle fixtures must combine local and remote parents, hydrate then republish without losing provenance, preserve local-install eligibility with retained origin, evict remote parents before commit, collide identities, cancel during every state transition and close during active hydration. Concurrent demand/prefetch must share downloads and support promotion. Failed attempts must consume budgets; corrupt unused prefetched chunks must neither poison unrelated valid entries nor enlarge the child's semantic parent closure. Keep existing tests such as `childReusesOnlyUsedRemoteDescriptorWithoutMaterializingParent`, `mixedClosureReuploadsEvictedLocalAncestorAndKeepsWriterPinnedThroughCommit` and `missingUnusedBytesOfUsedParentSuppressesChildPublicationWithoutFetchingThem` (`T/local/RemoteSkyCacheTest.java:1378–1624`).

Stop bulk-by-default when low-demand workloads lose more to extra bytes than they save in stalls; retain an adaptive or opt-in policy. Stop certificate rollout when writer/storage amplification erases reader savings. Stop asynchronous expansion when queue/ownership costs grow without reducing the measured critical path. Any stale accepted value, unbounded retained state, lost publication guarantee or deadlock blocks promotion regardless of speed.

The first useful deliverable is therefore a provenance-preserving prefetch path with unchanged semantic validation and matched end-to-end evidence. Its result determines whether transfer scheduling deserves further investment. Selective guards and exact certificates are the deeper route to avoiding work; source-scan replacement becomes justified only after the observations that make cached dependency edges trustworthy are explicitly represented.
