from pathlib import Path
import subprocess,fcntl
base=Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence')
repo=base.parent/'skycache-analysis-foundation-2026-10-28'
harness=repo/'docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/paired_baseline.py'
for binary,source,manifest,name in [
 ('actions-green-01-bazel',repo,'broad','actions-candidate-broad-01'),
 ('retention-green-02-bazel',base.parent/'skycache-foundation-control-2026-10-28','overlapped','actions-control-overlapped-01'),
 ('actions-green-01-bazel',repo,'overlapped','actions-candidate-overlapped-01')]:
    command=['python3',str(harness),'--bazel',str(base/binary),'--source-repo',str(source),
             '--source-commit','f5552db2b0758cbcadcdd6fd8e8241f4919f845e','--manifest',str(base/'actions4096'/(manifest+'.json')),
             '--run-dir',str(base/name),'--pairs','2']
    with (base.parent/'skyframe-measurements.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        result=subprocess.run(command)
        print(name,result.returncode,flush=True)
        if result.returncode: raise SystemExit(result.returncode)
