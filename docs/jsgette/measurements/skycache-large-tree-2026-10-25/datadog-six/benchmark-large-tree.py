"""Large Datadog scopes, zero-graph clean, preserved lockfile and rotated controls."""
from pathlib import Path
import importlib.util,json,subprocess,shutil,fcntl,sys,hashlib
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec=importlib.util.spec_from_file_location('wide',TASK/'work/datadog-wide.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);h=w.h
BUILD=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
scope=sys.argv[1]
assert scope in ('six','five')
w.ROOT=h.ROOT=BUILD/('datadog-'+scope)
w.CANDIDATE=BUILD/'bin/skycache-large-tree'
h.CACHE=w.ROOT/'skycache'
w.NAMES=['agent','cluster-agent','process-agent','trace-agent','security-agent','system-probe']
if scope=='five':w.NAMES.remove('agent')
w.TARGETS=['//cmd/'+n+':'+n for n in w.NAMES]
h.publications=lambda:{str(p.relative_to(h.CACHE)):h.sha(p) for p in sorted((h.CACHE/'leased-v2/publications-v4').rglob('*.manifest'))}
w.P={}
lock=(BUILD.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
w.ROOT.mkdir(exist_ok=False)
module=h.WS/'MODULE.bazel.lock'
before=module.read_bytes()
assert hashlib.sha256(before).hexdigest()=='1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
(w.ROOT/'MODULE.bazel.lock.before').write_bytes(before)
resolved=BUILD.parent/'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after'
assert h.sha(resolved)=='0bb43dc2960626918c20c350491be5cdf6fd18730bb34a956d4608a1e1aa98fc'
for name in ['benchmark-large-tree.py','datadog-wide.py','datadog-concurrent.py','datadog-download-modes.py','datadog-clean-benchmark.py']:
 shutil.copy2(TASK/'work'/name,w.ROOT/name)
module.write_bytes(resolved.read_bytes())
source=h.git('diff','--binary','HEAD')
(w.ROOT/'workspace.patch').write_text(source+'\n')
w.P.update(status='running',started_at=h.stamp(),targets=w.TARGETS,workspace=str(h.WS),output_base=str(h.OUT),
 analysis_cache=str(h.CACHE),source_revision=h.git('rev-parse','HEAD'),module_lock_sha256=h.sha(module),
 binary_sha256={'candidate':h.sha(w.CANDIDATE),'vanilla':h.sha(w.VANILLA)},runs=[],
 method='Three rotated repetitions of candidate off/on and preserved pre-SkyCache vanilla source comparator in each minimal/toplevel policy. Fresh JVM before synchronous clean; zero Skyframe nodes/edges, empty execroot and absent local action cache verified. Same output base, warm OS/dependency caches, 6 GiB heap, 16 jobs, 6144 MiB action budget. Disk cache disabled; Cache & Carry cache and BES enabled, 1 connection x16 requests. Clean timed separately; PSS/RSS process tree sampled every 100 ms. All binaries compared by digest.')
w.persist()
try:
 w.run('prepare-vanilla','toplevel','vanilla',True)
 w.run('prepare-minimal','minimal','on',True)
 assert h.publications(),'SkyCache did not publish the large tree'
 w.run('prepare-toplevel','toplevel','on',True)
 for i,order in enumerate([['off','on','vanilla'],['on','vanilla','off'],['vanilla','off','on']],1):
  for mode in (['minimal','toplevel'] if i%2 else ['toplevel','minimal']):
   for variant in order:w.run(f'r{i}-{mode}-{variant}',mode,variant)
 w.P['status']='passed'
except BaseException as e:w.P['status']='failed';w.P['failure']=repr(e);raise
finally:
 try:w.P['shutdown']=h.shutdown('final-shutdown')
 except Exception as e:w.P['shutdown_error']=repr(e)
 w.P['workspace_unchanged_during_runs']=h.git('diff','--binary','HEAD')==source
 module.write_bytes(before)
 w.P['module_lock_restored']=module.read_bytes()==before
 w.P['final_publications']=h.publications();w.P['finished_at']=h.stamp();w.persist();lock.close()
