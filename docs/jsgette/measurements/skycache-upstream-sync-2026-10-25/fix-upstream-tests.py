from pathlib import Path
import subprocess
R=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
p=R/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/analysis/FileDependencySerializerTest.java'
s=p.read_text();needle='graph, COMPRESSION_SERVICE, writer, executor, null);';assert s.count(needle)==5
s=s.replace(needle,'graph, COMPRESSION_SERVICE, writer, executor, null, LeafIdAssignment.lazy(), false);')
p.write_text(s)
subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java','-jar','/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar','--replace',str(p)],check=True)
subprocess.run(['git','add',str(p)],cwd=R,check=True)
