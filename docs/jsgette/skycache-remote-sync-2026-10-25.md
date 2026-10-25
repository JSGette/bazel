# SkyCache local/remote integration — 25 October 2026

The local implementation now includes the two tested allocation optimizations and the remote metadata implementation through `91d51164d3d315dc2d79bbe3d40af874b9dae9e0`. Tested product integration commit: `6931630c83d9222049e30bf7c50190ae810519f5`. Final branch head after documentation synchronization: `c635cafd98789c51646aa1938b6ed8d7ddddfaa4`. All **562 cases across 21 test targets pass**, as do real remote recovery, execution rewind and concurrent-worktree probes.

## Integrated behavior

- Preserve the lookup-key/SHA-256 reuse and uncompressed array decoding from `36a73d8fb4`. Compressed and chunked values retain streaming.
- Add the persistent SkyCache catalog, REAPI metadata CAS transport, authenticated chunk/record reads and selective segment fetching from remote commit `6a380e6eeb`.
- Include `91d51164d3`: deduplicate puts against bytes already fetched, then publish new children using existing remote parent descriptors. Unavailable cached data stops a lookup at the newer segment; deduplication never initiates a remote fetch.
- Keep sparse remote parents out of the durable local catalog and idle-eviction backing. Publication failures remain optional-cache failures. Source/configuration validation, final input rechecks, checksums and action rewind remain active.

The first merge was conflict-free. The parent-reuse update conflicted in the packed and segmented lookup functions. The resolution passes a single prepared key and digest through both ordinary and cached-only reads while retaining the newer-segment ordering rule. [Resolution and patch](measurements/skycache-remote-sync-2026-10-25/update/resolution.json), [source provenance](measurements/skycache-remote-sync-2026-10-25/update/source.json).

The local format remains `leased-v2` / `publications-v4`; the namespace and 256 MiB admission budget are unchanged. No 64 MiB budget experiment is introduced. Remote metadata is opt-in through `--experimental_remote_skycache` with a local staging directory. The remote catalog is a custom service supplied by the development worker; the Datadog action cache and BES remain Cache & Carry. Independent clones on different machines are still outside the supported sharing boundary.

## Final Datadog regression check

Six binaries: agent, cluster-agent, process-agent, trace-agent, security-agent and system-probe at Datadog `0cde96beb70963d0838119c308b9d0422ee66be0`. Full evaluation configures 237,112 targets including aspects. **Twelve measured builds**, three alternating repetitions per implementation and output policy, after four excluded preparations. This compares the final merged binary against optimized local commit `36a73d8fb4`; remote metadata is disabled in both arms.

Each build follows server shutdown and synchronous `bazel clean --noasync --noexpunge`. The harness verifies zero graph nodes/edges, empty execroot, no local action cache, and preserved SkyCache publications. Clean starts the JVM; its time is recorded separately. Both arms use native WSL storage on E:, the same resolved lockfile/output base/tools tree, JDK 25, a 6 GiB JVM heap, 16 jobs, no disk cache and Cache & Carry action cache/BES. Process-tree PSS is sampled every 100 ms. Warm OS/repository caches and a small sample limit generalization; PSS excludes the host page cache.

| Policy | Implementation | Median build s (range) | Median analysis s | Median peak PSS MiB (range) |
|---|---|---:|---:|---:|
| minimal | Optimized local baseline | 14.005 (13.734–14.021) | 11.806 | 3183 (2933–3191) |
| minimal | Merged SkyCache, local mode | 13.920 (13.831–14.070) | 11.763 | 2927 (2894–3073) |
| toplevel | Optimized local baseline | 23.782 (23.780–23.989) | 12.077 | 3129 (3104–3225) |
| toplevel | Merged SkyCache, local mode | 23.726 (23.475–23.875) | 11.945 | 3168 (2855–3184) |

Relative median changes: minimal: -0.6% build time, -8.1% peak PSS; toplevel: -0.2% build time, +1.3% peak PSS. These are merge-regression measurements, not evidence of a durable memory reduction or remote metadata speedup. No new post-GC live-heap comparison was collected here.

