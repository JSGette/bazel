from pathlib import Path
import hashlib,json,os,tarfile
root=Path('/mnt/skycache-v2-archive');dest=Path('/mnt/d/Codex/wsl-reset-2026-10-25')
files=[]
for parent,dirs,names in os.walk(root):
 p=Path(parent);depth=len(p.relative_to(root).parts)
 dirs[:]=[d for d in dirs if depth<2 and d not in ['bazel-user-root','build-output','bin','tmp','lost+found','cache','skycache','output-base','out-a','out-b']]
 for n in names:
  q=p/n
  if not q.is_symlink() and n.endswith(('.json','.jsonl','.log','.xml','.md','.txt','.py','.sh','.profile.gz')):files.append(q)
manifest={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
archive=dest/'old-C-benchmark-evidence.tar'
with tarfile.open(archive,'w') as tar:
 for p in files:tar.add(p,arcname=str(p.relative_to(root)),recursive=False)
with tarfile.open(archive) as tar:
 for name,expected in manifest.items():assert hashlib.sha256(tar.extractfile(name).read()).hexdigest()==expected,name
assert 'protocol.json' in manifest and 'summary.json' in manifest
with archive.open('rb') as f:checksum=hashlib.file_digest(f,'sha256').hexdigest()
report={'verified':True,'files':len(files),'bytes':archive.stat().st_size,'archive_sha256':checksum,'manifest':manifest}
(dest/'old-C-evidence-verified.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k!='manifest'},indent=2))
