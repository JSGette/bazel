// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.sun.management.ThreadMXBean;
import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;

/** Measures full content validation, including files spanning multiple I/O buffers. */
public final class LocalInputHashingBenchmark {
  public static void main(String[] args) throws Exception {
    Path root = Files.createDirectory(Path.of(args[0]));
    int count = Integer.parseInt(args[1]);
    Path workspace = Files.createDirectory(root.resolve("workspace"));
    Path output = Files.createDirectory(root.resolve("output"));
    Path files = Files.createDirectory(root.resolve("inputs"));
    var snapshot = new LocalInputSnapshot(workspace, output, Map.of());
    byte[] small = "x".repeat(1024).getBytes(StandardCharsets.UTF_8);
    for (int i = 0; i < count; i++) {
      snapshot.observe(
          Files.write(files.resolve(String.format(Locale.ROOT, "a%06d", i)), small), false);
    }
    for (int length : new int[] {0, 1, 65535, 65536, 65537, 131071, 131072, 262145}) {
      byte[] contents = new byte[length];
      for (int i = 0; i < length; i++) {
        contents[i] = (byte) (i * 31 + length);
      }
      snapshot.observe(Files.write(files.resolve("boundary-" + length), contents), false);
    }
    var witnesses = snapshot.witnesses();
    var digest = MessageDigest.getInstance("SHA-256");
    for (var witness : witnesses) {
      digest.update(witness.digest().getBytes(StandardCharsets.UTF_8));
    }
    String signature = HexFormat.of().formatHex(digest.digest());
    ThreadMXBean bean = (ThreadMXBean) ManagementFactory.getThreadMXBean();
    if (!bean.isThreadAllocatedMemorySupported()) {
      throw new IllegalStateException("thread allocation measurement unavailable");
    }
    bean.setThreadAllocatedMemoryEnabled(true);
    for (int round = -2; round < 5; round++) {
      long before = bean.getCurrentThreadAllocatedBytes();
      long start = System.nanoTime();
      for (var witness : witnesses) {
        if (!snapshot.matches(witness)) {
          throw new AssertionError("unchanged input mismatched: " + witness.path());
        }
      }
      long nanos = System.nanoTime() - start;
      long allocated = bean.getCurrentThreadAllocatedBytes() - before;
      if (round >= 0) {
        System.out.printf(
            Locale.ROOT,
            "{\"round\":%d,\"files\":%d,\"milliseconds\":%.3f,\"allocated_bytes\":%d,\"signature\":\"%s\"}%n",
            round,
            witnesses.size(),
            nanos / 1e6,
            allocated,
            signature);
      }
    }
  }

  private LocalInputHashingBenchmark() {}
}
