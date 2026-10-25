from pathlib import Path
import subprocess
R=Path('/home/jgett/REPOS/bazel-skycache-v2')
L=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidator.java'
T=R/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidatorTest.java'
s=L.read_text();s=s.replace('record Limits(int records, long bytes, int depth, int references, int nodeBytes) {','record Limits(int records, long bytes, int depth, int references, int nodeBytes, int memoEntries) {');s=s.replace('new Limits(250_000, 128L * 1024 * 1024, 4096, 100_000, 8 * 1024 * 1024);','new Limits(250_000, 128L * 1024 * 1024, 4096, 100_000, 8 * 1024 * 1024, 1_000_000);')
s=s.replace('    Limits {','    Limits(int records, long bytes, int depth, int references, int nodeBytes) {\n      this(records, bytes, depth, references, nodeBytes, records);\n    }\n\n    Limits {',1);s=s.replace('nodeBytes < 1)', 'nodeBytes < 1 || memoEntries < 1)');s=s.replace('validated.size() < limits.records()', 'validated.size() < limits.memoEntries()');s=s.replace('validatedWitnesses.size() < limits.records()', 'validatedWitnesses.size() < limits.memoEntries()');L.write_text(s)
s=T.read_text();marker='  @Test\n  public void fullSuccessMemoKeepsValidationAndFinalEvidenceWithoutGrowing()'
test='''  @Test
  public void successMemoCapacityDoesNotRaisePerLookupTraversalBudget() throws Exception {
    var first = root(leaf(file, false, List.of()));
    var second = root(leaf(Files.writeString(workspace.resolve("second"), "two"), false, List.of()));
    var validator = new LocalDependencyValidator(store, snapshot(), NONPROD_FINGERPRINTER,
        compression, new LocalDependencyValidator.Limits(2, 1024 * 1024, 100, 100, 1024, 10));
    assertThat(validator.validate(first)).isTrue();
    assertThat(validator.validate(second)).isTrue();
    assertThat(validator.statisticsJson()).contains("\\\"memo_entries\\\":4");
    var fresh = root(leaf(Files.writeString(workspace.resolve("third"), "three"), false, List.of()));
    var tooDeep = put(bytes(List.of(fresh), List.of(), List.of(), null));
    assertThat(validator.validate(tooDeep)).isFalse();
  }

'''
assert marker in s;s=s.replace(marker,test+marker);T.write_text(s)
subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java','-jar','/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar','--replace',str(L),str(T)],check=True)
subprocess.run(['git','diff','--check'],cwd=R,check=True)
