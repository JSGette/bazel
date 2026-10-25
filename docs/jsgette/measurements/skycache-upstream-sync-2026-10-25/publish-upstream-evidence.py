from pathlib import Path
import json,subprocess,hashlib,shutil,tarfile
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
D=Path('/mnt/e/scw/docs/docs/jsgette/measurements/skycache-upstream-sync-2026-10-25')
assert json.loads((R/'datadog-six-stable/results.json').read_text())['status']=='passed'
assert json.loads((R/'minimal-profile/results.json').read_text())['status']=='passed'
assert json.loads((R/'hit-profile/results.json').read_text())['status']=='passed'
for p in sorted((T/'work').glob('*upstream*.py')):shutil.copy2(p,R/p.name)
for name in ['prepare-sync-probes.py','run-sync-probes.py','promote-upstream.py','stop-sync-mixed-benchmark.py','verify-sync-artifacts.py','compare-sync-witnesses.py','explain-sync-witness-change.py']:
 shutil.copy2(T/'work'/name,R/name)
private=Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-upstream-sync-evidence.tar')
assert not private.exists()
skip={'build','build-vanilla','tmp'}
roots=[p for p in sorted(R.iterdir()) if p.name not in skip]
with tarfile.open(private,'w') as tar:
 for p in roots:
  tar.add(p,arcname=p.relative_to(R),recursive=True)
with private.open('rb') as f:archive_hash=hashlib.file_digest(f,'sha256').hexdigest()
count=0
with tarfile.open(private) as tar:
 for member in tar:
  if not member.isfile():continue
  p=R/member.name
  with p.open('rb') as f:expected=hashlib.file_digest(f,'sha256').hexdigest()
  assert hashlib.file_digest(tar.extractfile(member),'sha256').hexdigest()==expected,member.name
  count+=1
(R/'archive-verification.json').write_text(json.dumps(dict(path=str(private),bytes=private.stat().st_size,sha256=archive_hash,verified_file_members=count,excluded_build_outputs=sorted(skip)),indent=2)+'\n')
D.mkdir(exist_ok=False)
allowed={'.json','.jsonl','.log','.py','.patch','.txt','.xml'}
files=[p for p in R.iterdir() if p.is_file() and p.suffix in allowed and not p.name.endswith('.bep.json')]
for name in ['datadog-six','datadog-six-stable','execution','worktrees','minimal-profile','hit-profile','unit-test-results']:
 files += [p for p in (R/name).iterdir() if p.is_file() and p.suffix in allowed and not p.name.endswith('.bep.json') and not p.name.endswith('jfr-samples.json')]
raw=[]
for p in files:
 dest=D/p.relative_to(R);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
for p in R.rglob('*'):
 if p.is_file() and p.suffix in ('.bin','.gz','.jfr') and not any(x in p.relative_to(R).parts for x in skip):
  with p.open('rb') as f:d=hashlib.file_digest(f,'sha256').hexdigest()
  raw.append(dict(path=str(p.relative_to(R)),bytes=p.stat().st_size,sha256=d))
(D/'raw-evidence-hashes.json').write_text(json.dumps(raw,indent=2)+'\n')
files=sorted(p for p in D.rglob('*') if p.is_file());lines=[]
for p in files:
 with p.open('rb') as f:d=hashlib.file_digest(f,'sha256').hexdigest()
 lines.append(d+'  '+str(p.relative_to(D)))
(D/'SHA256SUMS').write_text('\n'.join(lines)+'\n')
print(json.dumps(dict(public_files=len(files)+1,public_bytes=sum(p.stat().st_size for p in files),archive_sha256=archive_hash,archive_bytes=private.stat().st_size),indent=2))
