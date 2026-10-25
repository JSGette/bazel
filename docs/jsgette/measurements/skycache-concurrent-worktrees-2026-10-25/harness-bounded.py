"""Two real Datadog worktrees, one SkyCache, clean concurrent reads/writes."""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import collections, fcntl, hashlib, importlib.util, json, os, re, shutil, signal, socket, subprocess, sys, threading, time

TASK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
ROOT = Path('/home/jgett/REPOS/.work/datadog-concurrent-2026-10-25')
SOURCE = Path('/home/jgett/REPOS/bazel-skycache-v2')
REPO = Path('/home/jgett/REPOS/datadog-agent')
OLD = ROOT.parent / 'datadog-ssd-fresh-2026-10-25'
BINARY = ROOT.parent / 'skycache-execution-2026-10-25/bin/skycache-bwob'
JDK = Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25')
CACHE = ROOT / 'shared-skycache'
TARGET = '//cmd/cluster-agent:cluster-agent'
REV = '0cde96beb70963d0838119c308b9d0422ee66be0'
EDIT = {
 'a': ('cmd/cluster-agent/subcommands/version/command.go', 'version.MakeCommand("Cluster Agent")', 'version.MakeCommand("Cluster Agent Skycache-A")'),
 'b': ('pkg/cli/subcommands/version/command.go', '- Serialization version: %s', '- Serialization version Skycache-B: %s'),
}
os.environ['SKYCACHE_WINDOWS_STORAGE_SNAPSHOT'] = str(TASK/'work/datadog-storage-live.json')
sys.path.insert(0, str(SOURCE/'tools/skycache'))
from datadog_probe import process_memory
from datadog_worktree_probe import relocate_copied_symlinks
from storage_preflight import check_storage
spec = importlib.util.spec_from_file_location('modes', TASK/'work/datadog-download-modes.py')
modes = importlib.util.module_from_spec(spec); spec.loader.exec_module(modes)
sha = modes.h.sha
save = modes.h.save
stamp = modes.h.stamp
P = {}

def grpc_stats(path):
 data=path.read_bytes();pos=0;methods=collections.Counter();statuses=collections.Counter();by_method=collections.Counter();logical={};terminal=[];read_bytes=0;reads=0;unidentified=0
 while pos<len(data):
  size,pos=modes.varint(data,pos);record=data[pos:pos+size];assert len(record)==size;pos+=size;f=modes.fields(record)
  rpc=f[7][0].decode() if 7 in f else None
  if 9 in f:
   assert 3 not in f;summary=modes.fields(f[9][0]);terminal.append({'rpc_id':rpc,'retries':summary.get(1,[0])[0],'exhausted':bool(summary.get(2,[0])[0])});continue
  name=f[3][0].decode();methods[name]+=1
  if rpc is None:unidentified+=1
  else:logical[rpc]=name
  status=modes.fields(f.get(2,[b''])[0]).get(1,[0])[0];statuses[str(status)]+=1;by_method[(name,str(status))]+=1
  details=modes.fields(f.get(4,[b''])[0])
  if 5 in details:
   d=modes.fields(details[5][0]);read_bytes+=d.get(3,[0])[0];reads+=1
 assert all(x['rpc_id'] in logical and x['exhausted'] for x in terminal)
 return {'method_attempt_counts':dict(methods),'status_counts':dict(statuses),'status_by_method':[{'method':k[0],'status':k[1],'count':v} for k,v in sorted(by_method.items())],
 'logical_rpc_count':len(logical),'rpc_entries_without_id':unidentified,'terminal_retry_summaries':len(terminal),'terminal_by_method':dict(collections.Counter(logical[x['rpc_id']] for x in terminal)),
 'bytestream_read_calls':reads,'bytestream_read_bytes':read_bytes,'log_bytes':len(data)}

def git(ws, *args):
 return subprocess.check_output(['git','-C',str(ws),*args],text=True).strip()

def env():
 e = dict(os.environ, BAZEL_REAL=str(BINARY), BAZELISK_SKIP_WRAPPER='true', DD_BAZEL_REMOTE_CACHE='off', TMPDIR=str(OLD/'tmp'))
 for k in ['PWD','OLDPWD','XDG_CACHE_HOME']: e.pop(k,None)
 return e

def ws(n): return ROOT/n
def out(n): return ROOT/('out-'+n)
def pid(n):
 try: return int((out(n)/'server/server.pid.txt').read_text())
 except (OSError,ValueError): return 0
