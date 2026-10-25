"""Finish the shared-cache gate with bounded remote transfer concurrency."""
from pathlib import Path
import fcntl,importlib.util,json,shutil,subprocess
HERE=Path(__file__).parent
s=importlib.util.spec_from_file_location('h',HERE/'datadog-concurrent.py');h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
R=h.ROOT
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
p=json.loads((R/'results.json').read_text());assert p['status']=='failed' and p['failure']=='KeyError(7)'
assert p['states']=={'a':'a','b':'b'}
shutil.copy2(R/'results.json',R/'results-transfer-failure.json');shutil.copy2(__file__,R/'bounded-continuation.py')
shutil.copy2(HERE/'datadog-concurrent.py',R/'harness-bounded.py')
h.P=p;p['status']='running';p['transfer_parser_failure']=p.pop('failure')
phase=p['phases'][-1];assert phase['label']=='concurrent-writers-retry'
phase['runs']=[]
for n in ['a','b']:
 row=json.loads((R/(phase['label']+'-'+n+'.result-before-grpc.json')).read_text());row['grpc']=h.grpc_stats(R/(phase['label']+'-'+n+'.grpc.bin'))
 phase['runs'].append(row);h.save(R/(row['label']+'.result.json'),row)
phase['publications_after']=h.pubs();phase['overlap_seconds']=max(0,min(r['end_monotonic'] for r in phase['runs'])-max(r['start_monotonic'] for r in phase['runs']))
phase['recovery_note']='Both exact per-build timing/output records recovered. Phase memory samples lost on RPC parser exception. A output matched control and published, but command exit 38/BES failure; B failed before output/publication. Neither is counted as a passing pair.'
p['protocol_events'].append({'at':h.stamp(),'event':'Second pair encountered repeated remote connection resets and failed BES; A produced correct output and published, B did not. Preserve failed pair. Parser now permits non-retrying RPCs without rpc_id (counts them explicitly). Bound only remote transfers to one connection with 16 concurrent requests per server; retain two concurrent builds, same source edits/cache/jobs/heap, and user-authorized BES. This isolates local SkyCache concurrency from unbounded remote transport pressure; it is not a default-network-concurrency pass.'})
original=h.command
def bounded(n,label,enabled,mode):
 return original(n,label,enabled,mode)+['--remote_max_connections=1','--remote_max_concurrency_per_connection=16']
h.command=bounded
p['bounded_remote_limits']={'connections_each':1,'concurrent_requests_per_connection':16}
health=subprocess.check_output(['curl','-fsS','--max-time','10','--http2-prior-knowledge','http://10.55.0.1:8080/healthz'],text=True)
assert health.strip()=='HEALTHY';h.persist()
try:
 for n in ['a','b']:assert h.git(h.ws(n),'diff','--binary','HEAD')+'\n'==(R/(n+'-state-'+n+'.patch')).read_text()
 h.phase('concurrent-writers-bounded',['a','b'],True,'minimal',p['states'],require_hits=True,require_published=True)
 h.phase('remote-metadata-fill',['a','b'],True,'minimal',p['states'],require_hits=True)
 h.phase('concurrent-readers',['a','b'],True,'minimal',p['states'],require_hits=True,require_no_ar=True)
 h.set_state('a','b');h.set_state('b','a')
 h.phase('swapped-readers-minimal',['a','b'],True,'minimal',p['states'],require_hits=True,require_no_ar=True)
 h.phase('swapped-readers-toplevel',['a','b'],True,'toplevel',p['states'],require_hits=True,require_no_ar=True)
 p['final_publications']=h.pubs();p['namespaces']=sorted({str(Path(k).parent) for k in p['final_publications']})
 assert len(p['namespaces'])==1
 p['status']='passed'
except BaseException as e:p['status']='failed';p['failure']=repr(e);raise
finally:
 p['final_shutdowns']={}
 for n in ['a','b']:
  try:p['final_shutdowns'][n]=h.shutdown(n,'bounded-final-shutdown-'+n)
  except Exception as e:p['final_shutdowns'][n]={'error':repr(e)}
 p['finished_at']=h.stamp();h.persist();lock.close()
