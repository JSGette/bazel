from pathlib import Path
import subprocess, json, os, time, shutil, fcntl, sys

S = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
OLD = Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
lock = (OLD.parent / 'skyframe-measurements.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
env = dict(os.environ, SKYCACHE_WINDOWS_STORAGE_SNAPSHOT='/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex/work/datadog-storage-live.json')
binary = str(R / 'bin/bazel-remote-sync')
jdk = '/home/jgett/REPOS/.tools/skycache-benchmark-jdk25'
worker = json.loads((R / 'build.json').read_text())['worker']
probes = [
    ('remote', S / 'tools/skycache/remote_mvp_probe.py', ['--bazel', binary, '--worker', worker]),
    ('execution', OLD / 'local-execution-probe.py', ['--bazel', binary, '--jdk', jdk]),
    ('worktrees', OLD / 'local-worktree-probe-segmented.py', ['--bazel', binary, '--jdk', jdk]),
]
results = []
try:
    for name, source, arguments in probes:
        script = R / source.name
        shutil.copy2(source, script)
        args = ['python3', str(script)] + arguments + ['--root', str(R / name)]
        start = time.monotonic()
        with (R / (name + '.runner.log')).open('x') as log:
            p = subprocess.run(args, env=env, stdout=log, stderr=subprocess.STDOUT)
        row = dict(name=name, argv=args, exit_code=p.returncode, seconds=time.monotonic() - start,
                   boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip())
        results.append(row)
        (R / 'probes.json').write_text(json.dumps(results, indent=2) + '\n')
        print(json.dumps(row), flush=True)
        if p.returncode:
            raise SystemExit(p.returncode)
finally:
    lock.close()
