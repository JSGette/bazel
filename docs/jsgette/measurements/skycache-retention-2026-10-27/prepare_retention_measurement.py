from pathlib import Path
import subprocess,sys
import prepare_retention_experiment as setup
ROOT=setup.ROOT;WORK=Path(__file__).parent
for name in ['agent','probe']:
 path=ROOT/name
 assert path.resolve().parent==ROOT.resolve()
 assert not (ROOT/(name+'-before')).exists()
 path.rename(ROOT/(name+'-before'))
setup.build_agent(ROOT/'agent',ROOT/'build-advisory/bazel')
setup.build_probe();setup.harness()

s=(WORK/'analyze_digests.py').read_text().replace('skycache-digests-2026-10-26','skycache-retention-2026-10-27')
s=s.replace("ROOT/('warm-v2' if name=='warm' else name)","ROOT/name")
s=s.replace("ROOT/('agent-slots' if name.startswith('slots-') else 'agent')","ROOT/('agent-before' if name=='diagnostic-before-v2' else 'agent')")
old="  assert 'SKYCACHE_DIGEST_AGENT '+mode+' com/google/devtools/build/lib/skyframe/serialization/AutoRegistry sha256='+agent['registry'] in logs"
new="""  for cls in ['AutoRegistry']:
   full='com/google/devtools/build/lib/skyframe/serialization/'+cls
   assert 'SKYCACHE_RETENTION_AGENT '+mode+' '+full+' sha256='+agent[mode][full+'.class'] in logs"""
assert old in s;s=s.replace(old,new)
s=s.replace("  if mode=='scratch':","  if mode in ['baseline','retention']:")
(WORK/'analyze_retention.py').write_text(s);compile(s,'analyze_retention','exec')

s=(WORK/'check_digest_registry.py').read_text()
s=s.replace('skycache-digests-2026-10-26/registry-slots','skycache-retention-2026-10-27/registry')
s=s.replace("'channels':Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26/build-128-v2/bazel'),'scratch':root.parent/'build-slots-v2/bazel'", "'channels':Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26/build-slots-v2/bazel'),'scratch':root.parent/'build-advisory/bazel'")
s=s.replace("'channels'","'reader'").replace("'scratch'","'retention'")
(WORK/'check_retention_registry.py').write_text(s)
subprocess.run([sys.executable,str(WORK/'check_retention_registry.py')],check=True)
command=__import__('json').loads((ROOT/'build-advisory/command.json').read_text())
subprocess.run(command[:command.index('test')]+['shutdown'],cwd='/home/jgett/REPOS/bazel-skycache-remote-mvp',check=True)
print('Prepared tested retention comparison')
