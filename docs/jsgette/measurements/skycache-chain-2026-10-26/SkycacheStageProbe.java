import java.io.PrintWriter;
import java.lang.classfile.*;
import java.lang.classfile.instruction.ReturnInstruction;
import java.lang.constant.*;
import java.lang.instrument.*;
import java.nio.file.*;
import java.security.ProtectionDomain;
import java.time.Instant;
import java.util.*;

/** Diagnostic method-boundary probes only. Product jars remain untouched. */
public class SkycacheStageProbe {
  static PrintWriter output;
  static final ThreadLocal<Map<String, ArrayDeque<Long>>> starts =
      ThreadLocal.withInitial(HashMap::new);
  static final Map<String, Set<String>> METHODS = Map.of(
      "LocalCacheSession", Set.of("finish", "closeStores"),
      "LocalInputSnapshot", Set.of("recordRepositoryBindings", "registerInheritedForFinalValidation",
          "isUnchanged", "captureSource", "globalWitnesses", "witnesses"),
      "LocalWitnessTable", Set.of("write"),
      "SegmentedFingerprintValueStore", Set.of("seal"),
      "RemoteSkyCache", Set.of("publishPending", "uploadLocal", "commit", "close", "prepareExclusive"));

  public static void enter(String method) {
    starts.get().computeIfAbsent(method, ignored -> new ArrayDeque<>()).push(System.nanoTime());
  }

  public static void leave(String method) {
    long end = System.nanoTime();
    var stack = starts.get().get(method);
    if (stack == null || stack.isEmpty()) {
      throw new AssertionError("unmatched diagnostic return " + method);
    }
    long begin = stack.pop();
    synchronized (output) {
      output.println("{\"method\":\"" + method + "\",\"thread\":\""
          + Thread.currentThread().getName() + "\",\"end_epoch_ms\":"
          + Instant.now().toEpochMilli() + ",\"duration_ns\":" + (end-begin) + "}");
      output.flush();
    }
  }

  public static void premain(String destination, Instrumentation instrumentation) throws Exception {
    output = new PrintWriter(Files.newBufferedWriter(Path.of(destination),
        StandardOpenOption.CREATE, StandardOpenOption.APPEND));
    instrumentation.addTransformer(new ClassFileTransformer() {
      @Override public byte[] transform(ClassLoader loader, String name, Class<?> cls,
          ProtectionDomain domain, byte[] bytes) {
        if (!name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/")) return null;
        String simple = name.substring(name.lastIndexOf('/')+1);
        Set<String> selected = METHODS.get(simple);
        if (selected == null) return null;
        try {
          var cf = ClassFile.of();
          return cf.transformClass(cf.parse(bytes), (builder, element) -> {
            if (element instanceof MethodModel method && selected.contains(method.methodName().stringValue())) {
              String id = simple + "." + method.methodName().stringValue() + method.methodType().stringValue();
              builder.transformMethod(method, MethodTransform.transformingCode(new CodeTransform() {
                @Override public void atStart(CodeBuilder code) {
                  code.ldc(id).invokestatic(ClassDesc.of("SkycacheStageProbe"), "enter",
                      MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String));
                }
                @Override public void accept(CodeBuilder code, CodeElement item) {
                  if (item instanceof ReturnInstruction) {
                    code.ldc(id).invokestatic(ClassDesc.of("SkycacheStageProbe"), "leave",
                        MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String));
                  }
                  code.with(item);
                }
              }));
            } else {
              builder.with(element);
            }
          });
        } catch (Throwable error) {
          synchronized (output) {
            output.println("{\"transform_error\":\"" + simple + ": " + error.getClass().getName() + "\"}");
            output.flush();
          }
          error.printStackTrace();
          return null;
        }
      }
    });
  }
}
