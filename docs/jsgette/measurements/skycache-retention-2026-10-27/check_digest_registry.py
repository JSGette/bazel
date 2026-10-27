from pathlib import Path
import hashlib, json, subprocess, zipfile

root=Path('/home/jgett/REPOS/.work/skycache-digests-2026-10-26/registry-slots')
root.mkdir(exist_ok=True)
work=Path(__file__).parent
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
binaries={'baseline':Path('/home/jgett/REPOS/.work/skycache-validation-2026-10-26/bin/bazel'),
 'channels':Path('/home/jgett/REPOS/.work/skycache-channels-2026-10-26/build-128-v2/bazel'),'scratch':root.parent/'build-slots-v2/bazel'}
for name,path in binaries.items():
 with zipfile.ZipFile(path) as archive:(root/(name+'.jar')).write_bytes(archive.read('A-server.jar'))
subprocess.run([str(jdk/'javac'),'-proc:none','-cp',str(root/'baseline.jar'),'-d',str(root/'classes'),str(work/'ChannelRegistryCheck.java')],check=True)
results={}
for name in binaries:
 result=subprocess.run([str(jdk/'java'),'--add-opens=java.base/java.lang=ALL-UNNAMED',
  '--add-opens=java.base/java.util=ALL-UNNAMED','--add-opens=java.base/java.lang.invoke=ALL-UNNAMED',
  '-cp',str(root/'classes')+':'+str(root/(name+'.jar')),
  'com.google.devtools.build.lib.skyframe.serialization.ChannelRegistryCheck'],capture_output=True,text=True)
 (root/(name+'.log')).write_text(result.stdout+result.stderr)
 if result.returncode:
  print(result.stderr);result.check_returncode()
 results[name]=dict(line.split('=',1) for line in result.stdout.splitlines() if '=' in line)
assert results['baseline']==results['channels']==results['scratch'],results
results['identical']=True
(root/'comparison.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
