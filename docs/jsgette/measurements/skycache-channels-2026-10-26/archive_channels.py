from pathlib import Path
import gzip, hashlib, json, shutil, subprocess

ROOT=Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26')
REPO=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
WORK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-26/review-our-current-progress-and-find/work')
DEST=REPO/'docs/jsgette/measurements/skycache-channels-2026-10-26'
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def compress(a,b):b.write_bytes(gzip.compress(a.read_bytes(),mtime=0))

DEST.mkdir(exist_ok=False)
for cohort in ['warm','cold']:
 native=ROOT/('warm-controlled' if cohort=='warm' else cohort)
 comparison=json.loads((ROOT/(cohort+'-comparison.json')).read_text())
 assert comparison['witness_tables_identical']
 for kind in ['comparison','witnesses']:
  shutil.copy2(ROOT/(cohort+'-'+kind+'.json'),DEST/(cohort+'-'+kind+'.json'))
 folder=DEST/cohort;folder.mkdir()
 r=json.loads((native/'results.json').read_text())
 assert r['status']=='passed' and r['source_states_unchanged'] and r['all_binaries_unchanged']
 compress(native/'results.json',folder/'results.json.gz')
 compact={k:v for k,v in r.items() if k!='runs'}
 compact['runs']=[]
 for row in r['runs']:
  compact['runs'].append({k:v for k,v in row.items() if k not in ['argv','rpc','build_metrics']})
  assert not row.get('rpc')
  compress(native/(row['label']+'-timeline.jsonl'),folder/(row['label']+'-timeline.jsonl.gz'))
 compact['complete_results']='results.json.gz'
 save(folder/'results.json',compact)
 shutil.copy2(native/'harness.py',folder/'harness.py')
for variant in ['128-v2','256-v2']:
 source=ROOT/('build-'+variant);target=DEST/('build-'+variant);target.mkdir()
 result=json.loads((source/'result.json').read_text())
 assert result['exit_code']==0 and all(t['status']=='PASSED' for t in result['tests'])
 for name in ['command.json','result.json']:shutil.copy2(source/name,target/name)
 compress(source/'source.diff',target/'source.diff.gz')
 shutil.copytree(source/'tests',target/'tests')
for name in ['build_channels.py','make_channel_benchmark.py','analyze_channels.py','archive_channels.py',
 'check_channel_registry.py','ChannelRegistryCheck.java']:
 shutil.copy2(WORK/name,DEST/name)
for name in ['SkycacheChannelAgent.java','build_channel_agent.py','verify_channel_evidence.py']:
 shutil.copy2(WORK/name,DEST/name)
shutil.copy2(ROOT/'agent/provenance.json',DEST/'agent-provenance.json')
shutil.copy2(ROOT/'registry/comparison.json',DEST/'registry-comparison.json')
for name in ['warm','warm-final']:
 compress(ROOT/name/'results.json',DEST/('excluded-'+name+'.json.gz'))
sources=['src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache.java',
 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCacheTest.java',
 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/BUILD',
 'src/main/java/com/google/devtools/build/lib/skyframe/serialization/AutoRegistry.java']
recordings=[]
for cohort in ['warm','cold']:
 native=ROOT/('warm-controlled' if cohort=='warm' else cohort)
 for pattern in ['*.profile.gz','*.bep.json']:
  for p in sorted(native.glob(pattern)):recordings.append(dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)))
save(DEST/'provenance.json',dict(source_base='126d3813c8',native_root=str(ROOT),
 source_sha256={p:sha(REPO/p) for p in sources},recordings=recordings,
 preparation_sha256=sha(Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26/preparation.json')),
 notes=['All timed endpoints bypass the forwarding proxy.',
  'Server startup, seed preparation, warmups and build/test work are outside timed build commands.',
  '128 and 256 prototypes differ only in the default idle-channel capacity.',
  'Initial build-256 validation preceded a restored close-time check and is not a timed candidate.',
  'Initial warm attempts aborted on zero hits: installation checksums intentionally separate binaries. The first prototype also changed dynamic codec tags; runtime helper exclusion preserves the final registry.',
  'Primary cohorts run one frozen executable with a startup-only agent. Baseline retains its original reader; candidates load exact compiled replacement reader bytes and helper classes. The common registry exclusion preserves tag inventory. No installation checksum or validation guard is changed.']))
assert not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/worktree/skycache_publication_probe_20261026').exists()
assert not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/output-base/server/server.pid.txt').exists()
print(json.dumps(dict(destination=str(DEST),files=sum(p.is_file() for p in DEST.rglob('*')))))
