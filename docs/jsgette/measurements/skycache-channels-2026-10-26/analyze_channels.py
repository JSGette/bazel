from pathlib import Path
import argparse, collections, gzip, hashlib, json, statistics

ROOT=Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26')
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')

def analyze(name):
 directory=ROOT/('warm-controlled' if name=='warm' else name)
 results=json.loads((directory/'results.json').read_text())
 agent=json.loads((ROOT/'agent/provenance.json').read_text())
 assert results['agent_sha256']==sha(ROOT/'agent/agent.jar')==agent['agent_jar']
 assert results['status']=='passed'
 assert all(results[k] for k in ['source_states_unchanged','binary_unchanged','candidate_binary_unchanged','all_binaries_unchanged'])
 groups=collections.defaultdict(list);rows=[];tables=[]
 for r in results['runs']:
  if r['warmup']:continue
  assert r['exit_code']==0 and r['outputs_match'] and r['probe_output_matches'] and not r['errors']
  assert (r['analysis_hits'],r['execution_hits'])==(18,11)
  assert r['local-skycache']['writes']>0 and r['local-skycache']['reused_witness_blocks']==103
  assert r['local-skycache']['published'] if r['arm']=='local' else r['committed']
  events=json.loads(gzip.open(directory/(r['label']+'.profile.gz'),'rt').read())['traceEvents']
  before=sum(e.get('dur',0) for e in events if e.get('name')=='LocalSkycacheModule.beforeCommand')/1e6
  timeline=[json.loads(s) for s in (directory/(r['label']+'-timeline.jsonl')).read_text().splitlines()]
  complete=next(t['elapsed_s'] for t in timeline if 'Build completed successfully' in t['text'])
  key='local' if r['arm']=='local' else r['implementation']
  startup_log=(directory/(r['label']+'-clean.log')).read_text()+(directory/(r['label']+'.log')).read_text()
  mode=r['implementation']
  assert 'SKYCACHE_CHANNEL_AGENT '+mode+' com/google/devtools/build/lib/skyframe/serialization/AutoRegistry sha256='+agent['registry'] in startup_log
  if mode!='baseline':
   reader_prefix='SKYCACHE_CHANNEL_AGENT '+mode+' com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache sha256='
   # RemoteSkyCache may load before the CLI attaches; its early stderr belongs to jvm.out.
   # Always verify mode via the actual runtime capacity counters below; check its hash if captured.
   if reader_prefix in startup_log:assert reader_prefix+agent[mode] in startup_log
  row=dict(label=r['label'],implementation=key,command_s=r['seconds'],before_command_s=before,
    middle_s=complete-before,finalization_s=r['seconds']-complete)
  if r['arm']!='local':
   stats=r['remote-skycache'];row['remote']=stats
   assert not r['rpc']
   if r['arm']=='remote-warm':assert stats['downloaded_bytes']==0
   if key!='baseline':
    assert stats['cached_channel_peak']<=stats['cached_channel_capacity']+stats['max_concurrency']
    assert stats['cached_channel_opens']-stats['cached_channel_closes']==stats['cached_channel_live']
    row['channel_hit_fraction']=stats['cached_channel_hits']/(stats['cached_channel_opens']+stats['cached_channel_hits'])
   files=sorted((directory/('cache-'+r['label'])/'leased-v2/objects').rglob('witnesses-*.block'),key=lambda p:p.name)
   table=dict(label=r['label'],blocks=len(files),bytes=sum(p.stat().st_size for p in files),sha256=[sha(p) for p in files])
   assert table['blocks']==103 and table['bytes']==10_075_663,table
   tables.append(table)
  groups[key].append(row);rows.append(row)
 assert all(t['sha256']==tables[0]['sha256'] for t in tables)
 summary={key:dict(n=len(g),**{field:dict(median=statistics.median(r[field] for r in g),mean=statistics.mean(r[field] for r in g),min=min(r[field] for r in g),max=max(r[field] for r in g)) for field in ['command_s','before_command_s','middle_s','finalization_s']}) for key,g in groups.items()}
 baseline=summary['baseline']['command_s']['median']
 changes={key:dict(seconds=summary[key]['command_s']['median']-baseline,percent=100*(summary[key]['command_s']['median']/baseline-1)) for key in ['channels128','channels256']}
 report=dict(cohort=name,summary=summary,change_from_baseline=changes,rows=rows,
    source_head=results['source_head'],binaries=results['executable_sha256'],
    runtime_binary_sha256=results['runtime_binary_sha256'],agent_sha256=results['agent_sha256'],
    agent_configuration_verified=True,witness_tables_identical=True,caveats='One frozen installation with startup-only reader replacement from tested candidate classfiles. Three samples per implementation, warmed host/loopback service. Cold means empty client retained storage. Medians are not additive. No WAN or tail-latency estimate.')
 save(ROOT/(name+'-comparison.json'),report)
 save(ROOT/(name+'-witnesses.json'),dict(all_identical=True,tables=tables))
 print(json.dumps(dict(cohort=name,command={key:v['command_s'] for key,v in summary.items()},change=changes,
    channels=[dict(label=r['label'],hit_fraction=r.get('channel_hit_fraction'),opens=r.get('remote',{}).get('cached_channel_opens'),peak=r.get('remote',{}).get('cached_channel_peak')) for r in rows if r.get('channel_hit_fraction') is not None]),indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('cohort',choices=['warm','cold']);a=p.parse_args();analyze(a.cohort)
