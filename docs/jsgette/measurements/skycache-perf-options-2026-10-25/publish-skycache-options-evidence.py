from pathlib import Path
import json,subprocess,shutil,tarfile,hashlib
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
D=Path('/mnt/e/scw/docs/docs/jsgette/measurements/skycache-perf-options-2026-10-25')
assert not D.exists()
for p in (T/'work').glob('*skycache*options*.py'):shutil.copy2(p,R/p.name)
for name in ['analyze-skycache-allocations.py','prototype-skycache-lookups.py','prototype-skycache-array-decode.py','finalize-options-build.py','compare-options-witnesses.py','check-options-toolchains.py','datadog-wide.py','datadog-concurrent.py','datadog-download-modes.py','datadog-clean-benchmark.py']:
 shutil.copy2(T/'work'/name,R/name)
private=Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-perf-options-evidence.tar');assert not private.exists()
roots=sorted(R.iterdir())
with tarfile.open(private,'w') as archive:
 for p in roots:archive.add(p,arcname=p.relative_to(R),recursive=True)
with private.open('rb') as f:archive_hash=hashlib.file_digest(f,'sha256').hexdigest()
count=0
with tarfile.open(private) as archive:
 for member in archive:
  if not member.isfile():continue
  with (R/member.name).open('rb') as f:expected=hashlib.file_digest(f,'sha256').hexdigest()
  assert hashlib.file_digest(archive.extractfile(member),'sha256').hexdigest()==expected,member.name
  count+=1
(R/'archive-verification.json').write_text(json.dumps(dict(path=str(private),bytes=private.stat().st_size,sha256=archive_hash,verified_regular_members=count,note='Includes prototype binaries, immutable stores, JFR, raw gRPC/BEP and all excluded attempts. Reused source build output base is outside this evidence root and excluded.'),indent=2)+'\n')
D.mkdir()
extensions={'.json','.jsonl','.log','.py','.patch','.txt','.xml'}
dirs=['lookup-reuse','lookup-reuse-fixed','controlled','profiles','lookup-reuse-tests','lookup-array-tests']
files=[p for p in R.iterdir() if p.is_file()]
for dirname in dirs:files += [p for p in (R/dirname).rglob('*') if p.is_file()]
raw=[]
for p in files:
 if p.suffix in extensions and not (p.name.endswith('.bep.json') or p.name.endswith('-events.json')):
  dest=D/p.relative_to(R);dest.parent.mkdir(exist_ok=True,parents=True);shutil.copy2(p,dest)
 elif p.suffix in {'.bin','.gz','.jfr'} or p.name.endswith(('.bep.json','-events.json')):
  with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
  raw.append(dict(path=p.relative_to(R).as_posix(),bytes=p.stat().st_size,sha256=digest))
(D/'private-raw-hashes.json').write_text(json.dumps(raw,indent=2)+'\n')
files=sorted(p for p in D.rglob('*') if p.is_file());lines=[]
for p in files:
 with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
 lines.append(digest+'  '+p.relative_to(D).as_posix())
(D/'SHA256SUMS').write_text('\n'.join(lines)+'\n')
print(json.dumps(dict(public_files=len(files)+1,public_bytes=sum(p.stat().st_size for p in files),archive_bytes=private.stat().st_size,archive_sha256=archive_hash),indent=2))
