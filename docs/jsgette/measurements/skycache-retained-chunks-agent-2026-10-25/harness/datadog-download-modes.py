"""Verified-clean Datadog builds with minimal/toplevel remote output policies."""
from pathlib import Path
import collections,fcntl,hashlib,importlib.util,json,os,re,shutil,signal,stat,subprocess,threading,time
BASE=Path(__file__).with_name('datadog-clean-benchmark.py')
spec=importlib.util.spec_from_file_location('clean_harness',BASE)
h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)
h.ROOT=R=Path('/home/jgett/REPOS/.work/datadog-download-modes-2026-10-25')

def varint(data,pos):
 value=0
 for shift in range(0,70,7):
  byte=data[pos];pos+=1;value|=(byte&127)<<shift
  if byte<128:return value,pos
 raise ValueError('oversized varint')
def fields(data):
 pos=0;out={}
 while pos<len(data):
  tag,pos=varint(data,pos);field,wire=tag>>3,tag&7
  if wire==0:value,pos=varint(data,pos)
  elif wire==2:
   size,pos=varint(data,pos);value=data[pos:pos+size];assert len(value)==size;pos+=size
  elif wire in [1,5]:
   size=8 if wire==1 else 4;value=data[pos:pos+size];assert len(value)==size;pos+=size
  else:raise ValueError(f'unsupported protobuf wire type {wire}')
  out.setdefault(field,[]).append(value)
 return out
def grpc_stats(path):
 data=path.read_bytes();pos=0;methods=collections.Counter();statuses=collections.Counter();read_bytes=0;reads=0;logical=set();top_reads=[]
 while pos<len(data):
  size,pos=varint(data,pos);assert 0<size<64*1024*1024
  record=data[pos:pos+size];assert len(record)==size;pos+=size;f=fields(record)
  name=f[3][0].decode();methods[name]+=1
  status=fields(f.get(2,[b''])[0]);statuses[str(status.get(1,[0])[0])]+=1
  if 7 in f:logical.add(f[7][0])
  details=fields(f.get(4,[b''])[0])
  if 5 in details:
   d=fields(details[5][0]);amount=d.get(3,[0])[0];read_bytes+=amount;reads+=1
   req=fields(d.get(1,[b''])[0]);resource=req.get(1,[b''])[0].decode()
   if expected in resource:top_reads.append({'resource':resource,'bytes':amount})
 return {'method_attempt_counts':dict(methods),'status_counts':dict(statuses),'logical_rpc_count':len(logical),
  'bytestream_read_calls':reads,'bytestream_read_bytes':read_bytes,'target_blob_reads':top_reads,'log_bytes':len(data)}

def outputs():
 root=h.OUT/'execroot/_main/bazel-out';count=0;size=0;archives=0;exports=0;targets=[]
 for directory,dirs,files in os.walk(root,followlinks=False):
  for name in files:
   path=Path(directory)/name;s=path.lstat()
   if not stat.S_ISREG(s.st_mode):continue
   count+=1;size+=s.st_size;archives+=name.endswith('.a');exports+=name.endswith('.x')
   if str(path).endswith('/cmd/cluster-agent/cluster-agent_/cluster-agent'):targets.append({'path':str(path),'size':s.st_size,'sha256':h.sha(path)})
 return {'regular_files':count,'regular_file_bytes':size,'a_files':archives,'x_files':exports,'target_files':targets}

