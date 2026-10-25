# Scaling SkyCache beyond the current publication limits

**Implementation update:** Compact witness blocks, segmented packs and separated bounded validation memo retention are now implemented and measured. See the [implementation and benchmark report](skycache-large-tree-2026-10-25.md). Selective per-value repository-binding validation remains future work. The proposal below records the earlier design state.

25 October 2026. Design proposal based on the existing implementation and retained Datadog evidence. No implementation changes, tests or benchmarks were run for this proposal. All task build/benchmark processes have been stopped at the user's request; Windows work is deferred.

## Recommendation

Use a small publication manifest that points to bounded, immutable metadata blocks and multiple pack segments. First introduce compact storage while preserving the current validation rules. Then move external-input validation from whole-publication admission to the dependency closure of each value actually reused.

This addresses both capacity and the user's build-without-bytes objective. Compression alone addresses encoded size; it does not avoid checking hundreds of thousands of unrelated input witnesses.

## What failed

The retained 237,112-configured-target Datadog build produced correct binaries but did not publish a reusable SkyCache entry. Inspection reconstructed 418,098 external/absolute input witnesses requiring **77,546,670 bytes (73.95 MiB)**, before final repository-binding witnesses. The current manifest payload cap is **64 MiB**.

Three Linux-header repository views account for 40,366,811 bytes; absolute paths into the shared repository cache account for 22,402,643 bytes. Together they account for approximately 81% of the reconstructed witness bytes. These views can share content, but their logical identities and repository bindings cannot simply be erased.

The same pack holds **1,664,716 unique records**, exceeding the separate **1,000,000-entry** seal limit. The observed error is the manifest rejection, which runs before sealing; the pack limit is an additional blocker established from the existing records and source. See the [larger-scope report](skycache-datadog-wide-2026-10-25.md) and [pack diagnosis](measurements/skycache-datadog-wide-2026-10-25/datadog-wide-2026-10-25-attempt2/pack-diagnosis.json).

## Option 1: Raise both limits as a temporary unblock

An opt-in 128 MiB manifest payload limit and two-million-entry pack limit would be reasonable experimental starting points for this retained scope. They are proposed values, not demonstrated sufficient limits: the final manifest was not retained, and input-validation limits can independently cause misses.

Update writer, reader and garbage-collector bounds together. The GC has its own manifest parser and size/count/scan bounds. Admission also has a default 256 MiB aggregate manifest-plus-index budget. At 48 bytes per index entry, the observed pack would require approximately 76.2 MiB for its index alone.

This is the smallest change but keeps full witness-list admission, whole-manifest allocations, and a large mutable writer index. The current writer builds a ByteArrayOutputStream and then copies its payload; the reader similarly retains encoded and decoded representations. Raising a file-size cap does not bound heap consumption. Keep bounded allocations and report capacity rejection explicitly. Do not turn all limits off.

Use this only if an immediate experiment is needed after testing resumes; it is not the recommended long-term layout.

## Option 2: Compact and block the witness representation

Replace repeated full strings with a versioned binary representation:

- Store SHA-256 values as 32 bytes rather than hexadecimal strings. Encode `missing` and `not-directory` as explicit tagged states; they are valid current witness values.
- Store repository/root identifiers in a dictionary and paths as relative components. Prefix-compress sorted paths and use bounded variable-length lengths and counts.
- Deduplicate identical content records, while preserving distinct logical path, operation, symlink-resolution and repository-binding evidence.
- Put witnesses into independently checksummed, optionally compressed blocks. Maintain both encoded and decoded limits. A small top-level manifest references the block directory and publication parents.
- Stream writing and block decoding so the format does not require multiple full-manifest byte arrays or an eagerly allocated list of every witness.

Compression ratios have not been measured. This approach can initially retain exactly the present eager admission/final-validation semantics, making it a narrower storage change. It reduces disk and allocation costs but cannot, by itself, eliminate global input-validation work.

## Option 3: Segment the value packs

Allow one publication to own multiple immutable pack segments. Rotate a segment by bounded entry count and byte size, seal its index, and release its mutable writer map. Spill or sort bounded chunks if concurrent writes would otherwise leave every record in one in-memory map.

