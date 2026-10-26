from pathlib import Path
import argparse, hashlib, json, shutil, subprocess, time, xml.etree.ElementTree as ET

p=argparse.ArgumentParser();p.add_argument('--variant',default='256');args=p.parse_args()
repo=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
root=Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26')/('build-'+args.variant)
root.mkdir(parents=True,exist_ok=False)
previous=Path('/home/jgett/REPOS/.work/skycache-validation-2026-10-26')
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
changed=['src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache.java',
 'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCacheTest.java',
 'src/main/java/com/google/devtools/build/lib/skyframe/serialization/AutoRegistry.java']
subprocess.run([str(jdk/'java'),'-jar','/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar','--replace',*changed],cwd=repo,check=True)
command=[x.replace(str(previous),str(root)) for x in json.loads((previous/'build.command.json').read_text())]
(root/'command.json').write_text(json.dumps(command,indent=2)+'\n')
(root/'source.diff').write_bytes(subprocess.check_output(['git','diff','HEAD'],cwd=repo))
started=time.monotonic()
with (root/'build.log').open('w') as log:r=subprocess.run(command,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
summary=dict(exit_code=r.returncode,seconds=time.monotonic()-started)
print(json.dumps(summary),flush=True)
if r.returncode:
 print((root/'build.log').read_text()[-12000:]);raise SystemExit(r.returncode)
binary=root/'bazel'
shutil.copy2('/home/jgett/REPOS/.work/skycache-remote-mvp/build/execroot/_main/bazel-out/k8-fastbuild/bin/src/bazel-dev',binary)
summary['binary_sha256']=hashlib.file_digest(binary.open('rb'),'sha256').hexdigest()
testlogs=Path('/home/jgett/REPOS/.work/skycache-remote-mvp/build/execroot/_main/bazel-out/k8-fastbuild/testlogs')
(root/'tests').mkdir()
tests=[]
for line in (root/'build.bep.json').read_text().splitlines():
 e=json.loads(line)
 if 'testSummary' not in e:continue
 label=e['id']['testSummary']['label'];xml=testlogs/label[2:].replace(':','/')/'test.xml'
 dest=root/'tests'/(label.split(':')[-1]+'.xml');shutil.copy2(xml,dest);dest.chmod(0o644)
 tree=ET.parse(dest)
 tests.append(dict(label=label,status=e['testSummary']['overallStatus'],cases=len(tree.findall('.//testcase')),failures=len(tree.findall('.//failure')),errors=len(tree.findall('.//error'))))
summary['tests']=tests;summary['cases']=sum(t['cases'] for t in tests)
summary['source_sha256']={f:hashlib.sha256((repo/f).read_bytes()).hexdigest() for f in changed}
(root/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(dict(cases=summary['cases'],targets=len(tests),binary_sha256=summary['binary_sha256'])),flush=True)
