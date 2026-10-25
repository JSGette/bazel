from pathlib import Path
import json,subprocess,time
RUN=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
R=Path('/home/jgett/REPOS/bazel-skycache-v2')
for name in ['execution','worktrees']:
 p=json.loads((RUN/name/'results.json').read_text());assert p.get('status')=='passed' or p.get('passed') is True
assert not subprocess.check_output(['git','status','--porcelain'],cwd=R,text=True).strip()
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()=='d4971808785aaa3cd46bf6dd1419cb65afb4319e'
args=['git','merge','--ff-only','1aac2aa2ec6fe31af2e979558f06a3b6971c601b']
p=subprocess.run(args,cwd=R,capture_output=True,text=True)
(RUN/'promote.log').write_text(p.stdout+p.stderr)
(RUN/'promote.json').write_text(json.dumps(dict(argv=args,cwd=str(R),exit_code=p.returncode,time=time.time()),indent=2)+'\n')
p.check_returncode();print('Main implementation branch fast-forwarded to 1aac2aa2ec.')
