from pathlib import Path
import json, subprocess, os, hashlib
ROOT=Path('/home/jgett/REPOS')
S=ROOT/'bazel-skycache-upstream-2026-10-25'
MAIN=ROOT/'bazel-skycache-v2'
REMOTE=ROOT/'bazel-skycache-remote-mvp'
R=ROOT/'.work/skycache-remote-sync-2026-10-25'
U=R/'update'
src=json.loads((U/'source.json').read_text())
assert json.loads((U/'datadog-six/results.json').read_text())['status']=='passed'
p={'commands':[],'remote_code':src['remote'],'built_commit':src['commit'],'built_tree':src['built_tree']}
def run(cwd,*args):
    c=subprocess.run(args,cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    p['commands'].append(dict(cwd=str(cwd),argv=args,exit_code=c.returncode,output=c.stdout))
    (R/'promotion.json').write_text(json.dumps(p,indent=2)+'\n')
    assert c.returncode==0,c.stdout
    return c.stdout.strip()
assert not run(S,'git','status','--porcelain')
assert run(S,'git','rev-parse','HEAD')==src['commit']
remote_head=run(REMOTE,'git','rev-parse','HEAD')
p['remote_head_observed']=remote_head
p['remote_status_observed']=run(REMOTE,'git','status','--porcelain')
if remote_head!=src['remote']:
    changes=run(S,'git','diff','--name-only',src['remote'],remote_head).splitlines()
    assert changes and all(x.startswith('docs/') for x in changes), changes
    p['documentation_updates']=changes
    run(S,'git','merge','--no-ff','-m','Merge remote SkyCache parent-reuse measurements and handoff',remote_head)
src['build_commit']=p['built_commit']
src['built_parents']=src['parents']
src['commit']=run(S,'git','rev-parse','HEAD')
src['tree']=run(S,'git','rev-parse','HEAD^{tree}')
src['parents']=run(S,'git','show','-s','--format=%P','HEAD').split()
src['remote_documentation']=remote_head
src['documentation_only_since_build']=run(S,'git','diff','--name-only',src['built_tree'],src['tree']).splitlines()
assert all(x.startswith('docs/') for x in src['documentation_only_since_build'])
src['status']=run(S,'git','status','--porcelain')
assert not src['status']
for name,base in [('remote-sync.patch',src['baseline']),
                  ('integrated-from-previous.patch','1aac2aa2ec6fe31af2e979558f06a3b6971c601b'),
                  ('integrated-from-upstream.patch',src['upstream'])]:
    (U/name).write_bytes(subprocess.check_output(['git','diff','--binary',base,'HEAD'],cwd=S))
(U/'source.json').write_text(json.dumps(src,indent=2)+'\n')
assert not run(MAIN,'git','status','--porcelain')
p['main_before']=run(MAIN,'git','rev-parse','HEAD')
assert p['main_before']==src['baseline']
run(MAIN,'git','merge','--ff-only',src['commit'])
p['main_after']=run(MAIN,'git','rev-parse','HEAD')
p['main_status']=run(MAIN,'git','status','--porcelain')
assert p['main_after']==src['commit'] and not p['main_status']
p['patch_checks']=[]
for directory in [R,U]:
    source=json.loads((directory/'source.json').read_text())
    for name,base in [('remote-sync.patch',source['baseline']),
                      ('integrated-from-previous.patch','1aac2aa2ec6fe31af2e979558f06a3b6971c601b'),
                      ('integrated-from-upstream.patch',source['upstream'])]:
        index=directory/(name+'.verify-index')
        assert not index.exists()
        env=dict(os.environ,GIT_INDEX_FILE=str(index))
        subprocess.run(['git','read-tree',base],cwd=S,env=env,check=True)
        subprocess.run(['git','apply','--cached',str(directory/name)],cwd=S,env=env,check=True)
        tree=subprocess.check_output(['git','write-tree'],cwd=S,env=env,text=True).strip()
        assert tree==source['tree'],(name,tree,source['tree'])
        index.unlink()
        p['patch_checks'].append(dict(path=str((directory/name).relative_to(R)),base=base,tree=tree,
                                     sha256=hashlib.sha256((directory/name).read_bytes()).hexdigest()))
(R/'promotion.json').write_text(json.dumps(p,indent=2)+'\n')
print(json.dumps({k:v for k,v in p.items() if k!='commands'},indent=2))
