# SkyCache upstream integration and refreshed baseline — 25 October 2026

Merged current upstream Bazel before changing the proposed 64 MiB budget. The sync passes 432 cases in 31 test targets plus execution/rewind and concurrent-worktree probes. Fresh Datadog measurements still show mixed performance: minimal is 11.2% slower than upstream vanilla; toplevel is 9.5% faster. No 64 MiB budget or paging change is included.

## Source and integration

- Upstream fetched from `bazelbuild/bazel`: `a5c50fcb65841b505ec72840d83381a704e70afa`, still the handoff baseline.
- Previous local implementation: `d4971808785aaa3cd46bf6dd1419cb65afb4319e`.
- Merge commit: `1aac2aa2ec6fe31af2e979558f06a3b6971c601b`; tree `ea0f321fb662c6f34267a25470ef07bd3d31c0db`.
- Integration checkout: `/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25`, branch `jsgette/skycache-upstream-2026-10-25`. The original `/home/jgett/REPOS/bazel-skycache-v2` / `jsgette/skycache-v2-local` was fast-forwarded after correctness probes passed.
- [Implementation relative to upstream](measurements/skycache-upstream-sync-2026-10-25/implementation-on-upstream.patch), [integration delta from the previous implementation](measurements/skycache-upstream-sync-2026-10-25/integration-from-premerge.patch), [source/test provenance](measurements/skycache-upstream-sync-2026-10-25/source-verification.json). Both patches were applied to temporary Git indexes and reconstructed the exact merge tree.

The merge incorporates all 160 upstream commits since the common ancestor, including the test-configuration key/baseline fix, active-directory selection removal, dense leaf IDs, depth ordering, transitive invalidation-edge reduction, bitmap lifecycle changes, phase metrics and `UploadLayout`. It does not cherry-pick an incomplete subset of the dependency chain.

Three files conflicted: `FileDependencySerializer`, `RemoteAnalysisCacheDeps`, and the serializer test. Resolution retained local content witnesses, external symlink evidence, conditional invalidation writes, execution reuse and upload failure handling. Upstream's centralized `putEntry` now delegates through the retained local conditional-write hook. Five local tests were updated for the new serializer constructor. The first build exposed those constructor mismatches; the second build and complete targeted suite passed.

**Compatibility:** `LocalInputSnapshot` now salts the project namespace with `local-project-v4-content-sha256`, because configuration-key serialized bytes changed. The segmented container remains `leased-v2/publications-v4` and `objects/segmented-v1`. Old publications remain intact under their prior namespace; old and new writers produced separate namespaces in the same cache directory. No cache purge or in-place migration occurred. Rollback uses the previous binary and its previous namespace.

**Selection correction:** `--experimental_active_directories` is ineffective in the merged upstream code. The old architecture text overstated its role: our prior default `async_bidi` uploader already did not filter uploads with that matcher. Top-down retrieval can still avoid traversing a restored value's graph. Experimental selective publication (E4) is separate and is not enabled by this sync.

## Correctness checks

`//src:bazel-dev`, all local SkyCache tests, all serialization/analysis test targets, and `BuildConfigurationKeyBaselineDiffCodecTest` passed: **432 cases / 31 test targets**. The codec/provider tests cover differing concurrent-test flags, test arguments, `run_under`, `test_filter`, transitioned configurations, trimmed sharing and output-directory baselines. See [test XML](measurements/skycache-upstream-sync-2026-10-25/unit-test-results) and [build protocol](measurements/skycache-upstream-sync-2026-10-25/build-tests-2.json).

The [execution probe](measurements/skycache-upstream-sync-2026-10-25/execution/results.json) checked clean/zero-graph metadata hits, both download policies, symlinks to unmaterialized inputs, unused missing intermediates, required missing intermediates, missing top-level outputs, `--noremote_accept_cached`, internal outputs and source mutation. Required missing blobs recovered through rewind; unused missing intermediates triggered no action-result lookup or CAS read. Its destructive eviction faults use a private HTTP cache, not the shared Cache & Carry server.

The [two-worktree probe](measurements/skycache-upstream-sync-2026-10-25/worktrees/results.json) used independent servers and shared segmented storage, with Cache & Carry enabled. It passed concurrent empty-cache writes, restarted readers, retained-value invalidation, independent source/Starlark edits, reverts and corrupted-chunk fallback. Every produced output was checked against expected contents.

