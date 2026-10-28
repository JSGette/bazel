from pathlib import Path
import subprocess,fcntl
b=Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence')
r=b.parent/'skycache-analysis-foundation-2026-10-28'
for name in ('actions','provider'):
 batch=b/('compare-'+name+'-quiet-01')
 cmd=['python3',str(r/'docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/measure_retained_server.py'),'--vanilla-bazel','/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25/bin/vanilla-upstream','--candidate-bazel',str(b/'actions-green-01-bazel'),'--workspace',str(batch/'workspace'),'--seed',str(batch/'caches/producer-seed'),'--manifest',str(batch/'workspace'/('overlapped.json' if name=='actions' else 'broad.json')),'--run-dir',str(b/('retained-'+name+'-02')),'--heap-mb','2048','--jobs','6','--jcmd','/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence/comparison-preflight-01/bazel-user/install/6b927db60252bed5c19643ba12d075ec/embedded_tools/jdk/bin/jcmd']
 with (b.parent/'skyframe-measurements.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  print('Starting retained '+name,flush=True)
  subprocess.run(cmd,check=True)
  print('Passed retained '+name,flush=True)
