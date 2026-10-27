"""Isolated red/green checks and native binaries for restore/limit investigation."""
from pathlib import Path
import argparse, fcntl, hashlib, json, shutil, subprocess, time, xml.etree.ElementTree as ET

p=argparse.ArgumentParser()
p.add_argument('--phase', choices=['red','green','build'], required=True)
args=p.parse_args()
repo=Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
root=Path('/home/jgett/REPOS/.work/skycache-restore-limits-2026-10-27')/args.phase
root.mkdir(parents=True,exist_ok=False)
previous=Path('/home/jgett/REPOS/.work/skycache-validation-2026-10-26')
base=json.loads((previous/'build.command.json').read_text())
targets=['//src/test/java/com/google/devtools/build/lib/remote:RemoteTests']
if args.phase=='build':
    targets=[x for x in base if x.startswith('//')]+targets
command=base[:base.index('test')+1]+targets+[x.replace(str(previous),str(root)) for x in base[base.index('test')+1:] if not x.startswith('//')]
command+=['--remote_upload_local_results=false']
if args.phase in ['red','green']:
    command+=['--test_filter=RemoteActionFileSystemTest'+('#getInputStream_forRemoteInputWithMissingParents' if args.phase=='red' else '')]
else:
    command+=['--test_filter=RemoteActionFileSystemTest|PathCanonicalizerTest|com.google.devtools.build.lib.skyframe.serialization|com.google.devtools.build.remote.worker']
(root/'command.json').write_text(json.dumps(command,indent=2)+'\n')
(root/'source.diff').write_bytes(subprocess.check_output(['git','diff','HEAD'],cwd=repo))
started=time.monotonic()
with Path('/home/jgett/REPOS/.work/skyframe-measurements.lock').open('a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    with (root/'build.log').open('x') as log:
        r=subprocess.run(command,cwd=repo,stdout=log,stderr=subprocess.STDOUT)
summary=dict(exit_code=r.returncode,seconds=time.monotonic()-started,phase=args.phase)
testlogs=Path('/home/jgett/REPOS/.work/skycache-remote-mvp/build/execroot/_main/bazel-out/k8-fastbuild/testlogs')
(root/'tests').mkdir()
tests=[]
if (root/'build.bep.json').exists():
    for line in (root/'build.bep.json').read_text().splitlines():
        e=json.loads(line)
        if 'testSummary' not in e:continue
        label=e['id']['testSummary']['label']
        xml=testlogs/label[2:].replace(':','/')/'test.xml'
        if not xml.exists():continue
        dest=root/'tests'/(label.split(':')[-1]+'.xml')
        shutil.copy2(xml,dest);dest.chmod(0o644)
        tree=ET.parse(dest)
        tests.append(dict(label=label,status=e['testSummary']['overallStatus'],cases=len(tree.findall('.//testcase')),failures=len(tree.findall('.//failure')),errors=len(tree.findall('.//error'))))
summary['tests']=tests
summary['cases']=sum(t['cases'] for t in tests)
if args.phase=='build' and r.returncode==0:
    binroot=testlogs.parent/'bin'
    for name,src in [('bazel','src/bazel-dev'),('worker','src/tools/remote/worker')]:
        shutil.copy2(binroot/src,root/name)
        summary[name+'_sha256']=hashlib.file_digest((root/name).open('rb'),'sha256').hexdigest()
(root/'result.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary),flush=True)
if r.returncode:
    print((root/'build.log').read_text()[-12000:])
raise SystemExit(r.returncode)
