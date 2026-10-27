from pathlib import Path
import collections,gzip,hashlib,json,statistics
ROOT=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26')

def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def analyze(name):
 directory=ROOT/('warm-v2' if name=='warm' else name)
 data=json.loads((directory/'results.json').read_text())
 assert data['status']=='passed'
 assert all(data[k] for k in ['source_states_unchanged','binary_unchanged','candidate_binary_unchanged','all_binaries_unchanged'])
 agent_root=ROOT/('agent-slots' if name.startswith('slots-') else 'agent')
 agent=json.loads((agent_root/'provenance.json').read_text())
 assert data['agent_sha256']==sha(agent_root/'agent.jar')==agent['agent_jar']
 rows=[];groups=collections.defaultdict(list);witnesses=[]
 for r in data['runs']:
  if r['warmup']:continue
  arm=r['arm'];mode=r['implementation'];local=arm in ['local','local-empty','eager','eager-parallel']
  assert r['outputs_match'] and r['probe_output_matches'] and not r['errors'] and r['exit_code']==0
  assert (r['analysis_hits'],r['execution_hits'])==((0,0) if arm=='local-empty' else (18,11))
  assert r['local-skycache']['published'] if local else r['committed']
  logs=(directory/(r['label']+'.log')).read_text()+(directory/(r['label']+'-clean.log')).read_text()
  assert 'SKYCACHE_DIGEST_AGENT '+mode+' com/google/devtools/build/lib/skyframe/serialization/AutoRegistry sha256='+agent['registry'] in logs
  events=json.loads(gzip.open(directory/(r['label']+'.profile.gz'),'rt').read())['traceEvents']
  before=sum(e.get('dur',0) for e in events if e.get('name')=='LocalSkycacheModule.beforeCommand')/1e6
  timeline=[json.loads(x) for x in (directory/(r['label']+'-timeline.jsonl')).read_text().splitlines()]
  complete=next(x['elapsed_s'] for x in timeline if 'Build completed successfully' in x['text'])
  row=dict(label=r['label'],arm=arm,implementation=mode,command_s=r['seconds'],
    hydrate_s=r.get('hydrate_seconds',0),before_command_s=before,
    middle_s=complete-before-r.get('hydrate_seconds',0),finalization_s=r['seconds']-complete,
    startup_and_clean_s=r['startup_and_clean_seconds'],validation=r.get('local-skycache-validation'),
    bazel_cpu_s=int(r['build_metrics'].get('timingMetrics',{}).get('cpuTimeInMs',0))/1000,
    heap_garbage_collected_bytes=sum(int(v['garbageCollected']) for v in r['build_metrics'].get('memoryMetrics',{}).get('garbageMetrics',[]) if v['type'].startswith('G1 ')))
  if 'page_cache' in r:
   row['page_cache']=r['page_cache'];assert row['page_cache']['resident_fraction_after']<.001
  if not local:
   stats=r['remote-skycache'];row['remote']=stats
   assert stats['cached_channel_capacity']==128 and stats['cached_channel_peak']<=228
   assert stats['downloaded_bytes']==(0 if arm in ['remote-warm','eager-parallel-remote'] else 1463742717)
   assert stats['authentication_cache_evictions']==0 and stats['local_cache_errors']==0
   if mode=='scratch':
    assert 0<stats['read_scratch_idle']<=64
    row['scratch_reuse_fraction']=stats['read_scratch_reused']/(stats['read_scratch_reused']+stats['read_scratch_created'])
   else:assert 'read_scratch_created' not in stats
  if arm.startswith('eager'):row['hydrate_stats']=r['hydrate_stats']
  if not local:
   files=sorted((p for p in (directory/('cache-'+r['label'])/'leased-v2/objects').rglob('witnesses-*.block')
       if p.parent.name not in data['seed_preparation']['publications']),key=lambda p:p.name)
   table=dict(label=r['label'],blocks=len(files),bytes=sum(p.stat().st_size for p in files),hashes=[sha(p) for p in files])
   assert table['blocks']==103 and table['bytes']==10075663
   witnesses.append(table)
  key=arm if local else arm+'-'+mode
  groups[key].append(row);rows.append(row)
 assert all(t['hashes']==witnesses[0]['hashes'] for t in witnesses)
 fields=['command_s','hydrate_s','before_command_s','middle_s','finalization_s','startup_and_clean_s','heap_garbage_collected_bytes','bazel_cpu_s']
 summary={k:dict(n=len(g),**{f:dict(mean=statistics.mean(r[f] for r in g),median=statistics.median(r[f] for r in g),min=min(r[f] for r in g),max=max(r[f] for r in g)) for f in fields}) for k,g in groups.items()}
 result=dict(cohort=name,summary=summary,rows=rows,witness_tables_identical=True,agent_verified=True)
 (ROOT/(name+'-comparison.json')).write_text(json.dumps(result,indent=2)+'\n')
 (ROOT/(name+'-witnesses.json')).write_text(json.dumps(witnesses,indent=2)+'\n')
 print(json.dumps({k:dict(n=v['n'],command=v['command_s'],mean_before=v['before_command_s']['mean'],mean_middle=v['middle_s']['mean'],mean_finish=v['finalization_s']['mean'],mean_hydrate=v['hydrate_s']['mean']) for k,v in summary.items()},indent=2))

if __name__=='__main__':
 import sys
 analyze(sys.argv[1])
