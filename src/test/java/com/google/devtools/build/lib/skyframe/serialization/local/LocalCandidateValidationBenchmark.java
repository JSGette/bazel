// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.sun.management.ThreadMXBean;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.atomic.AtomicLong;

/** Measures repeated validation of a publication whose last input is not materialized yet. */
public final class LocalCandidateValidationBenchmark {
  private static final KeyBytesProvider KEY =
      new KeyBytesProvider() {
        @Override
        public byte[] toBytes() {
          return new byte[] {17};
        }

        @Override
        public byte[] concat(byte[] suffix) {
          byte[] result = new byte[suffix.length + 1];
          result[0] = 17;
          System.arraycopy(suffix, 0, result, 1, suffix.length);
          return result;
        }
      };

  public static void main(String[] args) throws Exception {
    Path root = Files.createDirectory(Path.of(args[0]));
    int count = Integer.parseInt(args[1]);
    Path workspace = Files.createDirectory(root.resolve("workspace"));
    Path output = Files.createDirectory(root.resolve("output"));
    Path cache = Files.createDirectory(root.resolve("cache"));
    Path files = Files.createDirectory(root.resolve("inputs"));
    var inputs = new LocalInputSnapshot(workspace, output, Map.of());
    byte[] contents = "x".repeat(1024).getBytes(StandardCharsets.UTF_8);
    for (int i = 0; i < count; i++) {
      inputs.observe(
          Files.write(files.resolve(String.format(Locale.ROOT, "a%06d", i)), contents), false);
    }
    Path missing = Files.writeString(files.resolve("zz-missing"), "ready");
    inputs.observe(missing, false);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(KEY, new byte[] {7}).get();
    if (!writer.finish(true)) {
      throw new AssertionError("writer did not publish");
    }
    Files.delete(missing);
    var clock = new AtomicLong();
    var reader =
        new LocalCacheSession(
            cache, new LocalInputSnapshot(workspace, output, Map.of()), clock::get);
    ThreadMXBean bean = (ThreadMXBean) ManagementFactory.getThreadMXBean();
    if (!bean.isThreadAllocatedMemorySupported()) {
      throw new IllegalStateException("thread allocation measurement unavailable");
    }
    bean.setThreadAllocatedMemoryEnabled(true);
    try {
      for (int round = 0; round < 5; round++) {
        clock.addAndGet(2_000_000_000L);
        long before = bean.getCurrentThreadAllocatedBytes();
        long start = System.nanoTime();
        try {
          reader.get(KEY).get();
          throw new AssertionError("missing witness was accepted");
        } catch (ExecutionException expected) {
          // A miss is mandatory; the measured work is rejecting the same unavailable candidate.
        }
        long nanos = System.nanoTime() - start;
        long bytes = bean.getCurrentThreadAllocatedBytes() - before;
        System.out.printf(
            Locale.ROOT,
            "{\"round\":%d,\"files\":%d,\"milliseconds\":%.3f,\"allocated_bytes\":%d}%n",
            round,
            count,
            nanos / 1e6,
            bytes);
      }
      Files.writeString(missing, "ready");
      clock.addAndGet(2_000_000_000L);
      if (!java.util.Arrays.equals(reader.get(KEY).get(), new byte[] {7})) {
        throw new AssertionError("materialized inputs were not revalidated");
      }
    } finally {
      reader.finish(false);
    }
  }

  private LocalCandidateValidationBenchmark() {}
}
