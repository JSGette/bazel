from pathlib import Path
import subprocess,json,hashlib
repo=Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
binary=work/'bazel-depset-runfiles-aspect-mapping-validator-symlink-packed-review'
root=work/'datadog-crossrevision-mapping-smoke'
assert binary.is_file() and not root.exists()
argv=['python3',str(repo/'tools/skycache/datadog_worktree_probe.py'),
      '--bazel',str(binary),'--repository','/home/jgett/REPOS/datadog-agent','--root',str(root),
      '--revision','0cde96beb70963d0838119c308b9d0422ee66be0',
      '--resolved-lockfile','/home/jgett/REPOS/.work/skyframe-go/work/datadog-MODULE.bazel.lock',
      '--action-cache','/home/jgett/REPOS/.work/skyframe-go/work/action-cache',
      '--lock-file','/home/jgett/REPOS/.work/skyframe-measurements.lock',
      '--java-home','/home/jgett/REPOS/.tools/skycache-benchmark-jdk25',
      '--cross-revision-edits','--cross-revision-smoke','--build-timeout-seconds','300']
(work/'cross-revision-mapping-smoke-command.json').write_text(json.dumps({
    'purpose':'Focused clean two-server warm-revert diagnosis with shared repository mapping codec, action disk cache enabled; not full acceptance',
    'argv':argv,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest()},indent=2)+'\n')
with (work/'datadog-crossrevision-mapping-smoke.log').open('w') as log:
    r=subprocess.run(argv,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
print('smoke exit',r.returncode,flush=True)
print((work/'datadog-crossrevision-mapping-smoke.log').read_text()[-3000:],flush=True)
r.check_returncode()
