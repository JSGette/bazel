from pathlib import Path
import json,subprocess,os,hashlib,importlib.util
R=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
S=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
T=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
head='1aac2aa2ec6fe31af2e979558f06a3b6971c601b';up='a5c50fcb65841b505ec72840d83381a704e70afa'
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=S,text=True).strip()==head
assert not subprocess.check_output(['git','status','--porcelain'],cwd=S,text=True).strip()
for name,base in [('implementation-on-upstream.patch',up),('integration-from-premerge.patch','d4971808785aaa3cd46bf6dd1419cb65afb4319e')]:
 with (R/name).open('wb') as f:subprocess.run(['git','diff','--binary',base,head],cwd=S,stdout=f,check=True)
 index=R/('verify-'+name+'.index');assert not index.exists()
 env=dict(os.environ,GIT_INDEX_FILE=str(index))
 subprocess.run(['git','read-tree',base],cwd=S,env=env,check=True)
 subprocess.run(['git','apply','--cached',str(R/name)],cwd=S,env=env,check=True)
 tree=subprocess.check_output(['git','write-tree'],cwd=S,env=env,text=True).strip()
 assert tree=='ea0f321fb662c6f34267a25470ef07bd3d31c0db';index.unlink()
 print(name,tree)
p=json.loads((R/'datadog-six-stable/results.json').read_text());assert p['status']=='passed'
last=p['runs'][-1];assert last['label']=='r3-toplevel-on'
hashes={}
for name,f in last['output_inventory']['binaries'].items():
 path=Path(f['path'])
 with path.open('rb') as stream:d=hashlib.file_digest(stream,'sha256').hexdigest()
 assert d==p['expected_binaries'][name];hashes[name]=dict(path=str(path),sha256=d,bytes=path.stat().st_size)
(R/'top-output-verification.json').write_text(json.dumps(hashes,indent=2)+'\n')
print('Verified six materialized executable hashes against current upstream vanilla.')