## Matched Datadog baseline

Datadog commit `0cde96beb70963d0838119c308b9d0422ee66be0`; six binaries: agent, cluster-agent, process-agent, trace-agent, security-agent and system-probe. Full analysis configures **237,112 targets**. The resolved module lockfile was fixed during the runs and its original bytes restored afterward.

Three rotated repetitions per variant and download mode: **18 measured builds**, plus three excluded preparation builds. Each measurement starts a fresh JVM, runs synchronous `bazel clean --noasync --noexpunge`, and verifies zero graph nodes/edges, empty execroot, no local action cache and unchanged publication hashes. Clean is timed separately. All builds use the same native WSL ext4 output base on E:, JDK 25, 6 GiB JVM heap, 16 jobs and 6 GiB action-memory budget. OS/dependency caches are warm, disk cache is disabled, and Cache & Carry cache/BES is enabled at `grpc://10.55.0.1:8080`, with one connection and 16 concurrent requests. PSS/RSS covers the build process tree, sampled every 100 ms; it is not host or page-cache memory.

Here **vanilla** is a binary built from the exact unmodified upstream SHA above, not the older pre-SkyCache fork used in historical reports. **off** is the merged binary with local SkyCache disabled; **on** enables it.

| Download mode | Variant | Median build s | Range s | Median analysis s | Median peak PSS MiB |
|---|---|---:|---:|---:|---:|
| minimal | vanilla | 12.970 | 12.777–13.419 | 4.914 | 3658 |
| minimal | off | 13.424 | 13.424–13.519 | 5.250 | 3935 |
| minimal | on | 14.423 | 14.004–15.368 | 12.144 | 3285 |
| toplevel | vanilla | 25.420 | 25.268–25.722 | 5.297 | 3689 |
| toplevel | off | 25.473 | 25.372–25.632 | 5.158 | 3546 |
| toplevel | on | 23.012 | 22.777–23.120 | 11.791 | 3058 |

[Full commands, raw per-run metrics and checks](measurements/skycache-upstream-sync-2026-10-25/datadog-six-stable/results.json); [summary](measurements/skycache-upstream-sync-2026-10-25/summary.json). Three repetitions characterize this host/run, not statistical significance or cross-machine performance.

Every measured on-run restored **17 configured targets and 11 execution values**, configured only **24 targets**, and ran 26 internal actions. All six requested executable digests matched upstream vanilla. Minimal left the final binaries and intermediate archives absent; toplevel materialized the requested outputs. The last toplevel outputs were independently rehashed before subsequent profiling cleaned them: [hash verification](measurements/skycache-upstream-sync-2026-10-25/top-output-verification.json).

All six measured on-runs made **zero `GetActionResult` requests**. All three minimal on-runs made **zero CAS reads**. Toplevel on-runs made 10 reads totaling 422,596,080 bytes; intermediates remained absent. `FindMissingBlobs` in these hit runs was verified against the exact BES profile-upload digest, not an output-availability sweep: [checks](measurements/skycache-upstream-sync-2026-10-25/bes-profile-checks.json).

## Why minimal still regresses

The merged minimal hit spends a median **12.144 s in analysis**, versus **4.914 s** for upstream vanilla. Avoiding remote action-result lookups does save work, but local admission, proof traversal and restoring serialized values consume most of that saving. Minimal has no requested output transfer to amortize that cost; toplevel benefits from avoiding the broader normal action path while downloading just the required outputs.

Measured hit counters remain substantial: roughly **605,225 dependency records**, **183,063 additional witness checks**, about **796,900 local store reads** and **247.7 MB of local value bytes**. The successful memo does not fill, and there are no read-budget skips. The current large publication has 1,660,246 records and 418,833 global witnesses. Upstream reduced repeated dependency memo hits from roughly 1.12 million historically to about 632,000, but changed the unique proof-record count only slightly (historically about 609,000). This is not evidence that raising a capacity limit will fix latency.

A separate [verified-hit JFR profile](measurements/skycache-upstream-sync-2026-10-25/hit-profile/profile-summary.json) corroborates local admission and pack lookup/traversal as areas to investigate. Admission samples span about 4.4 seconds; later pack/validation samples span about six seconds. These are sampled observation windows, **not exclusive phase timings**. Asynchronous reads appear on separate worker stacks. The 1,184 Java execution samples include 165 attributed to local pack operations outside the synchronous validator stack and 59 inside dependency validation; method counts overlap. Do not interpret them as wall-time percentages. This profile was taken after refreshing changed external evidence and is excluded from the timing matrix.

