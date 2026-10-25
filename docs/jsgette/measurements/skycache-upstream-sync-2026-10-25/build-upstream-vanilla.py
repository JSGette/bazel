from pathlib import Path
import json,subprocess,os,time,hashlib
RUN=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
R=Path('/home/jgett/REPOS/bazel-upstream-vanilla-2026-10-25')
args=json.loads((RUN/'build-tests-2.command.json').read_text())
args=[a.replace(str(RUN/'build'),str(RUN/'build-vanilla')) if a.startswith('--output_base=') else a for a in args]
args=[a for a in args if not a.startswith('//src/test/')]
args=['build' if a=='test' else a for a in args]
args=[a.replace('build-tests-2','build-vanilla') for a in args]
t=time.monotonic()
(RUN/'build-vanilla.command.json').write_text(json.dumps(args,indent=2)+'\n')
with (RUN/'build-vanilla.log').open('x') as f:r=subprocess.run(args,cwd=R,env=dict(os.environ,TMPDIR=str(RUN/'tmp')),stdout=f,stderr=subprocess.STDOUT)
(RUN/'build-vanilla.json').write_text(json.dumps(dict(argv=args,seconds=time.monotonic()-t,exit_code=r.returncode),indent=2)+'\n')
if r.returncode:raise SystemExit(r.returncode)
dest=RUN/'bin/vanilla-upstream';dest.parent.mkdir(exist_ok=True)
dest.write_bytes((R/'bazel-bin/src/bazel-dev').read_bytes());dest.chmod(0o755)
print('Vanilla binary SHA256',hashlib.sha256(dest.read_bytes()).hexdigest(),flush=True)
subprocess.run(args[:args.index('build')]+['shutdown'],cwd=R,check=True)
