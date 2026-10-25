"""Summarize and archive the larger verified-clean Datadog comparison."""
from pathlib import Path
import hashlib,importlib.util,json,shutil,statistics,subprocess,tarfile
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
W=Path('/home/jgett/REPOS/.work')
R=W/'datadog-wide-apps-2026-10-25'
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
E=D/'docs/jsgette/measurements/skycache-datadog-wide-2026-10-25'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
p=json.loads((R/'results.json').read_text());assert p['status']=='passed' and p['workspace_unchanged']
measured=[r for r in p['runs'] if not r['preparation']]
assert len(measured)==18 and all(r['validation']=='passed' and r['exit_code']==0 for r in p['runs'])
assert all(r['graph_nodes_after_clean']==r['graph_edges_after_clean']==0 for r in p['runs'])
assert all(not r['execroot_after_clean'] and not r['action_cache_after_clean'] and r['publications_preserved'] for r in p['runs'])
E.mkdir(parents=True,exist_ok=False)
spec=importlib.util.spec_from_file_location('grpc_helpers',T/'work/datadog-concurrent.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
profile_checks=[]
for r in measured:
 if r['variant']!='on':continue
 assert r['analysis_hits']>0 and r['execution_hits']>0
 assert r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
 assert r['output_inventory']['archives']==r['output_inventory']['exports']==0
 if r['mode']=='minimal':assert r['grpc']['bytestream_read_calls']==0
 else:assert r['grpc']['bytestream_read_calls']==10 and r['grpc']['bytestream_read_bytes']==304075144
 data=(R/(r['label']+'.grpc.bin')).read_bytes();pos=0
 while pos<len(data):
  size,pos=c.modes.varint(data,pos);f=c.modes.fields(data[pos:pos+size]);pos+=size
  if not f.get(3,[b''])[0].decode().endswith('/FindMissingBlobs'):continue
  request=c.modes.fields(c.modes.fields(c.modes.fields(f[4][0])[10][0])[1][0])
  digests=[c.modes.fields(digest) for digest in request.get(2,[])]
  profile=R/(r['label']+'.profile.gz')
  assert len(digests)==1 and digests[0][1][0].decode()==sha(profile) and digests[0][2][0]==profile.stat().st_size
  profile_checks.append({'run':r['label'],'sha256':sha(profile),'bytes':profile.stat().st_size,'purpose':'BES profile upload, not action-result or output validation'})
save(E/'bes-profile-checks.json',profile_checks)
groups={}
for mode in ['minimal','toplevel']:
 for v in ['vanilla','off','on']:
  rows=[r for r in measured if r['mode']==mode and r['variant']==v];assert len(rows)==3
  cols={'build_seconds':[r['seconds'] for r in rows],'clean_plus_build_seconds':[r['clean_plus_build_seconds'] for r in rows],
   'analysis_seconds':[int(r['build_metrics']['timingMetrics'].get('analysisPhaseTimeInMs',0))/1000 for r in rows],
   'peak_pss_mib':[r['peak_pss_kib']/1024 for r in rows],'peak_rss_mib':[r['peak_rss_kib']/1024 for r in rows],
   'action_result_requests':[r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0) for r in rows],
   'cas_read_mib':[r['grpc']['bytestream_read_bytes']/1024**2 for r in rows]}
  groups[mode+'/'+v]={'runs':[r['label'] for r in rows],**{k:{'median':statistics.median(a),'min':min(a),'max':max(a),'values':a} for k,a in cols.items()}}
save(R/'summary.json',groups)
roots=[W/'datadog-wide-2026-10-25',W/'datadog-wide-dependencies-2026-10-25',W/'datadog-wide-2026-10-25-attempt2',W/'datadog-wide-five-2026-10-25',R]
cache_files=[q for q in Path(p['analysis_cache']).rglob('*') if q.is_file()]
save(R/'cache-size.json',{'files':len(cache_files),'bytes':sum(q.stat().st_size for q in cache_files),'published_manifests':len(p['final_publications'])})
raw=[(r,q) for r in roots for q in sorted(r.rglob('*')) if q.is_file()]
manifest=[{'name':str(Path(r.name)/q.relative_to(r)),'bytes':q.stat().st_size,'sha256':sha(q)} for r,q in raw]
archive=Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-datadog-wide-evidence.tar')
assert not archive.exists()
with tarfile.open(archive,'w') as tar:
 for r,q in raw:tar.add(q,arcname=str(Path(r.name)/q.relative_to(r)))
