from pathlib import Path
import json,hashlib,os,stat,collections
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
base=Path('/home/jgett/.cache/bazel/_bazel_jgett/install')
a=base/'80a15f52b9b3373384f22804a4dc15c5/embedded_tools'
b=base/'d150b73b6e4aa6af834d2a95ca3db351/embedded_tools'
def tree(p):
 out={}
 for q in p.rglob('*'):
  st=q.lstat();key=q.relative_to(p).as_posix()
  value=dict(mode=stat.S_IMODE(st.st_mode),kind=stat.S_IFMT(st.st_mode))
  if q.is_symlink():value['link']=os.readlink(q)
  elif q.is_file():value['sha256']=hashlib.sha256(q.read_bytes()).hexdigest()
  out[key]=value
 return out
x,y=tree(a),tree(b);diff=[k for k in x.keys()|y.keys() if x.get(k)!=y.get(k)]
d=json.loads((R/'cross-binary-witness-diff.json').read_text())
both=[v for v in d['changes'] if v['before'] is not None and v['after'] is not None]
result=dict(baseline_tools=str(a),candidate_tools=str(b),baseline_entries=len(x),candidate_entries=len(y),different_entries=diff,witness_changes=len(d['changes']),changed_existing_witnesses=both)
(R/'cross-binary-tools-check.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
assert not diff
