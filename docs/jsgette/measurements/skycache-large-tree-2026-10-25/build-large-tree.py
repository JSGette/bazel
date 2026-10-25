from pathlib import Path
import json,subprocess,os,time,sys,shutil
R=Path('/home/jgett/REPOS/bazel-skycache-v2')
RUN=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
L=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local'
T=R/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
label=sys.argv[1]
if label=='build-tests-1':
 p=T/'BUILD';s=p.read_text();a=s.index('    name = "SegmentedFingerprintValueStoreTest"');b=s.index('    deps = [',a)+len('    deps = [');s=s[:b]+'\n        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization",'+s[b:];p.write_text(s)
env=dict(os.environ,TMPDIR=str(RUN/'tmp'))
(RUN/'tmp').mkdir(exist_ok=True)
args=['/home/jgett/.local/bin/bazel','--output_user_root=/home/jgett/.cache/bazel/_bazel_jgett',
 '--output_base=/home/jgett/REPOS/.work/skycache-execution-2026-10-25/build',
 '--server_javabase=/home/jgett/REPOS/.tools/skycache-benchmark-jdk25','--host_jvm_args=-Xmx5g','--max_idle_secs=600',
 'test','//src:bazel-dev','//src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:all',
 '--jobs=6','--local_test_jobs=3','--local_resources=memory=6144','--test_output=errors','--color=no','--curses=no',
 '--disk_cache=','--remote_cache=grpc://10.55.0.1:8080','--remote_max_connections=1','--remote_max_concurrency_per_connection=16',
 '--remote_timeout=600','--remote_retries=1','--bes_backend=grpc://10.55.0.1:8080','--bes_results_url=http://192.168.2.71:8088/invocation/',
 '--build_event_json_file='+str(RUN/(label+'.bep.json')),'--profile='+str(RUN/(label+'.profile.gz'))]
t=time.monotonic()
with (RUN/(label+'.log')).open('x') as f:r=subprocess.run(args,cwd=R,env=env,stdout=f,stderr=subprocess.STDOUT)
result={'argv':args,'seconds':time.monotonic()-t,'exit_code':r.returncode}
(RUN/(label+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
sys.exit(r.returncode)
