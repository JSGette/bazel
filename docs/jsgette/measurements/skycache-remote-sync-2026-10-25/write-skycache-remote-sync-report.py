from pathlib import Path
import json, re

R=Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
D=Path('/mnt/e/scw/docs/docs/jsgette')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
s=json.loads((R/'summary.json').read_text())
src=json.loads((R/'update/source.json').read_text())
build=json.loads((R/'update/build.json').read_text())
promotion=json.loads((R/'promotion.json').read_text())
execution_builds=sum('build' in x['argv'] for x in json.loads((R/'update/execution/results.json').read_text())['runs'])
e='measurements/skycache-remote-sync-2026-10-25'
def table(matrix):
    labels={'integrated-local':'Optimized local baseline','remote-merged':'Merged SkyCache, local mode','vanilla':'Upstream vanilla'}
    lines=['| Policy | Implementation | Median build s (range) | Median analysis s | Median peak PSS MiB (range) |',
           '|---|---|---:|---:|---:|']
    for x in s['matrices'][matrix]['groups']:
        t=x['seconds'];m=x['pss_mib']
        lines.append(f"| {x['mode']} | {labels[x['implementation']]} | {t['median']:.3f} ({t['minimum']:.3f}–{t['maximum']:.3f}) | {x['analysis_seconds']['median']:.3f} | {m['median']:.0f} ({m['minimum']:.0f}–{m['maximum']:.0f}) |")
    return '\n'.join(lines)
final=s['matrices']['final']['groups']
changes=[]
for mode in ['minimal','toplevel']:
    a=next(x for x in final if x['mode']==mode and x['implementation']=='integrated-local')
    b=next(x for x in final if x['mode']==mode and x['implementation']=='remote-merged')
    changes.append(f"{mode}: {100*(b['seconds']['median']/a['seconds']['median']-1):+.1f}% build time, {100*(b['pss_mib']['median']/a['pss_mib']['median']-1):+.1f}% peak PSS")
remote=s['probes']['final']['remote']
remote_table=['| Phase | Analysis hits | Private writes | Deduplicated puts | Materialized parents | Reused parent descriptors |',
              '|---|---:|---:|---:|---:|---:|']
for x in remote:
    remote_table.append(f"| {x['phase']} | {x['hits']} | {x['writes']} | {x['deduplicated_writes']} | {x['transfers']['materialized_publications']} | {x['transfers']['reused_publications']} |")
