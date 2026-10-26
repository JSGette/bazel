from pathlib import Path
import gzip,hashlib,json,shutil,subprocess
ROOT=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26')
REPO=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
WORK=Path(__file__).parent
DEST=REPO/'docs/jsgette/measurements/skycache-digests-cold-2026-10-26'
COHORTS=['warm','cold','eager','empty','diagnostic','confirm','slots-warm','slots-cold']

def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def compress(a,b):b.write_bytes(gzip.compress(a.read_bytes(),mtime=0))

DEST.mkdir(exist_ok=False)
recordings=[]
for cohort in COHORTS:
 native=ROOT/('warm-v2' if cohort=='warm' else cohort)
 for kind in ['comparison','witnesses']:
  shutil.copy2(ROOT/(cohort+'-'+kind+'.json'),DEST/(cohort+'-'+kind+'.json'))
 folder=DEST/cohort;folder.mkdir()
 r=json.loads((native/'results.json').read_text())
 assert r['status']=='passed' and r['source_states_unchanged'] and r['all_binaries_unchanged']
 compress(native/'results.json',folder/'results.json.gz')
 compact={k:v for k,v in r.items() if k!='runs'};compact['runs']=[]
 for row in r['runs']:
  compact['runs'].append({k:v for k,v in row.items() if k not in ['argv','rpc','build_metrics','hydrate_argv']})
  assert not row.get('rpc')
  for suffix in ['-timeline.jsonl','-stages.jsonl','-hydrate.log']:
   path=native/(row['label']+suffix)
   if path.exists():compress(path,folder/(path.name+'.gz'))
 compact['complete_results']='results.json.gz';save(folder/'results.json',compact)
 shutil.copy2(native/'harness.py',folder/'harness.py')
 for pattern in ['*.profile.gz','*.bep.json','*.jfr','*.log']:
  for p in sorted(native.glob(pattern)):
   recordings.append(dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)))
for build in ['build-candidate','build-slots-v2']:
 source=ROOT/build;target=DEST/build;target.mkdir()
 result=json.loads((source/'result.json').read_text())
 assert result['exit_code']==0 and result['cases']==635 and len(result['tests'])==23
 for name in ['command.json','result.json']:shutil.copy2(source/name,target/name)
 compress(source/'source.diff',target/'source.diff.gz')
 shutil.copytree(source/'tests',target/'tests')
for name in ['build_digests.py','build_digest_agent.py','make_digest_benchmark.py','skycache_channel_benchmark.py',
 'SkycacheChannelAgent.java','analyze_digests.py','archive_digests.py','check_digest_registry.py',
 'ChannelRegistryCheck.java','cold_pages.py','HydrateSkycacheSeedParallel.java','HydrateSkycacheSeed.java',
 'prepare_cold_probe.py','summarize_cold_trace.py','verify_digest_evidence.py','run_digest_cohorts.py','run_digest_confirmation.py','inspect_digest_jfr.py',
 'prepare_slot_experiment.py','build_slot_agent.py','run_slot_experiment.py','write_digest_report.py','update_digest_main.py',
 'skycache_digest_benchmark.py','skycache_slots_benchmark.py']:
 shutil.copy2(WORK/name,DEST/name)
for name in ['SkycacheDigestAgent.java','SkycacheColdProbe.java','SkycacheColdProbeV2.java']:
 shutil.copy2(ROOT/'agent'/name,DEST/name)
for agent in ['agent','agent-slots']:
 shutil.copy2(ROOT/agent/'provenance.json',DEST/(agent+'-provenance.json'))
(DEST/'agent-slots').mkdir()
shutil.copy2(ROOT/'agent-slots/SkycacheDigestAgent.java',DEST/'agent-slots/SkycacheDigestAgent.java')
shutil.copy2(ROOT/'registry/comparison.json',DEST/'prototype-registry-comparison.json')
shutil.copy2(ROOT/'registry-slots/comparison.json',DEST/'registry-comparison.json')
shutil.copy2(ROOT/'cold-trace.json',DEST/'cold-trace.json')
shutil.copy2(ROOT/'combined-warm.json',DEST/'combined-warm.json')
shutil.copy2(ROOT/'monitor-comparison.json',DEST/'monitor-comparison.json')
compress(ROOT/'warm/results.json',DEST/'excluded-harness-attempt.json.gz')
save(DEST/'provenance.json',dict(source_base='869792d6b1',native_root=str(ROOT),
 selected_build='build-slots-v2',source_sha256=result['source_sha256'],recordings=recordings,
 preparation_sha256=sha(Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26/preparation.json')),
 notes=['All arms share one frozen installation and the identical publication seed. Exact tested reader/helper class bytes are selected by a startup-only agent; validation and installation guards are unchanged.',
 'All cold metadata files were private copies; Linux page residency was measured with mincore after per-file fsync and POSIX_FADV_DONTNEED. This does not flush host or device caches.',
 'No measured build overlaps another benchmark or build/test job. Fresh JVM/empty graph, fixed action-cache settings, output digests and hit counts verified.',
 'Eager timings include helper JVM startup, authenticated hydration and local build. Parallel prefetch is a benchmark-only 16-worker helper using the existing downloader; it is not a product feature.',
 'Initial warm attempt failed in harness before timing because a local argv variable shadowed parsed options. It is excluded.',
 'Confirmation cohort follows a slower first warm sample; all accepted initial measurements are retained. Both use the initial synchronized scratch pool.',
 'The selected fixed-slot atomic pool is measured separately in slots-warm and slots-cold. Do not merge its results with the initial pool. Both implementations preserve the same authentication and allocation strategy.']))
assert not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/worktree/skycache_publication_probe_20261026').exists()
assert not Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent/output-base/server/server.pid.txt').exists()
print(json.dumps(dict(destination=str(DEST),files=sum(p.is_file() for p in DEST.rglob('*')))))
