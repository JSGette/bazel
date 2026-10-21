"""Preserve an independent deploy jar for before/after dependency validation measurements."""
from pathlib import Path
import fcntl
import hashlib
import json
import shutil
import subprocess
import sys

mode = sys.argv[1]
assert mode in ('before', 'after')
repo = Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
package = 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
target = package+'/LocalDependencyValidationBenchmark_deploy.jar'
destination = work/('dependency-validator-witness-'+mode+'.jar')
assert not destination.exists()
if mode == 'before':
    assert not subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True).strip()
startup = ['/home/jgett/REPOS/.tools/bazel', '--host_jvm_args=-Xmx4g', '--output_base='+str(work/'build-output')]
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock', 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_SH)
    log_path = work/('dependency-validator-witness-'+mode+'-build.log')
    try:
        with log_path.open('w') as log:
            result = subprocess.run(startup+['build', '--config=efcache', '--bes_backend=',
                '--remote_upload_local_results=false', '--jobs=4', '--local_resources=memory=4096',
                '//'+package+':LocalDependencyValidationBenchmark_deploy.jar'], cwd=repo,
                stdout=log, stderr=subprocess.STDOUT)
        print(log_path.read_text()[-2000:], flush=True)
        result.check_returncode()
        shutil.copy2(repo/'bazel-bin'/target, destination)
        destination.chmod(0o444)
        metadata = {'mode': mode, 'path': str(destination),
            'sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
            'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip(),
            'diff': subprocess.check_output(['git', '--no-pager', 'diff', '--no-ext-diff', '--no-textconv'], cwd=repo, text=True),
            'source_build_flags': ['--config=efcache', '--bes_backend=', '--remote_upload_local_results=false']}
        (work/('dependency-validator-witness-'+mode+'-binary.json')).write_text(json.dumps(metadata, indent=2)+'\n')
        print(json.dumps({k:v for k,v in metadata.items() if k != 'diff'}), flush=True)
    finally:
        subprocess.run(startup+['shutdown'], cwd=repo, check=False)
