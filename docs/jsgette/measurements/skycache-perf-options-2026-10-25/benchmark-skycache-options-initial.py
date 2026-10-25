from pathlib import Path
import importlib.util,json,subprocess,shutil,fcntl,hashlib,os,sys
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec=importlib.util.spec_from_file_location('wide',T/'work/datadog-wide.py');w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);h=w.h
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
old=R.parent/'skycache-upstream-sync-2026-10-25'
label=sys.argv[1]
w.ROOT=h.ROOT=R/label;w.ROOT.mkdir(exist_ok=False)
h.CACHE=R/'cache'
if not h.CACHE.exists():
 for name in ['objects','publications-v4']:
  shutil.copytree(old/'hit-profile/skycache/leased-v2'/name,h.CACHE/'leased-v2'/name,copy_function=os.link)
w.NAMES=['agent','cluster-agent','process-agent','trace-agent','security-agent','system-probe'];w.TARGETS=['//cmd/'+n+':'+n for n in w.NAMES]
h.publications=lambda:{str(p.relative_to(h.CACHE)):h.sha(p) for p in sorted((h.CACHE/'leased-v2/publications-v4').rglob('*.manifest'))}
baseline=old/'bin/skycache-upstream';candidate=R/'bin'/label
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
module=h.WS/'MODULE.bazel.lock';before=module.read_bytes();assert hashlib.sha256(before).hexdigest()=='1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
module.write_bytes((R.parent/'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after').read_bytes())
prior=json.loads((old/'datadog-six-stable/results.json').read_text())
w.P=dict(status='running',runs=[],expected_binaries=prior['expected_binaries'],method='Three alternating baseline/candidate pairs per minimal and toplevel. Fresh JVM before synchronous clean, zero graph/empty execroot/no action cache; same frozen lockfile, native ext4 output base, C&C, 6GiB heap,16 jobs. Separate immutable-copy seed cache. Preparation excluded. No budget or validation semantics change.',baseline_sha=h.sha(baseline),candidate_sha=h.sha(candidate),patch=str(R/(label+'.patch')),started_at=h.stamp(),boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
def run(name,mode,variant,prep=False):
 w.CANDIDATE=baseline if variant=='baseline' else candidate
 w.run(name,mode,'on',prep)
 row=w.P['runs'][-1];row['implementation']=variant
 if not prep:
  assert row['analysis_hits']==17 and row['execution_hits']==11
  assert row['local-skycache.json']['writes']==0
  assert not row['local-skycache.json']['read_limit_skips']
  assert row['grpc']['method_attempt_counts'].get('build.bazel.remote.execution.v2.ActionCache/GetActionResult',0)==0
  if mode=='minimal':assert row['grpc']['bytestream_read_calls']==0
 w.persist()
try:
 run('prepare-minimal','minimal','baseline',True)
 run('prepare-toplevel','toplevel','baseline',True)
 for i,order in enumerate([['baseline','candidate'],['candidate','baseline'],['baseline','candidate']],1):
  for mode in (['minimal','toplevel'] if i%2 else ['toplevel','minimal']):
   for v in order:run(f'r{i}-{mode}-{v}',mode,v)
 w.P['status']='passed'
except BaseException as e:w.P['status']='failed';w.P['failure']=repr(e);raise
finally:
 try:w.P['shutdown']=h.shutdown('final-shutdown')
 except Exception as e:w.P['shutdown_error']=repr(e)
 module.write_bytes(before);w.P['module_lock_restored']=module.read_bytes()==before
 w.P['finished_at']=h.stamp();w.persist();lock.close()