def storage(): return check_storage([ROOT], minimum_bytes=30*1024**3)
def startup(n):
 return [str(ws(n)/'tools/bazel'), '--output_base='+str(out(n)), '--output_user_root=/home/jgett/.cache/bazel/_bazel_jgett',
 '--server_javabase='+str(JDK), '--host_jvm_args=-Xmx4g', '--max_idle_secs=600']
def flag(enabled): return '--experimental_local_skycache='+(str(CACHE) if enabled else '')
def pubs():
 return {str(p.relative_to(CACHE)):sha(p) for p in sorted((CACHE/'leased-v1/publications-v3').rglob('*.manifest'))}
def persist(): save(ROOT/'results.json',P)
def simple(n, args, label, timeout=180):
 command=startup(n)+args
 with (ROOT/(label+'.log')).open('x') as f:
  start=time.monotonic(); r=subprocess.run(command,cwd=ws(n),env=env(),stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 row={'argv':command,'seconds':time.monotonic()-start,'exit_code':r.returncode}
 assert r.returncode==0,label
 return row
def shutdown(n,label):
 if pid(n): return simple(n,['shutdown'],label,90)

def prepare(n,label,enabled):
 row={'worktree':n,'shutdown':shutdown(n,label+'-shutdown')}
 before=pubs()
 row['clean']=simple(n,['clean','--noasync','--noexpunge','--color=no','--curses=no',flag(enabled),'--symlink_prefix='+str(ROOT/'links'/n)+'-'],label+'-clean')
 assert before==pubs(),'clean changed shared publications'
 row['publications_preserved']=True
 row['execroot_after_clean']=sorted(q.name for q in (out(n)/'execroot').iterdir()) if (out(n)/'execroot').exists() else []
 row['action_cache_after_clean']=(out(n)/'action_cache').exists()
 assert not row['execroot_after_clean'] and not row['action_cache_after_clean']
 row['dump']=simple(n,['dump','--skyframe=summary'],label+'-graph')
 graph=(ROOT/(label+'-graph.log')).read_text()
 assert 'Node count: 0' in graph and 'Edge count: 0' in graph
 row['graph_nodes']=row['graph_edges']=0
 row['server_pid']=pid(n)
 return row

def command(n,label,enabled,mode):
 return startup(n)+['build',TARGET,'--jobs=8','--local_resources=memory=3072','--lockfile_mode=error',
 '--experimental_strict_repo_env','--color=no','--curses=no','--disk_cache=',
 '--remote_cache=grpc://10.55.0.1:8080','--remote_instance_name=','--remote_timeout=600','--remote_retries=1',
 '--noremote_local_fallback','--remote_upload_local_results' if enabled else '--noremote_upload_local_results',
 '--remote_accept_cached','--remote_download_outputs='+mode,'--bes_backend=grpc://10.55.0.1:8080',
 '--bes_results_url=http://192.168.2.71:8088/invocation/', '--repo_env=JAVA_HOME='+str(JDK),
 '--symlink_prefix='+str(ROOT/'links'/n)+'-', '--build_event_json_file='+str(ROOT/(label+'.bep.json')),
 '--profile='+str(ROOT/(label+'.profile.gz')), '--remote_grpc_log='+str(ROOT/(label+'.grpc.bin')),
 flag(enabled),'--experimental_active_directories=cmd/cluster-agent','--experimental_local_skycache_gc_max_size=0',
 '--experimental_local_skycache_gc_max_age=0','--experimental_analysis_cache_idle_retention=0']

def inventory(n):
 import stat
 result={'files':0,'bytes':0,'archives':0,'exports':0,'target_files':[]}
 for directory,dirs,files in os.walk(out(n)/'execroot/_main/bazel-out',followlinks=False):
  for name in files:
   q=Path(directory)/name;s=q.lstat()
   if not stat.S_ISREG(s.st_mode):continue
   result['files']+=1;result['bytes']+=s.st_size;result['archives']+=name.endswith('.a');result['exports']+=name.endswith('.x')
   if str(q).endswith('/cmd/cluster-agent/cluster-agent_/cluster-agent'):
    v=subprocess.run([str(q),'version'],cwd=ws(n),env=env(),capture_output=True,text=True,timeout=30)
    result['target_files'].append({'path':str(q),'sha256':sha(q),'bytes':s.st_size,'version_exit_code':v.returncode,'version_stdout':v.stdout,'version_stderr':v.stderr})
 return result

def run(n, label, enabled, mode, state, barrier):
 row={'label':label,'worktree':n,'state':state,'skycache':enabled,'mode':mode,'argv':command(n,label,enabled,mode)}
 before={k:(out(n)/k).stat().st_mtime_ns if (out(n)/k).exists() else 0 for k in ['local-skycache.json','local-skycache-validation.json']}
 row['server_pid_before']=pid(n)
 with (ROOT/(label+'.log')).open('x') as log:
  barrier.wait(); start=time.monotonic();row['start_monotonic']=start;row['started_at']=stamp()
  proc=subprocess.Popen(row['argv'],cwd=ws(n),env=env(),stdout=log,stderr=subprocess.STDOUT)
  try:code=proc.wait(timeout=900)
  except subprocess.TimeoutExpired:
   proc.send_signal(signal.SIGINT)
   try:code=proc.wait(timeout=30)
   except subprocess.TimeoutExpired:proc.kill();code=proc.wait()
   row['timeout']=True
  row['end_monotonic']=time.monotonic();row['seconds']=row['end_monotonic']-start
 row['exit_code']=code;row['server_pid_after']=pid(n)
 text=(ROOT/(label+'.log')).read_text(errors='replace')
 row['analysis_hits']=sum(int(a) for a,b in re.findall(r'CONFIGURED_TARGET: (\d+)/(\d+)',text))
 row['execution_hits']=sum(int(a) for a,b in re.findall(r'ACTION_EXECUTION: (\d+)/(\d+)',text))
 row['warnings']=re.findall(r'^WARNING: (.*)',text,re.M)
 row['publication_failures']=re.findall(r'Local SkyCache publication (?:discarded|skipped): (.*)',text)
 row['cache_disabled']='Local SkyCache disabled:' in text
 for k in before:
  q=out(n)/k
  if q.exists() and q.stat().st_mtime_ns!=before[k]:row[k]=json.loads(q.read_text())
 bep=ROOT/(label+'.bep.json')
 if bep.exists():
  entries=[json.loads(line) for line in bep.read_text().splitlines()]
  row['build_metrics']=next((e['buildMetrics'] for e in entries if 'buildMetrics' in e),{})
  row['runner_counts']=row['build_metrics'].get('actionSummary',{}).get('runnerCount',[])
  row['target_bep_files']=[f for e in entries for f in e.get('namedSetOfFiles',{}).get('files',[]) if f.get('name','').endswith('/cluster-agent')]
 row['output_inventory']=inventory(n)
 save(ROOT/(label+'.result-before-grpc.json'),row)
 row['grpc']=grpc_stats(ROOT/(label+'.grpc.bin'))
 save(ROOT/(label+'.result.json'),row)
 return row

def phase(label,names,enabled,mode,states,require_hits=False,require_published=False,require_no_ar=False):
 storage();print('PHASE '+label,flush=True)
 row={'label':label,'started_at':stamp(),'publications_before':pubs(),'preparation':[],'runs':[]}
 P['phases'].append(row);persist()
 with ThreadPoolExecutor(max_workers=2) as pool:
  futures=[pool.submit(prepare,n,label+'-'+n,enabled) for n in names]
  row['preparation']=[f.result() for f in futures]
 row['storage_before']=storage();persist()
 stop=threading.Event();samples=[];events=[];guards=[];errors=[];start=time.monotonic();seen=set(row['publications_before'])
 def monitor():
  next_guard=0
  while not stop.is_set():
   now=time.monotonic()-start
   samples.append([now,*process_memory([pid(n) for n in names])])
   for q in (CACHE/'leased-v1/publications-v3').rglob('*.manifest'):
    rel=str(q.relative_to(CACHE))
    if rel not in seen:events.append({'elapsed':now,'monotonic':time.monotonic(),'path':rel});seen.add(rel)
   if now>=next_guard:
    try:guards.append({'elapsed':now,'storage':storage()})
    except Exception as e:errors.append(repr(e));stop.set()
    next_guard=now+10
   stop.wait(.2)
 thread=threading.Thread(target=monitor);thread.start()
 try:
  barrier=threading.Barrier(len(names))
  with ThreadPoolExecutor(max_workers=2) as pool:
   futures=[pool.submit(run,n,label+'-'+n,enabled,mode,states[n],barrier) for n in names]
   row['runs']=[f.result() for f in futures]
 finally:stop.set();thread.join()
 row['publications_after']=pubs();row['publication_events']=events;row['guard_errors']=errors
 row['peak_combined_pss_kib']=max(s[2] for s in samples);row['peak_combined_rss_kib']=max(s[1] for s in samples)
 row['overlap_seconds']=max(0,min(r['end_monotonic'] for r in row['runs'])-max(r['start_monotonic'] for r in row['runs'])) if len(names)>1 else None
 save(ROOT/(label+'.samples.json'),samples);save(ROOT/(label+'.storage.json'),guards);persist()
 print(json.dumps({'phase':label,'overlap':row['overlap_seconds'],'peak_pss_mib':row['peak_combined_pss_kib']/1024,'runs':[{k:r[k] for k in ['worktree','seconds','exit_code','analysis_hits','execution_hits','local-skycache.json'] if k in r} for r in row['runs']]}),flush=True)
 assert not errors
 if len(names)>1:assert row['overlap_seconds']>1
 assert all(row['publications_after'].get(k)==v for k,v in row['publications_before'].items()),'prior publication changed'
 for r in row['runs']:
  assert r['exit_code']==0 and not r.get('timeout') and r['server_pid_before']==r['server_pid_after'],r['label']
  assert not r['publication_failures'] and not r['cache_disabled'],r['label']
  assert not any('Remote Cache:' in w for w in r['warnings']),r['warnings']
  assert len(r['target_bep_files'])==1
  digest=r['target_bep_files'][0]['digest'];state=r['state']
  if not enabled and state not in P['expected']:P['expected'][state]=digest
  assert digest==P['expected'][state],(r['label'],digest,P['expected'])
  inv=r['output_inventory']
  if mode=='minimal':assert not inv['target_files']
  else:
   assert len(inv['target_files'])==1 and inv['target_files'][0]['sha256']==digest
   target=inv['target_files'][0];assert target['version_exit_code']==0
   stdout=target['version_stdout'];assert ('Skycache-A' in stdout)==(state=='a') and ('Skycache-B' in stdout)==(state=='b')
  if enabled:
   stats=r['local-skycache.json'];assert not stats['failed']
   if require_hits:assert stats['shared_reads']>0 and r['analysis_hits']>0
   if require_published:assert stats['published'] and stats['writes']>0
  if require_no_ar:
   assert r['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
   assert r['execution_hits']>0
   if mode=='minimal':assert r['grpc']['bytestream_read_calls']==0 and inv['archives']==inv['exports']==0
 row['passed']=True;persist()

def set_state(n,state):
 for key,(name,old,new) in EDIT.items():
  original=git(REPO,'show','HEAD:'+name)+'\n';assert original.count(old)==1
  (ws(n)/name).write_text(original.replace(old,new) if key==state else original)
 patch=git(ws(n),'diff','--binary','HEAD')+'\n'
 (ROOT/(n+'-state-'+state+'.patch')).write_text(patch)
 P['states'][n]=state;persist()

def main():
 global P
 lock=(ROOT.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 resume='--resume' in sys.argv
 if resume:
  saved=json.loads((ROOT/'results.json').read_text())
  assert saved['status']=='failed' and saved['failure']=="AssertionError('seed-a')"
  shutil.copy2(ROOT/'results.json',ROOT/'results-initial.json')
  shutil.copy2(ROOT/'harness.py',ROOT/'harness-initial.py')
 else:
  assert not ROOT.exists();ROOT.mkdir()
 shutil.copy2(__file__,ROOT/'harness.py')
 previous=json.loads((ROOT.parent/'datadog-execution-2026-10-25-attempt3/results.json').read_text())
 assert sha(BINARY)==previous['binary_sha256']['candidate'] and git(REPO,'rev-parse','HEAD')==REV
 original_diff=git(REPO,'diff','--binary','HEAD');assert set(git(REPO,'diff','--name-only','HEAD').splitlines())=={'MODULE.bazel.lock'}
 modes.expected=previous['expected_output_sha256']
 P={'status':'running','started_at':stamp(),'binary':str(BINARY),'binary_sha256':sha(BINARY),
 'implementation_revision':git(SOURCE,'rev-parse','HEAD'),'source_revision':REV,'lock_sha256':sha(REPO/'MODULE.bazel.lock'),
 'remote_cache':'grpc://10.55.0.1:8080','cache':str(CACHE),'target':TARGET,'states':{'a':'base','b':'base'},
 'expected':{'base':modes.expected},'phases':[],'setup':{},'initial_storage':storage(),
 'limits':{'heap_gib_each':4,'jobs_each':8,'local_action_memory_mib_each':3072},
 'memory_scope':'Combined server process trees and descendants at 200 ms; excludes launcher/probe, OS page cache, unrelated processes.',
 'method':'Seed A, cross-worktree read B, independent changed cache-off controls without uploads, concurrent divergent writers, concurrent clean readers, swap changes and cross-read minimal/toplevel. Shutdown and clean with zero graph before every build. Controls precede cache-on changed builds. Existing external downloads copied with known-root symlinks relocated; no local action/output state copied.'}
 if resume:
  P=saved;P['status']='running';P['initial_failure']=P.pop('failure')
  P['protocol_events']=[{'at':stamp(),'event':'Initial relocated-external seed build succeeded but rejected publication for inputs changing during the invocation. Preserve failure. Stabilize B with cache off, then retry A seed without source/product changes.'}]
 persist()
 try:
  with socket.create_connection(('10.55.0.1',8080),5):pass
  assert not (OLD/'out-skycache/server/server.pid.txt').exists()
  for n in ([] if resume else ['a','b']):
   with (ROOT/('setup-'+n+'.log')).open('x') as log:
    subprocess.run(['git','-C',str(REPO),'worktree','add','--detach',str(ws(n)),REV],check=True,stdout=log,stderr=subprocess.STDOUT)
    shutil.copy2(REPO/'MODULE.bazel.lock',ws(n)/'MODULE.bazel.lock')
    (out(n)/'external').mkdir(parents=True)
    subprocess.run(['rsync','-a','--stats',str(OLD/'out-skycache/external')+'/',str(out(n)/'external')+'/'],check=True,stdout=log,stderr=subprocess.STDOUT)
   changes=relocate_copied_symlinks(out(n),[(REPO,ws(n)),(OLD/'out-skycache',out(n))]);save(ROOT/('external-relocation-'+n+'.json'),changes)
   P['setup'][n]={'workspace':str(ws(n)),'output_base':str(out(n)),'git_common_dir':git(ws(n),'rev-parse','--path-format=absolute','--git-common-dir'),'relocated_symlinks':len(changes)};persist()
  assert P['setup']['a']['git_common_dir']==P['setup']['b']['git_common_dir']
  if resume:
   assert not pubs()
   for n in ['a','b']:
    assert P['states'][n]=='base' and git(ws(n),'rev-parse','HEAD')==REV
    assert set(git(ws(n),'diff','--name-only','HEAD').splitlines())=={'MODULE.bazel.lock'}
   phase('stabilize-b',['b'],False,'minimal',P['states'])
  phase('seed-retry' if resume else 'seed',['a'],True,'minimal',P['states'],require_published=True)
  phase('cross-read-base',['b'],True,'minimal',P['states'],require_hits=True,require_no_ar=True)
  for n in ['a','b']:set_state(n,n)
  phase('controls',['a','b'],False,'toplevel',P['states'])
  assert len(set(P['expected'].values()))==3
  phase('concurrent-writers',['a','b'],True,'minimal',P['states'],require_hits=True,require_published=True)
  phase('concurrent-readers',['a','b'],True,'minimal',P['states'],require_hits=True,require_no_ar=True)
  set_state('a','b');set_state('b','a')
  phase('swapped-readers-minimal',['a','b'],True,'minimal',P['states'],require_hits=True,require_no_ar=True)
  phase('swapped-readers-toplevel',['a','b'],True,'toplevel',P['states'],require_hits=True,require_no_ar=True)
  P['final_publications']=pubs();P['namespaces']=sorted({str(Path(k).parent) for k in P['final_publications']})
  assert len(P['namespaces'])==1,'both worktrees must use exactly one namespace'
  P['status']='passed'
 except BaseException as e:P['status']='failed';P['failure']=repr(e);raise
 finally:
  P['final_shutdowns']={}
  for n in ['a','b']:
   try:P['final_shutdowns'][n]=shutdown(n,('resumed-final-shutdown-' if resume else 'final-shutdown-')+n)
   except Exception as e:P['final_shutdowns'][n]={'error':repr(e)}
  P['original_workspace_unchanged']=git(REPO,'diff','--binary','HEAD')==original_diff
  P['finished_at']=stamp();persist();lock.close()

if __name__=='__main__':main()
