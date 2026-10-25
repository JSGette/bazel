from pathlib import Path
import subprocess
R=Path('/home/jgett/REPOS/bazel-skycache-v2')
p=R/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidatorTest.java'
s=p.read_text();a=s.index('  public void fullSuccessMemoKeepsValidationAndFinalEvidenceWithoutGrowing()');b=s.index('  @Test',a)
part=s[a:b];part=part.replace('    var inputs = snapshot();','    var files = new java.util.ArrayList<Path>();\n    for (int i = 0; i < 8; i++) {\n      files.add(Files.writeString(workspace.resolve("memo-capacity-" + i), "original"));\n    }\n    var inputs = snapshot();',1)
part=part.replace('    Path lastFile = null;\n    for (int i = 0; i < 8; i++) {\n      lastFile = Files.writeString(workspace.resolve("memo-capacity-" + i), "original");\n      lastRoot = root(leaf(lastFile, false, List.of()));','    for (Path input : files) {\n      lastRoot = root(leaf(input, false, List.of()));')
part=part.replace('Files.writeString(lastFile, "changed");','Files.writeString(files.getLast(), "changed");');s=s[:a]+part+s[b:];p.write_text(s)
subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java','-jar','/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar','--replace',str(p)],check=True)
