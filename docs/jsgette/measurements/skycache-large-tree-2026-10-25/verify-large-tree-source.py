from pathlib import Path
import subprocess,os,json,xml.etree.ElementTree as ET,shutil,sys
R=Path('/home/jgett/REPOS/bazel-skycache-v2');RUN=R.parent/'.work/skycache-large-tree-2026-10-25'
assert not subprocess.check_output(['git','status','--porcelain'],cwd=R,text=True).strip()
index=RUN/'patch-check.index';assert not index.exists()
env=dict(os.environ,GIT_INDEX_FILE=str(index))
try:
 subprocess.run(['git','read-tree','6f451b2dc6f783dda879ff3c7ed3fc8146d0f410'],cwd=R,env=env,check=True)
 subprocess.run(['git','apply','--cached',str(RUN/'implementation-current.patch')],cwd=R,env=env,check=True)
 tree=subprocess.check_output(['git','write-tree'],cwd=R,env=env,text=True).strip()
 expected=subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=R,text=True).strip();assert tree==expected
finally:index.unlink(missing_ok=True)
logs=R.parent/'.work/skycache-execution-2026-10-25/build/execroot/_main/bazel-out/k8-fastbuild/testlogs/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
suffix='-'+sys.argv[1] if len(sys.argv)>1 else ''
dest=RUN/('unit-test-results'+suffix);dest.mkdir(exist_ok=False);rows=[]
for p in sorted(logs.glob('*/test.xml')):
 root=ET.parse(p).getroot();suites=[root] if root.tag=='testsuite' else list(root.iter('testsuite'))
 row={'target':p.parent.name,'tests':sum(int(s.get('tests',0)) for s in suites),'failures':sum(int(s.get('failures',0)) for s in suites),'errors':sum(int(s.get('errors',0)) for s in suites)}
 assert row['failures']==row['errors']==0;rows.append(row);shutil.copy2(p,dest/(p.parent.name+'.xml'))
assert len(rows)==15
result={'source_tree':tree,'patch_reconstructs_source_tree':True,'unit_tests':rows,'test_cases':sum(r['tests'] for r in rows)}
(RUN/('source-verification'+suffix+'.json')).write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
