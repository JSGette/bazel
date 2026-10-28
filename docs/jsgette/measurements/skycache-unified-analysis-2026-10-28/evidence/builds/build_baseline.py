from pathlib import Path
import subprocess,time,json,hashlib,shutil,fcntl
root=Path('/home/jgett/REPOS/.work/skycache-unified-analysis-plan-2026-10-28')
out=Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence')
cmd=['/home/jgett/.local/bin/bazel','--output_base=/home/jgett/REPOS/.work/skycache-unified-baseline-build','--server_javabase=/home/jgett/REPOS/.tools/skycache-benchmark-jdk25','--host_jvm_args=-Xmx5g','--max_idle_secs=120','build','//src:bazel-dev','--jobs=6','--local_resources=memory=6144','--color=no','--curses=no','--disk_cache=','--remote_cache=grpc://10.55.0.1:8080','--remote_max_connections=1','--remote_max_concurrency_per_connection=16','--remote_timeout=120','--remote_retries=1','--remote_upload_local_results=false',f'--build_event_json_file={out}/baseline-build.bep.json',f'--profile={out}/baseline-build.profile.gz']
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
assert not subprocess.check_output(['git','status','--porcelain=v1'],cwd=root), 'baseline must be clean'
(out/'baseline-build.command.json').write_text(json.dumps(cmd,indent=2)+'\n')
with Path('/home/jgett/REPOS/.work/skyframe-measurements.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 start=time.monotonic()
 with (out/'baseline-build.log').open('w') as log:
  r=subprocess.run(cmd,cwd=root,stdout=log,stderr=subprocess.STDOUT)
 result={'exit_code':r.returncode,'seconds':time.monotonic()-start,'source_commit':head}
 if r.returncode==0:
  binary=out/'baseline-bazel'
  shutil.copy2(root/'bazel-bin/src/bazel-dev',binary)
  result['binary_sha256']=hashlib.file_digest(binary.open('rb'),'sha256').hexdigest()
  result['source_clean_after_build']=not subprocess.check_output(['git','status','--porcelain=v1'],cwd=root)
 (out/'baseline-build.result.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps(result),flush=True)
 if r.returncode: print((out/'baseline-build.log').read_text()[-12000:])
 raise SystemExit(r.returncode)
