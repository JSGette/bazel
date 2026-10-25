from pathlib import Path
import subprocess,json,os,time,shutil,hashlib,sys
S=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
old=R.parent/'skycache-upstream-sync-2026-10-25'
label=sys.argv[1]
startup=['/home/jgett/.local/bin/bazel','--output_user_root=/home/jgett/.cache/bazel/_bazel_jgett','--output_base='+str(old/'build'),'--server_javabase=/home/jgett/REPOS/.tools/skycache-benchmark-jdk25','--host_jvm_args=-Xmx5g','--max_idle_secs=120']
args=startup+['test','//src:bazel-dev','//src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:all','--jobs=6','--local_test_jobs=3','--local_resources=memory=6144','--test_output=errors','--color=no','--curses=no','--disk_cache=','--remote_cache=grpc://10.55.0.1:8080','--remote_max_connections=1','--remote_max_concurrency_per_connection=16','--remote_timeout=600','--remote_retries=1','--bes_backend=grpc://10.55.0.1:8080','--bes_results_url=http://192.168.2.71:8088/invocation/','--build_event_json_file='+str(R/(label+'-build.bep.json')),'--profile='+str(R/(label+'-build.profile.gz'))]
start=time.monotonic()
if label=='lookup-array':
 args.insert(args.index('--jobs=6'),'//src/test/java/com/google/devtools/build/lib/skyframe/serialization:SerializationTests')
 args.insert(args.index('--jobs=6'),'//src/test/java/com/google/devtools/build/lib/skyframe/serialization:ChunkedValueSerializationTest')
(R/(label+'-build.command.json')).write_text(json.dumps(args,indent=2)+'\n')
with (R/(label+'-build.log')).open('x') as f:
 p=subprocess.run(args,cwd=S,env=dict(os.environ,TMPDIR=str(old/'tmp')),stdout=f,stderr=subprocess.STDOUT)
result=dict(exit_code=p.returncode,seconds=time.monotonic()-start)
if p.returncode==0:
 (R/'bin').mkdir(exist_ok=True)
 binary=R/'bin'/label
 shutil.copy2(S/'bazel-bin/src/bazel-dev',binary)
 result['binary_sha256']=hashlib.sha256(binary.read_bytes()).hexdigest()
 results=R/(label+'-tests');results.mkdir(exist_ok=True)
 for xml in (S/'bazel-testlogs/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local').glob('*/test.xml'):
  shutil.copy2(xml,results/(xml.parent.name+'.xml'))
 if label=='lookup-array':
  for target in ['SerializationTests','ChunkedValueSerializationTest']:
   xml=S/'bazel-testlogs/src/test/java/com/google/devtools/build/lib/skyframe/serialization'/target/'test.xml'
   shutil.copy2(xml,results/(target+'.xml'))
with (R/(label+'-shutdown.log')).open('w') as f:result['shutdown']=subprocess.run(startup+['shutdown'],cwd=S,stdout=f,stderr=subprocess.STDOUT).returncode
(R/(label+'-build.json')).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
sys.exit(p.returncode)
