// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService.NONPROD_FINGERPRINTER;

import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import java.io.DataInputStream;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;

/**
 * Measures manifest admission followed by dependency validation against an existing local cache.
 */
public final class LocalAdmissionValidationBenchmark {
  public static void main(String[] args) throws Exception {
    Path cache = Path.of(args[0]);
    var roots =
        Files.readAllLines(Path.of(args[3])).stream()
            .map(hex -> PackedFingerprint.fromBytes(HexFormat.of().parseHex(hex)))
            .toList();
    var environment = readEnvironment(Path.of(args[4]));
    var allocation = (com.sun.management.ThreadMXBean) ManagementFactory.getThreadMXBean();
    allocation.setThreadAllocatedMemoryEnabled(true);
    long thread = Thread.currentThread().threadId();
    System.out.print("{\"sampled_roots\":" + roots.size() + ",\"rounds\":[");
    for (int round = -2; round < 5; round++) {
      var inputs = new LocalInputSnapshot(Path.of(args[1]), Path.of(args[2]), environment);
      if (!Files.isDirectory(cache.resolve("publications-v3").resolve(inputs.cacheNamespace()))) {
        throw new IllegalStateException(
            "fixture environment or repository namespace does not match");
      }
      var store = new LocalCacheSession(cache, inputs);
      try {
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
        if (!inputs.isUnchanged() || store.sharedReads() == 0) {
          throw new IllegalStateException("fixture changed or no shared records were read");
        }
        if (round >= 0) {
          if (round > 0) {
            System.out.print(",");
          }
          System.out.printf(
              Locale.ROOT,
              "{\"fresh_ms\":%.3f,\"fresh_allocated_bytes\":%d,\"memo_ms\":%.3f,\"memo_allocated_bytes\":%d,\"fresh_stats\":%s,\"memo_stats\":%s,\"store_stats\":%s}",
              freshNanos / 1e6,
              freshBytes,
              memoNanos / 1e6,
              memoBytes,
              freshStats,
              validator.statisticsJson(),
              store.statisticsJson(false));
        }
      } finally {
        store.finish(false);
      }
    }
    System.out.println("]}");
  }

  private static Map<String, String> readEnvironment(Path path) throws Exception {
    var environment = new HashMap<String, String>();
    try (var input = new DataInputStream(Files.newInputStream(path))) {
      int count = input.readInt();
      if (count < 0 || count > 10000) {
        throw new IllegalArgumentException("invalid environment count");
      }
      for (int i = 0; i < count; i++) {
        if (environment.put(input.readUTF(), input.readUTF()) != null) {
          throw new IllegalArgumentException("duplicate environment key");
        }
      }
      if (input.read() != -1) {
        throw new IllegalArgumentException("trailing environment bytes");
      }
    }
    return Map.copyOf(environment);
  }
}
