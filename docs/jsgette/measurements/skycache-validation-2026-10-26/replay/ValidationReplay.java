package com.google.devtools.build.lib.skyframe.serialization.local;

import java.io.DataInputStream;
import java.io.IOException;
import java.lang.invoke.MethodHandle;
import java.lang.invoke.MethodHandles;
import java.lang.invoke.MethodType;
import java.lang.invoke.VarHandle;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executors;

/** Diagnostic prototype only: invokes the existing validator and its private pure checks. */
public final class ValidationReplay {
  private static final MethodHandle NAMESPACE;
  private static final MethodHandle SOURCE;
  private static final MethodHandle MATCHES;
  private static final VarHandle WITNESSES;
  static {
    try {
      var lookup=MethodHandles.privateLookupIn(LocalInputSnapshot.class,MethodHandles.lookup());
      NAMESPACE=lookup.findVirtual(LocalInputSnapshot.class,"captureNamespace",MethodType.methodType(String.class));
      SOURCE=lookup.findVirtual(LocalInputSnapshot.class,"captureSource",MethodType.methodType(String.class,LocalContentHasher.class));
      MATCHES=lookup.findVirtual(LocalInputSnapshot.class,"matches",MethodType.methodType(boolean.class,LocalInputSnapshot.Witness.class,LocalContentHasher.class));
      WITNESSES=lookup.findVarHandle(LocalInputSnapshot.class,"witnesses",ConcurrentHashMap.class);
    } catch (ReflectiveOperationException e) { throw new ExceptionInInitializerError(e); }
  }

  private record Batch(double seconds,LocalContentHasher.Statistics statistics) {}

  private static Batch check(LocalInputSnapshot snapshot,Collection<LocalInputSnapshot.Witness> witnesses,
      LocalContentHasher contents) throws Exception {
    long started=System.nanoTime();
    try {
      for(var witness:witnesses) {
        if(!(boolean)MATCHES.invokeExact(snapshot,witness,contents)) {
          throw new IOException("witness mismatch: "+witness.path());
        }
      }
    } catch (Exception e) { throw e; }
      catch (Throwable e) { throw new AssertionError(e); }
    return new Batch((System.nanoTime()-started)/1e9,contents.statistics());
  }

  @SuppressWarnings("unchecked")
  private static void run(LocalInputSnapshot snapshot,String variant,int workers,boolean warmup) throws Throwable {
    long started=System.nanoTime();
    var sourceContents=new LocalContentHasher();
    if(!snapshot.cacheNamespace().equals((String)NAMESPACE.invokeExact(snapshot))
        || !snapshot.sourceDigest().equals((String)SOURCE.invokeExact(snapshot,sourceContents))) {
      throw new AssertionError("source changed");
    }
    double sourceSeconds=(System.nanoTime()-started)/1e9;
    var witnesses=((ConcurrentHashMap<String,LocalInputSnapshot.Witness>)WITNESSES.get(snapshot)).values();
    var batches=new ArrayList<Batch>();
    if(workers==1) {
      batches.add(check(snapshot,witnesses,sourceContents));
    } else {
      var partitions=new ArrayList<ArrayList<LocalInputSnapshot.Witness>>();
      for(int i=0;i<workers;i++) partitions.add(new ArrayList<>());
      int next=0;
      for(var witness:witnesses) {
        int hash=(witness.listing() || witness.digest().length()!=64 ? witness.path() : witness.digest()).hashCode();
        int index=variant.contains("round") ? next++%workers : Math.floorMod(hash^(hash>>>16),workers);
        partitions.get(index).add(witness);
      }
      try(var executor=Executors.newFixedThreadPool(workers)) {
        var pending=new ArrayList<java.util.concurrent.Future<Batch>>();
        for(var partition:partitions) {
          pending.add(executor.submit(()->check(snapshot,partition,variant.startsWith("shared") ? new LocalContentHasher(sourceContents) : new LocalContentHasher())));
        }
        for(var future:pending) batches.add(future.get());
      }
      batches.add(new Batch(sourceSeconds,sourceContents.statistics()));
    }
    double seconds=(System.nanoTime()-started)/1e9;
    long files=0,bytes=0,hits=0; int retained=0;
    for(var batch:batches) { var s=batch.statistics(); files+=s.filesHashed(); bytes+=s.bytesHashed();hits+=s.hits();retained+=s.retainedDigests(); }
    System.out.printf(java.util.Locale.ROOT,
        "{\"variant\":\"%s\",\"workers\":%d,\"warmup\":%s,\"seconds\":%.9f,\"source_seconds\":%.9f,\"witness_seconds\":%.9f,\"files_hashed\":%d,\"bytes_hashed\":%d,\"digest_hits\":%d,\"retained_digests\":%d,\"batch_seconds\":%s}%n",
        variant,workers,warmup,seconds,sourceSeconds,seconds-sourceSeconds,files,bytes,hits,retained,batches.stream().map(Batch::seconds).toList());
  }

  public static void main(String[] args) throws Throwable {
    Path manifest=Path.of(args[0]),objects=Path.of(args[1]);
    String namespace;
    try(var in=new DataInputStream(Files.newInputStream(manifest))) {in.readInt();in.readUTF();namespace=in.readUTF();}
    var header=LocalCacheSession.readManifestHeader(manifest,namespace,Long.MAX_VALUE);
    var witnesses=LocalWitnessTable.read(objects.resolve(header.id()),header.blocks(),Long.MAX_VALUE);
    var snapshot=new LocalInputSnapshot(Path.of(args[2]),Path.of(args[3]),Map.of());
    snapshot.registerInheritedForFinalValidation(witnesses);
    if(!snapshot.isUnchanged()) throw new AssertionError("production validator rejects fixture");
    run(snapshot,"serial",1,true);
    String[][] order={{"serial","digest-4","shared-4","shared-round-4"},
                      {"shared-4","shared-round-4","serial","digest-4"},
                      {"shared-round-4","digest-4","shared-4","serial"}};
    for(var block:order) for(String variant:block) {
      int workers=variant.equals("serial")?1:Integer.parseInt(variant.substring(variant.length()-1));
      run(snapshot,variant,workers,false);
    }
  }
}
