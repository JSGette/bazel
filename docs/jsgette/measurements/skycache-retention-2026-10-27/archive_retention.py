from pathlib import Path
import gzip,hashlib,json,shutil
ROOT=Path('/home/jgett/REPOS/.work/skycache-retention-2026-10-27')
REPO=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
WORK=Path(__file__).parent
DEST=REPO/'docs/jsgette/measurements/skycache-retention-2026-10-27'
COHORTS=['cold','warm','diagnostic-before-v2','diagnostic-after']
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def compress(a,b):b.write_bytes(gzip.compress(a.read_bytes(),mtime=0))
DEST.mkdir(exist_ok=False)
recordings=[]
for cohort in COHORTS:
 native=ROOT/cohort;folder=DEST/cohort;folder.mkdir()
 r=json.loads((native/'results.json').read_text())
 assert r['status']=='passed' and r['source_states_unchanged'] and r['all_binaries_unchanged']
 for kind in ['comparison','witnesses']:
  shutil.copy2(ROOT/(cohort+'-'+kind+'.json'),DEST/(cohort+'-'+kind+'.json'))
 compress(native/'results.json',folder/'results.json.gz')
 compact={k:v for k,v in r.items() if k!='runs'};compact['runs']=[]
 for row in r['runs']:
  compact['runs'].append({k:v for k,v in row.items() if k not in ['argv','rpc','build_metrics']})
  assert not row.get('rpc')
  for suffix in ['-timeline.jsonl','-stages.jsonl']:
   p=native/(row['label']+suffix)
   if p.exists():compress(p,folder/(p.name+'.gz'))
 compact['complete_results']='results.json.gz';save(folder/'results.json',compact)
 shutil.copy2(native/'harness.py',folder/'harness.py')
 for pattern in ['*.profile.gz','*.bep.json','*.jfr','*.log']:
  for p in sorted(native.glob(pattern)):recordings.append(dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)))
source=ROOT/'build-advisory';target=DEST/'build-advisory';target.mkdir()
build=json.loads((source/'result.json').read_text());assert build['exit_code']==0 and build['cases']==637 and len(build['tests'])==23
for name in ['command.json','result.json']:shutil.copy2(source/name,target/name)
compress(source/'source.diff',target/'source.diff.gz');shutil.copytree(source/'tests',target/'tests')
for kind in ['agent-before','agent','probe-before','probe']:
 (DEST/kind).mkdir()
 for p in (ROOT/kind).glob('*.java'):shutil.copy2(p,DEST/kind/p.name)
 if (ROOT/kind/'provenance.json').exists():shutil.copy2(ROOT/kind/'provenance.json',DEST/kind/'provenance.json')
 save(DEST/kind/'files.json',[dict(name=p.name,sha256=sha(p),bytes=p.stat().st_size) for p in sorted((ROOT/kind).iterdir()) if p.is_file()])
for name in ['prepare_retention_experiment.py','prepare_retention_measurement.py','prepare_retention_build.py',
 'build_retention.py','run_retention_measurements.py','skycache_retention_benchmark.py','retention_cold_pages.py',
 'analyze_retention.py','check_retention_registry.py','ChannelRegistryCheck.java','SkycacheRetentionProbe.java',
 'summarize_retention_trace.py','archive_retention.py','write_retention_report.py','verify_retention_evidence.py',
 'build_digests.py','skycache_slots_benchmark.py','cold_pages.py','analyze_digests.py','check_digest_registry.py']:
 shutil.copy2(WORK/name,DEST/name)
shutil.copy2(ROOT/'registry/comparison.json',DEST/'registry-comparison.json')
shutil.copy2(ROOT/'trace-comparison.json',DEST/'trace-comparison.json')
save(DEST/'provenance.json',dict(source_base='f94779009f',native_root=str(ROOT),source_sha256=build['source_sha256'],
 recordings=recordings,notes=[
 'One frozen original installation; both primary arms use identical compiled f94779009f RemoteSkyCache/AutoRegistry classes. Only RemoteChunkCache and its nested class bytes vary. No namespace, installation or validation guard is bypassed.',
 'Primary evidence checks startup agent hash and mode through the AutoRegistry load log; other class-load stderr lines are not consistently forwarded to the client. The archived agent maps exact class bytes, and separate before/after method probes verify the changed store path.',
 'Private local/backend metadata pages were synced, evicted with POSIX_FADV_DONTNEED, and verified using mincore. No global page-cache flush; host/device caches remain uncontrolled.',
 'No benchmark overlaps a build/test job or another measured run. Fresh JVM/graph, same publication bytes, source/tool/environment identity and action-cache settings.',
 'Initial diagnostic-before setup failed before any timed run because the generator changed a legacy fixture dictionary key. diagnostic-before-v2 is the successful run; the empty failed setup directory remains at its native path.',
 'The retention change deliberately delegates advisory chunk durability to OS writeback. Complete local/publication durability is unchanged. Recovery tests inject missing/empty/corrupt/truncated states; no machine power-loss test was performed.']))
assert not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/worktree/skycache_publication_probe_20261026').exists()
assert not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/output-base/server/server.pid.txt').exists()
print('Archived',DEST)
