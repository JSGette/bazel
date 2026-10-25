from pathlib import Path
import json
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
D=Path('/mnt/e/scw/docs/docs/jsgette')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
s=json.loads((R/'benchmark-summary.json').read_text());p={v:json.loads((R/'profiles'/f'{v}-summary.json').read_text()) for v in ['baseline','array']}
source=json.loads((R/'source-verification.json').read_text());m='measurements/skycache-perf-options-2026-10-25'
rows=[]
for mode in ['minimal','toplevel']:
 for v,title in [('baseline','Current SkyCache'),('lookup','Lookup key/hash reuse'),('array','Lookup reuse + array decoding')]:
  q=s[mode+'/'+v];t=q['seconds'];mem=q['peak_pss_mib']
  rows.append(f"| {mode} | {title} | {t['median']:.3f} | {t['min']:.3f}–{t['max']:.3f} | {q['analysis_seconds']['median']:.3f} | {mem['median']:.0f} ({mem['min']:.0f}–{mem['max']:.0f}) |")
text=f'''# SkyCache performance and memory investigation — 25 October 2026

The next useful work is to remove repeated proof work and shorten the lifetime of validation data. A 64 MiB admission allowance does not address either cost. Two small experimental optimizations reduced sampled allocation traffic substantially but produced only a small, mixed timing change: minimal improved about 3%, while toplevel slowed about 2.4%. They have **not been promoted** to the implementation branch.

## What was tested

Baseline: upstream-synchronized SkyCache `1aac2aa2ec6fe31af2e979558f06a3b6971c601b`. Experimental branch `jsgette/skycache-perf-options`, commit `{source['experimental_commit']}`, tree `{source['tree']}`. The original `jsgette/skycache-v2-local` branch remains at the baseline. No budget, memo capacity, validation shortcut, persistent format or remote-cache policy changed.

1. **Reuse lookup bytes and SHA-256 across segment probes.** A segmented read previously copied and hashed the same key again for each segment. The prototype prepares it once per store lookup. Record-key comparison, record checksums, index validation and lifecycle locks remain. [Isolated patch]({m}/lookup-reuse.patch).
2. **Decode uncompressed shared values directly from the byte array.** The existing stream decoder allocates a 4 KiB buffer even when the entire small value is already available. The prototype uses the array decoder for uncompressed values; compressed and chunked values keep streaming. [Combined patch]({m}/prototype.patch).

Lookup reuse passed **201 cases / 15 targets**. The combined prototype passed **504 cases / 17 targets**, including serialization tests for compressed/uncompressed values, chunked values and missing chunks. See [test summary]({m}/tests-summary.json) and [source provenance]({m}/source-verification.json). These are experimental patches; no new two-worktree end-to-end probe was run in this investigation.

## Clean-build measurements

The same six Datadog binaries and commit as the upstream-sync report: agent, cluster-agent, process-agent, trace-agent, security-agent and system-probe, commit `0cde96beb70963d0838119c308b9d0422ee66be0`. **18 measured builds**, three rotated repetitions per implementation and download mode, after six excluded preparation builds. Fresh JVM before synchronous `bazel clean`; zero graph, empty execroot and absent action cache were checked. All variants use the same native WSL output base on E:, JDK 25, 6 GiB heap, 16 jobs and Cache & Carry cache/BES at `grpc://10.55.0.1:8080`. Disk cache is off; process-tree PSS is sampled every 100 ms. The resolved module lockfile was held fixed and the original bytes restored.

Each binary needs its **own populated SkyCache**: upstream's `FrontierNodeVersion` includes the Bazel installation MD5 in entry keys. The embedded-tools repository was pinned to one path for every variant after verifying all 714 entries were identical. This prevents physical repository-binding changes from becoming an accidental cache-miss benchmark. Publication record counts differ by a few entries between writers; the same outputs and proof obligations are checked. Both publications were ordered large-before-small in all three stores.

| Mode | Implementation | Median build s | Range s | Median analysis s | Median peak PSS MiB (range) |
|---|---|---:|---:|---:|---:|
{chr(10).join(rows)}

[Commands and per-run checks]({m}/controlled/results.json), [computed summary]({m}/benchmark-summary.json).

Every measured run restored **17 configured targets and 11 execution values**, wrote no new records and matched all six executable digests from the earlier vanilla baseline. All made zero `GetActionResult` requests. Minimal made zero CAS reads and left binaries absent. Toplevel made 10 reads / 422,596,080 bytes and produced the requested binaries, with intermediate `.a`/`.x` files absent. The small `FindMissingBlobs` traffic accompanies BES profile upload; it is not an action-result lookup.

The combined prototype's median peak PSS was lower by about 8.5% in minimal and 5.6% in toplevel, but the ranges overlap substantially. Three repetitions do not establish a durable RSS/PSS gain. The first baseline minimal run took 24.07 s and is retained in the table, not silently discarded. The other two were 13.27 s and 13.17 s. No same-run vanilla benchmark was repeated: this experiment compares two local changes against the pinned SkyCache baseline, not against historical vanilla timings.

## Allocation traffic is different from live memory

Separate verified-hit JFR profiles and post-command full-GC class histograms were collected. The WSL boot ID changed between the timing matrix and these profiles; the profiles' 57.48 s / 25.87 s elapsed times are excluded from timing claims, and no warm-page-cache comparison is claimed for them. Both had the expected positive hits, zero new writes, zero action-result requests and zero CAS reads.

| Diagnostic metric | Baseline | Combined prototype |
|---|---:|---:|
| Sample-weighted allocation traffic, GB (decimal) | {p['baseline']['allocation_weight_bytes']/1e9:.2f} | {p['array']['allocation_weight_bytes']/1e9:.2f} |
| Stream-decoder allocation weight, MB (decimal) | {p['baseline']['stream_decoder_allocation_weight_bytes']/1e6:.1f} | {p['array']['stream_decoder_allocation_weight_bytes']/1e6:.1f} |
| Post-command full-GC live objects, MiB | {p['baseline']['post_command_histogram_live_bytes']/2**20:.1f} | {p['array']['post_command_histogram_live_bytes']/2**20:.1f} |
| Recorded GC pauses, seconds | {p['baseline']['gc_pause_seconds']:.3f} | {p['array']['gc_pause_seconds']:.3f} |

These are **sampled allocation estimates**, not exact allocation counters or resident-memory measurements. The direct-buffer change removes most stream-decoder allocation; it does not shrink the objects retained by the cache/graph. The roughly 22% reduction in sampled traffic translates to a much smaller latency difference. GC pause time alone is under half a second in each profile; concurrent GC CPU is not included in that pause sum.

[Baseline profile summary]({m}/profiles/baseline-summary.json), [combined summary]({m}/profiles/array-summary.json), [baseline class histogram]({m}/profiles/baseline-histogram.txt), [combined histogram]({m}/profiles/array-histogram.txt).

The baseline histogram still contains **432,322 witness objects and 605,420 `ByteString` objects after command completion and a full GC**. The combined profile is almost identical. The first count exactly matches the validator's witness memo; most byte-string keys correspond to its roughly 603k successful dependency records. `LocalSkycacheModule.afterCommand()` closes the store but keeps `services.client`; that client owns the validator and its successful-key/witness sets, and the session owns `LocalInputSnapshot`'s witness map. `beforeCommand()` clears the references on the next command. The retained top-level roots are separately copied and much smaller. This is concrete evidence of temporary proof state surviving into idle time, though the histogram alone does not establish its complete retained size or prove every retaining path.

Full-depth Java execution samples also support the work diagnosis: baseline admission 179 samples, dependency validation 470, shared-value restoration 388, other pack reads 7, source capture 8 and other JVM/Bazel work 369. The combined profile still has 176 admission and 468 dependency-validation samples. These sampled categories are not exclusive wall-time phases. Export now explicitly uses `--stack-depth 128`; the earlier handoff's default-depth export under-attributed deeper stacks.

## Recommended work, in order

**1. Reuse all exact admission evidence through a compact shared witness index.** Admission checks about 418k global witnesses, but the separate positive-admission set stops at 250k. The measured reader then performs another **182,453–182,455 witness checks** during dependency validation. Some are necessary workspace checks, so that entire count cannot be called redundant. The gap between the admitted global population and the 250k retained positives demonstrates substantial avoidable repetition. Keep one immutable witness table with exact `(path, operation, digest)` identity and a compact validation-status index; reuse its canonical entries in the validator and final-check registration. Avoid creating another full object map merely to raise the cap. Scope reuse to the same command; failed checks still invalidate positives, resolution parents must still succeed first, and publication still rechecks used evidence freshly. Also deduplicate identical witness-block decoding between parent/child publications while verifying each block's bounds and checksum. Current list deduplication happens only after decoding both lists.

**2. Release temporary validation state after the command.** Preserve retained frontier roots and the publication hint needed by the next command, then release the completed validator's success sets and snapshot witness map after uploads, final validation and statistics finish. This targets idle live heap, not the current build's peak. Verify cancellation and callbacks have quiesced, warm-build invalidation still works, and idle-retention reset still works. Measure both live heap and process PSS afterward: G1 may keep freed heap committed, so clearing objects need not immediately return the same bytes to the OS.

**3. Persist compact leaf-validation summaries for retrieved entries.** This is the larger latency opportunity. The current hit still reads about **603k dependency records** and **795k local values / 247 MB**, largely unchanged by the prototypes. Store an exact transitive leaf closure for each reusable frontier value, expressed as IDs into shared witness blocks, cryptographically bound to its dependency root and format/version. A reader could validate the required leaves directly instead of repeatedly decoding and walking the internal proof DAG. Preserve source contents, directory listings, repository-name bindings, symlink-resolution parents, configurations and parent-publication closure. Missing or malformed summaries must fall back to the existing validator. Do not substitute timestamps or skip external evidence. This needs a format change and mutation/corruption/concurrency tests; the gain is a hypothesis, not a measured speedup. Dense IDs and bitsets can also replace large object-based positive sets without reintroducing the old 250k-memo traversal thrashing.

**4. Address writer bitmap lifetime independently.** `SkycacheUploadClient` uses lazy leaf IDs and `releaseBitmaps=false` because graph in-degrees are still growing. The serializer's dense-ID run compression and last-parent release therefore cannot deliver their full memory benefit on this path. A completed-graph or bounded completed-subgraph upload phase could assign dense IDs and release consumed closures. It trades overlap against memory; simply freeing bitmaps early is unsafe, and disabling reduction can increase reader work. Empty-cache preparations here still peaked around 7.1 GiB PSS, but there was no repeated matched baseline writer experiment, so no writer-memory gain/regression is claimed. Profile writer live closures before selecting this design.

**5. Investigate demand-driven shared-value restoration after the above.** Only 17 analysis hits still trigger roughly 192k additional local value reads beyond dependency records. Restoring full providers/nested values can materialize data that a minimal build never consumes. Lazy restoration could help, but it must preserve object identity, deferred errors and cache/lease lifetime across commands. The array decoder only reduces buffers around that work; it does not remove the work itself.

The two tested byte-level changes are small follow-ups rather than the main performance project. Keep their patches available for a later isolated comparison, but the mixed toplevel result does not justify claiming a general speedup or promoting them now. I recommend implementing the exact shared witness index and command-end release first, then prototyping leaf summaries for a larger clean-build improvement. Leave the 64 MiB allowance work deferred.

## Excluded attempts and recovery

The initial [unpinned attempt]({m}/lookup-reuse/results.json) changed the embedded-tools symlink target on a binary switch. Its publications had 213 differing witnesses, including the repository binding; all 714 embedded-tool entries themselves were verified equal. [Evidence]({m}/cross-binary-tools-check.json). The [fixed-repository attempt]({m}/lookup-reuse-fixed/results.json) restored publication admission but still had no analysis hits because entry keys carry the installation MD5. Those full-reconstruction runs are excluded. The final matrix instead populated separate stores for the three binaries and retained the repository-path control. No checksum override was used.

The first build's artifact-copy helper assumed an `io_bazel` execroot; the observed root was `_main`. Build/tests had passed, so the copy was repaired through `bazel-bin` without rebuilding. All failures, corrections, commands, summaries, patches and test XML are in the [evidence directory]({m}/). Full binaries, stores, JFR and raw gRPC/BEP are retained in the verified private archive described by [archive verification]({m}/archive-verification.json). The original implementation, old caches and previous evidence are preserved.
'''
path=D/'skycache-perf-options-2026-10-25.md';path.write_text(text)
(T/'outputs/SkyCache-performance-options.md').write_text(text)
arch=D/'skycache-architecture-2026-10-25.md';a=arch.read_text()
needle='Earlier measurements below are historical. This remains a prototype for discussion.'
assert needle in a
a=a.replace(needle,'The [performance and memory investigation](skycache-perf-options-2026-10-25.md) evaluates two unpromoted fast-path prototypes and prioritizes shared witness evidence, validation-state lifetime and leaf summaries. Earlier measurements below are historical. This remains a prototype for discussion.')
arch.write_text(a);(T/'outputs/SkyCache-architecture.md').write_text(a)
print(path)
