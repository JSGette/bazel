"""Stable upstream control matrix, preserving separate old/new cache namespaces."""
from pathlib import Path
import importlib.util,json,subprocess,shutil,fcntl,hashlib
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec=importlib.util.spec_from_file_location('wide',TASK/'work/datadog-wide.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);h=w.h
RUN=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
w.ROOT=h.ROOT=RUN/'datadog-six-stable'
w.CANDIDATE=RUN/'bin/skycache-upstream';w.VANILLA=RUN/'bin/vanilla-upstream'
h.CACHE=RUN/'datadog-six/skycache'
w.NAMES=['agent','cluster-agent','process-agent','trace-agent','security-agent','system-probe']
w.TARGETS=['//cmd/'+n+':'+n for n in w.NAMES]
h.publications=lambda:{str(p.relative_to(h.CACHE)):h.sha(p) for p in sorted((h.CACHE/'leased-v2/publications-v4').rglob('*.manifest'))}
lock=(RUN.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
w.ROOT.mkdir(exist_ok=False)
module=h.WS/'MODULE.bazel.lock';before=module.read_bytes()
assert hashlib.sha256(before).hexdigest()=='1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
(w.ROOT/'MODULE.bazel.lock.before').write_bytes(before)
resolved=RUN.parent/'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after'
for name in ['benchmark-upstream-stable.py','datadog-wide.py','datadog-concurrent.py','datadog-download-modes.py','datadog-clean-benchmark.py']:shutil.copy2(TASK/'work'/name,w.ROOT/name)
module.write_bytes(resolved.read_bytes());source=h.git('diff','--binary','HEAD')
(w.ROOT/'workspace.patch').write_text(source+'\n')
w.P=dict(status='running',started_at=h.stamp(),targets=w.TARGETS,workspace=str(h.WS),output_base=str(h.OUT),analysis_cache=str(h.CACHE),
 source_revision=h.git('rev-parse','HEAD'),module_lock_sha256=h.sha(module),runs=[],
 binary_sha256={k:h.sha(v) for k,v in dict(candidate=w.CANDIDATE,vanilla=w.VANILLA).items()},
 method='Upstream a5c50fcb65 vanilla versus merged 1aac2aa2ec SkyCache on/off. Six binaries. Three rotated repetitions per minimal/toplevel policy. No premerge binaries interleaved. Reuse safely isolated new namespace populated by preceding diagnostic attempt; three excluded preparation runs establish upstream state. All builds fresh JVM before synchronous clean; zero graph, empty execroot, absent action cache verified. Same output base, 6GiB heap,16 jobs, warm OS/dependency caches, no disk cache, Cache & Carry cache and BES (1 connection x16 requests). PSS/RSS sampled at100ms. No 64MiB budget changes.')
w.persist()
try:
 w.run('prepare-vanilla','toplevel','vanilla',True)
 w.run('prepare-minimal','minimal','on',True)
 w.run('prepare-toplevel','toplevel','on',True)
 for i,order in enumerate([['off','on','vanilla'],['on','vanilla','off'],['vanilla','off','on']],1):
  for mode in (['minimal','toplevel'] if i%2 else ['toplevel','minimal']):
   for v in order:w.run(f'r{i}-{mode}-{v}',mode,v)
 w.P['status']='passed'
except BaseException as e:w.P['status']='failed';w.P['failure']=repr(e);raise
finally:
 try:w.P['shutdown']=h.shutdown('final-shutdown')
 except Exception as e:w.P['shutdown_error']=repr(e)
 w.P['workspace_unchanged_during_runs']=h.git('diff','--binary','HEAD')==source
 module.write_bytes(before);w.P['module_lock_restored']=module.read_bytes()==before
 w.P['final_publications']=h.publications();w.P['finished_at']=h.stamp();w.persist();lock.close()
