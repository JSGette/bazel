package com.google.devtools.build.lib.skyframe.serialization.local;

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.*;
import java.util.*;

/** Replay content hashing only; production LocalContentHasher is compiled unchanged. */
public final class HashReplay {
  record Variant(String name, long threshold, int capacity) {}
  static final Variant BASE = new Variant("baseline", 1048576, 256);
  static final Variant MID = new Variant("64KiB", 65536, 16384);
  static final Variant SMALL = new Variant("8KiB", 8192, 65536);
  static String expected;

  static void run(List<Path> paths, Variant variant, int block, boolean warmup) throws Exception {
    System.gc();
    var hasher = new LocalContentHasher(variant.threshold(), variant.capacity());
    var aggregate = MessageDigest.getInstance("SHA-256");
    long start=System.nanoTime();
    for (Path path : paths) {
      var fingerprint=MessageDigest.getInstance("SHA-256");
      hasher.hashContents(path, fingerprint, Files.size(path));
      aggregate.update(fingerprint.digest());
    }
    long elapsed=System.nanoTime()-start;
    var stats=hasher.statistics();
    String digest=HexFormat.of().formatHex(aggregate.digest());
    if (expected == null) expected=digest;
    if (!digest.equals(expected)) throw new AssertionError("content digest sequence changed");
    System.out.printf(Locale.ROOT,
        "{\"variant\":\"%s\",\"block\":%d,\"warmup\":%s,\"seconds\":%.9f,"
        + "\"files_hashed\":%d,\"bytes_hashed\":%d,\"hits\":%d,\"retained_digests\":%d,"
        + "\"threshold_bytes\":%d,\"capacity\":%d,\"aggregate_sha256\":\"%s\"}%n",
        variant.name(), block, warmup, elapsed/1e9, stats.filesHashed(), stats.bytesHashed(),
        stats.hits(), stats.retainedDigests(), variant.threshold(), variant.capacity(), digest);
  }

  public static void main(String[] args) throws Exception {
    var paths=new ArrayList<Path>();
    try (var in=new DataInputStream(new BufferedInputStream(Files.newInputStream(Path.of(args[0]))))) {
      int count=in.readInt();
      if (count<1 || count>1_000_000) throw new IOException("path count");
      for (int i=0;i<count;i++) {
        int length=in.readInt();
        if (length<1 || length>65535) throw new IOException("path length");
        byte[] name=in.readNBytes(length);
        if (name.length!=length) throw new EOFException();
        paths.add(Path.of(new String(name,StandardCharsets.UTF_8)));
      }
      if (in.read()!=-1) throw new IOException("trailing bytes");
    }
    run(paths,BASE,0,true);
    Variant[][] orders={{BASE,MID,SMALL},{SMALL,MID,BASE},{MID,BASE,SMALL}};
    for (int i=0;i<orders.length;i++) for (var variant:orders[i]) run(paths,variant,i+1,false);
  }
}
