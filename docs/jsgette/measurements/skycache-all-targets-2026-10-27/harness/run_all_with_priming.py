"""Run resumed preparation, then the existing measurement pipeline in one environment."""
import argparse
import subprocess
import sys
import time
from pathlib import Path
from skycache_all_common import ROOT, save

parser=argparse.ArgumentParser();parser.add_argument('--tag',required=True)
parser.add_argument('--no-followup',action='store_true');parser.add_argument('--fresh-preparation',action='store_true')
args=parser.parse_args()
work=Path(__file__).parent
save(ROOT/'pipeline-status.json',dict(status='preparing_actions',tag=args.tag))
process=subprocess.Popen([sys.executable,str(work/'skycache_all_benchmark.py'),
    '--mode','prime-actions','--tag',args.tag]+([] if args.fresh_preparation else ['--resume-priming']))
trimmed=False
while process.poll() is None:
    log=ROOT/(args.tag+'-disabled.log')
    if not trimmed and log.exists() and 'INFO: Analyzed ' in log.read_text():
        time.sleep(5)
        if process.poll() is None:
            subprocess.run([sys.executable,str(work/'trim_all_priming_heap.py'),args.tag],check=True)
        trimmed=True
    time.sleep(2)
if process.returncode:
    save(ROOT/'pipeline-status.json',dict(status='preparation_failed',tag=args.tag,exit_code=process.returncode))
    raise SystemExit(process.returncode)
if args.no_followup:
    print('Action-cache preparation passed.',flush=True)
    raise SystemExit(0)
print('Preparation passed; starting cache seeding and matched measurements.',flush=True)
raise SystemExit(subprocess.run([sys.executable,str(work/'run_all_measurements.py')]).returncode)
