"""Larger Datadog build scope, verified empty graph before every run."""
from pathlib import Path
import collections, fcntl, importlib.util, json, os, re, shutil, signal, stat, subprocess, threading, time
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec=importlib.util.spec_from_file_location('concurrent_helpers',TASK/'work/datadog-concurrent.py')
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)
h=c.modes.h
ROOT=h.ROOT=Path('/home/jgett/REPOS/.work/datadog-wide-2026-10-25')
CANDIDATE=c.BINARY
VANILLA=h.OLD/'bin/vanilla'
h.CACHE=ROOT/'skycache'
NAMES=['agent','cluster-agent','process-agent','trace-agent','security-agent','system-probe']
TARGETS=['//cmd/'+n+':'+n for n in NAMES]
P={}
def persist():h.save(ROOT/'results.json',P)
def flag(v):return '--experimental_local_skycache='+str(h.CACHE) if v=='on' else '--color=no'
def command(label,mode,v):
 args=h.command('on' if v=='on' else 'off',label)
 args=[a for a in args if a!=h.TARGET and not a.startswith(('--experimental_active_directories=','--remote_download_outputs='))]
 if v=='vanilla':args=[a for a in args if not a.startswith(('--experimental_local_skycache','--experimental_analysis_cache_idle_retention'))]
 else:args+=['--experimental_active_directories=cmd']
 return args+TARGETS+['--remote_download_outputs='+mode,'--remote_grpc_log='+str(ROOT/(label+'.grpc.bin')),
 '--remote_max_connections=1','--remote_max_concurrency_per_connection=16']
def outputs():
 result={'regular_files':0,'bytes':0,'archives':0,'exports':0,'binaries':{}}
 for directory,dirs,files in os.walk(h.OUT/'execroot/_main/bazel-out',followlinks=False):
  for name in files:
   q=Path(directory)/name;s=q.lstat()
   if not stat.S_ISREG(s.st_mode):continue
   result['regular_files']+=1;result['bytes']+=s.st_size
   result['archives']+=name.endswith('.a');result['exports']+=name.endswith('.x')
   if name in NAMES and str(q).endswith('/cmd/'+name+'/'+name+'_/'+name):
    assert name not in result['binaries']
    result['binaries'][name]={'path':str(q),'bytes':s.st_size,'sha256':h.sha(q)}
 return result
def run(label,mode,v,preparation=False):
 h.BINARY=VANILLA if v=='vanilla' else CANDIDATE
 row={'label':label,'mode':mode,'variant':v,'preparation':preparation,'started_at':h.stamp(),'binary_sha256':h.sha(h.BINARY)}
 P['runs'].append(row);persist();h.storage()
 row['shutdown_before']=h.shutdown(label+'-shutdown')
 before=h.publications()
 row['clean']=h.simple(h.startup()+['clean','--noasync','--noexpunge','--color=no','--curses=no',flag(v),'--symlink_prefix='+str(h.OLD/'links/bazel-skycache-')],label+'-clean')
 assert before==h.publications();row['publications_preserved']=True
 root=h.OUT/'execroot'
 row['execroot_after_clean']=sorted(q.name for q in root.iterdir()) if root.exists() else []
 row['action_cache_after_clean']=(h.OUT/'action_cache').exists()
 assert not row['execroot_after_clean'] and not row['action_cache_after_clean']
 row['graph_dump']=h.simple(h.startup()+['dump','--skyframe=summary'],label+'-graph')
 graph=(ROOT/(label+'-graph.log')).read_text()
 assert 'Node count: 0' in graph and 'Edge count: 0' in graph
 row['graph_nodes_after_clean']=row['graph_edges_after_clean']=0
 server=h.pid();assert server>0
 row['argv']=command(label,mode,v);row['storage_before']=h.storage();persist()
 prior={n:(h.OUT/n).stat().st_mtime_ns if (h.OUT/n).exists() else 0 for n in ['local-skycache.json','local-skycache-validation.json']}
 samples=[];guards=[];errors=[];stop=threading.Event()
 with (ROOT/(label+'.log')).open('x') as log:
  start=time.monotonic();proc=subprocess.Popen(row['argv'],cwd=h.WS,env=h.env(),stdout=log,stderr=subprocess.STDOUT)
  def sample():
   while not stop.is_set():samples.append([time.monotonic()-start,*h.process_memory([proc.pid,h.pid()])]);stop.wait(.1)
  def guard():
   while not stop.wait(10):
    try:guards.append({'elapsed':time.monotonic()-start,'storage':h.storage()})
    except Exception as e:errors.append(str(e));return
  a=threading.Thread(target=sample);b=threading.Thread(target=guard);a.start();b.start();interrupted=None
  try:
   while True:
    try:code=proc.wait(timeout=1);break
    except subprocess.TimeoutExpired:
     if time.monotonic()-start>3600 and not errors:errors.append('3600-second watchdog')
     if errors and interrupted is None:proc.send_signal(signal.SIGINT);interrupted=time.monotonic()
     if interrupted and time.monotonic()-interrupted>30:proc.kill()
   seconds=time.monotonic()-start
  finally:stop.set();a.join();b.join()
 log=(ROOT/(label+'.log')).read_text(errors='replace')
 row.update(exit_code=code,seconds=seconds,clean_plus_build_seconds=row['clean']['seconds']+seconds,
  peak_pss_kib=max(x[2] for x in samples),peak_rss_kib=max(x[1] for x in samples),sample_count=len(samples),
  server_pid_before_build=server,server_pid_after_build=h.pid(),guard_errors=errors,
  analysis_hits=sum(int(a) for a,b in re.findall(r'CONFIGURED_TARGET: (\d+)/(\d+)',log)),
  execution_hits=sum(int(a) for a,b in re.findall(r'ACTION_EXECUTION: (\d+)/(\d+)',log)),
  remote_warnings=re.findall(r'^WARNING: Remote Cache: (.*)',log,re.M),
  publication_failures=re.findall(r'Local SkyCache publication (?:discarded|skipped): (.*)',log),cache_disabled='Local SkyCache disabled:' in log)
 h.save(ROOT/(label+'.samples.json'),samples);h.save(ROOT/(label+'.storage.json'),guards)
 for n in prior:
  q=h.OUT/n
  if q.exists() and q.stat().st_mtime_ns!=prior[n]:row[n]=json.loads(q.read_text())
 entries=[json.loads(s) for s in (ROOT/(label+'.bep.json')).read_text().splitlines()]
 row['build_metrics']=next((e['buildMetrics'] for e in entries if 'buildMetrics' in e),{})
 row['runner_counts']=row['build_metrics'].get('actionSummary',{}).get('runnerCount',[])
 row['bep_finished']=next((e['finished'] for e in entries if 'finished' in e),{})
 row['bep_binaries']={}
 for e in entries:
  for f in e.get('namedSetOfFiles',{}).get('files',[]):
   for name in NAMES:
    if f.get('name','').endswith('cmd/'+name+'/'+name+'_/'+name):row['bep_binaries'][name]=f
 row['output_inventory']=outputs()
 h.save(ROOT/(label+'.result-before-grpc.json'),row);persist()
 row['grpc']=c.grpc_stats(ROOT/(label+'.grpc.bin'));persist()
 print(json.dumps({k:row[k] for k in ['label','exit_code','seconds','peak_pss_kib','analysis_hits','execution_hits','runner_counts','grpc']}),flush=True)
 assert code==0 and not errors and not row['remote_warnings'] and server==h.pid(),label+' build'
 assert set(row['bep_binaries'])==set(NAMES),(label,row['bep_binaries'])
 digests={k:f['digest'] for k,f in row['bep_binaries'].items()}
 if 'expected_binaries' not in P:P['expected_binaries']=digests
 assert digests==P['expected_binaries'],label+' BEP digest mismatch'
 actual={k:f['sha256'] for k,f in row['output_inventory']['binaries'].items()}
 if mode=='toplevel':assert actual==P['expected_binaries'],label+' output digest mismatch'
 elif not preparation:assert not actual,label+' minimal materialized binary'
 if v=='on':
  assert not row['publication_failures'] and not row['cache_disabled'] and not row.get('local-skycache.json',{}).get('failed'),label+' publication'
 row['validation']='passed';persist()
