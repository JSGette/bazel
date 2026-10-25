from pathlib import Path
import importlib.util,json,subprocess,fcntl,hashlib
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec=importlib.util.spec_from_file_location('wide',T/'work/datadog-wide.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);h=w.h
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25');old=R.parent/'skycache-upstream-sync-2026-10-25'
prior=json.loads((R/'controlled/results.json').read_text());assert prior['status']=='passed'
w.ROOT=h.ROOT=R/'profiles';w.ROOT.mkdir(exist_ok=False)
bins={'baseline':old/'bin/skycache-upstream','array':R/'bin/lookup-array'}
w.NAMES=['agent','cluster-agent','process-agent','trace-agent','security-agent','system-probe'];w.TARGETS=['//cmd/'+n+':'+n for n in w.NAMES]
h.publications=lambda:{str(p.relative_to(h.CACHE)):h.sha(p) for p in sorted((h.CACHE/'leased-v2/publications-v4').rglob('*.manifest'))}
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
module=h.WS/'MODULE.bazel.lock';before=module.read_bytes();assert hashlib.sha256(before).hexdigest()=='1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
module.write_bytes((R.parent/'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after').read_bytes())
w.P=dict(status='running',runs=[],expected_binaries=prior['expected_binaries'],purpose='Verified-hit allocation/CPU JFR and post-command full-GC class histogram; diagnostic, excluded from timing matrix')
def jcmd(label,*args):
 argv=[str(h.JDK/'bin/jcmd'),str(h.pid()),*args];p=subprocess.run(argv,capture_output=True,text=True)
 (w.ROOT/(label+'.txt')).write_text(p.stdout+p.stderr)
 with (w.ROOT/'jcmd.jsonl').open('a') as f:f.write(json.dumps(dict(argv=argv,output=label+'.txt',exit_code=p.returncode))+'\n')
 p.check_returncode()
old_command=w.command
def command(label,mode,variant):
 jcmd(label+'-configure','JFR.configure','stackdepth=128');jcmd(label+'-start','JFR.start','name=skycache','settings=profile','maxsize=64m')
 return old_command(label,mode,variant)+['--override_repository=bazel_tools=/home/jgett/.cache/bazel/_bazel_jgett/install/80a15f52b9b3373384f22804a4dc15c5/embedded_tools']
w.command=command
try:
 for v,binary in bins.items():
  w.CANDIDATE=binary;h.CACHE=R/('controlled-cache-'+v)
  w.run(v,'minimal','on')
  row=w.P['runs'][-1]
  assert row['analysis_hits']==17 and row['execution_hits']==11 and row['local-skycache.json']['writes']==0
  assert row['grpc']['bytestream_read_calls']==0 and row['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
  jcmd(v+'-stop','JFR.stop','name=skycache','filename='+str(w.ROOT/(v+'.jfr')))
  jcmd(v+'-heap-before','GC.heap_info');jcmd(v+'-histogram','GC.class_histogram');jcmd(v+'-heap-after','GC.heap_info')
  row['after_histogram_memory']=h.memory();w.persist()
 w.P['status']='passed'
except BaseException as e:w.P['status']='failed';w.P['failure']=repr(e);raise
finally:
 try:w.P['shutdown']=h.shutdown('final-shutdown')
 except Exception as e:w.P['shutdown_error']=repr(e)
 module.write_bytes(before);w.P['module_lock_restored']=module.read_bytes()==before;w.persist();lock.close()
