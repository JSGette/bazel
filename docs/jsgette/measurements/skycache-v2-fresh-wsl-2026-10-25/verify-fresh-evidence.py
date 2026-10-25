from pathlib import Path
import hashlib,json,py_compile,re
D=Path('/home/jgett/REPOS/bazel-skyframe-findings')
E=D/'docs/jsgette/measurements/skycache-v2-fresh-wsl-2026-10-25'
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
for name in ['datadog-fresh-benchmark.py','analyze-fresh-benchmark.py','publish-fresh-evidence.py','render-fresh-report.py','inspect-fresh-profiles.py']:
 py_compile.compile(str(T/'work'/name),doraise=True)
hashes=(E/'SHA256SUMS').read_text().splitlines()
for line in hashes:
 h,name=line.split('  ',1);assert hashlib.sha256((E/name).read_bytes()).hexdigest()==h,name
secret=re.compile(r'(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Bearer\s+[A-Za-z0-9_.-]{20,}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----)')
suspect=[]
for f in E.rglob('*'):
 if f.is_file() and secret.search(f.read_text(errors='replace')):suspect.append(str(f.relative_to(E)))
assert not suspect,'Potential credentials in '+repr(suspect)
p=json.loads((E/'results.json').read_text());s=json.loads((E/'summary.json').read_text())
assert p['status']==s['status']=='passed'
assert not any('bep.json'==f.name[-8:] for f in E.rglob('*'))
print(json.dumps({'verified_hashes':len(hashes),'script_syntax':'passed','credential_pattern_scan':'passed','measured_builds':s['measured_builds'],'successful_builds':s['successful_builds'],'output_sha256':s['output_sha256']}))
