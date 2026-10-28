from pathlib import Path
import subprocess,fcntl
b=Path('/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence')
r=b.parent/'skycache-analysis-foundation-2026-10-28'
script=r/'docs/jsgette/measurements/skycache-unified-analysis-2026-10-28/compare_three_arms.py'
common=['python3',str(script),'--vanilla-bazel','/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25/bin/vanilla-upstream',
 '--vanilla-source-repo','/home/jgett/REPOS/bazel-upstream-vanilla-2026-10-25','--vanilla-source-commit','a5c50fcb65841b505ec72840d83381a704e70afa',
 '--current-bazel',str(b/'actions-green-01-bazel'),'--current-source-repo',str(r),'--current-source-commit','3afc50c9766916072de1ae99d9726e61cad56feb',
 '--heap-mb','2048','--jobs','6','--timeout-seconds','180']
for fixture,name in [('actions4096/overlapped.json','compare-actions-quiet-01'),('dag4096-retained/broad.json','compare-provider-quiet-01')]:
 cmd=common+['--manifest',str(b/fixture),'--run-dir',str(b/name)]
 with (b.parent/'skyframe-measurements.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  print('Starting '+name,flush=True)
  subprocess.run(cmd,check=True)
  print('Passed '+name,flush=True)