largest=max(x['bytes'] for x in s['publication_sizes']['final'])
report=f'''# SkyCache local/remote integration — 25 October 2026

The local implementation now includes the two tested allocation optimizations and the remote metadata implementation through `{promotion['remote_code']}`. Tested product integration commit: `{src.get('build_commit',src['commit'])}`. Final branch head after documentation synchronization: `{promotion['main_after']}`. All **{build['test_cases']} cases across {len(build['tests'])} test targets pass**, as do real remote recovery, execution rewind and concurrent-worktree probes.

## Integrated behavior

- Preserve the lookup-key/SHA-256 reuse and uncompressed array decoding from `36a73d8fb4`. Compressed and chunked values retain streaming.
- Add the persistent SkyCache catalog, REAPI metadata CAS transport, authenticated chunk/record reads and selective segment fetching from remote commit `6a380e6eeb`.
- Include `91d51164d3`: deduplicate puts against bytes already fetched, then publish new children using existing remote parent descriptors. Unavailable cached data stops a lookup at the newer segment; deduplication never initiates a remote fetch.
- Keep sparse remote parents out of the durable local catalog and idle-eviction backing. Publication failures remain optional-cache failures. Source/configuration validation, final input rechecks, checksums and action rewind remain active.

The first merge was conflict-free. The parent-reuse update conflicted in the packed and segmented lookup functions. The resolution passes a single prepared key and digest through both ordinary and cached-only reads while retaining the newer-segment ordering rule. [Resolution and patch]({e}/update/resolution.json), [source provenance]({e}/update/source.json).

The local format remains `leased-v2` / `publications-v4`; the namespace and 256 MiB admission budget are unchanged. No 64 MiB budget experiment is introduced. Remote metadata is opt-in through `--experimental_remote_skycache` with a local staging directory. The remote catalog is a custom service supplied by the development worker; the Datadog action cache and BES remain Cache & Carry. Independent clones on different machines are still outside the supported sharing boundary.

## Final Datadog regression check

Six binaries: agent, cluster-agent, process-agent, trace-agent, security-agent and system-probe at Datadog `0cde96beb70963d0838119c308b9d0422ee66be0`. Full evaluation configures 237,112 targets including aspects. **Twelve measured builds**, three alternating repetitions per implementation and output policy, after four excluded preparations. This compares the final merged binary against optimized local commit `36a73d8fb4`; remote metadata is disabled in both arms.

Each build follows server shutdown and synchronous `bazel clean --noasync --noexpunge`. The harness verifies zero graph nodes/edges, empty execroot, no local action cache, and preserved SkyCache publications. Clean starts the JVM; its time is recorded separately. Both arms use native WSL storage on E:, the same resolved lockfile/output base/tools tree, JDK 25, a 6 GiB JVM heap, 16 jobs, no disk cache and Cache & Carry action cache/BES. Process-tree PSS is sampled every 100 ms. Warm OS/repository caches and a small sample limit generalization; PSS excludes the host page cache.

{table('final')}

Relative median changes: {'; '.join(changes)}. These are merge-regression measurements, not evidence of a durable memory reduction or remote metadata speedup. No new post-GC live-heap comparison was collected here.

Every measured SkyCache build restores **17 configured-target values and 11 execution values**, writes no new records, and performs **zero GetActionResult requests**. Minimal performs zero output CAS reads and leaves all six final executables absent. Toplevel fetches ten blobs totaling 422,596,080 bytes; all six executable hashes match the vanilla reference. Both policies leave intermediate `.a`/`.x` files absent. Every observed FindMissingBlobs request in the measured matrices was checked against that run's BES profile digest.

[Final runs, commands, checks and metrics]({e}/update/datadog-six/results.json), [computed summary and profile checks]({e}/summary.json).

## Earlier merge and vanilla control

Before the remote parent-reuse commit arrived, integration `0c80d7fe68` completed a separate 18-build matrix with three repetitions per cell. Its vanilla arm is upstream `a5c50fcb65`. These results belong to that intermediate binary and are **not pooled with the final comparison**.

{table('intermediate')}

All earlier runs passed the same clean-state and output checks. Vanilla made 16,697 action-result calls per build. The first optimized-local minimal result was 18.627 seconds and is retained. An initial tools preflight detected the newly embedded `skycache.proto`; it stopped before any build or lockfile edit. The corrected comparison pins the merged tools superset for every arm and populates each binary's own SkyCache. All 574 pre-existing tool files/symlinks were identical; the protocol definition was the only added file. Each binary needs its own cache population because upstream entry keys include the installation MD5.

[Intermediate matrix]({e}/datadog-six-matched-tools/results.json), [excluded preflight]({e}/embedded-tools-preflight.json).

## End-to-end verification

The final build/test run covers local storage, validation, leases/GC, serialization, chunked values, the remote transport, catalog persistence and server behavior. **{build['test_cases']} cases / {len(build['tests'])} targets**, with zero failures. Bazel and the development worker build successfully. [Build provenance and case counts]({e}/update/build.json); XML and logs are retained alongside it.

The real remote probe uses six linked worktrees with independent output bases/staging caches and action caches disabled. Every output matches its expected digest, including the changed BUILD rule. Restart preserves receipts; evicted metadata and an offline server fall back successfully.

{chr(10).join(remote_table)}

This directly verifies the new parent-reuse mechanism in the combined binary: the unchanged reader still writes three new records, while the changed reader writes nineteen; neither materializes its remote parent. The earlier combined binary wrote nine and 227 records respectively and materialized parents. These small fixtures establish behavior, not a speedup or production-scale bandwidth estimate. [Final remote probe]({e}/update/remote/results.json).

The separate execution fixture passes {execution_builds} builds ({s['probes']['final']['execution_runs']} commands including clean, graph checks and shutdown) covering minimal/toplevel, missing required/final blobs, unused missing intermediates, source invalidation, symlinks and internal outputs. Fault injection uses a private HTTP CAS. The two-worktree fixture passes {s['probes']['final']['worktree_phases']} phases covering concurrent writers/readers, independent source/Starlark edits, reverts and corrupt-pack fallback with Cache & Carry. [Execution results]({e}/update/execution/results.json), [worktree results]({e}/update/worktrees/results.json). All fixture services shut down.

## Follow-on performance work

1. **Reuse exact witness evidence.** The earlier profiles still show substantial repeated admission/dependency work. A compact canonical witness index and validation bits can reuse exact `(path, operation, digest)` evidence across these stages. Preserve resolution-parent validation and fresh publication checks. This addresses work and object duplication directly.
2. **Trace executor-owned command state before changing cleanup.** The earlier performance report incorrectly attributed idle retention to `services.client`/`services.session` surviving command completion. `super.afterCommand()` already calls `Services.resetCommandState()` and clears them, including at the profiled baseline. Source inspection instead identifies `SkyframeExecutor`'s reader/manager dependencies and `RemoteAnalysisCacheDeps.LazyResolver` as a retaining path to investigate; the executor resets those fields on the next command. A heap-root/dominator trace is still required to establish retained sizes and choose a safe release point. The measured class histograms remain valid. [Correction with source excerpts]({e}/memory-lifetime-review.json).
3. **Profile repeated remote chunk reads and hashing.** A range read currently reloads and hashes a whole cached chunk under a synchronized download method, even when it only needs a small record. A bounded immutable in-memory page cache and per-digest fetch coalescing could reduce allocation and I/O. This is a source-based hypothesis, not a measured bottleneck; preserve digest verification, record checks and cached-only no-network semantics.
4. **Separate logical publication size from bytes transferred.** The largest final Datadog publication object directory is {largest:,} bytes ({largest/1024**3:.2f} GiB), whereas the remote bundle still permits only 256 MiB total logical file bytes. The current remote protocol therefore cannot carry this Datadog publication. Support larger logical closures with bounded metadata, streamed export, explicit transfer budgets and reused parent descriptors before attempting remote metadata performance at this scope. Raising the local admission budget does not solve this bound.

Writer bitmap lifetime, per-entry leaf summaries and demand-driven shared-value restoration remain longer-term candidates from the [performance investigation](skycache-perf-options-2026-10-25.md). The parent-descriptor work has now been integrated, so it is no longer an unimplemented recommendation.

## Reproduction and preservation

[Full integrated patch from upstream]({e}/update/integrated-from-upstream.patch), [patch from the previous local implementation]({e}/update/integrated-from-previous.patch), [remote merge patch]({e}/update/remote-sync.patch), [promotion and final branch state]({e}/promotion.json). Patches are verified by reconstructing the recorded Git trees in temporary indexes. Old caches, commits and excluded attempts remain intact; rollback can select `36a73d8fb4` without deleting them.

The [evidence directory]({e}/) contains scripts, exact commands, test XML, timings, output inventories, correction records and SHA-256 inventories. Large binaries, immutable caches, raw gRPC/BEP and profiles are preserved in the private archive described by [archive verification]({e}/archive-verification.json). Public text is normalized to LF and its staged Git bytes are checked against the published hash inventory.
'''
(D/'skycache-remote-sync-2026-10-25.md').write_text(report)
arch=D/'skycache-architecture-2026-10-25.md'
a=arch.read_text()
a=a.replace('Cache & Carry supplies the remote action cache and content-addressable store (CAS); SkyCache keeps graph values and output digests on a shared local filesystem. Output bytes remain in CAS until the build needs them.',
            'Cache & Carry supplies the remote action cache and content-addressable store (CAS). SkyCache keeps graph values and output digests in immutable local publications, with an optional remote metadata catalog and CAS transport. Output bytes remain in the action CAS until the build needs them.')
