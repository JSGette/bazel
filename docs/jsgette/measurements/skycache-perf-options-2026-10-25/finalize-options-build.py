from pathlib import Path
import subprocess,shutil,json,hashlib,xml.etree.ElementTree as ET
S=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
assert '15 tests pass' in (R/'lookup-reuse-build.log').read_text()
binary=R/'bin/lookup-reuse';shutil.copy2(S/'bazel-bin/src/bazel-dev',binary)
results=R/'lookup-reuse-tests';results.mkdir(exist_ok=True)
count=0
for p in (S/'bazel-testlogs/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local').glob('*/test.xml'):
 x=ET.parse(p);assert not (x.findall('.//failure')+x.findall('.//error'));count+=len(x.findall('.//testcase'))
 shutil.copy2(p,results/(p.parent.name+'.xml'))
argv=json.loads((R/'lookup-reuse-build.command.json').read_text());startup=argv[:argv.index('test')]
with (R/'lookup-reuse-shutdown.log').open('w') as f:p=subprocess.run(startup+['shutdown'],cwd=S,stdout=f,stderr=subprocess.STDOUT)
r=dict(exit_code=0,test_cases=count,test_targets=len(list(results.glob('*.xml'))),binary_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),shutdown=p.returncode,postprocessing_note='Initial copy assumed io_bazel execroot; actual _main. Build/tests passed; copied via observed bazel-bin link without rebuilding.')
(R/'lookup-reuse-build.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
