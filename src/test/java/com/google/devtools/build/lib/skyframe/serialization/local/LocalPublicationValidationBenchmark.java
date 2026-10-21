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
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/** Measures final validation with two overlapping, unequal parent witness lists. */
public final class LocalPublicationValidationBenchmark {
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

  private static Map<String, Long> io() throws Exception {
    var result = new HashMap<String, Long>();
    for (String line : Files.readAllLines(Path.of("/proc/self/io"))) {
      String[] parts = line.split(":\\s+");
      result.put(parts[0], Long.parseLong(parts[1]));
    }
    return result;
  }

  public static void main(String[] args) throws Exception {
    Path root = Files.createDirectory(Path.of(args[0]));
    int count = Integer.parseInt(args[1]);
    int size = Integer.parseInt(args[2]);
    Path workspace = Files.createDirectory(root.resolve("workspace"));
    Path output = Files.createDirectory(root.resolve("output"));
    Path cache = Files.createDirectory(root.resolve("cache"));
    Path files = Files.createDirectory(root.resolve("inputs"));
    var paths = new ArrayList<Path>();
    byte[] bytes = new byte[size];
    for (int i = 0; i < count + 2; i++) {
      Arrays.fill(bytes, (byte) i);
      paths.add(Files.write(files.resolve(String.format(Locale.ROOT, "%06d", i)), bytes));
    }
    for (int parent = 0; parent < 2; parent++) {
      var inputs = new LocalInputSnapshot(workspace, output, Map.of());
      for (int i = 0; i < count; i++) {
        inputs.observe(paths.get(i), false);
      }
      inputs.observe(paths.get(count + parent), false);
      var writer = new LocalCacheSession(cache, inputs);
      try {
        writer.put(new Key(parent), new byte[] {(byte) parent}).get();
        if (!writer.finish(true)) {
          throw new AssertionError("writer did not publish");
        }
      } finally {
        writer.finish(false);
      }
    }
    Path manifests =
        cache
            .resolve("leased-v1/publications-v3")
            .resolve(new LocalInputSnapshot(workspace, output, Map.of()).cacheNamespace());
    Set<Path> parents;
    try (var entries = Files.list(manifests)) {
      parents = entries.collect(Collectors.toSet());
    }
    if (parents.size() != 2) {
      throw new AssertionError("expected two parents");
    }
    ThreadMXBean bean = (ThreadMXBean) ManagementFactory.getThreadMXBean();
    bean.setThreadAllocatedMemoryEnabled(true);
    for (int round = -2; round < 5; round++) {
      var reader =
          new LocalCacheSession(cache, new LocalInputSnapshot(workspace, output, Map.of()));
      try {
        for (int parent = 0; parent < 2; parent++) {
          if (!Arrays.equals(reader.get(new Key(parent)).get(), new byte[] {(byte) parent})) {
            throw new AssertionError("parent not reused");
          }
        }
        reader.put(new Key(10 + round), new byte[] {42}).get();
        var beforeIo = io();
        long beforeAllocated = bean.getCurrentThreadAllocatedBytes();
        long start = System.nanoTime();
        boolean published = reader.finish(true);
        long nanos = System.nanoTime() - start;
        long allocated = bean.getCurrentThreadAllocatedBytes() - beforeAllocated;
        var afterIo = io();
        if (!published) {
          throw new AssertionError("child did not publish");
        }
        // This standalone benchmark owns this newly created cache. Keep the parent set fixed.
        try (var entries = Files.list(manifests)) {
          var added = entries.filter(path -> !parents.contains(path)).toList();
          if (added.size() != 1) {
            throw new AssertionError("unexpected publication count");
          }
          Files.delete(added.getFirst());
        }
        if (round >= 0) {
          System.out.printf(
              Locale.ROOT,
              "{\"round\":%d,\"shared_files\":%d,\"unique_files\":%d,\"file_bytes\":%d,\"milliseconds\":%.3f,\"allocated_bytes\":%d,\"read_characters\":%d,\"read_syscalls\":%d,\"physical_read_bytes\":%d}%n",
              round,
              count,
              count + 2,
              size,
              nanos / 1e6,
              allocated,
              afterIo.get("rchar") - beforeIo.get("rchar"),
              afterIo.get("syscr") - beforeIo.get("syscr"),
              afterIo.get("read_bytes") - beforeIo.get("read_bytes"));
        }
      } finally {
        reader.finish(false);
      }
    }
  }

  private LocalPublicationValidationBenchmark() {}
}
