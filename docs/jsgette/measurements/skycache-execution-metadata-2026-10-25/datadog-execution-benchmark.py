"""After-clean Datadog comparison: execution metadata, feature-off, and preserved vanilla."""
from pathlib import Path
import fcntl,hashlib,importlib.util,json,re,shutil,subprocess
BASE=Path(__file__).with_name('datadog-download-modes.py')
spec=importlib.util.spec_from_file_location('download_modes',BASE)
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
h=d.h
R=d.R=h.ROOT=Path('/home/jgett/REPOS/.work/datadog-execution-2026-10-25-attempt3')
CANDIDATE=Path('/home/jgett/REPOS/.work/skycache-execution-2026-10-25/bin/skycache-bwob')
VANILLA=h.OLD/'bin/vanilla'
h.CACHE=R/'analysis-cache'
h.BINARY=CANDIDATE
original_flag=h.flag
original_command=h.command
h.flag=lambda variant: '--color=no' if variant=='vanilla' else original_flag(variant)
def command(variant,label):
 args=original_command(variant,label)
 if variant=='vanilla':
  args=[a for a in args if not a.startswith(('--experimental_local_skycache',
        '--experimental_analysis_cache_idle_retention','--experimental_active_directories'))]
 return args
h.command=command
lock=(R.parent/'skyframe-measurements.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
R.mkdir(exist_ok=False)
for source,name in [(Path(__file__),'harness.py'),(BASE,'harness-modes.py'),(d.BASE,'harness-base.py')]:shutil.copy2(source,R/name)
previous=json.loads((R.parent/'datadog-clean-2026-10-25/results.json').read_text())
d.expected=previous['expected_output_sha256']
assert h.git('rev-parse','HEAD')==previous['source_revision']
assert h.sha(h.WS/'MODULE.bazel.lock')==previous['module_lock_sha256']
source=h.git('diff','--binary','HEAD');assert source+'\n'==(R.parent/'datadog-clean-2026-10-25/workspace.patch').read_text()
(R/'workspace.patch').write_text(source+'\n')
p=d.p={'status':'running','started_at':h.stamp(),'target':h.TARGET,'binary_sha256':{'candidate':h.sha(CANDIDATE),'vanilla':h.sha(VANILLA)},
 'source_revision':previous['source_revision'],'module_lock_sha256':previous['module_lock_sha256'],'expected_output_sha256':d.expected,
 'remote_cache':'grpc://10.55.0.1:8080','boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
 'method':'Two preparation runs, then three rotated repetitions of candidate-on, candidate-off, preserved vanilla per minimal/toplevel mode. Same output base, remote cache, JVM/resources. Shutdown and synchronous clean, verify graph zero and absent outputs/action cache before every build. One uninterrupted WSL process. RPC log, output inventory, sampled memory on every build.',
 'source_patch_sha256':hashlib.sha256(subprocess.check_output(['git','diff','--binary','HEAD'],cwd=h.SOURCE)).hexdigest(),'runs':[]}
def run(label,mode,variant,preparation=False):
 h.BINARY=VANILLA if variant=='vanilla' else CANDIDATE
 d.run(label,mode,variant,preparation)
 row=p['runs'][-1];row['binary_sha256']=h.sha(h.BINARY)
 log=(R/(label+'.log')).read_text()
 row['execution_hits']=sum(int(a) for a,b in re.findall(r'ACTION_EXECUTION: (\d+)/(\d+)',log))
 h.save(R/'results.json',p)
try:
 run('prepare-minimal','minimal','on',True)
 run('prepare-toplevel','toplevel','on',True)
 orders=[['off','on','vanilla'],['on','vanilla','off'],['vanilla','off','on']]
 for i,order in enumerate(orders,1):
  for mode in (['minimal','toplevel'] if i%2 else ['toplevel','minimal']):
   for variant in order:run(f'r{i}-{mode}-{variant}',mode,variant)
 p['status']='passed'
except BaseException as error:p['status']='failed';p['failure']=repr(error);raise
finally:
 try:p['final_shutdown']=h.shutdown('final-shutdown')
 except Exception as error:p['shutdown_error']=repr(error)
 p['workspace_unchanged']=h.git('diff','--binary','HEAD')==source
 p['finished_at']=h.stamp();h.save(R/'results.json',p);lock.close()
