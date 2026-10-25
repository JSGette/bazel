from pathlib import Path
import json,subprocess,hashlib,shutil,xml.etree.ElementTree as ET
R=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
RUN=Path('/home/jgett/REPOS/.work/skycache-upstream-sync-2026-10-25')
assert json.loads((RUN/'build-tests-2.json').read_text())['exit_code']==0
subprocess.run(['git','diff','--check'],cwd=R,check=True)
assert not subprocess.check_output(['git','diff','--name-only','--diff-filter=U'],cwd=R,text=True).strip()
subprocess.run(['git','commit','-m','Merge upstream Bazel a5c50fcb65 with local SkyCache integration'],cwd=R,check=True)
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()
tree=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=R,text=True).strip()
dest=RUN/'bin/skycache-upstream';dest.parent.mkdir(exist_ok=True)
shutil.copy2(R/'bazel-bin/src/bazel-dev',dest);dest.chmod(0o755)
xdir=RUN/'unit-test-results';xdir.mkdir(exist_ok=False);tests=[]
for p in (R/'bazel-testlogs/src/test/java/com/google/devtools/build/lib/skyframe').rglob('test.xml'):
 xml=ET.parse(p)
 cases=xml.findall('.//testcase');failures=xml.findall('.//failure')+xml.findall('.//error')
 assert not failures,(p,failures)
 name=p.parent.name;shutil.copy2(p,xdir/(name+'.xml'));tests.append(dict(name=name,cases=len(cases)))
result=dict(commit=head,tree=tree,binary=str(dest),binary_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),tests=tests,test_targets=len(tests),test_cases=sum(x['cases'] for x in tests))
(RUN/'source-verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
args=json.loads((RUN/'build-tests-2.command.json').read_text());subprocess.run(args[:args.index('test')]+['shutdown'],cwd=R,check=True)
