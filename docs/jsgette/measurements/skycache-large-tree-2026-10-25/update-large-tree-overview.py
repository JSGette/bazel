from pathlib import Path
import json
D=Path('/mnt/e/scw/docs/docs/jsgette');R=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
p=D/'skycache-architecture-2026-10-25.md';s=p.read_text()
a=s.index('Implementation snapshot:');b=s.index('\n\n',a)
s=s[:a]+'''Implementation snapshot: `d4971808785aaa3cd46bf6dd1419cb65afb4319e`, 25 October 2026. The [complete implementation patch](measurements/skycache-large-tree-2026-10-25/implementation-current.patch) and [large-tree implementation, benchmark and protocol](skycache-large-tree-2026-10-25.md) include the segmented format and bounded validation memo update. Earlier measurements below are labeled by their historical implementation scope. This remains a prototype for discussion.'''+s[b:]
s=s.replace('`LocalCacheSession`, `PackedFingerprintValueStore` |','`LocalCacheSession`, `SegmentedFingerprintValueStore`, `PackedFingerprintValueStore`, `LocalWitnessTable` |')
s=s.replace('<cache>/leased-v1/\n  objects/packed-v1/<publication-id>/...\n  publications-v3/<namespace>/<publication-id>.manifest','<cache>/leased-v2/\n  objects/segmented-v1/<publication-id>/\n    index\n    segment-00000.pack\n    segment-00000.index\n    witnesses-00000.block\n    ...\n  publications-v4/<namespace>/<publication-id>.manifest')
needle='Each writer owns private storage. It seals the pack before publishing a manifest, flushes manifest contents, atomically links the manifest into the publication directory and synchronizes that directory. A SHA-256 trailer checks manifest integrity. Readers discover only published manifests, rather than partially written packs.'
replacement='''Each writer owns private storage. Witness paths are prefix-compressed in bounded, independently checksummed blocks; SHA-256 values are stored as raw bytes. The small manifest references those blocks and parent publications. Value packs rotate at 16,384 records or approximately 32 MiB across sixteen fingerprint buckets. Sealing replaces mutable maps with compact indexes, which readers load on demand within an aggregate admission budget. All blocks and segments are durable before the manifest is atomically linked and the publication directory synchronized. Old `leased-v1` caches are preserved in an isolated format namespace.

The 256 MiB admission budget charges expanded witness paths as well as encoded/decoded blocks and segment indexes. Validation memo retention is bounded separately: at most one million dependency successes and one million witness successes. A full memo stops retaining new successes without invalidating completed validation. Per-lookup traversal still has its original 250,000-record and 128 MiB bounds. Global external/repository witness admission remains eager; selective validation is future work.'''
assert needle in s;s=s.replace(needle,replacement)
s=s.replace('## Evidence and limitations','## Historical evidence and limitations (snapshot 71521fa)')
a=s.index('The expanded Datadog scope exposed two current storage limits.');b=s.index('\n\n',a)
s=s[:a]+'''The previous format failed to publish the 237,112- and 220,062-configured-target scopes. The larger unpublished pack contained over 73.95 MiB of reconstructed witness manifest data and 1,664,716 records, beyond its 64 MiB manifest and one-million-entry pack limits. The segmented implementation now publishes both scopes; current measurements follow below.'''+s[b:]
s=s.replace('## Expanded Datadog results','## Historical expanded Datadog results (snapshot 71521fa)')
s=s.replace('The 220k/237k-target scopes described above remain outside the current publication limits. [Full expanded benchmark, failed scopes and reproducible evidence]', 'The larger scopes failed with that historical snapshot. [Historical expanded benchmark, failed scopes and reproducible evidence]')
groups=json.loads((R/'summary.json').read_text());rows=[]
for scope,count in [('six-final2',237112),('five-final2',220062)]:
 for mode in ('minimal','toplevel'):
  g=groups[scope];a=g[mode+'/vanilla'];b=g[mode+'/off'];c=g[mode+'/on'];time=lambda x:x['build_seconds']['median'];mem=lambda x:x['peak_pss_mib']['median']
  rows.append(f"| {count:,} | {mode} | {time(a):.2f} | {time(b):.2f} | {time(c):.2f} | {100*(1-time(c)/time(a)):.1f}% | {mem(a):.0f} | {mem(c):.0f} |")
section='''## Current larger-tree results (snapshot d497180)

Both previously rejected scopes now publish and reuse the segmented format. Each cell is a median of three after-clean builds with Cache & Carry; positive time reductions mean faster than vanilla and negative values mean slower.

| Configured targets | Policy | Vanilla s | Candidate off s | SkyCache s | Time reduction | Vanilla PSS MiB | SkyCache PSS MiB |
|---:|---|---:|---:|---:|---:|---:|---:|
'''+ '\n'.join(rows)+'''

The final SkyCache package suite passes 201 cases across 15 targets. All executable digests match the controls; raw traffic and materialization inventories are retained. Capacity is improved, while eager input admission and dependency traversal still cost time. The [full report](skycache-large-tree-2026-10-25.md) includes ranges, same-binary controls, RAM, remote calls, diagnostic regressions, source patches and the complete protocol. The old-format concurrent-worktree and fault-injection results above are historical; the new format adds unit coverage for concurrent rotation and retains the existing lease/GC and validation suite.

'''
s=s.replace('## Discussion questions',section+'## Discussion questions');p.write_text(s)
proposal=D/'skycache-large-tree-proposal-2026-10-25.md';s=proposal.read_text();at=s.index('\n\n')+2;s=s[:at]+'**Implementation update:** Compact witness blocks, segmented packs and separated bounded validation memo retention are now implemented and measured. See the [implementation and benchmark report](skycache-large-tree-2026-10-25.md). Selective per-value repository-binding validation remains future work. The proposal below records the earlier design state.\n\n'+s[at:];proposal.write_text(s)
Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/outputs/SkyCache-architecture.md').write_text(p.read_text())
print('Architecture overview and historical proposal cross-reference updated.')
