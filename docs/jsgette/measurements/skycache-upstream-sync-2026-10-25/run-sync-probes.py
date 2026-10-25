from pathlib import Path
import subprocess,json,time,os
RUN=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
JDK='/home/jgett/REPOS/.tools/skycache-benchmark-jdk25'
env=dict(os.environ,SKYCACHE_WINDOWS_STORAGE_SNAPSHOT='/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work/datadog-storage-live.json')
for name,script in [('execution','local-execution-probe.py'),('worktrees','local-worktree-probe-segmented.py')]:
 args=['python3',str(RUN/script),'--bazel',str(RUN/'bin/skycache-upstream'),'--jdk',JDK,'--root',str(RUN/name)]
 t=time.monotonic()
 with (RUN/(name+'.runner.log')).open('x') as f:r=subprocess.run(args,env=env,stdout=f,stderr=subprocess.STDOUT)
 (RUN/(name+'.runner.json')).write_text(json.dumps(dict(argv=args,seconds=time.monotonic()-t,exit_code=r.returncode),indent=2)+'\n')
 print(name,r.returncode,flush=True)
 if r.returncode:raise SystemExit(r.returncode)
