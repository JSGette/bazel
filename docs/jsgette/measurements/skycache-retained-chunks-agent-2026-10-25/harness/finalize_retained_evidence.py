from pathlib import Path
import hashlib
import json
import re
import shutil
import subprocess

repo = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
workspace = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-25/based-on-the-latest-architecture-of')
evidence = repo / 'docs/jsgette/measurements/skycache-retained-chunks-agent-2026-10-25'
source = 'b368beb0ddde51434ce20c063546047ecccb2a88'

def sha(path):
    with path.open('rb') as data:
        return hashlib.file_digest(data, 'sha256').hexdigest()

for line in (evidence / 'SHA256SUMS').read_text().splitlines():
    digest, name = line.split('  ', 1)
    path = evidence / name
    assert path.resolve().is_relative_to(evidence.resolve())
    assert sha(path) == digest, name

# Google Java formatting changes comments/whitespace only. Preserve literal tokens.
lexer = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\s]', re.S)
def tokens(text):
    return [token for token in lexer.findall(text)
            if not token.startswith('//') and not token.startswith('/*')]

files = []
for name in [
    'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCache.java',
    'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCacheTest.java',
]:
    before = subprocess.check_output(['git', 'show', source + ':' + name], cwd=repo)
    after = (repo / name).read_bytes()
    assert tokens(before.decode()) == tokens(after.decode()), name
    files.append(dict(path=name, tested_sha256=hashlib.sha256(before).hexdigest(),
                      formatted_sha256=hashlib.sha256(after).hexdigest(),
                      java_tokens_equal=True))
(evidence / 'formatting.json').write_text(json.dumps(dict(
    tested_commit=source, note='Post-measurement Google Java formatting only; Java tokens '
    'excluding comments and whitespace match the tested source, including string literals.',
    files=files), indent=2) + '\n')
for name in ['render_retained_agent.py', 'finalize_retained_evidence.py']:
    shutil.copyfile(workspace / name, evidence / 'harness' / name)
sums = evidence / 'SHA256SUMS'
sums.write_text(''.join(sha(path) + '  ' + path.relative_to(evidence).as_posix() + '\n'
                       for path in sorted(evidence.rglob('*')) if path.is_file() and path != sums))
for line in sums.read_text().splitlines():
    digest, name = line.split('  ', 1)
    assert sha(evidence / name) == digest, name
print(json.dumps(dict(passed=True, formatting_files=len(files),
                      checksummed_files=len(sums.read_text().splitlines()))))
