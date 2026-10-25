from pathlib import Path
import collections,json,statistics,re
ROOT=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
p=json.loads((ROOT/'results.json').read_text())
assert p['status']=='passed', 'Only summarize a complete, successful matrix.'
runs=[r for r in p['runs'] if r['role']!='preparation']
assert len(runs)==42 and len(p['cycles'])==9
assert all(r['exit_code']==0 and not r['guard_errors'] for r in runs)
assert all(not r['remote_cache_warnings'] for r in runs)
assert len({r['output_sha256'] for r in p['runs'] if r.get('exit_code')==0})==1
assert not p.get('shutdown_errors') and p['workspace_unchanged'] and not p['non_lockfile_source_changes']
assert all(c.get('receipt',{}).get('evicted') and c['receipt']['loading_state_dropped'] and c['receipt']['heap_after_bytes']<c['receipt']['heap_before_bytes'] for c in p['cycles'] if c['variant']=='skycache')
def stats(values):return {'n':len(values),'median':statistics.median(values),'min':min(values),'max':max(values),'values':values}
def hits(row):return sum(int(x.get('count',0)) for x in row['runner_counts'] if 'remote cache hit' in x['name'].lower())
summary={'status':'passed','successful_builds':sum(r.get('exit_code')==0 for r in p['runs']),
 'measured_builds':len(runs),'output_sha256':p['expected_output_sha256'],'groups':{},'idle':{},'comparisons':{},
 'limits':p['limits'],'remote_cache':p['remote_cache'],'binaries':p['binary_sha256'],
 'datadog_revision':p['datadog_revision'],'skycache_commit':p['skycache_commit'],'vanilla_commit':p['vanilla_commit']}
for variant in ['vanilla','off','skycache']:
 for role in ['fresh_server','warm','idle_recovery','remote_rebuild']:
  rows=[r for r in runs if r['variant']==variant and r['role']==role]
  if not rows:continue
  summary['groups'][variant+'/'+role]={
    'wall_seconds':stats([r['seconds'] for r in rows]),
    'peak_tree_pss_mib':stats([r['peak_tree_pss_kib']/1024 for r in rows]),
    'peak_tree_rss_mib':stats([r['peak_tree_rss_kib']/1024 for r in rows]),
    'remote_hits':[hits(r) for r in rows],
    'analysis_hits':[r['analysis_hits'] for r in rows],
    'action_cache_statistics':[r['action_cache_statistics'] for r in rows],
    'runner_counts':[r['runner_counts'] for r in rows],
    'memory_metrics':[r.get('memory_metrics',{}) for r in rows]}
 cycles=[c for c in p['cycles'] if c['variant']==variant]
 summary['idle'][variant]={
  'retained_12s_pss_mib':stats([c['idle_12s']['tree_pss_kib']/1024 for c in cycles]),
  'retained_20s_pss_mib':stats([c['idle_20s']['tree_pss_kib']/1024 for c in cycles]),
  'change_12_to_20s_mib':stats([(c['idle_12s']['tree_pss_kib']-c['idle_20s']['tree_pss_kib'])/1024 for c in cycles]),
  'receipts':[c.get('receipt') for c in cycles]}
for role in ['fresh_server','warm','idle_recovery','remote_rebuild']:
 a=summary['groups']['vanilla/'+role]['wall_seconds']['median'];b=summary['groups']['skycache/'+role]['wall_seconds']['median']
 summary['comparisons'][role]={'skycache_vs_vanilla_wall_change_percent':(b/a-1)*100,'vanilla_over_skycache_speed_ratio':a/b}
assert all(x>0 for v in ['vanilla','skycache'] for x in summary['groups'][v+'/remote_rebuild']['remote_hits'])
summary['preparation']=[{k:r.get(k) for k in ['label','exit_code','seconds','runner_counts','guard_errors']} for r in p['runs'] if r['role']=='preparation']
summary['warm_validation_overhead']=[]
for r in runs:
 if r['variant']=='skycache' and r['role']=='warm':
  text=(ROOT/(r['label']+'.log')).read_text()
  lookup=re.search(r'Invalidation lookup took ([0-9.]+) (ms|s)\.',text)
  metrics=r.get('local-skycache.json',{})
  summary['warm_validation_overhead'].append({'label':r['label'],'wall_seconds':r['seconds'],
   'invalidation_lookup_seconds':float(lookup[1])/(1000 if lookup[2]=='ms' else 1) if lookup else None,
   'dependency_validation':r.get('local-skycache-validation.json',{}),
   'cas_get_requests':metrics.get('cas_get_requests'),'cas_received_value_bytes':metrics.get('cas_received_value_bytes')})
(ROOT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({'comparisons':summary['comparisons'],'groups':{k:{n:v[n] for n in ['wall_seconds','peak_tree_pss_mib','remote_hits','analysis_hits']} for k,v in summary['groups'].items()},'idle':summary['idle']},indent=2))
