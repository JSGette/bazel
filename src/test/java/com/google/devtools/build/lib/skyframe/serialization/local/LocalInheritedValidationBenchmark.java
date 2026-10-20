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

/** Measures publication after reading parents with identical complete input witness lists. */
public final class LocalInheritedValidationBenchmark {
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
    Path manifests =
        cache
            .resolve("publications-v2")
            .resolve(new LocalInputSnapshot(workspace, output, Map.of()).sourceDigest());
    java.util.Set<Path> parentManifests;
    try (var entries = Files.list(manifests)) {
      parentManifests = entries.collect(java.util.stream.Collectors.toSet());
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
        for (int i = 0; i < publications; i++) {
          if (!Arrays.equals(reader.get(new Key(i)).get(), new byte[] {(byte) i})) {
            throw new AssertionError("parent value mismatched");
          }
        }
        reader.put(new Key(publications + round + 2), new byte[] {42}).get();
        long before = bean.getCurrentThreadAllocatedBytes();
        long start = System.nanoTime();
        boolean published = reader.finish(true);
        long nanos = System.nanoTime() - start;
        long allocated = bean.getCurrentThreadAllocatedBytes() - before;
        if (!published) {
          throw new AssertionError("child did not publish");
        }
        // Remove only this round's manifest in this freshly created benchmark directory. Keep
        // the original parent set fixed for the next round; undiscoverable packs remain on disk.
        try (var entries = Files.list(manifests)) {
          var added = entries.filter(path -> !parentManifests.contains(path)).toList();
          if (added.size() != 1) {
            throw new AssertionError("unexpected publication count");
          }
          Files.delete(added.getFirst());
        }
        if (round >= 0) {
          System.out.printf(
              Locale.ROOT,
              "{\"round\":%d,\"files\":%d,\"publications\":%d,\"milliseconds\":%.3f,\"allocated_bytes\":%d}%n",
              round,
              count,
              publications,
              nanos / 1e6,
              allocated);
        }
      } finally {
        reader.finish(false);
      }
    }
  }

  private LocalInheritedValidationBenchmark() {}
}
