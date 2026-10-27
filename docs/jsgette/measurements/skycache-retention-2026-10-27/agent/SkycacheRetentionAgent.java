import java.lang.instrument.*;
import java.security.*;
import java.util.HexFormat;
public final class SkycacheRetentionAgent {
  public static void premain(String mode, Instrumentation instrumentation) {
    if (!mode.equals("baseline") && !mode.equals("retention")) throw new IllegalArgumentException(mode);
    instrumentation.addTransformer(new ClassFileTransformer() {
      @Override public byte[] transform(ClassLoader loader, String name, Class<?> type,
          ProtectionDomain domain, byte[] original) {
        if (!name.equals("com/google/devtools/build/lib/skyframe/serialization/AutoRegistry")
            && !name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCache")
            && !name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache")) return null;
        try (var stream = SkycacheRetentionAgent.class.getResourceAsStream("/variant-"+mode+"/"+name+".class")) {
          if (stream == null) throw new IllegalStateException(name);
          byte[] bytes = stream.readAllBytes();
          System.err.println("SKYCACHE_RETENTION_AGENT "+mode+" "+name+" sha256="
              +HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)));
          return bytes;
        } catch (Exception e) { throw new AssertionError(e); }
      }
    });
  }
}
