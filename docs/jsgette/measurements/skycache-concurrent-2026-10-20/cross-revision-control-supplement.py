"""Add three omitted control-restart pairs after a successful 22-build core gate."""
from pathlib import Path
import fcntl,hashlib,json,os,subprocess
repo=Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
root=work/'datadog-crossrevision-mapping-eviction-review'
results=root/'results.json'
original=root/'results-core-22-builds.json'
command=json.loads((work/'cross-revision-mapping-eviction-full-command.json').read_text())
argv=command['argv']+['--resume','--restart-controls']
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    assert not original.exists()
    contents=results.read_bytes()
    run=json.loads(contents)
    assert run.get('passed') and len(run['phases'])==11 and not run['restart_controls']
    assert run['cross_revision_edits'] and not run['cross_revision_smoke']
    assert all(b['exit_code']==0 and not b['timed_out'] for p in run['phases'] for b in p['builds'])
    assert hashlib.sha256(Path(run['binary']).read_bytes()).hexdigest()==run['binary_sha256']==command['binary_sha256']
    original.write_bytes(contents)
    correction={'reason':'The initial longer runner omitted --restart-controls. Preserve its 22 completed builds and add the three cache-off restart pairs afterward.',
                'original_results':'results-core-22-builds.json','original_results_sha256':hashlib.sha256(contents).hexdigest(),
                'added_flag':'--restart-controls','measurement_order':'All three additional pairs occur after the eleven core pairs. Do not use their timing as an interleaved before/after performance comparison.',
                'existing_phase_records':'Unchanged; the existing harness revalidates archived successes on resume.'}
    run['restart_controls']=True
    run['passed']=False
    run['restart_controls_supplement']=correction
    temporary=root/'results-with-control-restarts.tmp'
    temporary.write_text(json.dumps(run,indent=2)+'\n')
    os.replace(temporary,results)
    (work/'cross-revision-control-supplement-command.json').write_text(json.dumps({'purpose':__doc__,'argv':argv,'correction':correction},indent=2)+'\n')
with (work/'cross-revision-control-supplement.log').open('w') as log:
    result=subprocess.run(argv,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
print('control supplement exit',result.returncode,flush=True)
print((work/'cross-revision-control-supplement.log').read_text()[-2000:],flush=True)
result.check_returncode()
run=json.loads(results.read_text())
assert run['passed'] and len(run['phases'])==14
assert {p['phase'] for p in run['phases'][-3:]}=={'restarted-control-base','restarted-control-edited','reverted-restarted-control'}
print('28 builds including separately ordered control restarts passed.',flush=True)