a=re.sub(r'^Implementation snapshot:.*$',f"Implementation snapshot: `{src['commit']}`, with remote implementation through `91d51164d3` and upstream `a5c50fcb65841b505ec72840d83381a704e70afa`. The [local/remote integration report](skycache-remote-sync-2026-10-25.md) contains the full patch, {build['test_cases']} passing test cases, real recovery/concurrency probes and final after-clean Datadog measurements. Both optimizations from the [performance investigation](skycache-perf-options-2026-10-25.md) are integrated. That investigation's module-retention explanation is corrected in the integration report. Earlier measurements below are historical. This remains a prototype for discussion.",a,count=1,flags=re.M)
a=a.replace('  Packs["Immutable local packs and publication manifests"] --> Validate',
            '  Packs["Immutable local packs and publication manifests"] --> Validate\n  Catalog["Optional remote publication catalog"] --> Metadata["Verified metadata chunks fetched on demand"]\n  Metadata --> Validate')
a=a.replace('  Publish --> Packs','  Publish --> Packs\n  Publish -->|reuse remote parent descriptors| Catalog',1)
a=a.replace('The integration has four layers:', 'The integration has five layers:')
a=a.replace('This is a shared local filesystem design.', 'The local storage layer uses a shared filesystem.')
row='| Remote metadata transport | Discover immutable publications, fetch verified ranges and publish children using remote parent descriptors | `RemoteSkyCache`, `SkyCacheTransport`, `SkyCacheCatalogStore` |\n'
needle='| Lifetime management |'
assert needle in a
a=a.replace(needle,row+needle,1)
needle="The backend also has an idle graph-eviction policy."
paragraph='''The optional remote backend keeps the same per-entry validator and worktree identity. It fetches manifests, witness blocks and root indexes during admission, then segment indexes and pack chunks as required. Cached-only put deduplication initiates no downloads. New children can reuse existing remote parent descriptors without importing or reuploading those parents; sparse publications never masquerade as durable local backing for idle eviction. The development catalog requires its custom service alongside REAPI CAS. The 256 MiB remote logical-bundle bound currently excludes the approximately 1.5 GB Datadog publication tested here. See the [remote implementation overview](skycache-remote-mvp.md) and [integration evidence](skycache-remote-sync-2026-10-25.md).

'''
a=a.replace(needle,paragraph+needle,1)
a=a.replace('## Current larger-tree results (snapshot d497180)', '## Historical segmented larger-tree results (snapshot d497180)')
latest='''## Latest integration measurements

The final local/remote integration was compared against the optimized local baseline after verified `bazel clean`, using the 237,112-target Datadog scope and Cache & Carry. Remote metadata was disabled for this large-scope regression check because the current bundle bound is smaller than the publication.

'''+table('final')+'\n\nEvery measured hit build performs zero action-result requests; minimal downloads no output bytes and toplevel fetches only the required output/runfile blobs. The [full report](skycache-remote-sync-2026-10-25.md) separates the final 12-build comparison from the earlier 18-build matrix with vanilla, and includes correctness probes for the combined remote backend.\n\n'
a=a.replace('## Discussion questions',latest+'## Discussion questions',1)
arch.write_text(a)
old=D/'skycache-perf-options-2026-10-25.md'
p=old.read_text()
p=p.replace('The next useful work is to remove repeated proof work and shorten the lifetime of validation data.', 'The next useful work is to remove repeated proof work and investigate the lifetime of validation data.')
p=p.replace('They have **not been promoted** to the implementation branch.', 'They were unpromoted when measured; both are now included in the [local/remote integration](skycache-remote-sync-2026-10-25.md).')
p=p.replace('The original `jsgette/skycache-v2-local` branch remains at the baseline.', 'At the time of this experiment, `jsgette/skycache-v2-local` remained at that baseline. The later [integration report](skycache-remote-sync-2026-10-25.md) records its promotion and remote synchronization.')
paragraph=next(x for x in p.split('\n\n') if x.startswith('The baseline histogram still contains'))
p=p.replace(paragraph,'''The baseline histogram still contains **432,322 witness objects and 605,420 `ByteString` objects after command completion and a full GC**. The combined profile is almost identical. The witness count matches the validator's memo count, but the histogram does not prove the retaining path or retained sizes. **Correction during remote integration:** the original explanation that `services.client` survived until `beforeCommand()` was incorrect. `LocalSkycacheModule.afterCommand()` calls `super.afterCommand()`, which invokes `Services.resetCommandState()` and already clears client/session references. The executor's reader/manager dependency providers and lazy resolvers are a separate retaining path to investigate. [Source-backed correction](skycache-remote-sync-2026-10-25.md#follow-on-performance-work).''')
paragraph=next(x for x in p.split('\n\n') if x.startswith('**2. Release temporary validation state'))
p=p.replace(paragraph,'''**2. Trace executor-owned validation state before changing cleanup.** Module-owned client/session references are already cleared by the superclass at command end. Trace heap roots through the executor's dependency providers and lazy resolvers, then select a safe release point for completed command data. Preserve retained frontier roots, publication hints, cancellation/upload completion and warm-build invalidation. Measure live heap and PSS separately; the current histogram establishes surviving objects, not an actionable module cleanup fix. This correction supersedes the original recommendation to clear the module references.''')
old.write_text(p)
(T/'outputs').mkdir(exist_ok=True)
(T/'outputs/SkyCache-remote-integration.md').write_text(report)
(T/'outputs/SkyCache-architecture.md').write_text(a)
(T/'outputs/SkyCache-performance-options.md').write_text(p)
print(json.dumps(dict(report=str(D/'skycache-remote-sync-2026-10-25.md'),commit=src['commit'],relative_changes=changes)))
