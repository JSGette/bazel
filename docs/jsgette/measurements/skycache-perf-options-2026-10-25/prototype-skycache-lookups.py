from pathlib import Path
import subprocess,json
R=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
E=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
assert not subprocess.check_output(['git','status','--porcelain'],cwd=R).strip()
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=R).decode().strip()=='1aac2aa2ec6fe31af2e979558f06a3b6971c601b'
subprocess.run(['git','switch','-c','jsgette/skycache-perf-options'],cwd=R,check=True)
p=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/PackedFingerprintValueStore.java'
s=p.read_text()
needle='  byte[] getIfPresent(KeyBytesProvider fingerprint) throws IOException {\n'
assert s.count(needle)==1
s=s.replace(needle,'''  byte[] getIfPresent(KeyBytesProvider fingerprint) throws IOException {
    byte[] key = checkedKey(fingerprint);
    return getIfPresent(key, digest(key, key.length));
  }

  // A segmented lookup computes and owns these bytes once, across all of its segment probes.
  byte[] getIfPresent(byte[] key, byte[] hash) throws IOException {
''')
needle='      byte[] key = checkedKey(fingerprint);\n      byte[] hash = digest(key, key.length);\n      Location location = pending == null ? find(hash) : pending.get(ByteString.copyFrom(hash));'
assert s.count(needle)==1;s=s.replace(needle,'      Location location = pending == null ? find(hash) : pending.get(ByteString.copyFrom(hash));')
s=s.replace('  private static byte[] checkedKey(KeyBytesProvider fingerprint)', '  static byte[] checkedKey(KeyBytesProvider fingerprint)')
p.write_text(s)
p=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/SegmentedFingerprintValueStore.java'
s=p.read_text();needle='  private byte[] find(Bucket bucket, KeyBytesProvider key) throws IOException {\n'
assert s.count(needle)==1
s=s.replace(needle,'''  private byte[] find(Bucket bucket, KeyBytesProvider key) throws IOException {
    byte[] bytes = PackedFingerprintValueStore.checkedKey(key);
    return find(bucket, bytes, hash(bytes));
  }

  private byte[] find(Bucket bucket, byte[] key, byte[] digest) throws IOException {
''').replace('store(bucket.segments.get(i)).getIfPresent(key)', 'store(bucket.segments.get(i)).getIfPresent(key, digest)')
needle='      Bucket bucket = buckets[bucket(key)];\n      synchronized (bucket) {\n        byte[] value = find(bucket, key);'
assert s.count(needle)==1
s=s.replace(needle,'''      byte[] bytes = PackedFingerprintValueStore.checkedKey(key);
      byte[] digest = hash(bytes);
      Bucket bucket = buckets[digest[0] & (BUCKETS - 1)];
      synchronized (bucket) {
        byte[] value = find(bucket, bytes, digest);''')
p.write_text(s)
paths=[str(R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local'/n) for n in ['PackedFingerprintValueStore.java','SegmentedFingerprintValueStore.java']]
subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java','-jar','/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar','--replace',*paths],check=True)
(E/'lookup-reuse.patch').write_bytes(subprocess.check_output(['git','diff','--binary'],cwd=R))
subprocess.run(['git','diff','--check'],cwd=R,check=True)
print(subprocess.check_output(['git','diff','--stat'],cwd=R).decode())
