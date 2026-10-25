from pathlib import Path
import subprocess, json, datetime

BASE = Path('/home/jgett/REPOS')
MAIN = BASE / 'bazel-skycache-v2'
WORK = BASE / 'bazel-skycache-upstream-2026-10-25'
REMOTE = BASE / 'bazel-skycache-remote-mvp'
RUN = BASE / '.work/skycache-remote-sync-2026-10-25'
RUN.mkdir(exist_ok=False)
protocol = {'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'commands': []}

def run(cwd, *args, allowed=(0,)):
    p = subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    protocol['commands'].append(dict(cwd=str(cwd), argv=args, exit_code=p.returncode, output=p.stdout))
    (RUN / 'integration.json').write_text(json.dumps(protocol, indent=2) + '\n')
    if p.returncode not in allowed:
        raise RuntimeError(p.stdout)
    return p.stdout.strip()

for name, path in [('main', MAIN), ('integration', WORK), ('remote', REMOTE)]:
    assert run(path, 'git', 'status', '--porcelain') == '', name
    protocol[name + '_before'] = run(path, 'git', 'rev-parse', 'HEAD')
assert protocol['main_before'].startswith('1aac2aa2ec')
assert protocol['integration_before'].startswith('36a73d8fb4')
assert protocol['remote_before'].startswith('6a380e6eeb')
run(MAIN, 'git', 'merge', '--ff-only', protocol['integration_before'])
run(WORK, 'git', 'switch', '-c', 'jsgette/skycache-remote-sync-2026-10-25')
run(WORK, 'git', 'merge', '--no-ff', '--no-commit', protocol['remote_before'], allowed=(0, 1))
protocol['merge_status'] = run(WORK, 'git', 'status', '--short')
protocol['unmerged'] = run(WORK, 'git', 'diff', '--name-only', '--diff-filter=U')
(RUN / 'integration.json').write_text(json.dumps(protocol, indent=2) + '\n')
print(json.dumps({k: v for k, v in protocol.items() if k != 'commands'}, indent=2))
