from pathlib import Path
import hashlib
import json
import re
import subprocess
import sys

root = Path(sys.argv[1])
source = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
prior = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25/update')
build = json.loads((prior / 'build.json').read_text())
worker = Path(build['worker'])
classpath = re.search(r'^  CLASSPATH="(\$\{RUNPATH\}[^"\n]+)"', worker.read_text(), re.M)[1]
paths = [Path(str(worker) + '.runfiles') / '_main' / p.removeprefix('${RUNPATH}')
         for p in classpath.split(':')]
entries = [{'path': str(p), 'bytes': p.stat().st_size,
            'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths]
destination = root / 'provenance.json'
if sys.argv[2:] == ['after']:
    data = json.loads(destination.read_text())
    data['worker_classpath_after'] = entries
    data['worker_classpath_unchanged'] = entries == data['worker_classpath_before']
    assert data['worker_classpath_unchanged']
else:
    assert not destination.exists()
    data = {
        'source_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip(),
        'source_status': subprocess.check_output(['git', 'status', '--porcelain'], cwd=source, text=True),
        'tested_product_commit': '6931630c83d9222049e30bf7c50190ae810519f5',
        'prior_build': build,
        'worker_classpath_before': entries,
    }
    assert not data['source_status']
destination.write_text(json.dumps(data, indent=2) + '\n')
print(json.dumps({'source': data['source_commit'], 'worker_jars': len(entries),
                  'worker_classpath_unchanged': data.get('worker_classpath_unchanged')}))
