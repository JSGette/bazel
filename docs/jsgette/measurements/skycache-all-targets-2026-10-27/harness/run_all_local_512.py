from pathlib import Path
import argparse
import subprocess
import sys
from skycache_all_common import ROOT,save

work=Path(__file__).parent
parser=argparse.ArgumentParser();parser.add_argument('--tag',default='local-matrix-512-heap14')
parser.add_argument('--arms',nargs='+',default=['vanilla','disabled','local-warm','local-cold'])
args=parser.parse_args()
steps=[['skycache_all_benchmark.py','--mode','measure','--tag',args.tag,
        '--arms']+args.arms,
       ['analyze_all_measurements.py','--tag',args.tag]]
for i,step in enumerate(steps):
    save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='running',cohort=args.tag))
    result=subprocess.run([sys.executable,str(work/step[0])]+step[1:])
    if result.returncode:
        save(ROOT/'pipeline-status.json',dict(step=i,argv=step,status='failed',exit_code=result.returncode,cohort=args.tag))
        raise SystemExit(result.returncode)
save(ROOT/'pipeline-status.json',dict(status='passed',cohort=args.tag))
