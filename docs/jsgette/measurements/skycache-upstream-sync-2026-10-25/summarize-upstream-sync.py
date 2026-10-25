from pathlib import Path
import json,statistics,importlib.util,hashlib
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
root=R/'datadog-six-stable';p=json.loads((root/'results.json').read_text())
assert p['status']=='passed' and p['module_lock_restored'] and p['workspace_unchanged_during_runs']
rows=[r for r in p['runs'] if not r['preparation']];assert len(rows)==18
assert all(r['validation']=='passed' and r['exit_code']==0 and r['graph_nodes_after_clean']==r['graph_edges_after_clean']==0 and not r['execroot_after_clean'] and not r['action_cache_after_clean'] and r['publications_preserved'] for r in p['runs'])
groups={}
for mode in ('minimal','toplevel'):
 for v in ('vanilla','off','on'):
  rs=[r for r in rows if r['mode']==mode and r['variant']==v];assert len(rs)==3
  cols=dict(seconds=[r['seconds'] for r in rs],pss_mib=[r['peak_pss_kib']/1024 for r in rs],rss_mib=[r['peak_rss_kib']/1024 for r in rs],analysis_seconds=[int(r['build_metrics']['timingMetrics']['analysisPhaseTimeInMs'])/1000 for r in rs],get_action_result=[r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0) for r in rs],cas_reads=[r['grpc']['bytestream_read_calls'] for r in rs])
  groups[mode+'/'+v]={k:dict(median=statistics.median(xs),min=min(xs),max=max(xs),values=xs) for k,xs in cols.items()}
for r in rows:
 if r['variant']!='on':continue
 assert r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
 assert r['output_inventory']['archives']==r['output_inventory']['exports']==0
 if r['mode']=='minimal':assert r['grpc']['bytestream_read_calls']==0
 assert r['local-skycache.json']['writes']==0 and not r['local-skycache.json']['failed']
 assert r['local-skycache.json']['read_limit_skips']==0
(R/'summary.json').write_text(json.dumps(groups,indent=2)+'\n')
spec=importlib.util.spec_from_file_location('layout',T/'work/inspect-large-tree.py');layout=importlib.util.module_from_spec(spec);spec.loader.exec_module(layout);layout.ROOT=R;layout.inspect('six')
spec=importlib.util.spec_from_file_location('grpc',T/'work/datadog-concurrent.py');c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
checks=[]
for r in rows:
 if r['variant']!='on':continue
 data=(root/(r['label']+'.grpc.bin')).read_bytes();pos=0
 while pos<len(data):
  size,pos=c.modes.varint(data,pos);f=c.modes.fields(data[pos:pos+size]);pos+=size
  if not f.get(3,[b''])[0].decode().endswith('/FindMissingBlobs'):continue
  req=c.modes.fields(c.modes.fields(c.modes.fields(f[4][0])[10][0])[1][0]);digests=[c.modes.fields(x) for x in req.get(2,[])]
  profile=root/(r['label']+'.profile.gz')
  matches=len(digests)==1 and digests[0][1][0].decode()==hashlib.sha256(profile.read_bytes()).hexdigest() and digests[0][2][0]==profile.stat().st_size
  assert matches
  checks.append(dict(run=r['label'],profile_upload=True))
(R/'bes-profile-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
print(json.dumps({k:{f:v[f]['median'] for f in ('seconds','analysis_seconds','pss_mib','get_action_result','cas_reads')} for k,v in groups.items()},indent=2))
