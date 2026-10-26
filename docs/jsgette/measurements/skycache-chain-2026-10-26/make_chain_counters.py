from pathlib import Path
import subprocess

root=Path('/home/jgett/REPOS/.work/skycache-chain-2026-10-26')
jdk=Path('/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin')
source=(root/'SkycacheChainProbe.java').read_text().replace('SkycacheChainProbe','SkycacheReadProbe')
extra='''
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
      output.print("{\\"summary\\":true,\\"unique_cached_range_chunks\\":"+chunks.size()+",\\"unique_cached_range_blocks\\":"+uniqueBlocks+",\\"unique_cached_range_bytes\\":"+uniqueBytes+",\\"counters\\":{");
      boolean first=true;
      for(var e:new TreeMap<>(counters).entrySet()) {
        if(!first) output.print(",");first=false;
        output.print("\\""+e.getKey()+"\\":"+e.getValue().sum());
      }
      output.println("}}");output.flush();
    }
  }
'''
source=source.replace('  public static void enter(',extra+'\n  public static void enter(')
source=source.replace('    instrumentation.addTransformer(', '    Runtime.getRuntime().addShutdownHook(new Thread(SkycacheReadProbe::summary));\n    instrumentation.addTransformer(')
source=source.replace('        if (selected == null) return null;', '        boolean hotClass=simple.equals("RemoteSkyCache") || simple.equals("RemoteSkyCache$FileSource") || simple.equals("PackedFingerprintValueStore");\n        if (selected == null && !hotClass) return null;')
source=source.replace('if (element instanceof MethodModel method && selected.contains(method.methodName().stringValue())) {', '''if (element instanceof MethodModel method && ((selected!=null && selected.contains(method.methodName().stringValue())) || (hotClass && Set.of("cached","readChunk","blockDigest","readCachedBytes","read","readData","isCached","openSegment").contains(method.methodName().stringValue())))) {
              boolean timed=selected!=null && selected.contains(method.methodName().stringValue());
              String name=method.methodName().stringValue();
              String descriptor=method.methodType().stringValue();''')
source=source.replace('''                  code.ldc(id).invokestatic(ClassDesc.of("SkycacheReadProbe"), "enter",
                      MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String));''','''                  if(timed) code.ldc(id).invokestatic(ClassDesc.of("SkycacheReadProbe"), "enter",
                      MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String));
                  else code.ldc(simple+"."+name+descriptor).invokestatic(ClassDesc.of("SkycacheReadProbe"), "count", MethodTypeDesc.of(ConstantDescs.CD_void, ConstantDescs.CD_String));
                  if(simple.equals("RemoteSkyCache") && name.equals("readCachedBytes"))
                    code.aload(1).iload(2).iload(3).invokestatic(ClassDesc.of("SkycacheReadProbe"),"covered",MethodTypeDesc.of(ConstantDescs.CD_void,ConstantDescs.CD_Object,ConstantDescs.CD_int,ConstantDescs.CD_int));
                  if(simple.equals("RemoteSkyCache$FileSource") && name.equals("read") && descriptor.equals("(Ljava/lang/String;Ljava/nio/ByteBuffer;JZ)V"))
                    code.ldc("remote_requested").aload(2).invokestatic(ClassDesc.of("SkycacheReadProbe"),"requested",MethodTypeDesc.of(ConstantDescs.CD_void,ConstantDescs.CD_String,ClassDesc.of("java.nio.ByteBuffer")));
                  if(simple.equals("PackedFingerprintValueStore") && name.equals("readData"))
                    code.ldc("packed_requested").aload(1).invokestatic(ClassDesc.of("SkycacheReadProbe"),"requested",MethodTypeDesc.of(ConstantDescs.CD_void,ConstantDescs.CD_String,ClassDesc.of("java.nio.ByteBuffer")));''')
source=source.replace('if (item instanceof ReturnInstruction) {','if (timed && item instanceof ReturnInstruction) {')
source=source.replace('String name=method.methodName()', 'String member=method.methodName()').replace('name.equals(', 'member.equals(').replace('simple+"."+name+descriptor','simple+"."+member+descriptor')
(root/'SkycacheReadProbe.java').write_text(source)
(root/'read-probe-manifest.mf').write_text('Manifest-Version: 1.0\nPremain-Class: SkycacheReadProbe\n\n')
subprocess.run([str(jdk/'javac'),'-d',str(root/'read-probe-classes'),str(root/'SkycacheReadProbe.java')],check=True)
subprocess.run([str(jdk/'jar'),'--create','--file',str(root/'read-probe.jar'),'--manifest',str(root/'read-probe-manifest.mf'),'-C',str(root/'read-probe-classes'),'.'],check=True)
work=Path(__file__).parent
harness=(work/'skycache_chain_benchmark.py').read_text().replace("ROOT = REPOS / '.work/skycache-chain-2026-10-26/runs'", "ROOT = REPOS / '.work/skycache-chain-2026-10-26/diagnostics'")
harness=harness.replace("ROOT.parent/'chain-probe.jar'","ROOT.parent/'read-probe.jar'")
start=harness.index("        run('diagnostic-local'")
end=harness.index("        results['status']='passed'",start)
harness=harness[:start]+'''        run('diagnostic-local','local',0,warmup=True,diagnostic=True)
        run('diagnostic-retained','remote-warm',1,warmup=True,diagnostic=True)
''' +harness[end:]
(work/'skycache_chain_diagnostic.py').write_text(harness)
print('Prepared detailed read-count diagnostics')
