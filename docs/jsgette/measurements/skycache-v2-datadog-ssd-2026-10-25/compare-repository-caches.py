from pathlib import Path
import hashlib,json,time
new=Path('/home/jgett/.cache/bazel/cache')
old=Path('/home/jgett/.cache/bazel/_bazel_jgett/cache')
root=Path('/home/jgett/REPOS/.work/skycache-v2-datadog-ssd-2026-10-25')
def sha(p):
 with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
r={'new_root':str(new),'existing_root':str(old),'started_unix':time.time(),'new_regular_bytes':0,'duplicate_bytes':0,'unique_bytes':0,'duplicate_files':0,'unique_files':0,'unique_paths':[]}
for p in new.rglob('*'):
 if p.is_symlink() or not p.is_file():continue
 n=p.stat().st_size;q=old/p.relative_to(new);r['new_regular_bytes']+=n
 if q.is_file() and q.stat().st_size==n and sha(p)==sha(q):r['duplicate_bytes']+=n;r['duplicate_files']+=1
 else:r['unique_bytes']+=n;r['unique_files']+=1;r['unique_paths'].append({'path':str(p.relative_to(new)),'bytes':n})
r['finished_unix']=time.time()
(root/'duplicate-cache-audit.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps({k:v for k,v in r.items() if k!='unique_paths'},indent=2))
