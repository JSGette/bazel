// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.Locale;

/** Standalone file/pack benchmark; each invocation requires a fresh directory. */
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
    boolean packed = args.length > 3 && args[3].equals("packed");
    FingerprintValueStore store =
        packed
            ? PackedFingerprintValueStore.create(root, "ab".repeat(32))
            : ConcurrentFileFingerprintValueStore.create(root, "ab".repeat(32));
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
    start = System.nanoTime();
    if (store instanceof PackedFingerprintValueStore writer) {
      writer.seal();
      writer.close();
    }
    long seal = System.nanoTime() - start;
    start = System.nanoTime();
    store =
        packed
            ? PackedFingerprintValueStore.open(root, "ab".repeat(32))
            : ConcurrentFileFingerprintValueStore.create(root, "ab".repeat(32));
    long open = System.nanoTime() - start;
    for (int round = 0; round < 5; round++) {
      start = System.nanoTime();
      if (!packed) {
        for (int i = 0; i < count; i++) {
          if (store.put(new Key(i), value).get()) {
            throw new AssertionError("duplicate publication was novel");
          }
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
          "{\"backend\":\"%s\",\"round\":%d,\"entries\":%d,\"value_bytes\":%d,"
              + "\"novel_ms\":%.3f,\"seal_ms\":%.3f,\"publication_ms\":%.3f,\"open_ms\":%.3f,"
              + "\"duplicate_ms\":%s,\"read_ms\":%.3f}%n",
          packed ? "packed" : "files",
          round,
          count,
          size,
          novel / 1e6,
          seal / 1e6,
          (novel + seal) / 1e6,
          open / 1e6,
          packed ? "null" : Double.toString(duplicates / 1e6),
          reads / 1e6);
    }
    if (store instanceof PackedFingerprintValueStore reader) {
      reader.close();
    }
  }

  private ConcurrentFileStoreBenchmark() {}
}