def main():
 lock=(ROOT.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 ROOT.mkdir(exist_ok=False)
 for n in ['datadog-wide.py','datadog-concurrent.py','datadog-download-modes.py','datadog-clean-benchmark.py']:shutil.copy2(TASK/'work'/n,ROOT/n)
 source=h.git('diff','--binary','HEAD');assert set(h.git('diff','--name-only','HEAD').splitlines())=={'MODULE.bazel.lock'}
 (ROOT/'workspace.patch').write_text(source+'\n')
 P.update(status='running',started_at=h.stamp(),targets=TARGETS,workspace=str(h.WS),output_base=str(h.OUT),analysis_cache=str(h.CACHE),
  source_revision=h.git('rev-parse','HEAD'),module_lock_sha256=h.sha(h.WS/'MODULE.bazel.lock'),
  binary_sha256={'candidate':h.sha(CANDIDATE),'vanilla':h.sha(VANILLA)},
  boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),runs=[],
  method='Six Linux Agent binaries. Vanilla toplevel warmup, SkyCache minimal/toplevel preparation, then three rotated repetitions of vanilla/off/on per policy. Shutdown and synchronous clean before every build; verify zero graph and deleted outputs/action cache. Same output base, 6 GiB JVM, 16 jobs, 6144 MiB action budget, bounded remote transport 1 connection x 16 requests. Cache & Carry remote cache and BES enabled. Warm OS and external repository caches. Build time excludes clean; combined time also recorded. Process-tree PSS/RSS sampled every 100 ms.')
 persist()
 try:
  run('prepare-vanilla','toplevel','vanilla',True)
  run('prepare-minimal','minimal','on',True)
  run('prepare-toplevel','toplevel','on',True)
  for i,order in enumerate([['off','on','vanilla'],['on','vanilla','off'],['vanilla','off','on']],1):
   for mode in (['minimal','toplevel'] if i%2 else ['toplevel','minimal']):
    for v in order:run(f'r{i}-{mode}-{v}',mode,v)
  P['status']='passed'
 except BaseException as e:P['status']='failed';P['failure']=repr(e);raise
 finally:
  try:P['shutdown']=h.shutdown('final-shutdown')
  except Exception as e:P['shutdown_error']=repr(e)
  P['workspace_unchanged']=h.git('diff','--binary','HEAD')==source
  P['final_publications']=h.publications();P['finished_at']=h.stamp();persist();lock.close()
if __name__=='__main__':main()
