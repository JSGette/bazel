from pathlib import Path
import json
import subprocess
import sys
from skycache_all_common import ROOT,save

work=Path(__file__).parent
steps=[['skycache_all_benchmark.py','--mode','prime-remote','--tag','cache-prime-5'],
       ['skycache_all_benchmark.py','--mode','stabilize','--tag','cache-stabilize'],
       ['prepare_all_seed.py'],
       ['skycache_all_benchmark.py','--mode','measure','--tag','matrix'],
       ['analyze_all_measurements.py']]
for i,step in enumerate(steps):
    save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='running'))
    result=subprocess.run([sys.executable,str(work/step[0])]+step[1:])
    if result.returncode:
        save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='failed',exit_code=result.returncode))
        raise SystemExit(result.returncode)
save(ROOT/'pipeline-status.json',dict(status='passed'))
