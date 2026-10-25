from pathlib import Path
import json, subprocess, shutil, tarfile, hashlib

R=Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25')
S=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
D=Path('/mnt/e/scw/docs')
E=D/'docs/jsgette/measurements/skycache-remote-sync-2026-10-25'
assert not E.exists()
assert json.loads((R/'update/datadog-six/results.json').read_text())['status']=='passed'
for p in (T/'work').glob('*skycache-remote*.py'):shutil.copy2(p,R/p.name)
for name in ['record-skycache-lifetime-review.py','check-remote-sync-tools.py','datadog-wide.py','datadog-concurrent.py','datadog-download-modes.py','datadog-clean-benchmark.py']:
    shutil.copy2(T/'work'/name,R/name)
shutil.copy2(S/'tools/skycache/storage_preflight.py',R/'storage_preflight.py')

source=json.loads((R/'update/source.json').read_text())
remote_names=subprocess.check_output(['git','ls-tree','-r','--name-only',source['remote_documentation'],'--','docs/jsgette'],cwd=S,text=True).splitlines()
remote_names=[x for x in remote_names if x=='docs/jsgette/skycache-remote-mvp.md' or x.startswith('docs/jsgette/measurements/skycache-remote-')]
remote_files=[]
for name in remote_names:
    data=subprocess.check_output(['git','show',source['remote_documentation']+':'+name],cwd=S)
    dest=D/name
    if dest.exists():assert dest.read_bytes().replace(b'\r\n',b'\n')==data, name
    dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
    remote_files.append(dict(path=name,sha256=hashlib.sha256(data).hexdigest()))
(R/'remote-documents.json').write_text(json.dumps(dict(commit=source['remote_documentation'],files=remote_files),indent=2)+'\n')

archive_path=Path('/mnt/d/Codex/wsl-reset-2026-10-25/skycache-remote-sync-evidence.tar')
assert not archive_path.exists()
progress=T/'work/skycache-remote-sync-archive-progress.json'
def mark(**kwargs):progress.write_text(json.dumps(kwargs,indent=2)+'\n')
roots=sorted(R.iterdir())
mark(phase='creating',archive=str(archive_path))
print('Creating private evidence archive.',flush=True)
with tarfile.open(archive_path,'w') as archive:
    for i,p in enumerate(roots):
        archive.add(p,arcname=p.relative_to(R),recursive=True)
        mark(phase='creating',completed_roots=i+1,total_roots=len(roots),current=p.name,bytes=archive_path.stat().st_size)
mark(phase='verifying',bytes=archive_path.stat().st_size)
print('Verifying archive contents and SHA-256.',flush=True)
with archive_path.open('rb') as f:archive_hash=hashlib.file_digest(f,'sha256').hexdigest()
count=0
with tarfile.open(archive_path) as archive:
    for member in archive:
        if not member.isfile():continue
        with (R/member.name).open('rb') as f:expected=hashlib.file_digest(f,'sha256').hexdigest()
        assert hashlib.file_digest(archive.extractfile(member),'sha256').hexdigest()==expected,member.name
        count+=1
        if count%1000==0:mark(phase='verifying',verified_regular_members=count,bytes=archive_path.stat().st_size)
record=dict(path=str(archive_path),bytes=archive_path.stat().st_size,sha256=archive_hash,verified_regular_members=count,
            note='Contains both merge iterations, binaries, caches, raw gRPC/BEP/profiles, correctness fixtures and excluded tools preflight. Source-build output base and worker runfiles are outside this root; reproducible source patches and worker launcher digest are recorded. Hard links and symlinks are preserved. Archive verification metadata is created after the snapshot.')
(R/'archive-verification.json').write_text(json.dumps(record,indent=2)+'\n')
E.mkdir()
files=[]
for folder in [R,R/'update']:
    files += [p for p in folder.iterdir() if p.is_file()]
    for name in ['tests','remote','execution','worktrees','datadog-six-matched-tools','datadog-six']:
        directory=folder/name
        if not directory.exists():continue
        files += [p for p in (directory.rglob('*') if name=='tests' else directory.iterdir()) if p.is_file()]
extensions={'.json','.jsonl','.log','.py','.patch','.diff','.txt','.xml'}
raw=[]
for p in sorted(set(files)):
    if p.name.endswith('.bep.json') or p.suffix in {'.bin','.gz','.jfr'}:
        with p.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        raw.append(dict(path=p.relative_to(R).as_posix(),bytes=p.stat().st_size,sha256=digest))
    elif p.suffix in extensions:
        dest=E/p.relative_to(R);dest.parent.mkdir(parents=True,exist_ok=True)
        data=p.read_bytes()
        # Protocol text is canonicalized for the repository's LF attributes. Git patches must
        # remain byte exact, and were generated with native Linux line endings.
        if p.suffix in {'.patch','.diff'}:assert b'\r\n' not in data,p
        dest.write_bytes(data.replace(b'\r\n',b'\n'))
(E/'private-raw-hashes.json').write_text(json.dumps(raw,indent=2)+'\n')
public=sorted(p for p in E.rglob('*') if p.is_file())
(E/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.relative_to(E).as_posix()+'\n' for p in public))
mark(phase='complete',archive=record,public_files=len(public)+1,remote_files=len(remote_files))
print(json.dumps(dict(archive=record,public_files=len(public)+1,remote_files=len(remote_files)),indent=2),flush=True)
