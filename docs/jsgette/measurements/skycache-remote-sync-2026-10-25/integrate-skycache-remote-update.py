from pathlib import Path
import json, subprocess, datetime
S = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
REMOTE = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25/update')
R.mkdir(exist_ok=False)
p = {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'commands': []}
def run(cwd, *args, allowed=(0,)):
    c = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    p['commands'].append(dict(cwd=str(cwd), argv=args, exit_code=c.returncode, output=c.stdout))
    (R / 'integration.json').write_text(json.dumps(p, indent=2) + '\n')
    assert c.returncode in allowed, c.stdout
    return c.stdout.strip()
assert not run(S, 'git', 'status', '--porcelain')
p['base'] = run(S, 'git', 'rev-parse', 'HEAD')
assert p['base'].startswith('0c80d7fe68')
p['remote_snapshot'] = run(REMOTE, 'git', 'rev-parse', 'HEAD')
assert p['remote_snapshot'].startswith('91d51164d3')
p['remote_status'] = run(REMOTE, 'git', 'status', '--porcelain')
run(S, 'git', 'merge', '--no-ff', '--no-commit', p['remote_snapshot'], allowed=(0, 1))
p['unmerged'] = run(S, 'git', 'diff', '--name-only', '--diff-filter=U')
p['status'] = run(S, 'git', 'status', '--short')
(R / 'integration.json').write_text(json.dumps(p, indent=2) + '\n')
print(json.dumps({k:v for k,v in p.items() if k != 'commands'}, indent=2))