Every measured SkyCache build restores **17 configured-target values and 11 execution values**, writes no new records, and performs **zero GetActionResult requests**. Minimal performs zero output CAS reads and leaves all six final executables absent. Toplevel fetches ten blobs totaling 422,596,080 bytes; all six executable hashes match the vanilla reference. Both policies leave intermediate `.a`/`.x` files absent. Every observed FindMissingBlobs request in the measured matrices was checked against that run's BES profile digest.

[Final runs, commands, checks and metrics](measurements/skycache-remote-sync-2026-10-25/update/datadog-six/results.json), [computed summary and profile checks](measurements/skycache-remote-sync-2026-10-25/summary.json).

## Earlier merge and vanilla control

Before the remote parent-reuse commit arrived, integration `0c80d7fe68` completed a separate 18-build matrix with three repetitions per cell. Its vanilla arm is upstream `a5c50fcb65`. These results belong to that intermediate binary and are **not pooled with the final comparison**.

| Policy | Implementation | Median build s (range) | Median analysis s | Median peak PSS MiB (range) |
|---|---|---:|---:|---:|
| minimal | Optimized local baseline | 14.922 (14.422–18.627) | 12.686 | 3331 (2839–3394) |
| minimal | Merged SkyCache, local mode | 14.624 (14.169–14.931) | 12.374 | 2898 (2871–3352) |
| minimal | Upstream vanilla | 13.473 (13.321–13.920) | 5.304 | 3599 (3480–3734) |
| toplevel | Optimized local baseline | 24.881 (24.275–24.942) | 13.196 | 2915 (2769–3056) |
| toplevel | Merged SkyCache, local mode | 23.882 (23.038–24.727) | 12.660 | 2510 (2367–2872) |
| toplevel | Upstream vanilla | 26.125 (25.430–26.482) | 5.433 | 3620 (3596–4031) |

All earlier runs passed the same clean-state and output checks. Vanilla made 16,697 action-result calls per build. The first optimized-local minimal result was 18.627 seconds and is retained. An initial tools preflight detected the newly embedded `skycache.proto`; it stopped before any build or lockfile edit. The corrected comparison pins the merged tools superset for every arm and populates each binary's own SkyCache. All 574 pre-existing tool files/symlinks were identical; the protocol definition was the only added file. Each binary needs its own cache population because upstream entry keys include the installation MD5.

[Intermediate matrix](measurements/skycache-remote-sync-2026-10-25/datadog-six-matched-tools/results.json), [excluded preflight](measurements/skycache-remote-sync-2026-10-25/embedded-tools-preflight.json).

## End-to-end verification

The final build/test run covers local storage, validation, leases/GC, serialization, chunked values, the remote transport, catalog persistence and server behavior. **562 cases / 21 targets**, with zero failures. Bazel and the development worker build successfully. [Build provenance and case counts](measurements/skycache-remote-sync-2026-10-25/update/build.json); XML and logs are retained alongside it.

The real remote probe uses six linked worktrees with independent output bases/staging caches and action caches disabled. Every output matches its expected digest, including the changed BUILD rule. Restart preserves receipts; evicted metadata and an offline server fall back successfully.

| Phase | Analysis hits | Private writes | Deduplicated puts | Materialized parents | Reused parent descriptors |
|---|---:|---:|---:|---:|---:|
| writer | 0 | 275 | 0 | 0 | 0 |
| reader | 7 | 3 | 6 | 0 | 1 |
| restarted | 7 | 3 | 6 | 0 | 1 |
| mutated | 6 | 19 | 209 | 0 | 1 |
| evicted | 0 | 275 | 0 | 0 | 0 |
| offline | 0 | 275 | 0 | 0 | 0 |