def run(label,mode,variant,preparation):
 h.storage();row={'label':label,'mode':mode,'variant':variant,'preparation':preparation,'started_at':h.stamp()}
 p['runs'].append(row);h.save(R/'results.json',p)
 row['shutdown_before']=h.shutdown(label+'-shutdown-before');before=h.publications()
 row['clean']=h.simple(h.startup()+['clean','--noasync','--noexpunge','--color=no','--curses=no',h.flag(variant),'--symlink_prefix='+str(h.OLD/'links/bazel-skycache-')],label+'-clean')
 assert before==h.publications();row['publications_preserved']=True
 row['execroot_entries_after_clean']=sorted(q.name for q in (h.OUT/'execroot').iterdir()) if (h.OUT/'execroot').exists() else []
 row['action_cache_exists_after_clean']=(h.OUT/'action_cache').exists()
 assert not row['execroot_entries_after_clean'] and not row['action_cache_exists_after_clean']
 row['graph_dump']=h.simple(h.startup()+['dump','--skyframe=summary'],label+'-graph-after-clean')
 graph=(R/(label+'-graph-after-clean.log')).read_text();assert 'Node count: 0' in graph and 'Edge count: 0' in graph
 row['graph_nodes_after_clean']=0;server=h.pid();assert server>0
 row['argv']=[('--remote_download_outputs='+mode) if a=='--remote_download_outputs=all' else a for a in h.command(variant,label)]+['--remote_grpc_log='+str(R/(label+'.grpc.bin'))]
 row['storage_before']=h.storage();h.save(R/'results.json',p)
 samples=[];guards=[];errors=[];stop=threading.Event()
 prior={n:(h.OUT/n).stat().st_mtime_ns if (h.OUT/n).exists() else 0 for n in ['local-skycache.json','local-skycache-validation.json']}
 with (R/(label+'.log')).open('x') as log:
  t=time.monotonic();proc=subprocess.Popen(row['argv'],cwd=h.WS,env=h.env(),stdout=log,stderr=subprocess.STDOUT)
  def sample():
   while not stop.is_set():samples.append([time.monotonic()-t,*h.process_memory([proc.pid,h.pid()])]);stop.wait(.1)
  def guard():
   while not stop.wait(10):
    try:guards.append({'elapsed':time.monotonic()-t,'storage':h.storage()})
    except Exception as e:errors.append(str(e));return
  a=threading.Thread(target=sample);b=threading.Thread(target=guard);a.start();b.start();interrupted=None
  try:
   while True:
    try:code=proc.wait(timeout=1);break
    except subprocess.TimeoutExpired:
     if time.monotonic()-t>600 and not errors:errors.append('600-second watchdog')
     if errors and interrupted is None:proc.send_signal(signal.SIGINT);interrupted=time.monotonic()
     if interrupted and time.monotonic()-interrupted>30:proc.kill()
   seconds=time.monotonic()-t
  finally:stop.set();a.join();b.join()
 text=(R/(label+'.log')).read_text(errors='replace')
 row.update(exit_code=code,seconds=seconds,peak_pss_kib=max(x[2] for x in samples),peak_rss_kib=max(x[1] for x in samples),
  server_pid_before_build=server,server_pid_after_build=h.pid(),guard_errors=errors,
  analysis_hits=sum(int(a) for a,b in re.findall(r'CONFIGURED_TARGET: (\d+)/(\d+)',text)),
  remote_warnings=re.findall(r'^WARNING: Remote Cache: (.*)',text,re.M),
  publication_failures=re.findall(r'Local SkyCache publication (?:discarded|skipped): (.*)',text),cache_disabled='Local SkyCache disabled:' in text)
 h.save(R/(label+'.samples.json'),samples);h.save(R/(label+'.storage.json'),guards)
 for n in prior:
  q=h.OUT/n
  if q.exists() and q.stat().st_mtime_ns!=prior[n]:row[n]=json.loads(q.read_text())
 entries=[json.loads(line) for line in (R/(label+'.bep.json')).read_text().splitlines()]
 row['build_metrics']=next(e['buildMetrics'] for e in entries if 'buildMetrics' in e)
 row['runner_counts']=row['build_metrics']['actionSummary'].get('runnerCount',[])
 row['target_bep_files']=[f for e in entries for f in e.get('namedSetOfFiles',{}).get('files',[]) if f.get('name','').endswith('/cluster-agent')]
 row['output_inventory']=outputs();row['grpc']=grpc_stats(R/(label+'.grpc.bin'))
 h.save(R/'results.json',p)
 print(json.dumps({k:row[k] for k in ['label','seconds','peak_pss_kib','analysis_hits','runner_counts','grpc','output_inventory']}),flush=True)
 assert code==0 and not errors and not row['remote_warnings'] and h.pid()==server
 assert len(row['target_bep_files'])==1 and row['target_bep_files'][0]['digest']==expected
 if mode=='minimal':assert not row['output_inventory']['target_files']
 else:assert len(row['output_inventory']['target_files'])==1 and row['output_inventory']['target_files'][0]['sha256']==expected
 if variant=='on':assert not row['publication_failures'] and not row['cache_disabled'] and not row.get('local-skycache.json',{}).get('failed')

if __name__=='__main__':
 lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 R.mkdir(exist_ok=False);shutil.copy2(__file__,R/'harness.py');shutil.copy2(BASE,R/'harness-base.py')
 previous=json.loads((R.parent/'datadog-clean-2026-10-25/results.json').read_text());expected=previous['expected_output_sha256']
 assert h.sha(h.BINARY)==previous['binary_sha256'] and h.git('rev-parse','HEAD')==previous['source_revision']
 assert h.sha(h.WS/'MODULE.bazel.lock')==previous['module_lock_sha256']
 source=h.git('diff','--binary','HEAD');assert source+'\n'==(R.parent/'datadog-clean-2026-10-25/workspace.patch').read_text()
 (R/'workspace.patch').write_text(source+'\n')
 p={'status':'running','started_at':h.stamp(),'target':h.TARGET,'binary_sha256':h.sha(h.BINARY),'skycache_revision':previous['skycache_revision'],
  'source_revision':previous['source_revision'],'module_lock_sha256':previous['module_lock_sha256'],'expected_output_sha256':expected,
  'remote_cache':'grpc://10.55.0.1:8080','initial_publications':h.publications(),'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
  'method':'Two preparation runs, then three rotated pairs per output mode. Same binary, output base, JVM limits and remote cache. Shutdown and synchronous clean before each build; verify graph zero and no local outputs/action cache. One uninterrupted WSL process for the series. All runs record gRPC logs and output inventory.','runs':[]}
 try:
  run('prepare-minimal','minimal','on',True);run('prepare-toplevel','toplevel','on',True)
  orders=[[('minimal','off'),('minimal','on'),('toplevel','on'),('toplevel','off')],
   [('toplevel','on'),('toplevel','off'),('minimal','off'),('minimal','on')],
   [('minimal','on'),('minimal','off'),('toplevel','off'),('toplevel','on')]]
  for round_no,order in enumerate(orders,1):
   for mode,variant in order:run(f'r{round_no}-{mode}-{variant}',mode,variant,False)
  p['status']='passed'
 except BaseException as e:p['status']='failed';p['failure']=repr(e);raise
 finally:
  try:p['final_shutdown']=h.shutdown('final-shutdown')
  except Exception as e:p['shutdown_error']=repr(e)
  p['workspace_unchanged']=h.git('diff','--binary','HEAD')==source;p['finished_at']=h.stamp();h.save(R/'results.json',p);lock.close()
