from pathlib import Path
import subprocess,sys
root=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26')
work=Path(__file__).parent
for cohort in ['warm','cold']:
 command=[sys.executable,str(work/'skycache_slots_benchmark.py'),'--root',str(root/('slots-'+cohort)),
  '--cohort',cohort,'--candidate',str(root/'build-slots-v2/bazel'),'--candidate128','/home/jgett/REPOS/.work/skycache-channels-2026-10-26/build-128-v2/bazel']
 subprocess.run(command,check=True)
 subprocess.run([sys.executable,str(work/'analyze_digests.py'),'slots-'+cohort],check=True)