Use a bounded index directory to route fingerprint lookups to the relevant segment. Open/cache index pages under an explicit memory budget instead of loading every segment index eagerly. Dividing one huge index into files and immediately loading all of them does not improve RAM use.

Keep segments beneath one logical publication so the existing 64-publication admission limit is not accidentally consumed by internal segments. The publication header becomes visible only after all referenced files are sealed and durable. Reader leases and GC must retain all segments, metadata blocks and transitive parent publications reachable from that header. Missing or corrupt dependencies cause a safe miss.

This removes the single-pack entry ceiling while preserving bounded individual files. It needs to accompany either the temporary cap increase or the new witness layout.

## Option 4: Validate only the evidence required by a reused value

Currently `LocalInputSnapshot.globalWitnesses()` promotes **every non-workspace witness** into the publication manifest. `LocalCacheSession` checks that list before admitting the candidate, even when a lookup needs only one small part of it. That is the architectural scaling problem.

Keep the root manifest concerned with format/version, namespace/configuration identity, parent references and metadata/pack roots. Retain content evidence in the existing dependency DAG, or in blocks referenced by that DAG. When a cached value is selected, validate its complete input dependency closure, memoize validated subtrees for the invocation, and register the evidence actually used for final publication validation. A changed unrelated subtree should not invalidate every value in the publication.

Do not implement this by merely deleting the global witness filter or skipping admission checks. The current global repository-binding pass is a correctness guard: a witness for bytes at an absolute shared-cache path does not prove that the logical repository name still resolves there. Every affected cached value must carry the repository binding and full symlink/path-resolution evidence needed to authorize its reuse. Preserve content, directory-listing, negative-lookup, mode, environment/configuration and final-input-stability checks.

Content-addressed Merkle blocks are useful for sharing evidence and skipping already validated subtrees within an invocation. An old root hash, Git revision or unchanged directory timestamp is not proof that a mutable tree still has the same contents. Skipping fresh validation across invocations requires a verified immutable repository identity or a trustworthy change detector with conservative fallback.

This stays separate from remote output availability. SkyCache hits should still avoid GetActionResult sweeps; minimal should avoid downloading outputs, toplevel should materialize requested outputs/runfiles, and missing required CAS blobs should use rewind. Rewind repairs missing output bytes; it does not make stale source or repository evidence safe.

## Suggested delivery order

1. Introduce a new publication format with a small header and compact witness blocks, retaining current validation semantics initially. Keep old readers/writers isolated by format namespace; preserve old caches for fallback.
2. Add multiple bounded pack segments, bounded writer buffers and lazy index access. Update leases, parent closure and GC in the same change.
3. Move repository-binding evidence into each relevant dependency closure, then make admission and validation selective. Treat this as a correctness change with its own review.
4. Audit the next limits: the validator currently bounds records/success memoization at 250,000 and per-lookup bytes at 128 MiB. These are potential subsequent miss causes, not failures observed in the retained large-scope experiment. Separate optional memo capacity from traversal/work bounds, and preserve conservative fallback when a required traversal exceeds its budget.

When the user resumes testing, use the retained 220k/237k scopes to verify publication, hit eligibility, metadata admitted, witnesses actually checked, peak RAM and after-clean elapsed time. Add targeted evidence for unrelated-subtree changes, repository rebinding, symlink changes, missing CAS rewind, interrupted publication and concurrent readers/writers with GC. These checks are proposed only; none were started.

## Source locations inspected

Source tree `f6a4aaf7fafcf54b4e06097e1eddb2710a07c74d`, corresponding to implementation snapshot `71521fabb3420707c39d73329fa9aa9622e8c382`.

- `LocalInputSnapshot.java`: global witnesses at line 86; repository bindings at line 148; path-resolution evidence at line 279.
- `LocalCacheSession.java`: admission from line 619; global witness encoding and size rejection from line 780; whole-manifest reader from line 872.
- `PackedFingerprintValueStore.java`: entry/index bounds from line 59; index admission from line 96; sealing from line 249.
- `LocalCacheGarbageCollector.java`: independent publication parser from line 306.
- `LocalDependencyValidator.java`: default bounds from line 39 and complete dependency traversal from line 143.

All Java files are under `src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/`. The [published implementation patch](measurements/skycache-execution-metadata-2026-10-25/implementation-current.patch) retains this source snapshot.
