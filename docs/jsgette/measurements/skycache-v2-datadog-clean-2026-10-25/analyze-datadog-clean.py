from pathlib import Path
import json,statistics
R=Path('/home/jgett/REPOS/.work/datadog-clean-2026-10-25')
p=json.loads((R/'results.json').read_text())
assert p['status']=='passed' and len(p['runs'])==6 and p['workspace_unchanged'] and not p.get('shutdown_error')
for r in p['runs']:
 assert r['exit_code']==0 and not r['remote_warnings'] and not r['guard_errors']
 assert r['graph_nodes_after_clean']==0 and not r['action_cache_exists_after_clean'] and not r['execroot_entries_after_clean']
 assert r['persistent_publications_preserved'] and r['server_pid_before_build']==r['server_pid_after_build']
 assert r['output_sha256']==p['expected_output_sha256']
 assert 'Edge count: 0' in (R/(r['label']+'-graph-after-clean.log')).read_text()
def stat(xs):return {'median':statistics.median(xs),'min':min(xs),'max':max(xs),'values':xs,'n':len(xs)}
s={'status':'passed','target':p['target'],'build_count':6,'groups':{},'same_binary':p['binary_sha256'],'output_sha256':p['expected_output_sha256']}
for v in ['off','on']:
 rows=[r for r in p['runs'] if r['variant']==v];assert len(rows)==3
 g={}
 for key,values in {
  'wall_seconds':[r['seconds'] for r in rows],
  'clean_seconds':[r['clean']['seconds'] for r in rows],
  'clean_plus_build_seconds':[r['clean_plus_build_seconds'] for r in rows],
  'analysis_seconds':[float(r['build_metrics']['timingMetrics']['analysisPhaseTimeInMs'])/1000 for r in rows],
  'execution_seconds':[float(r['build_metrics']['timingMetrics']['executionPhaseTimeInMs'])/1000 for r in rows],
  'peak_pss_mib':[r['peak_tree_pss_kib']/1024 for r in rows],
  'peak_rss_mib':[r['peak_tree_rss_kib']/1024 for r in rows]
 }.items():g[key]=stat(values)
 g['analysis_hits']=[r['analysis_hits'] for r in rows]
 g['remote_hits']=[sum(x['count'] for x in r['runner_counts'] if x['name']=='remote cache hit') for r in rows]
 g['local_actions']=[sum(x['count'] for x in r['runner_counts'] if x.get('execKind')=='Local') for r in rows]
 g['action_cache_statistics']=[r['action_cache_statistics'] for r in rows]
 s['groups'][v]=g
s['on_vs_off_percent']={k:100*(s['groups']['on'][k]['median']/s['groups']['off'][k]['median']-1) for k in ['wall_seconds','analysis_seconds','peak_pss_mib','clean_plus_build_seconds']}
restored_rounds=sorted(r['round'] for r in p['runs'] if r['variant']=='on' and r['analysis_hits']>0)
s['restored_snapshot_pairs']={'rounds':restored_rounds,'groups':{},'note':'Secondary description of pairs with observed successful restoration. All original runs remain in primary results.'}
for v in ['off','on']:
 rows=[r for r in p['runs'] if r['variant']==v and r['round'] in restored_rounds]
 s['restored_snapshot_pairs']['groups'][v]={
  'wall_seconds':stat([r['seconds'] for r in rows]),
  'analysis_seconds':stat([float(r['build_metrics']['timingMetrics']['analysisPhaseTimeInMs'])/1000 for r in rows]),
  'peak_pss_mib':stat([r['peak_tree_pss_kib']/1024 for r in rows])}
(R/'summary.json').write_text(json.dumps(s,indent=2)+'\n')
print(json.dumps(s,indent=2))
