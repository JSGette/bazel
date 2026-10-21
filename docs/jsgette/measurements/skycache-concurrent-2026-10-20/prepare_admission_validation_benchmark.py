from pathlib import Path
import fcntl,hashlib,json,shutil,subprocess,sys

mode=sys.argv[1]
assert mode in ('before','after')
repo=Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
package='src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
name='LocalAdmissionValidationBenchmark'
source=repo/package/(name+'.java')
destination=work/('admission-validation-'+mode+'.jar')
assert not destination.exists()
if mode=='before':
    changed=subprocess.check_output(['git','diff','--name-only'],cwd=repo,text=True).splitlines()
    assert changed==[package+'/BUILD'],changed
java='/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/java'
subprocess.run([java,'-jar','/home/jgett/REPOS/.tools/google-java-format.jar','--replace',str(source)],check=True)
subprocess.run(['/home/jgett/REPOS/.tools/buildifier',str(repo/package/'BUILD')],check=True)
startup=['/home/jgett/REPOS/.tools/bazel','--host_jvm_args=-Xmx4g','--output_base='+str(work/'build-output')]
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_SH)
    log_path=work/('admission-validation-'+mode+'-build.log')
    try:
        with log_path.open('w') as log:
            result=subprocess.run(startup+['build','--config=efcache','--bes_backend=',
                '--remote_upload_local_results=false','--jobs=4','--local_resources=memory=4096',
                '//'+package+':'+name+'_deploy.jar'],cwd=repo,stdout=log,stderr=subprocess.STDOUT)
        print(log_path.read_text()[-2400:],flush=True)
        result.check_returncode()
        shutil.copy2(repo/'bazel-bin'/package/(name+'_deploy.jar'),destination)
        destination.chmod(0o444)
        metadata={'mode':mode,'path':str(destination),'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),
            'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
            'benchmark_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'diff':subprocess.check_output(['git','--no-pager','diff','--no-ext-diff','--no-textconv'],cwd=repo,text=True),
            'source_build_flags':['--config=efcache','--bes_backend=','--remote_upload_local_results=false']}
        (work/('admission-validation-'+mode+'-binary.json')).write_text(json.dumps(metadata,indent=2)+'\n')
        print(json.dumps({k:v for k,v in metadata.items() if k!='diff'}),flush=True)
    finally:
        subprocess.run(startup+['shutdown'],cwd=repo,check=False)
