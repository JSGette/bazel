from pathlib import Path
import subprocess, json, re
S = Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
R = Path('/home/jgett/REPOS/.work/skycache-remote-sync-2026-10-25/update')
folder = S / 'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local'
packed = folder / 'PackedFingerprintValueStore.java'
segmented = folder / 'SegmentedFingerprintValueStore.java'
patch = subprocess.check_output(['git', 'diff', '--cc'], cwd=S)
(R / 'unresolved-merge.diff').write_bytes(patch)
text = packed.read_text()
start = text.index('<<<<<<< HEAD')
end = text.index('\n', text.index('>>>>>>> ', start))
text = text[:start] + '''    byte[] key = checkedKey(fingerprint);
    return getIfPresent(key, digest(key, key.length), false);
  }

  /** Returns an absent key as null, but distinguishes uncached data to preserve segment order. */
  byte[] getIfPresentCached(KeyBytesProvider fingerprint) throws IOException {
    byte[] key = checkedKey(fingerprint);
    return getIfPresent(key, digest(key, key.length), true);
  }

  // A segmented lookup computes and owns these bytes once, across all of its segment probes.
  byte[] getIfPresent(byte[] key, byte[] hash, boolean cachedOnly) throws IOException {''' + text[end:]
assert not re.search(r'^(<<<<<<<|=======|>>>>>>>)', text, re.M)
packed.write_text(text)
text = segmented.read_text()
start = text.index('<<<<<<< HEAD')
end = text.index('\n', text.index('>>>>>>> ', start))
text = text[:start] + '''    byte[] bytes = PackedFingerprintValueStore.checkedKey(key);
    return find(bucket, bytes, hash(bytes), false);
  }

  private byte[] find(Bucket bucket, byte[] key, byte[] digest, boolean cachedOnly)
      throws IOException {
    for (int i = bucket.segments.size() - 1; i >= 0; i--) {
      var store = store(bucket.segments.get(i), cachedOnly);
      // An unavailable newer index or matching record stops this search. Skipping it could
      // return a different older value than the ordinary lookup would return.
      byte[] value = store.getIfPresent(key, digest, cachedOnly);''' + text[end:]
text = text.replace('find(bucket, bytes, digest);', 'find(bucket, bytes, digest, false);')
old = '''      Bucket bucket = buckets[bucket(key)];
      synchronized (bucket) {
        return find(bucket, key, true);
      }'''
new = '''      byte[] bytes = PackedFingerprintValueStore.checkedKey(key);
      byte[] digest = hash(bytes);
      Bucket bucket = buckets[digest[0] & (BUCKETS - 1)];
      synchronized (bucket) {
        return find(bucket, bytes, digest, true);
      }'''
assert text.count(old) == 1
text = text.replace(old, new)
assert not re.search(r'^(<<<<<<<|=======|>>>>>>>)', text, re.M)
segmented.write_text(text)
subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java', '-jar',
                '/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar', '--replace',
                str(packed), str(segmented)], check=True)
subprocess.run(['git', 'add', str(packed.relative_to(S)), str(segmented.relative_to(S))], cwd=S, check=True)
subprocess.run(['git', 'diff', '--cached', '--check'], cwd=S, check=True)
delta = subprocess.check_output(['git', 'diff', '91d51164d3', '--', 'src'], cwd=S)
(R / 'local-optimizations-on-remote.patch').write_bytes(delta)
(R / 'resolution.json').write_text(json.dumps(dict(
    files=[str(x.relative_to(S)) for x in [packed, segmented]],
    decision='Thread cachedOnly through the prepared-key/hash lookup; compute key bytes and SHA-256 once for normal and cached-only segmented reads. Preserve the remote newest-segment stop rule, record verification, lifecycle locks, and no-network cached-only reads.',
    staged_tree=subprocess.check_output(['git', 'write-tree'], cwd=S, text=True).strip()), indent=2) + '\n')
print(delta.decode())
