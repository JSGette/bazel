from pathlib import Path
import subprocess
R=Path('/home/jgett/REPOS/bazel-skycache-v2')
L=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidator.java'
T=R/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidatorTest.java'
s=L.read_text();s=s.replace('private final LongAdder memoHits = new LongAdder();','private final LongAdder memoHits = new LongAdder();\n  private final LongAdder memoCapacitySkips = new LongAdder();')
old='''            if (validated.size() >= limits.records()) {
              throw new IOException("dependency success memo capacity exceeded");
            }
            validated.add(frame.key());'''
new='''            // This memo is an optimization, not evidence required for a successful traversal.
            // Once full, later lookups must repeat validation. Traversal and in-flight limits
            // still fail closed, and every successful witness remains registered with inputs.
            if (validated.size() < limits.records()) {
              validated.add(frame.key());
            } else {
              memoCapacitySkips.increment();
            }'''
assert old in s;s=s.replace(old,new)
s=s.replace('\\"memo_entries\\":%d,','\\"memo_entries\\":%d,\\"memo_capacity_skips\\":%d,')
s=s.replace('        validated.size(),','        validated.size(),\n        memoCapacitySkips.sum(),')
L.write_text(s)
s=T.read_text();marker='  @Test\n  public void witnessMemoIsBoundedWithoutDroppingFinalEvidence()'
test='''  @Test
  public void fullSuccessMemoKeepsValidationAndFinalEvidenceWithoutGrowing() throws Exception {
    var inputs = snapshot();
    var validator = new LocalDependencyValidator(store, inputs, NONPROD_FINGERPRINTER,
        compression, new LocalDependencyValidator.Limits(3, 1024 * 1024, 100, 100, 1024));
    PackedFingerprint lastRoot = null;
    Path lastFile = null;
    for (int i = 0; i < 8; i++) {
      lastFile = Files.writeString(workspace.resolve("memo-capacity-" + i), "original");
      lastRoot = root(leaf(lastFile, false, List.of()));
      assertThat(validator.validate(lastRoot)).isTrue();
    }
    assertThat(validator.statisticsJson()).contains("\\\"memo_entries\\\":3");
    assertThat(validator.validate(lastRoot)).isTrue();
    // The uncached success must be validated again; corrupt evidence cannot reuse it.
    store.put(lastRoot, new byte[] {99}).get();
    assertThat(validator.validate(lastRoot)).isFalse();
    assertThat(inputs.isUnchanged()).isTrue();
    Files.writeString(lastFile, "changed");
    assertThat(inputs.isUnchanged()).isFalse();
  }

'''
assert marker in s;s=s.replace(marker,test+marker);T.write_text(s)
subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java','-jar','/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar','--replace',str(L),str(T)],check=True)
subprocess.run(['git','diff','--check'],cwd=R,check=True)
print('Capacity fix and focused regression staged in the source; benchmark binary unchanged.')
