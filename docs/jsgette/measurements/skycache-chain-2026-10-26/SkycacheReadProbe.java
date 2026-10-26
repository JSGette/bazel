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
public class SkycacheReadProbe {
  static PrintWriter output;
  static final ThreadLocal<Map<String, ArrayDeque<Long>>> starts =
      ThreadLocal.withInitial(HashMap::new);
  static final Map<String, Set<String>> METHODS = Map.ofEntries(
      Map.entry("LocalSkycacheModule", Set.of("beforeCommand","afterCommand")),
      Map.entry("LocalCacheSession", Set.of("refreshCandidates","finish","writeWitnesses","closeStores")),
      Map.entry("LocalInputSnapshot", Set.of("captureNamespace","captureSource","recordRepositoryBindings","registerInheritedForFinalValidation","isUnchanged","hasSameGlobalWitnesses")),
      Map.entry("LocalWitnessTable", Set.of("read","write","copy")),
      Map.entry("LocalAnalysisCacheClient", Set.of("lookup")),
      Map.entry("SegmentedFingerprintValueStore", Set.of("open","seal")),
      Map.entry("RemoteSkyCache", Set.of("prepareExclusive","reuseRetainedChunk","publishPending","uploadLocal","commit","close")));


  static final java.util.concurrent.ConcurrentHashMap<String, java.util.concurrent.atomic.LongAdder> counters = new java.util.concurrent.ConcurrentHashMap<>();
  static final java.util.concurrent.ConcurrentHashMap<Object, SeenChunk> chunks = new java.util.concurrent.ConcurrentHashMap<>();
  static final class SeenChunk { final BitSet blocks=new BitSet(); final long size; SeenChunk(long size) { this.size=size; } }
  public static void count(String key) { counters.computeIfAbsent(key,k->new java.util.concurrent.atomic.LongAdder()).increment(); }
  public static void requested(String kind, java.nio.ByteBuffer buffer) {
    count(kind+".calls");
    counters.computeIfAbsent(kind+".bytes",k->new java.util.concurrent.atomic.LongAdder()).add(buffer.remaining());
  }
  public static void covered(Object digest, int offset, int length) {
    count("cached_ranges.calls");
    counters.computeIfAbsent("cached_ranges.bytes",k->new java.util.concurrent.atomic.LongAdder()).add(length);
    if(chunks.size()>=4096 && !chunks.containsKey(digest)) { count("coverage_overflow"); return; }
    SeenChunk seen=chunks.computeIfAbsent(digest,d->{
      try { return new SeenChunk((Long)d.getClass().getMethod("getSizeBytes").invoke(d)); }
      catch(ReflectiveOperationException e) { throw new AssertionError(e); }
    });
    synchronized(seen) { if(length>0) seen.blocks.set(offset/4096,(offset+length+4095)/4096); }
  }
  static void summary() {
    long uniqueBytes=0,uniqueBlocks=0;
    for(var seen:chunks.values()) synchronized(seen) {
      for(int b=seen.blocks.nextSetBit(0);b>=0;b=seen.blocks.nextSetBit(b+1)) {
        uniqueBlocks++; uniqueBytes+=Math.min(4096,seen.size-4096L*b);
      }
    }
    synchronized(output) {
      output.print("{\"summary\":true,\"unique_cached_range_chunks\":"+chunks.size()+",\"unique_cached_range_blocks\":"+uniqueBlocks+",\"unique_cached_range_bytes\":"+uniqueBytes+",\"counters\":{");
      boolean first=true;
      for(var e:new TreeMap<>(counters).entrySet()) {
        if(!first) output.print(",");first=false;
        output.print("\""+e.getKey()+"\":"+e.getValue().sum());
      }
      output.println("}}");output.flush();
    }
  }

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
    Runtime.getRuntime().addShutdownHook(new Thread(SkycacheReadProbe::summary));
    instrumentation.addTransformer(new ClassFileTransformer() {
      @Override public byte[] transform(ClassLoader loader, String name, Class<?> cls,
          ProtectionDomain domain, byte[] bytes) {
        if (!name.startsWith("com/google/devtools/build/lib/skyframe/serialization/local/")) return null;
        String simple = name.substring(name.lastIndexOf('/')+1);
        Set<String> selected = METHODS.get(simple);
        boolean hotClass=simple.equals("RemoteSkyCache") || simple.equals("RemoteSkyCache$FileSource") || simple.equals("PackedFingerprintValueStore");
        if (selected == null && !hotClass) return null;
        try {
          var cf = ClassFile.of();
          return cf.transformClass(cf.parse(bytes), (builder, element) -> {
            if (element instanceof MethodModel method && ((selected!=null && selected.contains(method.methodName().stringValue())) || (hotClass && Set.of("cached","readChunk","blockDigest","readCachedBytes","read","readData","isCached","openSegment").contains(method.methodName().stringValue())))) {
              boolean timed=selected!=null && selected.contains(method.methodName().stringValue());
              String member=method.methodName().stringValue();
              String descriptor=method.methodType().stringValue();
              String id = simple + "." + method.methodName().stringValue() + method.methodType().stringValue();
              builder.transformMethod(method, MethodTransform.transformingCode(new CodeTransform() {
                @Override public void atStart(CodeBuilder code) {
                  if(timed) code.ldc(id).invokestatic(ClassDesc.of("SkycacheReadProbe"), "enter",
                      MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String));
                  else code.ldc(simple+"."+member+descriptor).invokestatic(ClassDesc.of("SkycacheReadProbe"), "count", MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String));
                  if(simple.equals("RemoteSkyCache") && member.equals("readCachedBytes"))
                    code.aload(1).iload(2).iload(3).invokestatic(ClassDesc.of("SkycacheReadProbe"),"covered",MethodTypeDesc.of(ConstantDescs.CD_void,ConstantDescs.CD_Object,ConstantDescs.CD_int,ConstantDescs.CD_int));
                  if(simple.equals("RemoteSkyCache$FileSource") && member.equals("read") && descriptor.equals("(Ljava/lang/String;Ljava/nio/ByteBuffer;JZ)V"))
                    code.ldc("remote_requested").aload(2).invokestatic(ClassDesc.of("SkycacheReadProbe"),"requested",MethodTypeDesc.of(ConstantDescs.CD_void,ConstantDescs.CD_String,ClassDesc.of("java.nio.ByteBuffer")));
                  if(simple.equals("PackedFingerprintValueStore") && member.equals("readData"))
                    code.ldc("packed_requested").aload(1).invokestatic(ClassDesc.of("SkycacheReadProbe"),"requested",MethodTypeDesc.of(ConstantDescs.CD_void,ConstantDescs.CD_String,ClassDesc.of("java.nio.ByteBuffer")));
                }
                @Override public void accept(CodeBuilder code, CodeElement item) {
                  if (timed && item instanceof ReturnInstruction) {
                    code.ldc(id).invokestatic(ClassDesc.of("SkycacheReadProbe"), "leave",
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