with tarfile.open(archive) as tar:
 assert len(tar.getmembers())==len(manifest)
 expected={x['name']:x['sha256'] for x in manifest}
 for m in tar.getmembers():
  with tar.extractfile(m) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==expected[m.name]
save(E/'raw-evidence-manifest.json',manifest)
save(E/'private-backup.json',{'path':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive),'verified':True})
for root,q in raw:
 if q.parent!=root:continue
 if q.name.endswith(('.bep.json','.grpc.bin','.profile.gz')):continue
 dest=E/root.name/q.name;dest.parent.mkdir(exist_ok=True)
 if q.suffix=='.json':
  data=json.loads(q.read_text())
  def sanitize(x):
   if isinstance(x,dict):
    if 'build_metrics' in x:x['build_metrics']={k:v for k,v in x['build_metrics'].items() if k in ['timingMetrics','memoryMetrics','actionSummary','buildGraphMetrics','targetMetrics','packageMetrics','cumulativeMetrics']}
    for v in x.values():sanitize(v)
   elif isinstance(x,list):
    for v in x:sanitize(v)
  sanitize(data);save(dest,data)
 else:shutil.copy2(q,dest)
for name in ['publish-wide-protocol.py','prepare-datadog-wide.py','inspect-wide-pack.py']:shutil.copy2(T/'work'/name,E/name)
table=[]
for key,g in groups.items():
 mode,v=key.split('/');m=lambda k:g[k]['median']
 table.append(f"| {mode} | {v} | {m('build_seconds'):.2f} | {g['build_seconds']['min']:.2f}–{g['build_seconds']['max']:.2f} | {m('analysis_seconds'):.2f} | {m('clean_plus_build_seconds'):.2f} | {m('peak_pss_mib'):.0f} | {m('peak_rss_mib'):.0f} | {m('action_result_requests'):.0f} | {m('cas_read_mib'):.2f} |")
gains=[]
for mode in ['minimal','toplevel']:
 for base in ['vanilla','off']:
  def reduction(k):return 100*(1-groups[mode+'/on'][k]['median']/groups[mode+'/'+base][k]['median'])
  gains.append(f"| {mode} | {base} | {reduction('build_seconds'):.1f}% | {reduction('peak_pss_mib'):.1f}% |")
details=[]
for r in measured:
 t=r['build_metrics'].get('targetMetrics',{});tim=r['build_metrics'].get('timingMetrics',{})
 details.append({'label':r['label'],'target_metrics':t,'timing_metrics':tim,'analysis_hits':r['analysis_hits'],'execution_hits':r['execution_hits'],
  'output_inventory':r['output_inventory'],'runner_counts':r['runner_counts']})
save(E/'scope-and-output-details.json',details)
scope_rows=[]
for v in ['vanilla','off','on']:
 r=next(r for r in measured if r['variant']==v and r['mode']=='minimal')
 t=r['build_metrics'].get('targetMetrics',{});o=r['output_inventory']
 scope_rows.append(f"| {v} | {t.get('targetsConfigured','unavailable')} | {r['analysis_hits']} | {r['execution_hits']} | {o['regular_files']} | {o['bytes']} | {o['archives']} / {o['exports']} |")
