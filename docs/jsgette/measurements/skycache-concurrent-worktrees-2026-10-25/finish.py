"""Validate the completed paired publication, then run strict clean hit checks."""
from pathlib import Path
import fcntl,importlib.util,json,shutil
HERE=Path(__file__).parent
s=importlib.util.spec_from_file_location('h',HERE/'datadog-concurrent.py');h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
R=h.ROOT
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
p=json.loads((R/'results.json').read_text());assert p['status']=='failed' and p['failure']=='AssertionError()'
phase=p['phases'][-1];assert phase['label']=='concurrent-writers-bounded'
shutil.copy2(R/'results.json',R/'results-minimal-assertion.json');shutil.copy2(__file__,R/'finish.py')
shutil.copy2(HERE/'datadog-concurrent.py',R/'harness-final.py')
h.P=p;p['status']='running';p['minimal_assertion_failure']=p.pop('failure')
for r in phase['runs']:
 assert r['exit_code']==0 and r['server_pid_before']==r['server_pid_after']
 assert not r['publication_failures'] and not r['cache_disabled'] and not any('Remote Cache:' in w for w in r['warnings'])
 assert len(r['target_bep_files'])==1 and r['target_bep_files'][0]['digest']==p['expected'][r['state']]
 s=r['local-skycache.json'];assert s['published'] and not s['failed'] and s['writes']>0 and s['shared_reads']>0 and r['analysis_hits']>0
 for f in r['output_inventory']['target_files']:
  assert f['sha256']==p['expected'][r['state']] and f['version_exit_code']==0
  assert ('Skycache-A' in f['version_stdout'])==(r['state']=='a') and ('Skycache-B' in f['version_stdout'])==(r['state']=='b')
assert phase['overlap_seconds']>1 and not phase['guard_errors']
assert all(phase['publications_after'].get(k)==v for k,v in phase['publications_before'].items())
phase['passed']=True
p['protocol_events'].append({'at':h.stamp(),'event':'Both bounded concurrent writer builds exited zero, read shared entries, published new entries, and matched independent control digests. The harness then incorrectly required absent local final output for every minimal build. Local executed actions legitimately produce files even in minimal mode; retain the failure and restrict no-materialization assertions to the strict cache-hit phases. Revalidate completed writer outputs/markers without rerunning builds.'})
original=h.command
def bounded(n,label,enabled,mode):return original(n,label,enabled,mode)+['--remote_max_connections=1','--remote_max_concurrency_per_connection=16']
h.command=bounded;h.persist()
try:
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
  try:p['final_shutdowns'][n]=h.shutdown(n,'finish-final-shutdown-'+n)
  except Exception as e:p['final_shutdowns'][n]={'error':repr(e)}
 p['finished_at']=h.stamp();h.persist();lock.close()
