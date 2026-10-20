// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.sun.management.ThreadMXBean;
import java.lang.management.ManagementFactory;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Locale;
import java.util.Map;

/** Measures candidate admission cost and retained pack descriptors on a missing key. */
public final class LocalAdmissionBenchmark {
  private record Key(int value) implements KeyBytesProvider {
    @Override
    public byte[] toBytes() {
      return ByteBuffer.allocate(4).putInt(value).array();
    }

    @Override
    public byte[] concat(byte[] suffix) {
      return ByteBuffer.allocate(4 + suffix.length).putInt(value).put(suffix).array();
    }
  }

  public static void main(String[] args) throws Exception {
    Path root = Files.createDirectory(Path.of(args[0]));
    int count = Integer.parseInt(args[1]);
    int publications = Integer.parseInt(args[2]);
    Path workspace = Files.createDirectory(root.resolve("workspace"));
    Path output = Files.createDirectory(root.resolve("output"));
    Path cache = Files.createDirectory(root.resolve("cache"));
    Path files = Files.createDirectory(root.resolve("inputs"));
    var paths = new ArrayList<Path>();
    byte[] bytes = new byte[1024];
    for (int i = 0; i < count; i++) {
      Arrays.fill(bytes, (byte) i);
      paths.add(Files.write(files.resolve(String.format(Locale.ROOT, "%06d", i)), bytes));
    }
    for (int writerIndex = 0; writerIndex < publications; writerIndex++) {
      var inputs = new LocalInputSnapshot(workspace, output, Map.of());
      for (Path path : paths) {
        inputs.observe(path, false);
      }
      var writer = new LocalCacheSession(cache, inputs);
      writer.put(new Key(writerIndex), new byte[] {(byte) writerIndex}).get();
      if (!writer.finish(true)) {
        throw new AssertionError("writer did not publish");
      }
    }
    ThreadMXBean bean = (ThreadMXBean) ManagementFactory.getThreadMXBean();
    if (!bean.isThreadAllocatedMemorySupported()) {
      throw new IllegalStateException("thread allocation measurement unavailable");
    }
    bean.setThreadAllocatedMemoryEnabled(true);
    for (int round = -2; round < 5; round++) {
      var reader =
          new LocalCacheSession(cache, new LocalInputSnapshot(workspace, output, Map.of()));
      try {
        long before = bean.getCurrentThreadAllocatedBytes();
        long start = System.nanoTime();
        try {
          reader.get(new Key(-1)).get();
          throw new AssertionError("missing key unexpectedly resolved");
        } catch (java.util.concurrent.ExecutionException expected) {
          // A missing key forces discovery regardless of which publications fit the budget.
        }
        long nanos = System.nanoTime() - start;
        long allocated = bean.getCurrentThreadAllocatedBytes() - before;
        long descriptors = openDescriptors(cache);
        if (round >= 0) {
          System.out.printf(
              Locale.ROOT,
              "{\"round\":%d,\"files\":%d,\"publications\":%d,\"milliseconds\":%.3f,\"allocated_bytes\":%d,\"retained_descriptors\":%d}%n",
              round,
              count,
              publications,
              nanos / 1e6,
              allocated,
              descriptors);
        }
      } finally {
        reader.finish(false);
        if (openDescriptors(cache) != 0) {
          throw new AssertionError("reader leaked descriptors");
        }
      }
    }
  }

  private static long openDescriptors(Path cache) throws java.io.IOException {
    long count = 0;
    try (var descriptors = Files.list(Path.of("/proc/self/fd"))) {
      for (Path descriptor : descriptors.toList()) {
        try {
          if (Files.readSymbolicLink(descriptor).startsWith(cache)) {
            count++;
          }
        } catch (java.nio.file.NoSuchFileException ignored) {
          // Other JVM threads may close unrelated descriptors while procfs is enumerated.
        }
      }
    }
    return count;
  }

  private LocalAdmissionBenchmark() {}
}
