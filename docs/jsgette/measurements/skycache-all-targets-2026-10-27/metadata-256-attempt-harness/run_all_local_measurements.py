from pathlib import Path
import subprocess
import sys
from skycache_all_common import ROOT,save

work=Path(__file__).parent
steps=[['run_all_with_priming.py','--tag','disk-action-prime','--no-followup','--fresh-preparation'],
       ['skycache_all_benchmark.py','--mode','prime-local','--tag','local-prime'],
       ['skycache_all_benchmark.py','--mode','measure','--tag','local-matrix',
        '--arms','vanilla','disabled','local-warm','local-cold'],
       ['analyze_all_measurements.py','--tag','local-matrix']]
for i,step in enumerate(steps):
    save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='running',cohort='local-matrix'))
    result=subprocess.run([sys.executable,str(work/step[0])]+step[1:])
    if result.returncode:
        save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='failed',exit_code=result.returncode,cohort='local-matrix'))
        raise SystemExit(result.returncode)
save(ROOT/'pipeline-status.json',dict(status='passed',cohort='local-matrix'))
