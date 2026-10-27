from pathlib import Path
import subprocess,sys
WORK=Path(__file__).parent
ROOT=Path('/home/jgett/REPOS/.work/skycache-retention-2026-10-27')
BASE='/home/jgett/REPOS/.work/skycache-digests-2026-10-26/build-slots-v2/bazel'
for cohort,folder in [('cold','cold'),('warm','warm'),('diagnostic','diagnostic-after')]:
 subprocess.run([sys.executable,str(WORK/'skycache_retention_benchmark.py'),'--root',str(ROOT/folder),
  '--cohort',cohort,'--candidate',str(ROOT/'build-advisory/bazel'),'--candidate128',BASE],check=True)
 subprocess.run([sys.executable,str(WORK/'analyze_retention.py'),folder],check=True)