The first diagnostic profile was a safe miss: 100 external Python `__pycache__` symlink referents changed from directories to missing. Reproducing the stored fingerprints proves that transition; the process responsible was not identified. There were 906 witness differences overall. It rebuilt and published fresh evidence, so its 86-second elapsed time is **not** a cache-hit regression: [evidence](measurements/skycache-upstream-sync-2026-10-25/minimal-profile/witness-change-explanation.json). The later profile required positive analysis/execution hits, no new writes and no action-result/CAS reads. This also illustrates why selective admission needs sound dependency scoping rather than simply omitting external checks.

## Writer memory and excluded diagnostic sequence

The initial empty-namespace population observed peak PSS of **5,534 MiB before the merge and 7,358 MiB after it**, with elapsed times of 49.84 s and 45.69 s. These are single preparation observations, not a paired performance claim. Upstream's asynchronous uploader uses lazy leaf IDs and intentionally retains bitmaps because graph in-degrees can still change; only the synchronous path can reclaim them early. This is a plausible memory contributor, not a proven attribution. Repeat isolated writer profiling before claiming a writer-memory gain or regression from the reduction algorithm.

An initial sequence interleaved pre-merge and merged binaries. After switching back, the pre-merge reader configured 236,112 targets and wrote 406,980 records despite execution hits; it was not the intended steady full-hit baseline. That sequence was deliberately stopped and retained under [datadog-six](measurements/skycache-upstream-sync-2026-10-25/datadog-six/results.json), with its [stop reason](measurements/skycache-upstream-sync-2026-10-25/datadog-six/stop-reason.json). The published timing table comes exclusively from the replacement stable-upstream matrix. The exact cause of the cross-version graph reconstruction was not isolated.

## Decision before the 64 MiB work

**Fresh benchmarks were necessary and are now complete.** Both serialization semantics and the producer changed, and the matched control is now current upstream. No need to repeat Windows or every historical workload before prototyping the reader budget; a later budget/paging candidate must be compared against this pinned binary, with the second large scope and concurrency rechecked before a broad claim.

The root manifest is already only **5,598 bytes**; it did not approach its 64 MiB cap. The memory question is the expanded witness table and segment indexes: the large publication is charged **149,302,696 bytes (142.39 MiB)**, including **66.37 MiB of expanded witnesses** and **76.01 MiB of indexes**. Two admitted publications total **219,220,742 bytes (209.07 MiB)** of accounting. This is an admission estimate, not an exact heap cap, and the current 256 MiB limit does not preallocate 256 MiB.

A useful next design is a **64 MiB initial allowance per publication**, growing only that publication to the next bounded tier when its authenticated descriptors prove it needs more. Charge actual retained metadata against a separate global cap, rather than reserving every publication's rounded tier. Known descriptor sizes should trigger growth before expensive decoding/validation, instead of repeating a failed full pass. Corruption, checksum failures or invalid evidence must remain misses, not reasons to enlarge a budget.

Actual paging is a separate change: retain compact descriptors, load bounded witness/index blocks on demand, and evict or stream blocks while preserving leases, checksums and full required evidence. Current segment indexes are lazy-loaded but retained once opened; candidate witnesses are fully decoded and globally validated. A hard 64 MiB page cache may churn when broad validation touches almost all metadata. Paging can control resident memory; it does not by itself remove the observed validation work.

Keep the dependency-success memo independent. The prior 250k-entry experiment caused about 82 million repeated proof reads when it stopped retaining successes. Per-publication metadata growth must not reintroduce that traversal thrashing. No budget, memo capacity, validation shortcut or paging implementation was changed in this sync.

## Evidence and recovery

The [measurement directory](measurements/skycache-upstream-sync-2026-10-25/) includes commands, build/test failures and repairs, stable and excluded runs, profiles summarized above, output checks, implementation patches and a SHA-256 inventory. Full binaries, caches, gRPC/BEP records and JFR files are retained in the private archive described by [archive verification](measurements/skycache-upstream-sync-2026-10-25/archive-verification.json), as well as the native WSL evidence directory. Build output bases are excluded from the archive. Existing evidence and caches are preserved.
