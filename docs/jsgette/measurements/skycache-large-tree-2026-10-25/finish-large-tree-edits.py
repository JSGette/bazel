from pathlib import Path
import shutil,subprocess
R=Path('/home/jgett/REPOS/bazel-skycache-v2')
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
L=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local'
T=R/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
p=L/'LocalCacheSession.java';s=p.read_text();assert '.resolve("packed-v1")' in s;s=s.replace('.resolve("packed-v1")','.resolve("segmented-v1")');p.write_text(s)
for name in ['LocalWitnessTableTest','SegmentedFingerprintValueStoreTest']:
 shutil.copy2(TASK/'work/large-tree'/(name+'.java'),T/(name+'.java'))
p=T/'BUILD';s=p.read_text();s+='''
java_test(
    name = "LocalWitnessTableTest",
    size = "medium",
    srcs = ["LocalWitnessTableTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:local_skycache_module",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)

java_test(
    name = "SegmentedFingerprintValueStoreTest",
    size = "medium",
    srcs = ["SegmentedFingerprintValueStoreTest.java"],
    tags = ["no_windows"],
    deps = [
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:string_key",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization:write_status",
        "//src/main/java/com/google/devtools/build/lib/skyframe/serialization/local:packed_store",
        "//third_party/java/guava:concurrent",
        "//third_party/java/junit",
        "//third_party/java/truth",
    ],
)
''';p.write_text(s)
# Also prove isolation from the immediately preceding leased layout, not just pre-lease caches.
p=T/'LocalCacheSessionTest.java';s=p.read_text();idx=s.rindex('\n}')
s=s[:idx]+'''
  @Test
  public void segmentedLayoutLeavesPreviousLeasedCacheUntouched() throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    Path old = Files.createDirectories(cache.resolve("leased-v1/publications-v3"));
    Path sentinel = Files.writeString(old.resolve("old.manifest"), "old-format-sentinel");
    var writer = new LocalCacheSession(cache,
        snapshot(temporary.newFolder("workspace").toPath(), temporary.newFolder("output").toPath()));
    writer.put(new Key("new"), new byte[] {1}).get();
    assertThat(writer.finish(true)).isTrue();
    new LocalCacheGarbageCollector(cache, new LocalCacheGarbageCollector.Policy(1, 0)).run();
    assertThat(Files.readString(sentinel)).isEqualTo("old-format-sentinel");
  }
'''+s[idx:];p.write_text(s)
print(subprocess.check_output(['git','-C',str(R),'status','--short'],text=True))
