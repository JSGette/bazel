// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Locale;

/** Standalone local-filesystem benchmark; each invocation requires a fresh directory. */
public final class ConcurrentFileStoreBenchmark {
  private record Key(int index) implements KeyBytesProvider {
    @Override
    public byte[] toBytes() {
      return ByteBuffer.allocate(4).putInt(index).array();
    }

    @Override
    public byte[] concat(byte[] suffix) {
      return ByteBuffer.allocate(4 + suffix.length).putInt(index).put(suffix).array();
    }
  }

  public static void main(String[] args) throws Exception {
    Path root = Files.createDirectory(Path.of(args[0]));
    int count = Integer.parseInt(args[1]);
    int size = Integer.parseInt(args[2]);
    var store = ConcurrentFileFingerprintValueStore.create(root, "ab".repeat(32));
    byte[] value = new byte[size];
    Arrays.fill(value, (byte) 73);
    // Warm the JVM and filesystem code without populating the measured keys.
    for (int i = 0; i < 200; i++) {
      store.put(new Key(-i - 1), value).get();
    }
    long start = System.nanoTime();
    for (int i = 0; i < count; i++) {
      if (!store.put(new Key(i), value).get()) {
        throw new AssertionError("first publication was not novel");
      }
    }
    long novel = System.nanoTime() - start;
    for (int round = 0; round < 5; round++) {
      start = System.nanoTime();
      for (int i = 0; i < count; i++) {
        if (store.put(new Key(i), value).get()) {
          throw new AssertionError("duplicate publication was novel");
        }
      }
      long duplicates = System.nanoTime() - start;
      start = System.nanoTime();
      for (int i = 0; i < count; i++) {
        if (!Arrays.equals(store.get(new Key(i)).get(), value)) {
          throw new AssertionError("retrieval changed bytes");
        }
      }
      long reads = System.nanoTime() - start;
      System.out.printf(
          Locale.ROOT,
          "{\"round\":%d,\"entries\":%d,\"value_bytes\":%d,\"novel_ms\":%.3f,"
              + "\"duplicate_ms\":%.3f,\"read_ms\":%.3f}%n",
          round,
          count,
          size,
          novel / 1e6,
          duplicates / 1e6,
          reads / 1e6);
    }
  }

  private ConcurrentFileStoreBenchmark() {}
}
