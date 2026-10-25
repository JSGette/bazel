"""Matched clean-build baseline after upstream integration; no budget changes."""
from pathlib import Path
import importlib.util,json,subprocess,shutil,fcntl,hashlib
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
spec=importlib.util.spec_from_file_location('wide',TASK/'work/datadog-wide.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w);h=w.h
RUN=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
OLD=RUN.parent/'skycache-large-tree-2026-10-25'
w.ROOT=h.ROOT=RUN/'datadog-six'
pre=OLD/'bin/skycache-large-tree-final2';candidate=RUN/'bin/skycache-upstream'
w.VANILLA=RUN/'bin/vanilla-upstream';h.CACHE=w.ROOT/'skycache'
w.NAMES=['agent','cluster-agent','process-agent','trace-agent','security-agent','system-probe']
w.TARGETS=['//cmd/'+n+':'+n for n in w.NAMES]
h.publications=lambda:{str(p.relative_to(h.CACHE)):h.sha(p) for p in sorted((h.CACHE/'leased-v2/publications-v4').rglob('*.manifest'))}
lock=(RUN.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
w.ROOT.mkdir(exist_ok=False)
module=h.WS/'MODULE.bazel.lock';before=module.read_bytes()
assert hashlib.sha256(before).hexdigest()=='1988ae97ea0d31e99aa8683a9849a448049fe4d12f16c644b931d31553c77ee8'
(w.ROOT/'MODULE.bazel.lock.before').write_bytes(before)
resolved=RUN.parent/'datadog-wide-dependencies-2026-10-25/MODULE.bazel.lock.after'
assert h.sha(resolved)=='0bb43dc2960626918c20c350491be5cdf6fd18730bb34a956d4608a1e1aa98fc'
for name in ['benchmark-upstream-sync.py','datadog-wide.py','datadog-concurrent.py','datadog-download-modes.py','datadog-clean-benchmark.py']:shutil.copy2(TASK/'work'/name,w.ROOT/name)
module.write_bytes(resolved.read_bytes());source=h.git('diff','--binary','HEAD')
(w.ROOT/'workspace.patch').write_text(source+'\n')
w.P=dict(status='running',started_at=h.stamp(),targets=w.TARGETS,workspace=str(h.WS),output_base=str(h.OUT),analysis_cache=str(h.CACHE),
 source_revision=h.git('rev-parse','HEAD'),module_lock_sha256=h.sha(module),runs=[],
 binary_sha256={k:h.sha(v) for k,v in dict(premerge=pre,candidate=candidate,vanilla=w.VANILLA).items()},
 method='Upstream a5c50fcb65 vanilla versus merged SkyCache on/off. Six binaries. Three rotated repetitions per minimal/toplevel policy, plus three interleaved premerge minimal controls. All builds fresh JVM before synchronous clean; zero graph, empty execroot, absent action cache verified. Same output base, 6GiB heap, 16 jobs, warm OS/dependency caches, no disk cache, Cache & Carry remote cache and BES (1 connection x16 requests). First-write runs use empty per-version namespace in the same initially empty cache. PSS/RSS sampled at100ms. No 64MiB budget changes.')
w.persist()
old_expected=json.loads((OLD/'datadog-six-final2/results.json').read_text())['expected_binaries']
def run(label,mode,variant,preparation=False,cohort='merged'):
 w.CANDIDATE=pre if cohort=='premerge' else candidate
 w.P['expected_binaries']=old_expected if cohort=='premerge' else new_expected
 w.run(label,mode,variant,preparation)
 w.P['runs'][-1]['cohort']=cohort;w.persist()
try:
 w.CANDIDATE=candidate
 w.run('prepare-vanilla','toplevel','vanilla',True)
 new_expected=w.P['expected_binaries'];w.P['upstream_expected_binaries']=new_expected;w.P['premerge_expected_binaries']=old_expected
 run('prepare-premerge-minimal','minimal','on',True,'premerge')
 oldpubs=h.publications();assert oldpubs
 run('prepare-merged-minimal','minimal','on',True)
 w.P['old_publications_preserved_after_new_writer']=all(h.publications().get(k)==v for k,v in oldpubs.items())
 assert w.P['old_publications_preserved_after_new_writer']
 namespaces={p.parent.name for p in (h.CACHE/'leased-v2/publications-v4').rglob('*.manifest')}
 w.P['format_namespaces']=sorted(namespaces);assert len(namespaces)==2
 run('prepare-merged-toplevel','toplevel','on',True)
 for i,order in enumerate([['off','on','vanilla'],['on','vanilla','off'],['vanilla','off','on']],1):
  if i%2:run(f'r{i}-minimal-premerge','minimal','on',False,'premerge')
  for mode in (['minimal','toplevel'] if i%2 else ['toplevel','minimal']):
   for v in order:run(f'r{i}-{mode}-{v}',mode,v)
  if not i%2:run(f'r{i}-minimal-premerge','minimal','on',False,'premerge')
 w.P['status']='passed'
except BaseException as e:w.P['status']='failed';w.P['failure']=repr(e);raise
finally:
 try:w.P['shutdown']=h.shutdown('final-shutdown')
 except Exception as e:w.P['shutdown_error']=repr(e)
 w.P['workspace_unchanged_during_runs']=h.git('diff','--binary','HEAD')==source
 module.write_bytes(before);w.P['module_lock_restored']=module.read_bytes()==before
 w.P['final_publications']=h.publications();w.P['finished_at']=h.stamp();w.persist();lock.close()
