from pathlib import Path
import re,subprocess
R=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
P=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/analysis'
for p,side,count in [(P/'FileDependencySerializer.java','theirs',3),(P/'RemoteAnalysisCacheDeps.java','ours',2),(R/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/analysis/FileDependencySerializerTest.java','ours',1)]:
 s=p.read_text()
 s,n=re.subn(r'<<<<<<< HEAD\n(.*?)=======\n(.*?)>>>>>>> [^\n]+\n',lambda m:m[1 if side=='ours' else 2],s,flags=re.S)
 assert n==count,(p,n)
 if p.name=='FileDependencySerializer.java':
  assert s.count('WriteStatus writeStatus = writer.put(key, value);')==1
  s=s.replace('WriteStatus writeStatus = writer.put(key, value);','WriteStatus writeStatus = writeInvalidation(key, value);')
 if p.name=='RemoteAnalysisCacheDeps.java':
  s=s.replace('import java.util.Optional;\n','').replace('    this.minimizeMemory = minimizeMemory;\n','')
  s=s.replace('import java.util.concurrent.atomic.AtomicBoolean;','import java.util.concurrent.atomic.AtomicBoolean;\nimport java.util.function.Predicate;')
 p.write_text(s)
 subprocess.run(['git','add',str(p)],cwd=R,check=True)
p=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalInputSnapshot.java'
s=p.read_text();assert s.count('"local-project-v3-content-sha256"')==1
s=s.replace('    update(digest, "local-project-v3-content-sha256");','    // Upstream test-configuration keys changed wire format. Keep old publications isolated.\n    update(digest, "local-project-v4-content-sha256");')
p.write_text(s)
subprocess.run(['git','add',str(p)],cwd=R,check=True)
print(subprocess.check_output(['git','diff','--name-only','--diff-filter=U'],cwd=R,text=True))
