from pathlib import Path
import subprocess,shutil,json,hashlib
R=Path('/home/jgett/REPOS/bazel-skycache-v2')
RUN=R.parent/'.work/skycache-large-tree-2026-10-25'
p=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/BUILD'
s=p.read_text().replace('srcs = ["PackedFingerprintValueStore.java", "SegmentedFingerprintValueStore.java"],','srcs = [\n        "PackedFingerprintValueStore.java",\n        "SegmentedFingerprintValueStore.java",\n    ],').replace('        "LocalWitnessTable.java",\n','').replace('        "LocalInputSnapshot.java",','        "LocalInputSnapshot.java",').replace('        "LocalRepositoryMappingCodec.java",','        "LocalRepositoryMappingCodec.java",')
# Maintain the alphabetical source list after LocalRunfilesCodec.
line='        "LocalRunfilesCodec.java",'
assert line in s;s=s.replace(line,line+'\n        "LocalWitnessTable.java",');p.write_text(s)
subprocess.run(['git','diff','--check'],cwd=R,check=True)
build=json.loads((RUN/'build-tests-2.json').read_text());assert build['exit_code']==0
startup=build['argv'][:build['argv'].index('test')]
with (RUN/'build-server-shutdown.log').open('x') as f:subprocess.run(startup+['shutdown'],cwd=R,stdout=f,stderr=subprocess.STDOUT,check=True)
RUN.joinpath('bin').mkdir(exist_ok=True)
binary=RUN/'bin/skycache-large-tree'
shutil.copy2(R.parent/'.work/skycache-execution-2026-10-25/build/execroot/_main/bazel-out/k8-fastbuild/bin/src/bazel-dev',binary)
with binary.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
diff=subprocess.check_output(['git','diff','--binary','HEAD'],cwd=R)
(RUN/'source-tracked.patch').write_bytes(diff)
(RUN/'binary.json').write_text(json.dumps({'path':str(binary),'sha256':digest,'base':subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip()},indent=2)+'\n')
print('candidate',digest,flush=True)
subprocess.run(['curl','-fsS','--max-time','10','--http2-prior-knowledge','http://10.55.0.1:8080/healthz'],check=True)
