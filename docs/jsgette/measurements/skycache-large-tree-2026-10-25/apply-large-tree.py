"""Apply the new layout to the clean, native WSL source checkout."""
from pathlib import Path
import json, subprocess, shutil
ROOT=Path('/home/jgett/REPOS/bazel-skycache-v2')
TASK=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-24/ex')
RUN=Path('/home/jgett/REPOS/.work/skycache-large-tree-2026-10-25')
L=ROOT/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/local'
T=ROOT/'src/test/java/com/google/devtools/build/lib/skyframe/serialization/local'
assert subprocess.check_output(['git','-C',str(ROOT),'rev-parse','HEAD'],text=True).strip()=='71521fabb3420707c39d73329fa9aa9622e8c382'
assert not subprocess.check_output(['git','-C',str(ROOT),'status','--porcelain'],text=True).strip()
RUN.mkdir(exist_ok=False)
(RUN/'before.json').write_text(json.dumps({'base':'71521fabb3420707c39d73329fa9aa9622e8c382','stage':'compact witnesses and segmented packs; validation unchanged','old_layout':'leased-v1','new_layout':'leased-v2'},indent=2))
for f in (TASK/'work/large-tree').glob('*.java'):shutil.copy2(f,L/f.name)

def replace(s,a,b):
    assert a in s,a[:120]
    return s.replace(a,b)

p=L/'PackedFingerprintValueStore.java';s=p.read_text()
s=replace(s,'private final Map<ByteString, Location> pending;', 'private Map<ByteString, Location> pending;')
s=replace(s,'private final byte[] index;', 'private byte[] index;\n  private final String indexName;')
s=replace(s,'    syncDirectory(format);\n    FileChannel data =', '''    syncDirectory(format);
    return createFiles(namespace, "data.pack", "index");
  }

  static PackedFingerprintValueStore createSegment(Path namespace, String prefix) throws IOException {
    return createFiles(namespace, prefix + ".pack", prefix + ".index");
  }

  private static PackedFingerprintValueStore createFiles(Path namespace, String dataName, String indexName)
      throws IOException {
    FileChannel data =''')
s=replace(s,'namespace.resolve("data.pack"),\n            Set.of', 'namespace.resolve(dataName),\n            Set.of')
s=replace(s,'new PackedFingerprintValueStore(namespace, data, new ConcurrentHashMap<>(), null)', 'new PackedFingerprintValueStore(namespace, data, new ConcurrentHashMap<>(), null, indexName)')
s=replace(s,'    Path namespace = format.resolve(id);\n    byte[] index;', '''    return openFiles(format.resolve(id), "data.pack", "index", maxIndexBytes);
  }

  static PackedFingerprintValueStore openSegment(Path namespace, String prefix, long maxIndexBytes)
      throws IOException {
    return openFiles(namespace, prefix + ".pack", prefix + ".index", maxIndexBytes);
  }

  private static PackedFingerprintValueStore openFiles(
      Path namespace, String dataName, String indexName, long maxIndexBytes) throws IOException {
    byte[] index;''')
s=replace(s,'namespace.resolve("index"), READ', 'namespace.resolve(indexName), READ')
s=replace(s,'namespace.resolve("data.pack"), READ', 'namespace.resolve(dataName), READ')
s=replace(s,'new PackedFingerprintValueStore(namespace, data, null, index)', 'new PackedFingerprintValueStore(namespace, data, null, index, indexName)')
s=replace(s,'Path namespace, FileChannel data, Map<ByteString, Location> pending, byte[] index)', 'Path namespace, FileChannel data, Map<ByteString, Location> pending, byte[] index, String indexName)')
s=replace(s,'    this.namespace = namespace;', '    this.namespace = namespace;\n    this.indexName = indexName;')
start=s.index('      byte[] key = checkedKey(fingerprint);',s.index('public ListenableFuture<byte[]> get'))
end=s.index('\n    } catch (IOException e)',start)
s=s[:start]+'''      byte[] value = getIfPresent(fingerprint);
      if (value == null) {
        throw new IOException("packed cache key is absent");
      }
      return immediateFuture(value);'''+s[end:]
insert='''
  byte[] getIfPresent(KeyBytesProvider fingerprint) throws IOException {
    lifecycle.readLock().lock();
    try {
      if (closed) {
        throw new IOException("packed cache is closed");
      }
      byte[] key = checkedKey(fingerprint);
      byte[] hash = digest(key, key.length);
      Location location = pending == null ? find(hash) : pending.get(ByteString.copyFrom(hash));
      return location == null ? null : readRecord(location, key);
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  long dataBytes() throws IOException {
    return data.size();
  }

  byte[] indexDigest() {
    return Arrays.copyOfRange(index, index.length - 32, index.length);
  }

'''
s=s.replace('  /** Makes all completed writes durable',insert+'  /** Makes all completed writes durable')
s=replace(s,'Files.createLink(namespace.resolve("index"), temporary);','Files.createLink(namespace.resolve(indexName), temporary);')
s=replace(s,'      sealed = true;', '''      index = buffer.array();
      pending.clear();
      pending = null;
      sealed = true;''')
p.write_text(s)

