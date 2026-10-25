"""Paired Datadog builds after synchronous bazel clean, one binary, cache off/on."""
from pathlib import Path
from datetime import datetime,timezone
import fcntl,hashlib,json,os,re,shutil,signal,socket,subprocess,sys,threading,time
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
ROOT=Path('/home/jgett/REPOS/.work/datadog-clean-2026-10-25')
OLD=Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25')
WS=Path('/home/jgett/REPOS/datadog-agent')
SOURCE=Path('/home/jgett/REPOS/bazel-skycache-v2')
OUT=OLD/'out-skycache';CACHE=OLD/'analysis-cache'
JDK=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25')
BINARY=OLD/'bin/skycache';TARGET='//cmd/cluster-agent:cluster-agent'
os.environ['SKYCACHE_WINDOWS_STORAGE_SNAPSHOT']=str(TASK/'work/datadog-storage-live.json')
sys.path.insert(0,str(SOURCE/'tools/skycache'))
from datadog_probe import process_memory
from storage_preflight import check_storage
def stamp():return datetime.now(timezone.utc).isoformat()
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,obj):
 tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(obj,indent=2)+'\n');tmp.replace(p)
def git(*args):return subprocess.check_output(['git','-C',str(WS),*args],text=True).strip()
def pid():
 try:return int((OUT/'server/server.pid.txt').read_text())
 except (OSError,ValueError):return 0
def memory():
 rss,pss,count=process_memory([pid()]);return {'pid':pid(),'rss_kib':rss,'pss_kib':pss,'process_count':count}
def storage():return check_storage([ROOT],minimum_bytes=30*1024**3)
def env():
 e=dict(os.environ,BAZEL_REAL=str(BINARY),BAZELISK_SKIP_WRAPPER='true',DD_BAZEL_REMOTE_CACHE='off',TMPDIR=str(OLD/'tmp'))
 for k in ['PWD','OLDPWD','XDG_CACHE_HOME']:e.pop(k,None)
 return e
def startup():
 return [str(WS/'tools/bazel'),'--output_base='+str(OUT),'--output_user_root=/home/jgett/.cache/bazel/_bazel_jgett',
  '--server_javabase='+str(JDK),'--host_jvm_args=-Xmx6g','--max_idle_secs=600']
def flag(variant):return '--experimental_local_skycache='+(str(CACHE) if variant=='on' else '')
def publications():
 return {str(p.relative_to(CACHE)):sha(p) for p in sorted((CACHE/'leased-v1/publications-v3').rglob('*')) if p.is_file()}
def command(v,label):
 return startup()+['build',TARGET,'--jobs=16','--local_resources=memory=6144','--lockfile_mode=error',
  '--experimental_strict_repo_env','--color=no','--curses=no','--disk_cache=',
  '--remote_cache=grpc://10.55.0.1:8080','--remote_instance_name=','--remote_timeout=600','--remote_retries=1',
  '--noremote_local_fallback','--remote_upload_local_results','--remote_accept_cached','--remote_download_outputs=all',
  '--bes_backend=','--repo_env=JAVA_HOME='+str(JDK),'--symlink_prefix='+str(OLD/'links/bazel-skycache-'),
  '--build_event_json_file='+str(ROOT/(label+'.bep.json')),'--profile='+str(ROOT/(label+'.profile.gz')),
  flag(v),'--experimental_active_directories=cmd/cluster-agent','--experimental_local_skycache_gc_max_size=0',
  '--experimental_local_skycache_gc_max_age=0','--experimental_analysis_cache_idle_retention=0']
