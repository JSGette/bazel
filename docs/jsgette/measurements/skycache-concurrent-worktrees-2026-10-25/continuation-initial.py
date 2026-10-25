"""Recover completed controls after the parser failure, then continue the matrix."""
from pathlib import Path
import fcntl,importlib.util,json,re,shutil,time
HERE=Path(__file__).parent
s=importlib.util.spec_from_file_location('h',HERE/'datadog-concurrent.py');h=importlib.util.module_from_spec(s);s.loader.exec_module(h)
R=h.ROOT
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
p=json.loads((R/'results.json').read_text());assert p['status']=='failed' and p['failure']=='KeyError(3)'
assert p['states']=={'a':'a','b':'b'}
shutil.copy2(R/'results.json',R/'results-parser-failure.json');shutil.copy2(R/'harness.py',R/'harness-parser-failure.py')
shutil.copy2(HERE/'datadog-concurrent.py',R/'harness.py');shutil.copy2(__file__,R/'continuation.py')
h.P=p;p['status']='running';p['parser_failure']=p.pop('failure')
p['protocol_events'].append({'at':h.stamp(),'event':'Both controls completed successfully. Parser did not understand synthetic terminal retry-summary records (proto field 9, correlated by rpc_id). Recover control B from BEP/log/output; retain raw evidence, explicitly mark unavailable phase memory samples and timing source. Continue without rerunning completed controls. Add a remote-metadata population pass after local changed actions, which are intentionally ineligible for execution reuse until observed remote-backed.'})
phase=p['phases'][-1];assert phase['label']=='controls' and phase['runs']==[]
a=json.loads((R/'controls-a.result.json').read_text());a['grpc']=h.grpc_stats(R/'controls-a.grpc.bin')
entries=[json.loads(line) for line in (R/'controls-b.bep.json').read_text().splitlines()]
finished=next(e['finished'] for e in entries if 'finished' in e);assert finished['overallSuccess'] and finished['exitCode']['code']==0
text=(R/'controls-b.log').read_text();assert 'Build completed successfully' in text
b={'label':'controls-b','worktree':'b','state':'b','skycache':False,'mode':'toplevel','argv':h.command('b','controls-b',False,'toplevel'),
 'exit_code':0,'seconds':float(re.search(r'INFO: Elapsed time: ([0-9.]+)s',text)[1]),'timing_source':'Bazel reported elapsed time; harness monotonic interval was lost on parser exception',
 'recovered_from':'Successful BEP finished event, build log, saved output binary and gRPC log',
 'analysis_hits':0,'execution_hits':0,'warnings':re.findall(r'^WARNING: (.*)',text,re.M),'publication_failures':[],
 'build_metrics':next(e['buildMetrics'] for e in entries if 'buildMetrics' in e),
 'target_bep_files':[f for e in entries for f in e.get('namedSetOfFiles',{}).get('files',[]) if f.get('name','').endswith('/cluster-agent')],
 'output_inventory':h.inventory('b'),'grpc':h.grpc_stats(R/'controls-b.grpc.bin')}
b['runner_counts']=b['build_metrics']['actionSummary']['runnerCount']
for r in [a,b]:
 assert len(r['target_bep_files'])==1 and len(r['output_inventory']['target_files'])==1
 target=r['output_inventory']['target_files'][0];digest=r['target_bep_files'][0]['digest']
 assert target['sha256']==digest and target['version_exit_code']==0
 assert ('Skycache-A' in target['version_stdout'])==(r['state']=='a') and ('Skycache-B' in target['version_stdout'])==(r['state']=='b')
 assert any(x['name']=='linux-sandbox' and x['count']>0 for x in r['runner_counts'])
 p['expected'][r['state']]=digest
 h.save(R/(r['label']+'.result.json'),r)
assert len(set(p['expected'].values()))==3
phase.update(runs=[a,b],passed=True,publications_after=h.pubs(),overlap_seconds=None,peak_combined_pss_kib=None,
 recovery_note='Both controls passed; monitor samples and exact B monotonic timing unavailable after parser exception. No combined control memory/overlap claim.')
assert phase['publications_before']==phase['publications_after'];h.persist()
try:
 for n in ['a','b']:
  assert h.git(h.ws(n),'diff','--binary','HEAD')+'\n'==(R/(n+'-state-'+n+'.patch')).read_text()
 h.phase('concurrent-writers',['a','b'],True,'minimal',p['states'],require_hits=True,require_published=True)
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
  try:p['final_shutdowns'][n]=h.shutdown(n,'continued-final-shutdown-'+n)
  except Exception as e:p['final_shutdowns'][n]={'error':repr(e)}
 p['finished_at']=h.stamp();h.persist();lock.close()
