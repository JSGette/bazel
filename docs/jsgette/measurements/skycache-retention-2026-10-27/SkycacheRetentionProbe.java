import java.io.PrintWriter;
import java.lang.classfile.*;
import java.lang.classfile.instruction.*;
import java.lang.constant.*;
import java.lang.instrument.*;
import java.nio.file.*;
import java.security.ProtectionDomain;
import java.util.*;

/** Normal-return diagnostic boundaries; never loaded for primary timing. */
public final class SkycacheRetentionProbe {
  static PrintWriter output;
  static final ThreadLocal<Map<String, ArrayDeque<Long>>> starts = ThreadLocal.withInitial(HashMap::new);
  static final Map<String,Set<String>> METHODS=Map.of(
      "RemoteChunkCache",Set.of("store"),
      "RemoteSkyCache",Set.of("downloadLocked","rememberAuthentication"),
      "SkyCacheTransport",Set.of("download"));
  public static void enter(String method) {
    starts.get().computeIfAbsent(method,k->new ArrayDeque<>()).push(System.nanoTime());
  }
  public static void leave(String method) {
    long end=System.nanoTime();long begin=starts.get().get(method).pop();
    synchronized(output) {
      output.println("{\"method\":\""+method+"\",\"thread\":\""+Thread.currentThread().getName()
          +"\",\"begin_ns\":"+begin+",\"end_ns\":"+end+",\"duration_ns\":"+(end-begin)+"}");
      output.flush();
    }
  }
  static void mark(CodeBuilder code,String label,String method) {
    code.ldc(label).invokestatic(ClassDesc.of("SkycacheRetentionProbe"),method,
        MethodTypeDesc.of(ConstantDescs.CD_void,ConstantDescs.CD_String));
  }
  public static void premain(String destination,Instrumentation instrumentation) throws Exception {
    output=new PrintWriter(Files.newBufferedWriter(Path.of(destination),StandardOpenOption.CREATE,StandardOpenOption.APPEND));
    instrumentation.addTransformer(new ClassFileTransformer() {
      @Override public byte[] transform(ClassLoader loader,String name,Class<?> type,ProtectionDomain domain,byte[] bytes) {
        if (!name.startsWith("com/google/devtools/build/lib/skyframe/serialization/")) return null;
        String simple=name.substring(name.lastIndexOf('/')+1);var selected=METHODS.get(simple);
        if (selected==null) return null;
        try {
          var cf=ClassFile.of();
          return cf.transformClass(cf.parse(bytes),(builder,element)-> {
            if (element instanceof MethodModel method && selected.contains(method.methodName().stringValue())) {
              String id=simple+"."+method.methodName().stringValue();
              builder.transformMethod(method,MethodTransform.transformingCode(new CodeTransform() {
                int forces=0;
                @Override public void atStart(CodeBuilder code) { mark(code,id,"enter"); }
                @Override public void accept(CodeBuilder code,CodeElement item) {
                  String label=null;
                  if (simple.equals("RemoteChunkCache") && item instanceof InvokeInstruction call) {
                    String called=call.name().stringValue();
                    if (called.equals("durableDirectory") || called.equals("ensureDirectory")) label="store.directory_setup";
                    if (called.equals("force")) label=++forces==1?"store.file_force":"store.directory_force";
                  }
                  if (label!=null) mark(code,label,"enter");
                  if (item instanceof ReturnInstruction) mark(code,id,"leave");
                  code.with(item);
                  if (label!=null) mark(code,label,"leave");
                }
              }));
            } else builder.with(element);
          });
        } catch(Throwable e) {
          synchronized(output) {output.println("{\"transform_error\":\""+simple+": "+e.getClass().getName()+"\"}");output.flush();}
          e.printStackTrace();return null;
        }
      }
    });
  }
}
