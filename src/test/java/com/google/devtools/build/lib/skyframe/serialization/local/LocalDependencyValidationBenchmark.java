// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService.NONPROD_FINGERPRINTER;

import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;

/** Measures validation of sampled real dependency roots, with repositories already present. */
public final class LocalDependencyValidationBenchmark {
  public static void main(String[] args) throws Exception {
    var roots =
        Files.readAllLines(Path.of(args[4])).stream()
            .map(hex -> PackedFingerprint.fromBytes(HexFormat.of().parseHex(hex)))
            .toList();
    var allocation = (com.sun.management.ThreadMXBean) ManagementFactory.getThreadMXBean();
    allocation.setThreadAllocatedMemoryEnabled(true);
    long thread = Thread.currentThread().threadId();
    try (var store = PackedFingerprintValueStore.open(Path.of(args[0]), args[1])) {
      System.out.print("{\"sampled_roots\":" + roots.size() + ",\"rounds\":[");
      for (int round = -2; round < 5; round++) {
        var inputs = new LocalInputSnapshot(Path.of(args[2]), Path.of(args[3]), Map.of());
        var validator =
            new LocalDependencyValidator(
                store, inputs, NONPROD_FINGERPRINTER, new CompressionServiceImpl());
        long beforeBytes = allocation.getThreadAllocatedBytes(thread);
        long start = System.nanoTime();
        for (var root : roots) {
          if (!validator.validate(root)) {
            throw new IllegalStateException("root failed: " + root.toHex());
          }
        }
        long freshNanos = System.nanoTime() - start;
        long freshBytes = allocation.getThreadAllocatedBytes(thread) - beforeBytes;
        String freshStats = validator.statisticsJson();
        beforeBytes = allocation.getThreadAllocatedBytes(thread);
        start = System.nanoTime();
        for (var root : roots) {
          if (!validator.validate(root)) {
            throw new IllegalStateException("memo root failed");
          }
        }
        long memoNanos = System.nanoTime() - start;
        long memoBytes = allocation.getThreadAllocatedBytes(thread) - beforeBytes;
        if (!inputs.isUnchanged()) {
          throw new IllegalStateException("inputs changed");
        }
        if (round >= 0) {
          if (round > 0) {
            System.out.print(",");
          }
          System.out.printf(
              Locale.ROOT,
              "{\"fresh_ms\":%.3f,\"fresh_allocated_bytes\":%d,\"memo_ms\":%.3f,\"memo_allocated_bytes\":%d,\"fresh_stats\":%s,\"memo_stats\":%s}",
              freshNanos / 1e6,
              freshBytes,
              memoNanos / 1e6,
              memoBytes,
              freshStats,
              validator.statisticsJson());
        }
      }
      System.out.println("]}");
    }
  }
}
