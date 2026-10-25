"""Validate and publish the concurrency experiment, retaining private raw evidence."""
from pathlib import Path
import hashlib,importlib.util,json,shutil,struct,subprocess,tarfile
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
R=Path('/home/jgett/REPOS/.work/datadog-concurrent-2026-10-25')
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
E=D/'docs/jsgette/measurements/skycache-concurrent-worktrees-2026-10-25'
def sha(q):
 with q.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(q,x):q.write_text(json.dumps(x,indent=2)+'\n')
p=json.loads((R/'results.json').read_text());assert p['status']=='passed' and p['original_workspace_unchanged']
assert sha(Path(p['binary']))==p['binary_sha256']
passed=[x for x in p['phases'] if x.get('passed')]
assert all(r['exit_code']==0 for x in passed for r in x['runs'])
assert subprocess.check_output(['git','-C','/home/jgett/REPOS/bazel-skycache-v2','rev-parse','HEAD'],text=True).strip()==p['implementation_revision']
assert not subprocess.check_output(['git','-C','/home/jgett/REPOS/bazel-skycache-v2','status','--porcelain'],text=True).strip()
assert sha(Path('/home/jgett/REPOS/datadog-agent/MODULE.bazel.lock'))==p['lock_sha256']
assert subprocess.check_output(['git','-C','/home/jgett/REPOS/datadog-agent','diff','--name-only','HEAD'],text=True).strip()=='MODULE.bazel.lock'
assert len(set(p['expected'].values()))==3 and len(p['namespaces'])==1
if E.exists() and not (E/'publish-concurrent-protocol-initial.py').exists():
 shutil.copy2(E/'publish-concurrent-protocol.py',E/'publish-concurrent-protocol-initial.py')
 (E/'publication-tool-note.txt').write_text('Initial publisher stopped at backing object presence assertion because it omitted the packed-v1 layout component. Corrected to the implementation layout, then reran archive/checksum verification. No benchmark was rerun or product code changed.\n')
E.mkdir(parents=True,exist_ok=True)
# Raw events/RPC metadata can contain environment values or headers; retain them privately.
raw=[q for q in sorted(R.iterdir()) if q.is_file()]
manifest=[{'name':q.name,'bytes':q.stat().st_size,'sha256':sha(q)} for q in raw]
if (E/'raw-evidence-manifest.json').exists():assert json.loads((E/'raw-evidence-manifest.json').read_text())==manifest
save(E/'raw-evidence-manifest.json',manifest)
archive=Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-concurrent-worktrees-evidence.tar')
if not archive.exists():
 with tarfile.open(archive,'w') as tar:
  for q in raw:tar.add(q,arcname=q.name)
with tarfile.open(archive) as tar:
 assert len(tar.getmembers())==len(manifest)
 expected={x['name']:x['sha256'] for x in manifest}
 for m in tar.getmembers():
  with tar.extractfile(m) as f:assert hashlib.file_digest(f,'sha256').hexdigest()==expected[m.name]
save(E/'private-backup.json',{'path':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive),'verified':True})
for q in raw:
 if q.name.endswith(('.grpc.bin','.bep.json','.profile.gz')):continue
 if q.suffix=='.json':
  data=json.loads(q.read_text())
  def sanitize(x):
   if isinstance(x,dict):
    if 'build_metrics' in x:x['build_metrics']={k:v for k,v in x['build_metrics'].items() if k in ['timingMetrics','memoryMetrics','actionSummary','buildGraphMetrics','cumulativeMetrics']}
    for v in x.values():sanitize(v)
   elif isinstance(x,list):
    for v in x:sanitize(v)
  sanitize(data);save(E/q.name,data)
 else:shutil.copy2(q,E/q.name)
for name in ['datadog-concurrent.py','datadog-download-modes.py','datadog-clean-benchmark.py','publish-concurrent-protocol.py']:
 shutil.copy2(T/'work'/name,E/name)