def simple(argv,label,timeout=180):
 t=time.monotonic()
 with (ROOT/(label+'.log')).open('x') as f:r=subprocess.run(argv,cwd=WS,env=env(),stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
 row={'argv':argv,'seconds':time.monotonic()-t,'exit_code':r.returncode}
 assert r.returncode==0,label
 return row
def shutdown(label):
 if pid():return simple(startup()+['shutdown'],label,90)

def main():
 lock=(ROOT.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert OUT.resolve()==Path('/home/jgett/REPOS/.work/datadog-ssd-fresh-2026-10-25/out-skycache')
 assert not CACHE.resolve().is_relative_to(OUT.resolve())
 ROOT.mkdir(exist_ok=False);shutil.copy2(__file__,ROOT/'harness.py')
 old=json.loads((OLD/'results.json').read_text());assert old['status']=='passed'
 assert sha(BINARY)==old['binary_sha256']['skycache']
 assert git('rev-parse','HEAD')==old['datadog_revision'] and sha(WS/'MODULE.bazel.lock')==old['lock_sha256']
 assert set(git('diff','--name-only','HEAD').splitlines())<= {'MODULE.bazel.lock'}
 source_diff=git('diff','--binary','HEAD')
 (ROOT/'workspace.patch').write_text(source_diff+'\n')
 with socket.create_connection(('10.55.0.1',8080),5):pass
 initial_publications=publications();assert initial_publications
 p={'status':'running','started_at':stamp(),'target':TARGET,'source_revision':old['datadog_revision'],
  'skycache_revision':old['skycache_commit'],'binary_sha256':sha(BINARY),'module_lock_sha256':sha(WS/'MODULE.bazel.lock'),
  'workspace':str(WS),'output_base':str(OUT),'analysis_cache':str(CACHE),'remote_cache':old['remote_cache'],
  'expected_output_sha256':old['expected_output_sha256'],'initial_publications':initial_publications,
  'initial_storage':storage(),'runs':[],
  'method':'Three alternating pairs, off/on, on/off, off/on. Same candidate binary and output base. Shut down before each synchronous clean for equal JVM history; clean starts the server, build uses that same PID. Verify graph count zero, empty execroot, deleted action_cache, and preserved external SkyCache publications. Time clean separately. No --expunge, --async, or --nouse_action_cache. Cache & Carry enabled for both.',
  'limits':old['limits'],'limitations':['Warm OS page cache and existing external repository downloads.','SkyCache store was populated by the preceding experiment.','PSS/RSS are sampled process-tree memory, not host memory or filesystem cache.','Small sample; network transfer variability is uncontrolled.']}
 save(ROOT/'results.json',p)
 try:
  for round_no,order in enumerate([['off','on'],['on','off'],['off','on']],1):
   for v in order:
    label=f'r{round_no}-{v}';storage()
    row={'label':label,'round':round_no,'variant':v,'started_at':stamp()};p['runs'].append(row);save(ROOT/'results.json',p)
    row['shutdown_before']=shutdown(label+'-shutdown-before')
    before=publications()
    row['clean']=simple(startup()+['clean','--noasync','--noexpunge','--color=no','--curses=no',flag(v),'--symlink_prefix='+str(OLD/'links/bazel-skycache-')],label+'-clean')
    after=publications();assert before==after,'clean altered persistent SkyCache publications'
    row['persistent_publications_preserved']=True
    execroot=OUT/'execroot'
    row['execroot_entries_after_clean']=sorted(q.name for q in execroot.iterdir()) if execroot.exists() else []
    row['action_cache_exists_after_clean']=(OUT/'action_cache').exists()
    assert not row['execroot_entries_after_clean'] and not row['action_cache_exists_after_clean'],label+' clean did not clear outputs/cache'
    row['graph_dump']=simple(startup()+['dump','--skyframe=summary'],label+'-graph-after-clean')
    graph=(ROOT/(label+'-graph-after-clean.log')).read_text();m=re.search(r'Node count: (\d+)',graph)
    assert m and int(m[1])==0,graph
    row['graph_nodes_after_clean']=int(m[1]);row['after_clean_memory']=memory()
    server_pid=pid();assert server_pid>0
    row['argv']=command(v,label);row['storage_before']=storage();save(ROOT/'results.json',p)
    samples=[];guards=[];errors=[];stop=threading.Event()
    prior={n:(OUT/n).stat().st_mtime_ns if (OUT/n).exists() else 0 for n in ['local-skycache.json','local-skycache-validation.json']}
    with (ROOT/(label+'.log')).open('x') as log:
     t=time.monotonic();proc=subprocess.Popen(row['argv'],cwd=WS,env=env(),stdout=log,stderr=subprocess.STDOUT)
     def sample():
      while not stop.is_set():samples.append([time.monotonic()-t,*process_memory([proc.pid,pid()])]);stop.wait(.1)
     def guard():
      while not stop.wait(10):
       try:guards.append({'elapsed':time.monotonic()-t,'storage':storage()})
       except Exception as e:errors.append(str(e));return
     a=threading.Thread(target=sample);b=threading.Thread(target=guard);a.start();b.start();interrupted=None
     try:
      while True:
       try:code=proc.wait(timeout=1);break
       except subprocess.TimeoutExpired:
        elapsed=time.monotonic()-t
        if elapsed>1800 and not errors:errors.append('1800-second watchdog')
        if errors and interrupted is None:proc.send_signal(signal.SIGINT);interrupted=time.monotonic()
        if interrupted and time.monotonic()-interrupted>30:proc.kill()
      seconds=time.monotonic()-t
     finally:stop.set();a.join();b.join()
    text=(ROOT/(label+'.log')).read_text(errors='replace')
    row.update(exit_code=code,seconds=seconds,clean_plus_build_seconds=row['clean']['seconds']+seconds,
     peak_tree_pss_kib=max(x[2] for x in samples),peak_tree_rss_kib=max(x[1] for x in samples),
     sample_count=len(samples),guard_errors=errors,server_pid_before_build=server_pid,server_pid_after_build=pid(),after_build_memory=memory(),
     analysis_hits=sum(int(a) for a,b in re.findall(r'CONFIGURED_TARGET: (\d+)/(\d+)',text)),
     remote_warnings=re.findall(r'^WARNING: Remote Cache: (.*)',text,re.M),
     publication_failures=re.findall(r'Local SkyCache publication (?:discarded|skipped): (.*)',text),
     cache_disabled='Local SkyCache disabled:' in text)
    save(ROOT/(label+'.samples.json'),samples);save(ROOT/(label+'.storage.json'),guards)
    for n in prior:
     q=OUT/n
     if q.exists() and q.stat().st_mtime_ns!=prior[n]:row[n]=json.loads(q.read_text())
    for line in (ROOT/(label+'.bep.json')).read_text().splitlines():
     e=json.loads(line)
     if 'buildMetrics' in e:row['build_metrics']=e['buildMetrics']
    metrics=row.get('build_metrics',{});row['runner_counts']=metrics.get('actionSummary',{}).get('runnerCount',[])
    row['action_cache_statistics']=metrics.get('actionSummary',{}).get('actionCacheStatistics',{})
    files=list((OUT/'execroot/_main/bazel-out').glob('*/bin/cmd/cluster-agent/cluster-agent_/cluster-agent'))
    if code==0 and len(files)==1:row['output_sha256']=sha(files[0])
    save(ROOT/'results.json',p)
    print(json.dumps({k:row[k] for k in ['label','exit_code','seconds','peak_tree_pss_kib','analysis_hits','runner_counts']}),flush=True)
    assert code==0 and not errors and not row['remote_warnings'],label
    assert row['output_sha256']==p['expected_output_sha256'] and pid()==server_pid,label
    assert sum(x['count'] for x in row['runner_counts'] if x['name']=='remote cache hit')>0,label
    assert row['action_cache_statistics'].get('hits',0)==0,label
    if v=='on':assert row['analysis_hits']>0 and not row['publication_failures'] and not row['cache_disabled'] and not row.get('local-skycache.json',{}).get('failed'),label
    else:assert row['analysis_hits']==0,label
  p['status']='passed'
 except BaseException as e:p['status']='failed';p['failure']=repr(e);raise
 finally:
  try:p['final_shutdown']=shutdown('final-shutdown')
  except Exception as e:p['shutdown_error']=repr(e)
  p['workspace_unchanged']=git('diff','--binary','HEAD')==source_diff
  p['final_publications']=publications();p['finished_at']=stamp();save(ROOT/'results.json',p);lock.close()
if __name__=='__main__':main()