digests='\n'.join(f'| {k} | `{v}` |' for k,v in p['expected_binaries'].items())
scope=int(next(r for r in measured if r['variant']=='vanilla')['build_metrics']['targetMetrics']['targetsConfigured'])
report=f'''# Larger Datadog SkyCache comparison after clean — 25 October 2026

Six Linux application binaries were built with Cache & Carry after verified synchronous `bazel clean`: Cluster Agent, Process Agent, Trace Agent, DogStatsD, Installer and Private Action Runner. The vanilla build configured **{scope:,} targets including aspects**, compared with 57,369 for the preceding Cluster Agent scope (**{scope/57369:.2f} times as many**). The broader core/security/system-probe scope reached 237,112 configured targets but exceeded current SkyCache publication limits, as detailed below. No product source or limits were changed for these tests.

## Repeated measurements

Each cell is the median of three runs. `vanilla` is the preserved pre-SkyCache source build at `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`, not a stock Bazel release. `off` and `on` use the same candidate executable at implementation commit `71521fabb3420707c39d73329fa9aa9622e8c382`; this pair isolates enabling SkyCache more closely.

| Policy | Variant | Build s | Build range s | Analysis s | Clean + build s | Peak PSS MiB | Peak RSS MiB | GetActionResult | CAS read MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(table)}

Positive percentages below mean a reduction from the specified baseline; negative values mean a regression.

Analysis time remained near four seconds: SkyCache minimal measured 4.20 seconds versus 4.04 for vanilla, and toplevel measured 4.13 versus 3.96. Thus this experiment does not show faster analysis in isolation. The total-build gain coincides with avoiding most action processing and remote lookups, while local dependency validation remains substantial. Memory was also mixed: minimal peak PSS fell 15.6% versus vanilla; toplevel was only 1.2% lower, and 11.0% higher than the same candidate with SkyCache disabled. PSS ranges are retained in the summary.

| Policy | Baseline | Build time reduction | Peak PSS reduction |
|---|---|---:|---:|
{chr(10).join(gains)}

The [individual results](measurements/skycache-datadog-wide-2026-10-25/{R.name}/results.json) preserve every observation, command, binary hash, clean check, RPC count and sample. [Summary JSON](measurements/skycache-datadog-wide-2026-10-25/{R.name}/summary.json) includes minima and maxima. Three repetitions on a shared network service do not establish statistical significance.

## Protocol and scope

Datadog revision `{p['source_revision']}`; resolved lockfile SHA-256 `{p['module_lock_sha256']}`. Exact targets:

```text
{chr(10).join(p['targets'])}
```

Before every build, the harness shut down its server, ran `clean --noasync --noexpunge`, and verified zero Skyframe nodes/edges, empty execroot, absent local action cache and byte-identical persistent SkyCache manifests. The clean starts a fresh JVM; the build uses that same PID and an empty graph. Build time excludes clean/JVM startup; the combined column includes the separately measured clean. OS page cache and downloaded external repositories remain warm. This deliberately exercises output deletion as well as analysis-cache discard.

All variants use the same workspace/output base, JDK 25, 6 GiB JVM heap, 16 jobs and 6144 MiB local action-memory budget. Disk action caching is disabled. Cache & Carry remote cache and BES are enabled; the remote transport is capped at one connection and 16 concurrent requests for every variant. SkyCache idle eviction and GC are disabled for the experiment; explicit clean clears the graph. Active directories are `cmd`, covering the six requested command packages.

The sequence first prepares vanilla toplevel outputs, then populates SkyCache in minimal/toplevel, followed by 18 measured builds in rotating variant/policy order. Preparation is excluded from the comparison. Process-tree RSS/PSS is sampled every 100 ms, including the build client, server and action descendants. It excludes unrelated processes and OS filesystem cache; memory sampling can miss short peaks.

Representative first minimal run per variant:

| Variant | Targets configured | Configured-target hits | Execution hits | Local regular files | Local bytes | Intermediate .a / .x files |
|---|---:|---:|---:|---:|---:|---:|
{chr(10).join(scope_rows)}

Configured-target cache hits describe restored frontier values, not the full transitive graph avoided. A SkyCache hit run can configure fewer targets because it stops traversal at those values. [Scope and output details](measurements/skycache-datadog-wide-2026-10-25/scope-and-output-details.json) retains all runs, analysis timing, inventories and runner counts.

## Correctness and cache traffic

All six requested application output digests matched the vanilla preparation in every successful measured build. Toplevel checks additionally hashed the actual executable files; measured minimal builds required those final binaries to remain absent. Intermediate archive counts and total local output bytes are recorded rather than inferred from the output policy.

| Binary | SHA-256 |
|---|---|
{digests}

All six measured SkyCache builds made **zero GetActionResult requests**. Each minimal hit made **zero CAS reads**; each toplevel hit read ten requested output/runfile blobs totaling **304,075,144 bytes**. No intermediate `.a` or `.x` files were materialized in the SkyCache hit runs. Necessary internal manifests were still generated.

The RPC figures include the complete logged build. Hit runs still make `GetCapabilities`, and any `FindMissingBlobs` request was verified against the exact hash and size of that run's BES profile upload. [Profile checks](measurements/skycache-datadog-wide-2026-10-25/bes-profile-checks.json) distinguish this from action-result or build-output validation. Remote CAS bytes are counted from ByteStream responses. The original missing-CAS rewind and source-invalidation regressions remain documented in the [execution-metadata report](skycache-execution-metadata-2026-10-25.md); this larger-scope comparison changes no product source.

## Larger scopes exceeded publication limits

The first scope requested core Agent, Cluster Agent, Process Agent, Trace Agent, Security Agent and System Probe. All six binaries built correctly at **237,112 configured targets including aspects**. Its vanilla cold preparation took 457.02 seconds, executing 10,993 local actions and uploading their results; that observation is not a warm-cache performance baseline. Candidate minimal then built successfully in 58.45 seconds with 16,697 remote action-cache hits, but publication failed with `input manifest exceeds size limit`. No reusable manifest was admitted.

Removing only core Agent left a five-binary scope at **220,062 configured targets**. Vanilla toplevel built in 22.58 seconds; candidate minimal took 25.69 seconds and hit the same publication limit. These differ in output policy and include preparation, so they are not a paired speedup comparison. Both failed scopes and the source inputs are retained, not included in the successful six-application medians.

Read-only inspection of the six-binary unpublished pack verified the checksums of 347,658 serialized dependency-evidence records and reconstructed 418,098 unique external/absolute file witnesses. Their encoded witness data alone occupies **77,546,670 bytes (73.95 MiB)**, above the **64 MiB manifest limit**, before additional final repository-binding witnesses. Three Linux-header repository views account for 40,366,811 bytes, and absolute paths into the shared repository cache account for another 22,402,643 bytes. This is a concrete limit in the eager global witness manifest, beyond per-entry dependency validation.

The pack contains **1,664,716 unique records**, exceeding its separate **1,000,000-entry sealing limit**. Manifest admission happens before pack sealing, so the manifest error is the observed rejection; the entry limit is an additional source-verified blocker, not a second runtime error. Increasing only the manifest cap would leave that blocker. The unpublished pack is 1,421,905,110 bytes and was preserved for inspection. [Pack diagnosis](measurements/skycache-datadog-wide-2026-10-25/datadog-wide-2026-10-25-attempt2/pack-diagnosis.json) records exact counts, grouping and the reconstruction boundary.

The repeated comparison therefore uses the six application binaries listed above, which still exceed the earlier configured-target and action counts while fitting the current publication format. The implementation is not demonstrated to accelerate the 220k/237k scopes. Compacting or restructuring global witness storage and supporting larger or partitioned packs are follow-up design work; neither was bypassed for the benchmark.

The successful application scope retained one publication totaling 878,015,248 bytes across its cache files, approximately 837.3 MiB. This is local graph/metadata storage in addition to the remote output cache.

## Preparation and evidence retention

The first six-target attempt stopped during analysis because newly reached module-extension dependencies required a lockfile refresh. Its logs and partial metrics are preserved, excluded from performance medians. A separate `--nobuild --lockfile_mode=update` preparation completed successfully, after which all compared builds used `--lockfile_mode=error` and the same frozen lockfile. Both pre- and post-resolution lockfiles are retained. Compiler and dependency warnings remain in the logs. The read-only pack diagnosis ran during the excluded application preparation, not during the measured matrix.

The original Datadog lockfile and tracked workspace diff were restored exactly after all measured builds, and the benchmark server was shut down. Product sources remain unchanged.

Public evidence includes commands, logs, output digests, sampled memory/storage, lockfiles and checksums. Raw BEP, profiles, gRPC logs and the cache packs are retained in a SHA-256-verified 3,820,072,960-byte archive on D: because these formats can contain environment values or request headers. [Private backup metadata](measurements/skycache-datadog-wide-2026-10-25/private-backup.json) records the archive. The experiment tests the specified Linux build scope; it is not a full Datadog unit/E2E test suite or a qualification of arbitrary Bazel rules.
'''
(D/'docs/jsgette/skycache-datadog-wide-2026-10-25.md').write_text(report)
(T/'outputs/SkyCache-Datadog-wide.md').write_text(report)
files=sorted(q for q in E.rglob('*') if q.is_file())
(E/'SHA256SUMS').write_text(''.join(sha(q)+'  '+str(q.relative_to(E))+'\n' for q in files))
print(json.dumps({'summary':groups,'evidence_files':len(files)+1,'archive':str(archive)},indent=2))
