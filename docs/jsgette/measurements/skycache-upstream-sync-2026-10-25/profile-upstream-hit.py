"""Profile a verified hit after refreshing external /tmp Python cache evidence."""
from pathlib import Path
import importlib.util,json,subprocess,shutil,fcntl,hashlib,os
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec=importlib.util.spec_from_file_location('wide',T/'work/datadog-wide.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);h=w.h
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
prior=json.loads((R/'datadog-six-stable/results.json').read_text());assert prior['status']=='passed'
w.ROOT=h.ROOT=R/'hit-profile';h.CACHE=w.ROOT/'skycache';w.CANDIDATE=R/'bin/skycache-upstream'
w.NAMES=['agent','cluster-agent','process-agent','trace-agent','security-agent','system-probe'];w.TARGETS=['//cmd/'+n+':'+n for n in w.NAMES]
h.publications=lambda:{str(p.relative_to(h.CACHE)):h.sha(p) for p in sorted((h.CACHE/'leased-v2/publications-v4').rglob('*.manifest'))}
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
w.ROOT.mkdir(exist_ok=False)
id='e50350031be8c90ec814cb3f9cd3e2dca9e11018c0edd32f0d06ce60da4998a3'
src=R/'datadog-six/skycache/leased-v2';p=next(src.glob('publications-v4/*/'+id+'.manifest'))
dest=h.CACHE/'leased-v2'/p.relative_to(src);dest.parent.mkdir(parents=True);os.link(p,dest)
dest=h.CACHE/'leased-v2/objects/segmented-v1'/id;dest.parent.mkdir(parents=True)
shutil.copytree(src/'objects/segmented-v1'/id,dest,copy_function=os.link)
module=h.WS/'MODULE.bazel.lock';before=module.read_bytes()
assert hashlib.sha256(before).hexdigest()=='1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
module.write_bytes((R.parent/'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after').read_bytes())
w.P=dict(status='running',runs=[],expected_binaries=prior['expected_binaries'],purpose='JFR sampled-stack diagnosis on verified hit; excluded from performance matrix',source_commit='1aac2aa2ec6fe31af2e979558f06a3b6971c601b',boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),seed_publication=id)
orig=w.command
def jcmd(*args):
 cmd=[str(h.JDK/'bin/jcmd'),str(h.pid()),*args];p=subprocess.run(cmd,capture_output=True,text=True)
 with (w.ROOT/'jfr-commands.jsonl').open('a') as f:f.write(json.dumps(dict(argv=cmd,stdout=p.stdout,stderr=p.stderr,exit_code=p.returncode))+'\n')
 p.check_returncode()
def command(*args):
 jcmd('JFR.configure','stackdepth=128');jcmd('JFR.start','name=skycache','settings=profile','maxsize=64m');return orig(*args)
try:
 w.run('prepare-minimal','minimal','on',True)
 w.run('prepare-toplevel','toplevel','on',True)
 w.command=command
 w.run('minimal-jfr','minimal','on')
 row=w.P['runs'][-1]
 jcmd('JFR.stop','name=skycache','filename='+str(w.ROOT/'minimal.jfr'))
 assert row['analysis_hits']==17 and row['execution_hits']==11
 assert row['local-skycache.json']['writes']==0 and row['grpc']['bytestream_read_calls']==0
 assert row['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
 w.P['status']='passed'
except BaseException as e:w.P['status']='failed';w.P['failure']=repr(e);raise
finally:
 w.P['shutdown']=h.shutdown('final-shutdown');module.write_bytes(before)
 w.P['module_lock_restored']=module.read_bytes()==before;w.persist();lock.close()
