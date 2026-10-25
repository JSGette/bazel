"""Archive raw evidence, stage a reproducible public protocol, and write the report."""
from pathlib import Path
import json,hashlib,shutil,tarfile,subprocess
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
R=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
D=Path('/mnt/e/scw/docs');E=D/'docs/jsgette/measurements/skycache-large-tree-2026-10-25'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
groups=json.loads((R/'summary.json').read_text());binary=json.loads((R/'binary-final2.json').read_text());verification=json.loads((R/'source-verification-final2.json').read_text())
assert verification['test_cases']==201 and verification['patch_reconstructs_source_tree']
E.mkdir(parents=True,exist_ok=False)
scripts=['apply-large-tree.py','finish-large-tree-edits.py','refine-large-tree.py','build-large-tree.py','prepare-large-tree-benchmark.py','benchmark-large-tree.py','inspect-large-tree.py','refine-validator-capacity.py','fix-capacity-fixture.py','stop-large-tree-preparation.py','separate-validator-capacity.py','finalize-large-tree-candidate.py','verify-large-tree-source.py','verify-large-tree-outputs.py','summarize-large-tree.py','publish-large-tree.py','update-large-tree-overview.py','watch-datadog-storage.ps1']
for n in scripts:shutil.copy2(T/'work'/n,R/n)
staged=R/'initial-source-staging';staged.mkdir(exist_ok=False)
for q in (T/'work/large-tree').glob('*.java'):shutil.copy2(q,staged/q.name)
raw=[q for q in sorted(R.rglob('*')) if q.is_file() and q.relative_to(R).parts[0]!='tmp']
manifest=[{'name':str(q.relative_to(R)),'bytes':q.stat().st_size,'sha256':sha(q)} for q in raw]
archive=Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-large-tree-evidence.tar');assert not archive.exists()
with tarfile.open(archive,'w') as tar:
 for q in raw:tar.add(q,arcname=str(q.relative_to(R)))
with tarfile.open(archive) as tar:
 assert len(tar.getmembers())==len(manifest)
 expected={x['name']:x['sha256'] for x in manifest}
 for item in tar.getmembers():
  with tar.extractfile(item) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==expected[item.name]
save(E/'raw-evidence-manifest.json',manifest)
save(E/'private-backup.json',{'path':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive),'verified':True})
for q in raw:
 rel=q.relative_to(R)
 if rel.parts[0] in ('bin','initial-source-staging') or 'skycache' in rel.parts or q.name.endswith(('.bep.json','.grpc.bin','.profile.gz')):continue
 if q.name=='source-tracked.patch':continue
 dest=E/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(q,dest)
tables=[];gains=[];scope_rows=[];layouts=[];analysis_notes=[]
for scope in ('six-final2','five-final2'):
 p=json.loads((R/('datadog-'+scope)/'results.json').read_text());g=groups[scope]
 assert p['status']=='passed' and p['module_lock_restored'] and p['workspace_unchanged_during_runs']
 configured=int(next(r for r in p['runs'] if r['variant']=='vanilla')['build_metrics']['targetMetrics']['targetsConfigured'])
 analysis_notes.append(f"At {configured:,} configured targets, median minimal-mode analysis takes {g['minimal/on']['analysis_seconds']['median']:.2f} s with SkyCache versus {g['minimal/vanilla']['analysis_seconds']['median']:.2f} s for vanilla.")
 rows=[]
 for key,v in g.items():
  mode,variant=key.split('/');m=lambda k:v[k]['median']
  rows.append(f"| {configured:,} | {mode} | {variant} | {m('build_seconds'):.2f} | {v['build_seconds']['min']:.2f}–{v['build_seconds']['max']:.2f} | {m('analysis_seconds'):.2f} | {m('clean_plus_build_seconds'):.2f} | {m('peak_pss_mib'):.0f} | {m('peak_rss_mib'):.0f} | {m('get_action_result'):.0f} | {m('cas_read_mib'):.2f} |")
 tables.extend(rows)
 for mode in ('minimal','toplevel'):
  for base in ('vanilla','off'):
   reduction=lambda k:100*(1-g[mode+'/on'][k]['median']/g[mode+'/'+base][k]['median'])
   gains.append(f"| {configured:,} | {mode} | {base} | {reduction('build_seconds'):.1f}% | {reduction('peak_pss_mib'):.1f}% |")
 for r in p['runs']:
  if r['preparation'] or not r['label'].startswith('r1-minimal'):continue
  o=r['output_inventory'];v=r.get('local-skycache-validation.json',{})
  scope_rows.append(f"| {configured:,} | {r['variant']} | {r['build_metrics']['targetMetrics'].get('targetsConfigured',0)} | {r['analysis_hits']} | {r['execution_hits']} | {o['regular_files']} | {o['bytes']} | {o['archives']} / {o['exports']} | {v.get('records_read',0)} | {v.get('rejected_roots',0)} |")
 layout=json.loads((R/('datadog-'+scope)/'publication-layout.json').read_text());first=max(layout['publications'],key=lambda x:x['record_count'])
 layouts.append(f"| {configured:,} | {first['manifest_bytes']} | {first['witness_count']:,} | {first['witness_blocks']} | {first['witness_encoded_bytes']/1024**2:.2f} | {first['witness_expanded_bytes']/1024**2:.2f} | {first['record_count']:,} | {first['segment_count']} | {first['index_bytes']/1024**2:.2f} |")
