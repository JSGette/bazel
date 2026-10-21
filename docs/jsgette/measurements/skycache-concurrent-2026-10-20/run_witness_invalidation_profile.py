"""Profile only warm revert in a fresh two-server diagnostic; raw JFR stays local."""
from pathlib import Path
import hashlib
import json
import subprocess
import textwrap

repo=Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
root=work/'datadog-crossrevision-witness-jfr'
binary=work/'bazel-depset-runfiles-aspect-mapping-witness-validator-symlink-packed-review'
probe=work/'datadog_witness_profile_probe.py'
jcmd='/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/jcmd'
assert not root.exists() and not probe.exists()
source=(repo/'tools/skycache/datadog_worktree_probe.py').read_text()
source=source.replace('from datadog_probe import process_memory',
    "import sys\nsys.path.insert(0, '/home/jgett/REPOS/bazel-skycache-concurrent/tools/skycache')\nfrom datadog_probe import process_memory")
marker='                pair("reverted-warm-graphs", ("a", "b"), cache=True, expected=baseline)'
assert source.count(marker)==1
profile='''started_profiles = []
try:
    for name in ("a", "b"):
        output = root / ("out-" + name)
        pid = int((output / "server/server.pid.txt").read_text())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\\x00")
        assert ("--output_base=" + str(output)).encode() in cmdline, (name, pid)
        command = [JCMD, str(pid), "JFR.start", "name=skycache-validation", "settings=profile",
                   "filename=" + str(root / (name + "-warm-revert.jfr")), "dumponexit=true"]
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        (root / (name + "-jfr-start.log")).write_text(result.stdout + result.stderr)
        result.check_returncode()
        started_profiles.append((name, pid))
    pair("reverted-warm-graphs", ("a", "b"), cache=True, expected=baseline)
finally:
    for name, pid in started_profiles:
        result = subprocess.run([JCMD, str(pid), "JFR.stop", "name=skycache-validation"],
            capture_output=True, text=True, timeout=30)
        (root / (name + "-jfr-stop.log")).write_text(result.stdout + result.stderr)
        result.check_returncode()
'''.replace('JCMD',repr(jcmd))
probe.write_text(source.replace(marker,textwrap.indent(profile.rstrip(), ' '*16)))
argv=['python3',str(probe),'--bazel',str(binary),'--repository','/home/jgett/REPOS/datadog-agent',
    '--root',str(root),'--revision','0cde96beb70963d0838119c308b9d0422ee66be0',
    '--resolved-lockfile','/home/jgett/REPOS/.work/skyframe-go/work/datadog-MODULE.bazel.lock',
    '--action-cache','/home/jgett/REPOS/.work/skyframe-go/work/action-cache',
    '--lock-file','/home/jgett/REPOS/.work/skyframe-measurements.lock',
    '--java-home','/home/jgett/REPOS/.tools/skycache-benchmark-jdk25',
    '--cross-revision-edits','--cross-revision-smoke','--build-timeout-seconds','300']
(work/'witness-invalidation-profile-command.json').write_text(json.dumps({
    'purpose':__doc__, 'scope':'JFR diagnostic, not acceptance or an uninstrumented performance comparison.',
    'argv':argv,'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
    'diagnostic_probe_sha256':hashlib.sha256(probe.read_bytes()).hexdigest()},indent=2)+'\n')
with (work/'witness-invalidation-profile.log').open('w') as log:
    result=subprocess.run(argv,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
print('Profile diagnostic exit',result.returncode,flush=True)
if result.returncode:
    print((work/'witness-invalidation-profile.log').read_text()[-2000:],flush=True)
result.check_returncode()
run=json.loads((root/'results.json').read_text())
assert run['passed']
print(json.dumps({'passed':run['passed'],'phases':len(run['phases']),
    'profiles':{name:(root/(name+'-warm-revert.jfr')).stat().st_size for name in ('a','b')}}),flush=True)
