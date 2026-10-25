from pathlib import Path
import fcntl
import hashlib
import json
import shutil
import subprocess
import datetime

work = Path('/home/jgett/REPOS/.work')
repo = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
root = work / 'skycache-remote-mvp/comparison-local-remote-vanilla-1'
lock = (work / 'skyframe-measurements.lock').open('a')
print('Waiting for shared measurement lock', flush=True)
fcntl.flock(lock, fcntl.LOCK_EX)
root.mkdir(exist_ok=False)
binary_dir = root / 'bin'
binary_dir.mkdir()
sources = {
    'skycache': (work / 'skycache-remote-sync-2026-10-25/update/bin/bazel-remote-sync',
                 '518de2101dbf40346f17200a520d3ce4dfd65a998f5909c537f3dd2a72689d3f'),
    'vanilla': (work / 'skycache-upstream-sync-2026-10-25/bin/vanilla-upstream',
                '34a95ea73a865b8382e433ec616369fec24e51e69f7c02c148dd30097dd5863b'),
}
for name, (source, expected) in sources.items():
    target = binary_dir / name
    shutil.copy2(source, target)
    assert hashlib.sha256(target.read_bytes()).hexdigest() == expected, name
worker = work / 'skycache-remote-mvp/build/execroot/_main/bazel-out/k8-fastbuild/bin/src/tools/remote/worker'
assert hashlib.sha256(worker.read_bytes()).hexdigest() == 'a8c63fbe7191f5c17db30e7ceed4e6c75d8d311510077b9ebd3c01ff89084d23'
tools = Path('/home/jgett/.cache/bazel/_bazel_jgett/install/30645dc200dd3c4da7733204f17aaae1/embedded_tools')
provenance = {
    'skycache_commit': '6931630c83d9222049e30bf7c50190ae810519f5',
    'vanilla_commit': 'a5c50fcb65841b505ec72840d83381a704e70afa',
    'sources': {name: {'path': str(path), 'sha256': digest} for name, (path, digest) in sources.items()},
    'worker': str(worker),
    'common_tools': str(tools),
    'measurement_lock': str(lock.name),
    'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'runs': [],
}
script = repo / 'tools/skycache/remote_read_benchmark.py'
provenance['harness_sha256'] = hashlib.sha256(script.read_bytes()).hexdigest()
path = root / 'provenance.json'
def save():
    path.write_text(json.dumps(provenance, indent=2) + '\n')
save()
try:
    for name, selected in [('narrow', 16), ('wide', 512)]:
        argv = ['python3', str(script), '--vanilla-bazel', str(binary_dir / 'vanilla'),
                '--new-bazel', str(binary_dir / 'skycache'), '--tools-dir', str(tools),
                '--worker', str(worker), '--root', str(root / name),
                '--packages', '512', '--selected', str(selected), '--repetitions', '3']
        print('Running ' + name, flush=True)
        with (root / (name + '.runner.log')).open('w') as log:
            result = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT)
        provenance['runs'].append({'scope': name, 'argv': argv, 'exit_code': result.returncode})
        save()
        assert result.returncode == 0, name + ' failed; inspect runner log'
        report = json.loads((root / name / 'results.json').read_text())
        assert report['passed'], report.get('failure')
        print(json.dumps({'scope': name, 'summary': report['summary']}), flush=True)
    provenance['passed'] = True
finally:
    provenance['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save()
    lock.close()