This directly verifies the new parent-reuse mechanism in the combined binary: the unchanged reader still writes three new records, while the changed reader writes nineteen; neither materializes its remote parent. The earlier combined binary wrote nine and 227 records respectively and materialized parents. These small fixtures establish behavior, not a speedup or production-scale bandwidth estimate. [Final remote probe](measurements/skycache-remote-sync-2026-10-25/update/remote/results.json).

The separate execution fixture passes 12 builds (37 commands including clean, graph checks and shutdown) covering minimal/toplevel, missing required/final blobs, unused missing intermediates, source invalidation, symlinks and internal outputs. Fault injection uses a private HTTP CAS. The two-worktree fixture passes 10 phases covering concurrent writers/readers, independent source/Starlark edits, reverts and corrupt-pack fallback with Cache & Carry. [Execution results](measurements/skycache-remote-sync-2026-10-25/update/execution/results.json), [worktree results](measurements/skycache-remote-sync-2026-10-25/update/worktrees/results.json). All fixture services shut down.

## Follow-on performance work

1. **Reuse exact witness evidence.** The earlier profiles still show substantial repeated admission/dependency work. A compact canonical witness index and validation bits can reuse exact `(path, operation, digest)` evidence across these stages. Preserve resolution-parent validation and fresh publication checks. This addresses work and object duplication directly.
2. **Trace executor-owned command state before changing cleanup.** The earlier performance report incorrectly attributed idle retention to `services.client`/`services.session` surviving command completion. `super.afterCommand()` already calls `Services.resetCommandState()` and clears them, including at the profiled baseline. Source inspection instead identifies `SkyframeExecutor`'s reader/manager dependencies and `RemoteAnalysisCacheDeps.LazyResolver` as a retaining path to investigate; the executor resets those fields on the next command. A heap-root/dominator trace is still required to establish retained sizes and choose a safe release point. The measured class histograms remain valid. [Correction with source excerpts](measurements/skycache-remote-sync-2026-10-25/memory-lifetime-review.json).
3. **Profile repeated remote chunk reads and hashing.** A range read currently reloads and hashes a whole cached chunk under a synchronized download method, even when it only needs a small record. A bounded immutable in-memory page cache and per-digest fetch coalescing could reduce allocation and I/O. This is a source-based hypothesis, not a measured bottleneck; preserve digest verification, record checks and cached-only no-network semantics.
4. **Separate logical publication size from bytes transferred.** The largest final Datadog publication object directory is 1,503,171,697 bytes (1.40 GiB), whereas the remote bundle still permits only 256 MiB total logical file bytes. The current remote protocol therefore cannot carry this Datadog publication. Support larger logical closures with bounded metadata, streamed export, explicit transfer budgets and reused parent descriptors before attempting remote metadata performance at this scope. Raising the local admission budget does not solve this bound.

Writer bitmap lifetime, per-entry leaf summaries and demand-driven shared-value restoration remain longer-term candidates from the [performance investigation](skycache-perf-options-2026-10-25.md). The parent-descriptor work has now been integrated, so it is no longer an unimplemented recommendation.

## Reproduction and preservation

[Full integrated patch from upstream](measurements/skycache-remote-sync-2026-10-25/update/integrated-from-upstream.patch), [patch from the previous local implementation](measurements/skycache-remote-sync-2026-10-25/update/integrated-from-previous.patch), [remote merge patch](measurements/skycache-remote-sync-2026-10-25/update/remote-sync.patch), [promotion and final branch state](measurements/skycache-remote-sync-2026-10-25/promotion.json). Patches are verified by reconstructing the recorded Git trees in temporary indexes. Old caches, commits and excluded attempts remain intact; rollback can select `36a73d8fb4` without deleting them.

The [evidence directory](measurements/skycache-remote-sync-2026-10-25/) contains scripts, exact commands, test XML, timings, output inventories, correction records and SHA-256 inventories. Large binaries, immutable caches, raw gRPC/BEP and profiles are preserved in the private archive described by [archive verification](measurements/skycache-remote-sync-2026-10-25/archive-verification.json). Public text is normalized to LF and its staged Git bytes are checked against the published hash inventory.