report=f'''# SkyCache large-tree implementation and after-clean benchmarks

25 October 2026. Native Linux in WSL on the E: SSD. Windows work remains deferred.

The formerly rejected 237,112- and 220,062-configured-target Datadog scopes now publish reusable SkyCache data. The implementation replaces large witness manifests and single value packs, and separates bounded validation memo retention from traversal limits. Performance and RAM results below include all three repeated observations per variant and output policy; preparation and interrupted diagnostic attempts are excluded.

## Implementation

Source commit `{binary['source_commit']}`, tree `{binary['source_tree']}`. Final executable SHA-256 `{binary['sha256']}`.

- `leased-v2/publications-v4` contains small checksummed headers, witness-block descriptors and parent publication IDs. `leased-v2/objects/segmented-v1` contains flat immutable witness blocks and pack segments. Old `leased-v1` data remains untouched and invisible to the new reader/GC; rollback uses the preserved old format.
- Witness paths use prefix compression, SHA-256 digests use 32 raw bytes, and negative witness states have explicit tags. Each block has at most 4,096 witnesses or roughly 1 MiB of encoded source payload, is independently compressed and checksummed, and is bounded before allocation. Admission charges fully expanded paths as well as encoded/decoded bytes against the existing 256 MiB metadata budget.
- Sixteen fingerprint buckets route lookups. Each mutable pack rotates at 16,384 records or roughly 32 MiB, seals its compact index and releases the mutable map. Readers open segment indexes on demand, within the aggregate admission budget. Rotation is checked before the next write; one bounded record can exceed the byte target. Explicit limits remain: 4,096 segments, 256 MiB writer index-entry budget, 64 MiB individual values, and bounded root directories.
- All files are durable before atomic publication. Reader leases, parent closure, GC, corruption fallback and final input revalidation remain in force. Global external/repository witness admission is still eager. Selective per-value repository-binding validation is a separate, unimplemented stage.
- A full success memo no longer invalidates an otherwise fully validated dependency result. It stops retaining additional successes. The default memo now holds at most 1,000,000 dependency successes and 1,000,000 witness successes. The independent per-lookup 250,000-record, 128 MiB, depth, reference and in-flight limits remain unchanged. This bounded increase prevents the repeated-traversal behavior observed with a saturated 250,000-entry memo; it is not an unlimited cache.

The [incremental patch](measurements/skycache-large-tree-2026-10-25/implementation-large-tree.patch) applies after `71521fabb3420707c39d73329fa9aa9622e8c382`. The [combined implementation patch](measurements/skycache-large-tree-2026-10-25/implementation-current.patch) applies after `6f451b2dc6f783dda879ff3c7ed3fc8146d0f410`; applying it in a temporary Git index reconstructs the exact source tree above.

## Capacity

Largest publication in each final cache:

| Configured targets | Manifest bytes | Witnesses | Blocks | Witness disk MiB | Expanded path/evidence MiB | Records | Segments | Index MiB |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(layouts)}

The previous six-target attempt required at least 73.95 MiB of witness manifest data and 1,664,716 records, exceeding its 64 MiB manifest and one-million-entry pack limits. Those historical counts were reconstructed from an unpublished pack; they are not byte-identical fixtures of the final format. The current layout inspection authenticates headers and indexes and records all descriptor sizes in `publication-layout.json` for each scope.

## Repeated performance

All cells are medians of three runs. `off` and `on` use the same final executable. `vanilla` is the preserved pre-SkyCache source comparator at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, SHA-256 `1ef0d6ff94b24c787d539f63672f93f0da9bd96d49750d729489cbffc80aa588`; it is not an official stock Bazel release.

| Configured targets | Policy | Variant | Build s | Range s | Analysis s | Clean + build s | Peak PSS MiB | Peak RSS MiB | GetActionResult | CAS read MiB |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(tables)}

Positive values mean reductions; negative values mean regressions:

| Configured targets | Policy | Baseline | Build time reduction | Peak PSS reduction |
|---:|---|---|---:|---:|
{chr(10).join(gains)}

Capacity is fixed; build-time performance remains mixed. Minimal is 4.0% slower at 237k targets and 20.6% slower at 220k, while toplevel is 12.5% and 8.0% faster, respectively, against the preserved vanilla comparator. Median peak PSS falls by 8.5–26.2%. {" ".join(analysis_notes)} Avoiding action-result traffic does not eliminate the cost of eager input admission and dependency validation.

These are small samples on a shared remote service with warm OS and external-repository caches. They do not establish statistical significance. PSS/RSS are sampled process-tree memory, not total host RAM or filesystem page cache, and may miss short peaks. Individual values, ranges, clean timings, RPC counts and all commands are in the [measurement directory](measurements/skycache-large-tree-2026-10-25).

First minimal run per scope/variant:

| Scope | Variant | Targets configured | Analysis hits | Execution hits | Local files | Local bytes | .a / .x | Validation records read | Rejected roots |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(scope_rows)}

Configured-target hits count restored frontier values, not the transitive graph avoided. `scope-and-output-details.json` preserves each run's validation and admission counters. Any remaining GetActionResult requests correspond to work not restored through SkyCache; the implementation adds no all-action validation sweep. CAS bytes and materialized outputs are measured rather than inferred from the policy. `bes-profile-checks.json` distinguishes FindMissingBlobs profile uploads from output validation.

All twelve final measured SkyCache builds make zero GetActionResult requests. All six minimal builds make zero CAS reads. Each six-target toplevel run reads ten blobs totaling 422,596,080 bytes; each five-target toplevel run reads nine totaling 297,860,368 bytes. Neither policy materializes intermediate `.a` or `.x` files on a hit. The last toplevel run in each scope additionally maps every downloaded digest to an actual materialized output, retained in `top-output-read-verification.json`.

## Protocol and correctness

Six targets: `//cmd/agent:agent`, `//cmd/cluster-agent:cluster-agent`, `//cmd/process-agent:process-agent`, `//cmd/trace-agent:trace-agent`, `//cmd/security-agent:security-agent`, `//cmd/system-probe:system-probe`. The five-target scope removes only core Agent. Datadog revision `0cde96beb70963d0838119c308b9d0422ee66be0`; frozen resolved lock SHA-256 `0bb43dc2960626918c20c350491be5cdf6fd18730bb34a956d4608a1e1aa98fc`.

Every build follows server shutdown and `clean --noasync --noexpunge`. The harness verifies zero Skyframe nodes and edges, empty execroot, absent local action cache, unchanged persistent publication hashes, and the same new server PID throughout the build. Each variant uses the same workspace/output base, JDK 25, 6 GiB JVM, 16 jobs and 6144 MiB action-memory budget. Disk caching is disabled. Cache & Carry remote cache and BES are enabled at `grpc://10.55.0.1:8080`, with one connection and sixteen requests per connection. Explicit clean clears the graph; idle eviction and GC are disabled for measurement.

Each scope has three excluded preparation builds followed by eighteen measured builds. Variant order rotates off/on/vanilla, on/vanilla/off, vanilla/off/on; output-policy order also alternates. Build wall time includes synchronous publication and final validation but excludes separately timed clean. PSS/RSS is sampled every 100 ms; host and WSL storage checks remain active.

All requested executable digests match vanilla preparation. Toplevel also hashes actual executable files; measured minimal runs require final executables to remain absent. File inventories record intermediate archives and exports. All **201 test cases in 15 targets pass**, including witness size/corruption/budget handling, concurrent segment rotation, leases/GC, repository changes, old-format isolation, full memo behavior and unchanged traversal limits. This is the SkyCache package suite plus the specified Datadog builds, not a full Datadog unit/E2E test run or Windows qualification.

The original Datadog lockfile is restored after each scope (SHA-256 `1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8`), tracked workspace changes are checked, and owned build servers are shut down.

## Diagnostic attempts retained

The initial segmented-format candidate passed the test package and published the large tree. A complete comparison round exposed the original 250,000-entry success memo rejecting valid roots: six-target minimal on took 33.68 s versus vanilla 14.22 s, and toplevel on 51.24 s versus vanilla 26.83 s. The second round was deliberately interrupted to address this; the interrupted row is excluded.

The next candidate stopped rejecting successes when the memo filled, but its excluded reuse preparation performed 82,389,346 record reads and was stopped after 133.90 s. This demonstrates that dropping retention alone can cause severe repeated traversal. The final candidate separates a bounded one-million-entry memo from the unchanged traversal limits. Both interrupted attempts, source hashes, commands, output digests, samples and stop reasons are retained, not blended into final medians.

Two development test failures are retained: an assertion assumed eager pack opening, and a new fixture modified its workspace after taking the stability snapshot. The former now verifies the descriptor bound under lazy opening; the latter creates fixtures before the snapshot. Subsequent full test runs pass.

Public evidence includes patches, scripts, logs, metrics, output hashes, samples, XML test results, and checksums. Raw BEP, gRPC, profiles, binaries and cache files are in the verified private archive recorded in [private-backup.json](measurements/skycache-large-tree-2026-10-25/private-backup.json). Raw content may contain environment details, so it is not committed to the public branch. All historical caches and failed attempts remain preserved.
'''
(D/'docs/jsgette/skycache-large-tree-2026-10-25.md').write_text(report)
(T/'outputs/SkyCache-large-tree-results.md').write_text(report)
files=sorted(q for q in E.rglob('*') if q.is_file());(E/'SHA256SUMS').write_text(''.join(sha(q)+'  '+str(q.relative_to(E))+'\n' for q in files))
print(json.dumps({'public_files':len(files)+1,'archive_bytes':archive.stat().st_size,'archive_sha256':sha(archive),'report':str(D/'docs/jsgette/skycache-large-tree-2026-10-25.md')}))
