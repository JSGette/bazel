from pathlib import Path
import json,hashlib,struct,os,stat
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
p=json.loads((R/'minimal-profile/witness-diff.json').read_text())
rows=[x for x in p['changes'] if x['path'].startswith('absolute/') and '__pycache__' in x['path'] and x['before'] and x['after']]
def digest(parts):
 h=hashlib.sha256()
 for x in parts:
  b=x.encode();h.update(struct.pack('>i',len(b)));h.update(b)
 return h.hexdigest()
proof=[]
for x in rows:
 path=Path(x['path'][len('absolute/'):])
 if not path.is_symlink():continue
 target=os.readlink(path);base=['symlink',stat.filemode(path.lstat().st_mode)[1:],target]
 missing=digest(base+['missing']);directory={mode:digest(base+['directory',mode]) for mode in ('rwxr-xr-x','rwx------','r-xr-xr-x','rwxrwxr-x','rwxrwxrwx')}
 if missing==x['after'] and x['before'] in directory.values():
  proof.append(dict(path=x['path'],target=target,before_type='directory',before_mode=next(k for k,v in directory.items() if v==x['before']),after_type='missing',current_target_exists=path.exists()))
result=dict(total_changed_witnesses=len(p['changes']),proven_directory_to_missing_symlinks=len(proof),examples=proof[:5],meaning='Exact fingerprint reproduction proves these external symlink referents changed from directories to missing. The process responsible was not identified. This is a safe cache rejection, not a measured hit or budget overflow.')
(R/'minimal-profile/witness-change-explanation.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
