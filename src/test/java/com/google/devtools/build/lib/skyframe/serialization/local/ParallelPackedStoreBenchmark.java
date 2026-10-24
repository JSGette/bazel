// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** Repeated positional pack writes (including seal), then verified concurrent reads. */
public final class ParallelPackedStoreBenchmark {
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

  private interface Operation {
    void run(int i) throws Exception;
  }

  private static double measure(ExecutorService pool, int width, int count, Operation operation)
      throws Exception {
    var ready = new java.util.concurrent.CountDownLatch(width);
    var go = new java.util.concurrent.CountDownLatch(1);
    var tasks = new ArrayList<java.util.concurrent.Future<?>>();
    for (int worker = 0; worker < width; worker++) {
      final int offset = worker;
      tasks.add(
          pool.submit(
              () -> {
                ready.countDown();
                go.await();
                for (int i = offset; i < count; i += width) operation.run(i);
                return null;
              }));
    }
    ready.await();
    long start = System.nanoTime();
    go.countDown();
    for (var task : tasks) task.get();
    return (System.nanoTime() - start) / 1e6;
  }

  public static void main(String[] args) throws Exception {
    Path root = Files.createDirectory(Path.of(args[0]));
    int count = Integer.parseInt(args[1]), size = Integer.parseInt(args[2]);
    byte[] value = new byte[size];
    for (int i = 0; i < size; i++) value[i] = (byte) (i * 31);
    int number = 0;
    for (int round = -2; round < 5; round++) {
      int[] widths = round % 2 == 0 ? new int[] {1, 2, 4, 8} : new int[] {8, 4, 2, 1};
      for (int width : widths) {
        String id = String.format(Locale.ROOT, "%064x", ++number);
        try (var pool = Executors.newFixedThreadPool(width)) {
          double write, seal, open, read;
          try (var writer = PackedFingerprintValueStore.create(root, id)) {
            write =
                measure(
                    pool,
                    width,
                    count,
                    i -> {
                      if (!writer.put(new Key(i), value).get())
                        throw new AssertionError("not novel");
                    });
            long start = System.nanoTime();
            writer.seal();
            seal = (System.nanoTime() - start) / 1e6;
          }
          long start = System.nanoTime();
          try (var reader = PackedFingerprintValueStore.open(root, id)) {
            open = (System.nanoTime() - start) / 1e6;
            read =
                measure(
                    pool,
                    width,
                    count,
                    i -> {
                      if (!Arrays.equals(reader.get(new Key(i)).get(), value))
                        throw new AssertionError("bad bytes");
                    });
          }
          if (round >= 0)
            System.out.printf(
                Locale.ROOT,
                "{\"round\":%d,\"threads\":%d,\"entries\":%d,\"value_bytes\":%d,\"write_ms\":%.3f,\"seal_ms\":%.3f,\"durable_write_ms\":%.3f,\"open_ms\":%.3f,\"read_ms\":%.3f}%n",
                round,
                width,
                count,
                size,
                write,
                seal,
                write + seal,
                open,
                read);
        }
        // Only this fixture's closed pack is removed; each round starts with a new publication.
        Files.delete(root.resolve(id).resolve("data.pack"));
        Files.delete(root.resolve(id).resolve("index"));
        Files.delete(root.resolve(id));
      }
    }
  }
}
