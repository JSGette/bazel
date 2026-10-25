from pathlib import Path
import os,signal,json,datetime
R=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
matches=[]
for p in Path('/proc').iterdir():
 if not p.name.isdigit():continue
 try:args=(p/'cmdline').read_bytes().split(b'\0')
 except OSError:continue
 if args[0]==str(R/'bin/skycache-large-tree-final').encode() and any(b'datadog-six-final/prepare-toplevel.bep.json' in a for a in args):matches.append(int(p.name))
assert len(matches)==1,matches
(R/'datadog-six-final/revision-stop.json').write_text(json.dumps({'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pid':matches[0],'reason':'Stop excluded preparation: bounded memo no longer rejects successes, but saturated memo causes repeated validation. Separate retained-success capacity from unchanged traversal budget and repeat with a fresh cache.'},indent=2)+'\n')
os.kill(matches[0],signal.SIGINT)