# Verify each atomic manifest, its identity/common namespace and parent closure.
cache=Path(p['cache']);publications=[]
for rel,digest in p['final_publications'].items():
 q=cache/rel;b=q.read_bytes();assert sha(q)==digest
 payload=b[:-32];assert hashlib.sha256(payload).digest()==b[-32:]
 assert payload[:4]==b'SKS3';pos=4
 def utf():
  global pos
  size=struct.unpack_from('>H',payload,pos)[0];pos+=2
  value=payload[pos:pos+size].decode();pos+=size;return value
 identity= utf();namespace=utf();source=utf()
 assert identity==q.stem and namespace==q.parent.name
 count=struct.unpack_from('>i',payload,pos)[0];pos+=4
 parents=[utf() for _ in range(count)]
 for parent in parents:assert (q.parent/(parent+'.manifest')).is_file()
 objects=cache/'leased-v1/objects/packed-v1'/identity
 assert objects.is_dir() and (objects/'index').is_file()
 publications.append({'path':rel,'sha256':digest,'bytes':len(b),'id':identity,'namespace':namespace,'source_digest':source,'parents':parents,'objects_present':True})
assert len({x['namespace'] for x in publications})==1
save(E/'publication-integrity.json',publications)
spec=importlib.util.spec_from_file_location('h',T/'work/datadog-concurrent.py');h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
profile_checks=[]
for phase in passed:
 if phase['label'] not in ['concurrent-readers','swapped-readers-minimal','swapped-readers-toplevel']:continue
 for r in phase['runs']:
  data=(R/(r['label']+'.grpc.bin')).read_bytes();pos=0
  while pos<len(data):
   size,pos=h.modes.varint(data,pos);f=h.modes.fields(data[pos:pos+size]);pos+=size
   if not f.get(3,[b''])[0].decode().endswith('/FindMissingBlobs'):continue
   req=h.modes.fields(h.modes.fields(h.modes.fields(f[4][0])[10][0])[1][0])
   digests=[h.modes.fields(digest) for digest in req.get(2,[])]
   profile=R/(r['label']+'.profile.gz')
   assert len(digests)==1 and digests[0][1][0].decode()==sha(profile) and digests[0][2][0]==profile.stat().st_size
   profile_checks.append({'label':r['label'],'sha256':sha(profile),'bytes':profile.stat().st_size,'purpose':'BES profile upload availability check; not an action-result or output validation'})
save(E/'bes-profile-checks.json',profile_checks)
writer=next(x for x in passed if x['label'].startswith('concurrent-writers'))
assert len(writer['runs'])==2 and writer['overlap_seconds']>0
assert all(x['local-skycache.json']['published'] and x['local-skycache.json']['shared_reads']>0 for x in writer['runs'])
for phase in passed:
 if phase['label'] in ['concurrent-readers','swapped-readers-minimal','swapped-readers-toplevel']:
  for r in phase['runs']:
   assert r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
   assert r['output_inventory']['archives']==r['output_inventory']['exports']==0
