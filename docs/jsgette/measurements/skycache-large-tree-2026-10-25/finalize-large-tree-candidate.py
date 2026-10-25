from pathlib import Path
import subprocess,shutil,json,hashlib,sys
R=Path('/home/jgett/REPOS/bazel-skycache-v2');RUN=R.parent/'.work/skycache-large-tree-2026-10-25'
version=sys.argv[1] if len(sys.argv)>1 else 'final'
b=json.loads((RUN/('build-tests-5.json' if version=='final2' else 'build-tests-4.json')).read_text());assert b['exit_code']==0
subprocess.run(['git','diff','--check'],cwd=R,check=True)
subprocess.run(['git','add','src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidator.java','src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidatorTest.java'],cwd=R,check=True)
subprocess.run(['git','commit','-m',('Separate retained validation memo capacity from traversal limits' if version=='final2' else 'Keep valid dependency results when the bounded success memo fills')],cwd=R,check=True)
with (RUN/('build-server-shutdown-'+version+'.log')).open('x') as f:subprocess.run(b['argv'][:b['argv'].index('test')]+['shutdown'],cwd=R,stdout=f,stderr=subprocess.STDOUT,check=True)
binary=RUN/('bin/skycache-large-tree-'+version);assert not binary.exists()
shutil.copy2(R.parent/'.work/skycache-execution-2026-10-25/build/execroot/_main/bazel-out/k8-fastbuild/bin/src/bazel-dev',binary)
with binary.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
rev=subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()
(RUN/('binary-'+version+'.json')).write_text(json.dumps({'path':str(binary),'sha256':digest,'source_commit':rev,'source_tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=R,text=True).strip()},indent=2)+'\n')
for name,base in [('implementation-large-tree.patch','71521fabb3420707c39d73329fa9aa9622e8c382'),('implementation-current.patch','6f451b2dc6f783dda879ff3c7ed3fc8146d0f410')]:
 if version=='final2':shutil.copy2(RUN/name,RUN/(name+'.memo250k'))
 (RUN/name).write_bytes(subprocess.check_output(['git','diff','--binary',base,'HEAD'],cwd=R))
print(rev,digest,flush=True)
