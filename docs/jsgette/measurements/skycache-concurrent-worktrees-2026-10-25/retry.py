"""Resume the paired writer/reader matrix after the Cache & Carry appliance outage."""
from pathlib import Path
import fcntl,importlib.util,json,shutil,subprocess
HERE=Path(__file__).parent
s=importlib.util.spec_from_file_location('h',HERE/'datadog-concurrent.py');h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
R=h.ROOT
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
p=json.loads((R/'results.json').read_text());assert p['status']=='failed' and p['failure']=="AssertionError('concurrent-writers-a')"
assert p['states']=={'a':'a','b':'b'}
shutil.copy2(R/'results.json',R/'results-appliance-outage.json');shutil.copy2(__file__,R/'retry.py')
shutil.copy2(HERE/'datadog-concurrent.py',R/'harness-with-retry-parser.py')
h.P=p;p['status']='running';p['appliance_failure']=p.pop('failure')
p['protocol_events'].append({'at':h.stamp(),'event':'Concurrent writer pair failed with remote GetCapabilities connection refused; exit 34 included failed BES upload. Other user-authorized LAN/BES task enabled BES in future harness launches and reported an appliance restart/licensing connectivity outage. Preserve its BES setting. Retry only after /healthz returns HEALTHY; same SkyCache and source variants, no product changes or cache reset.'})
health=subprocess.check_output(['curl','-fsS','--max-time','10','--http2-prior-knowledge','http://10.55.0.1:8080/healthz'],text=True)
assert health.strip()=='HEALTHY';p['retry_health']=health;h.persist()
try:
 for n in ['a','b']:
  assert h.git(h.ws(n),'diff','--binary','HEAD')+'\n'==(R/(n+'-state-'+n+'.patch')).read_text()
 h.phase('concurrent-writers-retry',['a','b'],True,'minimal',p['states'],require_hits=True,require_published=True)
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
  try:p['final_shutdowns'][n]=h.shutdown(n,'retry-final-shutdown-'+n)
  except Exception as e:p['final_shutdowns'][n]={'error':repr(e)}
 p['finished_at']=h.stamp();h.persist();lock.close()
