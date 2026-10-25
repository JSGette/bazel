from pathlib import Path
import json,statistics,hashlib,importlib.util,sys
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
R=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
spec=importlib.util.spec_from_file_location('inspect_layout',T/'work/inspect-large-tree.py');layout=importlib.util.module_from_spec(spec);spec.loader.exec_module(layout)
spec=importlib.util.spec_from_file_location('grpc',T/'work/datadog-concurrent.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
allgroups={};profiles=[];details=[]
for scope in ('six-final2','five-final2'):
 root=R/('datadog-'+scope);p=json.loads((root/'results.json').read_text())
 assert p['status']=='passed' and p['workspace_unchanged_during_runs'] and p['module_lock_restored']
 rows=[r for r in p['runs'] if not r['preparation']];assert len(rows)==18
 assert all(r['validation']=='passed' and r['exit_code']==0 and r['graph_nodes_after_clean']==r['graph_edges_after_clean']==0 and not r['execroot_after_clean'] and not r['action_cache_after_clean'] and r['publications_preserved'] for r in p['runs'])
 groups={}
 for mode in ('minimal','toplevel'):
  for v in ('vanilla','off','on'):
   rs=[r for r in rows if r['mode']==mode and r['variant']==v];assert len(rs)==3
   cols={'build_seconds':[r['seconds'] for r in rs],
    'analysis_seconds':[int(r['build_metrics']['timingMetrics'].get('analysisPhaseTimeInMs',0))/1000 for r in rs],
    'clean_plus_build_seconds':[r['clean_plus_build_seconds'] for r in rs],
    'peak_pss_mib':[r['peak_pss_kib']/1024 for r in rs],'peak_rss_mib':[r['peak_rss_kib']/1024 for r in rs],
    'get_action_result':[r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0) for r in rs],
    'cas_reads':[r['grpc']['bytestream_read_calls'] for r in rs],'cas_read_mib':[r['grpc']['bytestream_read_bytes']/1024**2 for r in rs]}
   groups[mode+'/'+v]={'runs':[r['label'] for r in rs],**{k:{'median':statistics.median(a),'min':min(a),'max':max(a),'values':a} for k,a in cols.items()}}
 for r in rows:
  details.append({'scope':scope,**{k:r[k] for k in ('label','seconds','analysis_hits','execution_hits','output_inventory','runner_counts','grpc')},'target_metrics':r['build_metrics']['targetMetrics'],'validation':r.get('local-skycache-validation.json'),'store':r.get('local-skycache.json')})
  if r['variant']!='on':continue
  assert r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
  assert r['output_inventory']['archives']==r['output_inventory']['exports']==0
  if r['mode']=='minimal':assert r['grpc']['bytestream_read_calls']==0
  data=(root/(r['label']+'.grpc.bin')).read_bytes();pos=0
  while pos<len(data):
   size,pos=c.modes.varint(data,pos);f=c.modes.fields(data[pos:pos+size]);pos+=size
   if not f.get(3,[b''])[0].decode().endswith('/FindMissingBlobs'):continue
   req=c.modes.fields(c.modes.fields(c.modes.fields(f[4][0])[10][0])[1][0])
   digests=[c.modes.fields(x) for x in req.get(2,[])];profile=root/(r['label']+'.profile.gz')
   matches=len(digests)==1 and digests[0][1][0].decode()==sha(profile) and digests[0][2][0]==profile.stat().st_size
   profiles.append(dict(scope=scope,run=r['label'],profile_upload=matches,digests=[dict(sha256=x[1][0].decode(),bytes=x[2][0]) for x in digests]))
 save(root/'summary.json',groups);allgroups[scope]=groups
 layout.inspect(scope)
save(R/'summary.json',allgroups);save(R/'scope-and-output-details.json',details);save(R/'bes-profile-checks.json',profiles)
print(json.dumps({scope:{k:dict(seconds=g['build_seconds']['median'],pss_mib=g['peak_pss_mib']['median'],get_action_result=g['get_action_result']['median'],cas_mib=g['cas_read_mib']['median']) for k,g in groups.items()} for scope,groups in allgroups.items()},indent=2))
