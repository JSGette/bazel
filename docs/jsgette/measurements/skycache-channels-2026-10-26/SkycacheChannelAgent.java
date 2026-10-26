import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.MessageDigest;
import java.security.ProtectionDomain;
import java.util.HexFormat;

/** Benchmark-only class replacement before first definition; never changes installation identity. */
public final class SkycacheChannelAgent {
  private static byte[] resource(String name) throws Exception {
    try (var stream = SkycacheChannelAgent.class.getResourceAsStream("/" + name)) {
      if (stream == null) throw new IllegalStateException(name);
      return stream.readAllBytes();
    }
  }

  public static void premain(String mode, Instrumentation instrumentation) throws Exception {
    if (!mode.equals("baseline") && !mode.equals("channels128") && !mode.equals("channels256")) {
      throw new IllegalArgumentException(mode);
    }
    byte[] registry = resource("registry.bin");
    byte[] reader = mode.equals("baseline") ? null : resource(mode + ".bin");
    instrumentation.addTransformer(new ClassFileTransformer() {
      @Override public byte[] transform(ClassLoader loader, String name, Class<?> type,
          ProtectionDomain domain, byte[] original) {
        byte[] replacement = switch (name) {
          case "com/google/devtools/build/lib/skyframe/serialization/AutoRegistry" -> registry;
          case "com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache" -> reader;
          default -> null;
        };
        if (replacement != null) {
          try {
            System.err.println("SKYCACHE_CHANNEL_AGENT " + mode + " " + name + " sha256="
                + HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(replacement)));
          } catch (Exception e) {
            throw new AssertionError(e);
          }
        }
        return replacement;
      }
    });
  }
}
