from pathlib import Path
import subprocess
R=Path('/home/jgett/REPOS/bazel-skycache-upstream-2026-10-25')
E=Path('/home/jgett/REPOS/.work/skycache-perf-options-2026-10-25')
p=R/'src/main/java/com/google/devtools/build/lib/skyframe/serialization/SharedValueDeserializationContext.java'
s=p.read_text();needle='      deserializeAndComplete(() -> maybeDecompressBytes(bytes));'
assert s.count(needle)==1
s=s.replace(needle,'''      // Uncompressed bytes are already in memory. Avoid a stream decoder's 4 KiB buffer
      // for each small shared value; compressed and chunked values still use streaming.
      if (bytes.length > 0 && bytes[0] == (byte) 0) {
        try {
          deserializeAndComplete(CodedInputStream.newInstance(bytes, 1, bytes.length - 1));
        } catch (SerializationException | IOException | RuntimeException | Error e) {
          onFailure(e);
        }
        return;
      }
      deserializeAndComplete(() -> maybeDecompressBytes(bytes));''')
s=s.replace('        deserializeAndComplete(inputStream);','        deserializeAndComplete(CodedInputStream.newInstance(inputStream));')
s=s.replace('    private void deserializeAndComplete(InputStream inputStream)','    private void deserializeAndComplete(CodedInputStream input)')
s=s.replace('codec.deserializeDeferred(innerContext, CodedInputStream.newInstance(inputStream));','codec.deserializeDeferred(innerContext, input);')
p.write_text(s)
subprocess.run(['/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java','-jar','/home/jgett/REPOS/.tools/google-java-format-1.28.0-all-deps.jar','--replace',str(p)],check=True)
(E/'lookup-array.patch').write_bytes(subprocess.check_output(['git','diff','--binary'],cwd=R))
subprocess.run(['git','diff','--check'],cwd=R,check=True)
print(subprocess.check_output(['git','diff','--stat'],cwd=R).decode())
