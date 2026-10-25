from pathlib import Path
import subprocess
TASK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work')
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25/update')
for script, extra in [('probe-skycache-remote-sync.py',[str(R)]), ('benchmark-skycache-remote-update.py',[])]:
    with (R/(script+'.runner.log')).open('x') as log:
        p = subprocess.run(['python3',str(TASK/script),*extra], stdout=log,stderr=subprocess.STDOUT)
    print(script, p.returncode, flush=True)
    if p.returncode: raise SystemExit(p.returncode)
