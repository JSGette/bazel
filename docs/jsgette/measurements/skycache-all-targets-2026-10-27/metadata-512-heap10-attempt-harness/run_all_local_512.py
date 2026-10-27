from pathlib import Path
import subprocess
import sys
from skycache_all_common import ROOT,save

work=Path(__file__).parent
steps=[['skycache_all_benchmark.py','--mode','measure','--tag','local-matrix-512',
        '--arms','vanilla','disabled','local-warm','local-cold'],
       ['analyze_all_measurements.py','--tag','local-matrix-512']]
for i,step in enumerate(steps):
    save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='running',cohort='local-matrix-512'))
    result=subprocess.run([sys.executable,str(work/step[0])]+step[1:])
    if result.returncode:
        save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='failed',exit_code=result.returncode,cohort='local-matrix-512'))
        raise SystemExit(result.returncode)
save(ROOT/'pipeline-status.json',dict(status='passed',cohort='local-matrix-512'))
