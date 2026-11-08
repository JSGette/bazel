import hashlib
import json
from pathlib import Path
import subprocess

gitdir = '/mnt/c/Users/jgett/Documents/Codex/2026-10-31/start-contemplating-a-new-design-based/bazel-source/.git'
revision = 'c4662a87d4'
relative = 'docs/jsgette/measurements/shared-evaluation-2026-11-07/harness/'
root = Path('/mnt/shared-evaluation-20261107/harness-diagnostics-v1')
files = ['baseline.py', 'fixture.py', 'output_bases.py', 'm1_resources.py', 'm0_diagnostics.py']
payloads = {name: subprocess.check_output(['git', '--git-dir', gitdir, 'show', revision + ':' + relative + name]) for name in files}
expected = {
    'baseline.py': 'f418157ada895e181f75c43cd4c7c05e69077598013b9097420f650fa6c6f906',
    'fixture.py': '2de503bc8d4b5e7e1817b5a73e9bb78c1d7bacc56f5ae19847b8f686ebaff78a',
    'output_bases.py': 'd63dd9b7ba66cb88b5abcf914e835a8a2bf0bad9e6aa94f6203ab884caa3bbae',
    'm0_diagnostics.py': '5f754c50f941d99f88f41c47d428f2f5d54a0ff04a589b12a4ab8252074a864c',
}
hashes = {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()}
for name, digest in expected.items():
    assert hashes[name] == digest, (name, hashes[name])
root.mkdir()
for name, data in payloads.items():
    with (root / name).open('xb') as out:
        out.write(data)
record = {'revision': revision, 'root': str(root), 'sha256': hashes}
(root / 'bundle.json').write_text(json.dumps(record, indent=2) + '\n')
print(json.dumps(record))