p=L/'BUILD';s=p.read_text();s=replace(s,'srcs = ["PackedFingerprintValueStore.java"],','srcs = ["PackedFingerprintValueStore.java", "SegmentedFingerprintValueStore.java"],')
s=replace(s,'        "LocalInputSnapshot.java",','        "LocalInputSnapshot.java",\n        "LocalWitnessTable.java",');p.write_text(s)

for p in [L/'LocalCacheSession.java',L/'LocalCacheGarbageCollector.java',L/'AnalysisCacheIdleRetentionTask.java',*T.glob('*.java')]:
    s=p.read_text().replace('leased-v1','leased-v2')
    s=s.replace('leased-v2/publications-v3','leased-v2/publications-v4').replace('leased-v2/objects/packed-v1','leased-v2/objects/segmented-v1')
    if p.parent==L or p.name=='LocalCacheGarbageCollectorTest.java':
        s=s.replace('"publications-v3"','"publications-v4"').replace('"objects/packed-v1"','"objects/segmented-v1"').replace('"objects/packed-v1/"','"objects/segmented-v1/"')
    if p.name=='LocalCacheGarbageCollectorTest.java':s=s.replace('"publications-v3/"','"publications-v4/"')
    p.write_text(s)

p=L/'LocalCacheSession.java';s=p.read_text().replace('PackedFingerprintValueStore','SegmentedFingerprintValueStore').replace('0x534b5333','0x534b5334')
s=s.replace('long encodedBytes) {}','long metadataBytes) {}').replace('manifest.encodedBytes()','manifest.metadataBytes()')
s=replace(s,'''        List<LocalInputSnapshot.Witness> witnesses = inputs.globalWitnesses();
        out.writeInt(witnesses.size());
        for (var witness : witnesses) {
          out.writeUTF(witness.path());
          out.writeBoolean(witness.listing());
          out.writeUTF(witness.digest());
        }''','''        var blocks = LocalWitnessTable.write(objects.resolve(id), inputs.globalWitnesses());
        LocalWitnessTable.writeDescriptors(out, blocks);''')
s=replace(s,'''      List<LocalInputSnapshot.Witness> witnesses = new ArrayList<>();
      int witnessCount = boundedCount(in);
      for (int i = 0; i < witnessCount; i++) {
        witnesses.add(new LocalInputSnapshot.Witness(in.readUTF(), in.readBoolean(), in.readUTF()));
      }''','''      var blocks = LocalWitnessTable.readDescriptors(in);
      long metadataBytes = bytes.length + LocalWitnessTable.metadataBytes(blocks);
      if (metadataBytes > remainingBytes) {
        throw new SegmentedFingerprintValueStore.BudgetExceededException();
      }
      var witnesses = LocalWitnessTable.read(objects.resolve(namespace), blocks, remainingBytes - bytes.length);''')
s=replace(s,'return new Manifest(namespace, List.copyOf(witnesses), List.copyOf(parents), bytes.length);','return new Manifest(namespace, witnesses, List.copyOf(parents), metadataBytes);')
s=replace(s,'        } catch (IOException e) {\n          rejected.incrementAndGet();','''        } catch (SegmentedFingerprintValueStore.BudgetExceededException e) {
          readLimitSkips++;
          inspected.remove(filename);
        } catch (IOException e) {
          rejected.incrementAndGet();''')
p.write_text(s)

p=L/'LocalCacheGarbageCollector.java';s=p.read_text().replace('0x534b5333','0x534b5334')
a=s.index('      int witnesses = count(input);');b=s.index('      if (input.read() != -1',a)
s=s[:a]+'''      LocalWitnessTable.readDescriptors(input);
      byte[] scratch = new byte[32];
'''+s[b:]
s=replace(s,'if (!name.equals("data.pack") && !name.equals("index") && !pending(name, ".index")) {','''if (!name.equals("data.pack") && !name.equals("index") && !pending(name, ".index")
            && !name.matches("segment-[0-9]{5}\\\\.(pack|index)")
            && !name.matches("witnesses-[0-9]{5}\\\\.block")) {''')
p.write_text(s)

p=T/'LocalCacheSessionTest.java';s=p.read_text()
s=s.replace('p.getFileName().toString().equals("data.pack")','p.getFileName().toString().endsWith(".pack")')
s=replace(s,'long bytes = Files.size(manifest) + Files.size(index);','''long bytes;
    try (var stored = SegmentedFingerprintValueStore.open(format, id, Long.MAX_VALUE)) {
      bytes = Files.size(manifest) + stored.indexBytes();
    }''')
s=replace(s,'() -> PackedFingerprintValueStore.open(format, id, Files.size(index) - 1)','() -> SegmentedFingerprintValueStore.open(format, id, Files.size(index) - 1)')
p.write_text(s)
p=T/'LocalCacheGarbageCollectorTest.java';s=p.read_text().replace('0x534b5333','0x534b5334')
s=replace(s,'''      out.writeInt(1);
      out.writeUTF("file");
      out.writeBoolean(false);
      out.writeUTF("e".repeat(64));''','''      out.writeInt(0); // No witness blocks are needed by these GC framing fixtures.''')
p.write_text(s)
print(subprocess.check_output(['git','-C',str(ROOT),'diff','--stat'],text=True))