rows=[]
for phase in passed:
 for r in phase['runs']:
  s=r.get('local-skycache.json',{});g=r['grpc'];ar=g['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)
  rows.append(f"| {phase['label']} | {r['worktree'].upper()} / {r['state']} | {r['mode']} | {r['seconds']:.2f} | {r['analysis_hits']} / {r['execution_hits']} | {ar} | {s.get('writes',0):,} |")
checks=[]
for phase in passed:
 if phase['overlap_seconds'] is not None:
  checks.append(f"| {phase['label']} | {phase['overlap_seconds']:.2f} | {phase['peak_combined_pss_kib']/1024:.0f} |")
controls=next(x for x in passed if x['label']=='controls')
versions='\n'.join('```text\n'+r['output_inventory']['target_files'][0]['version_stdout'].strip()+'\n```' for r in controls['runs'])
report=f'''# Shared SkyCache with two concurrent Datadog worktrees — 25 October 2026

With remote transfers capped at 16 concurrent requests per server, two WSL Datadog Git worktrees successfully read and published to **one SkyCache namespace** while building different source changes concurrently. Their outputs matched independent cache-disabled controls. After swapping the edits between worktrees and running synchronous `bazel clean`, each reused the other worktree's publication and retained the correct executable digest and runtime version marker. No product changes were made for this test. Default-limit attempts encountered appliance/transport failures and are retained separately below.

## Setup and acceptance

- Datadog revision `{p['source_revision']}`; the original checkout and its resolved module lockfile were preserved. A and B share its Git common directory, have separate source trees and separate output bases, and use `{p['cache']}` for SkyCache.
- Candidate implementation `{p['implementation_revision']}`; executable SHA-256 `{p['binary_sha256']}`. Cache & Carry at `{p['remote_cache']}` was enabled throughout; disk action cache was disabled.
- A changes `cmd/cluster-agent/subcommands/version/command.go` to print `Cluster Agent Skycache-A`. B independently changes `pkg/cli/subcommands/version/command.go` to print `Serialization version Skycache-B`. The [source patches](measurements/skycache-concurrent-worktrees-2026-10-25/a-state-a.patch) and [B patch](measurements/skycache-concurrent-worktrees-2026-10-25/b-state-b.patch) include the established resolved lockfile.
- Each server: 4 GiB maximum JVM heap, eight jobs, 3072 MiB local action-memory budget. Both build `{p['target']}`. The passing writer/reader matrix additionally sets `--remote_max_connections=1 --remote_max_concurrency_per_connection=16` on each server, after unstable default-limit attempts. These transport limits do not serialize the two builds or the local SkyCache. Existing external repository downloads were copied and known-root symlinks relocated; local outputs/action caches were not copied.
- Before **every** build: shutdown, synchronous `clean --noasync --noexpunge`, and assertions for zero Skyframe nodes/edges, empty execroot, absent action cache, unchanged persistent publications. All concurrent pairs start at a barrier; recorded monotonic intervals prove writer/reader overlap. The recovered B control lacks its harness PID/timing record, as explained below.
- The changed cache-disabled controls run before the cache-enabled changed builds and disable remote uploads. Thus their newly compiled changed results cannot come from this experiment's SkyCache-produced results. The same candidate binary is used for controls.

## Results

`CT / execution` is the count of restored configured targets and action-execution values. Writes count accepted new local SkyCache entries. Times include cache publication but exclude separately recorded clean/JVM startup. B's recovered control uses Bazel-reported elapsed time; all other rows use the harness's monotonic timing.

| Phase | Worktree / source variant | Policy | Seconds | CT / execution | GetActionResult | New SkyCache entries |
|---|---|---|---:|---:|---:|---:|
{chr(10).join(rows)}

Both concurrent writer builds read existing shared entries and successfully published new entries. Their build intervals overlapped for **{writer['overlap_seconds']:.2f} seconds**. All prior manifests remained byte-identical. The final {len(publications)} manifests passed checksum, identity, common-namespace, parent-reference and backing-object-presence checks. Publication events and exact timestamps are in [results.json](measurements/skycache-concurrent-worktrees-2026-10-25/results.json); integrity evidence is in [publication-integrity.json](measurements/skycache-concurrent-worktrees-2026-10-25/publication-integrity.json).

| Concurrent phase | Overlap seconds | Combined peak PSS MiB |
|---|---:|---:|
{chr(10).join(checks)}

Memory is sampled combined server process-tree PSS every 200 ms, including action descendants. It excludes the Python probe, client launchers, unrelated processes and OS page cache. Other unrelated Bazel servers were present on the machine and were left untouched. These observations establish functional overlap; they are not a controlled speedup benchmark or a statistical contention study.

## Output isolation and build without bytes

Independent control output:

{versions}

| Source state | Executable SHA-256 |
|---|---|
| Baseline | `{p['expected']['base']}` |
| A only | `{p['expected']['a']}` |
| B only | `{p['expected']['b']}` |

The three hashes are distinct. Every successful later build's BEP output digest matched the correct control. Final `toplevel` builds also verified the actual file bytes and executed `cluster-agent version`, requiring the expected marker and rejecting the other marker. Swapping source variants forces A to consume B's cached variant and B to consume A's, ruling out mere reuse of each worktree's own prior state.

The strict clean hit phases require **zero `GetActionResult`**. `minimal` additionally requires zero CAS reads, no final executable and no intermediate `.a`/`.x` files. Final `toplevel` builds fetch only requested output/runfile blobs and match control bytes, with no intermediate `.a`/`.x` files. Newly executed local actions may materialize files during a cache miss; the no-materialization assertions apply to fully cached minimal builds. A separate `remote-metadata-fill` pass allows newly compiled/uploaded local outputs to be observed as remote-backed metadata before the strict hit checks.

Hit builds still issue one remote `GetCapabilities` request. Three final hit builds also made one `FindMissingBlobs` request each for their BES profile: the queried hash and size exactly match that run's `.profile.gz`, as verified in [bes-profile-checks.json](measurements/skycache-concurrent-worktrees-2026-10-25/bes-profile-checks.json). These were profile-upload checks, not action-result or build-output validation. Each final toplevel build read five requested output/runfile blobs totaling 121,907,152 bytes.

## Preserved setup failure and scope

The initial seed build itself succeeded and produced the baseline digest, but publication was safely rejected with `inputs changed during the invocation`. This occurred on the first build after relocating external repositories. The same source and executable published successfully on a clean retry after repository setup stabilized. The protocol preserves the original failure and harness; it does not count that attempt as a successful publication. No product change or cache validation bypass was used.

The B control also completed successfully despite many Cache & Carry transient errors. Its gRPC log exposed an instrumentation parser bug: synthetic terminal retry-summary records have `rpc_id` and field 9 but no method field. The corrected parser correlates these records with their attempts and reports them separately, retaining all error counts. B's successful BEP, executable, runtime marker, digest and RPC log were recovered without repeating the build. The phase memory samples and B's exact monotonic timing were lost on that parser exception and are explicitly unavailable. A recovery-script assumption about an omitted protobuf default exit-code field was corrected before any further build.

The first concurrent writer pair then failed with Cache & Carry `GetCapabilities` connection refused, and neither published an incomplete build. The separate user-authorized LAN/BES task reported an appliance restart/licensing connectivity outage. It also enabled BES publishing in future harness launches; that setting was preserved. After `/healthz` returned `HEALTHY`, the writer pair was retried against the same cache and source states. All attempts are retained. BES traffic from the writer retry onward is included in timings/RPC evidence; these runs are not directly comparable to the earlier BES-disabled performance series.

That first retry still encountered repeated transport resets. A completed with the correct output and published but returned exit 38 for failed BES delivery; B failed before producing its output. These are not counted as a passing pair. Its logs also contained valid non-retrying RPC entries without `rpc_id`; the parser now counts those explicitly rather than assuming every entry has an ID. Exact build records were recovered, but that phase's memory samples were unavailable. The subsequent passing matrix capped remote transfer concurrency at 16 requests per server. The evidence does not establish whether connection pressure, appliance recovery, background BES uploads, or their combination caused the earlier transport failures, nor does it prove success at the default remote limits.

Both bounded writers exited zero and published. The harness then incorrectly asserted that every minimal build must leave the final executable absent. That assertion was narrowed to the strict cache-hit phases: locally executed actions legitimately produce output files. The completed writers were revalidated against their independent hashes and runtime markers, without rerunning them. Their saved memory samples and monotonic overlap are intact.

This checks two processes sharing one local SSD cache, divergent source content, clean readers/writers, cross-worktree variants and output policies. It does not prove arbitrary process counts, crash recovery, GC contention or network-filesystem semantics. Garbage collection was disabled to isolate the requested read/write test.

## Evidence

The [evidence directory](measurements/skycache-concurrent-worktrees-2026-10-25) contains the reproducible harnesses, commands, build/clean/graph logs, patches, RPC counts, memory/storage samples, failed attempts and SHA-256 checksums. Raw BEP, gRPC logs and profiles remain in a verified private archive on D: because they can include environment values or request headers. The archive checksum and location are in [private-backup.json](measurements/skycache-concurrent-worktrees-2026-10-25/private-backup.json).
'''
q=D/'docs/jsgette/skycache-concurrent-worktrees-2026-10-25.md';q.write_text(report)
(T/'outputs/SkyCache-concurrent-worktrees.md').write_text(report)
files=sorted(q for q in E.rglob('*') if q.is_file() and q.name!='SHA256SUMS')
(E/'SHA256SUMS').write_text(''.join(sha(q)+'  '+str(q.relative_to(E))+'\n' for q in files))
print(json.dumps({'report':str(q),'evidence_files':len(files)+1,'private_archive':str(archive),'passed_phases':len(passed),'builds':sum(len(x['runs']) for x in p['phases']),'publications':len(publications)},indent=2))
