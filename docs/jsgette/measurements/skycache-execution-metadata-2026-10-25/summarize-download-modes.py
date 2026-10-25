from pathlib import Path
import json,statistics,sys
R=Path(sys.argv[1])
p=json.loads((R/'results.json').read_text())
assert p['status']=='passed' and p['workspace_unchanged']
def stats(x):return {'median':statistics.median(x),'min':min(x),'max':max(x),'n':len(x),'values':x}
s={'status':'passed','root':str(R),'groups':{}}
for mode in ['minimal','toplevel']:
 for variant in sorted({r['variant'] for r in p['runs']}):
  rows=[r for r in p['runs'] if not r['preparation'] and r['mode']==mode and r['variant']==variant]
  assert len(rows)==3
  for r in rows:
   assert r['exit_code']==0 and not r['guard_errors'] and not r['remote_warnings']
   assert not r['publication_failures'] and not r['cache_disabled']
   assert r['graph_nodes_after_clean']==0 and not r['execroot_entries_after_clean'] and not r['action_cache_exists_after_clean']
   assert r['publications_preserved'] and r['server_pid_before_build']==r['server_pid_after_build']
   assert r['target_bep_files'][0]['digest']==p['expected_output_sha256']
   assert r['output_inventory']['a_files']==r['output_inventory']['x_files']==0
   if mode=='minimal':assert not r['output_inventory']['target_files']
   else:assert r['output_inventory']['target_files'][0]['sha256']==p['expected_output_sha256']
  g={key:stats(values) for key,values in {
   'wall_seconds':[r['seconds'] for r in rows],
   'clean_seconds':[r['clean']['seconds'] for r in rows],
   'clean_plus_build_seconds':[r['clean']['seconds']+r['seconds'] for r in rows],
   'peak_pss_mib':[r['peak_pss_kib']/1024 for r in rows],
   'peak_rss_mib':[r['peak_rss_kib']/1024 for r in rows],
   'analysis_seconds':[int(r['build_metrics']['timingMetrics']['analysisPhaseTimeInMs'])/1000 for r in rows],
   'get_action_result':[r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0) for r in rows],
   'cas_read_calls':[r['grpc']['bytestream_read_calls'] for r in rows],
   'cas_read_bytes':[r['grpc']['bytestream_read_bytes'] for r in rows],
   'analysis_hits':[r['analysis_hits'] for r in rows],
   'execution_hits':[r.get('execution_hits',0) for r in rows],
  }.items()}
  s['groups'][mode+'-'+variant]=g
(R/'summary.json').write_text(json.dumps(s,indent=2)+'\n')
print(json.dumps({k:{m:v[m]['median'] for m in ['wall_seconds','peak_pss_mib','get_action_result','cas_read_bytes']} for k,v in s['groups'].items()},indent=2))
