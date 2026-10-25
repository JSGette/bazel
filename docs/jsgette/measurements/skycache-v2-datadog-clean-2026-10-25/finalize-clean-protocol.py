from pathlib import Path
from datetime import datetime,timedelta,timezone
import json,shutil
R=Path('/home/jgett/REPOS/.work/datadog-clean-2026-10-25')
p=json.loads((R/'results.json').read_text())
d=json.loads((R/'snapshot-diagnosis.json').read_text())
v=json.loads((R/'manifest-validation-corrected.json').read_text())
assert p['status']=='passed' and len(p['runs'])==6 and p['workspace_unchanged']
assert v['publications'][0]['first_mismatch']['path']==d['example']['path']
assert v['publications'][1]['first_mismatch'] is None
assert v['publications'][1]['checked']==142366
backup=R/'results-before-postprocessing.json';assert not backup.exists();shutil.copy2(R/'results.json',backup)
boot=datetime.fromisoformat(d['observed_at'])-timedelta(seconds=d['uptime_seconds'])
start=datetime.fromisoformat(next(r['started_at'] for r in p['runs'] if r['label']=='r2-on'))
p['limitations'][0]='OS page cache and WSL runtime history were not controlled. Existing external repository downloads were retained. The observed WSL boot preceded r2-on by approximately '+str(round((start-boot).total_seconds()))+' seconds; restored-run analysis timing varied substantially.'
p['postprocessing_events']=[
 {'at':datetime.now(timezone.utc).isoformat(),'event':'Verified the old manifest mismatch at the Python pycache symlink and all 142366 replacement witnesses. Initial standalone decoder output is retained; it incorrectly represented an empty root-relative path as a dot. Correcting the read-only decoder resolved that artifact. No benchmark input changed.'},
 {'event':'Recorded uncontrolled WSL/runtime and OS-cache history as a measurement limitation. Boot time estimated from observed UTC time minus /proc/uptime.','estimated_wsl_boot_utc':boot.isoformat(),'r2_on_started_utc':start.isoformat(),'seconds_between_boot_and_r2_on':(start-boot).total_seconds()}]
(R/'results.json').write_text(json.dumps(p,indent=2)+'\n')
print(json.dumps(p['postprocessing_events'],indent=2))
