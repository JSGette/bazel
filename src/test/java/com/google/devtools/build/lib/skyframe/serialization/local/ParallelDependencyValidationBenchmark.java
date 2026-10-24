// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService.NONPROD_FINGERPRINTER;

import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.StringKey;
import com.google.protobuf.CodedOutputStream;
import java.io.ByteArrayOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;

/**
 * Actual packed I/O, dependency decoding and filesystem validation; warm filesystem, fresh memos.
 */
public final class ParallelDependencyValidationBenchmark {
  private static byte[] node(List<PackedFingerprint> nested, List<StringKey> leaves)
      throws Exception {
    var bytes = new ByteArrayOutputStream();
    var out = CodedOutputStream.newInstance(bytes);
    out.writeInt32NoTag(nested.size());
    out.writeInt32NoTag(leaves.size());
    out.writeInt32NoTag(0);
    out.writeBoolNoTag(false);
    for (var child : nested) child.writeTo(out);
    for (var leaf : leaves) out.writeStringNoTag(leaf.key());
    out.flush();
    return bytes.toByteArray();
  }

  private static PackedFingerprint put(PackedFingerprintValueStore store, byte[] bytes)
      throws Exception {
    var key = NONPROD_FINGERPRINTER.fingerprint(bytes);
    store.put(key, bytes).get();
    return key;
  }

  public static void main(String[] args) throws Exception {
    Path directory = Files.createDirectory(Path.of(args[0]));
    Path workspace = Files.createDirectory(directory.resolve("workspace"));
    Path output = Files.createDirectory(directory.resolve("output"));
    Path packs = Files.createDirectory(directory.resolve("packs"));
    var roots = new ArrayList<PackedFingerprint>();
    String id = "ab".repeat(32);
    int count = 8192;
    for (int i = 0; i < count; i++) {
      Files.writeString(workspace.resolve("file-" + i), "content-" + i + "x".repeat(256));
    }
    try (var writer = PackedFingerprintValueStore.create(packs, id)) {
      var inputs = new LocalInputSnapshot(workspace, output, Map.of());
      var leaves = new ArrayList<StringKey>();
      for (int i = 0; i < count; i++) {
        byte[] evidence =
            inputs.captureDependency(workspace.resolve("file-" + i), false, List.of());
        var key =
            new StringKey("local-input-v1/" + NONPROD_FINGERPRINTER.fingerprint(evidence).toHex());
        writer.put(key, evidence).get();
        leaves.add(key);
      }
      var groups = new ArrayList<PackedFingerprint>();
      for (int group = 0; group < 128; group++) {
        groups.add(put(writer, node(List.of(), leaves.subList(group * 64, (group + 1) * 64))));
      }
      for (int root = 0; root < 512; root++) {
        var dependencies = new ArrayList<PackedFingerprint>();
        for (int j = 0; j < 8; j++) dependencies.add(groups.get((root + j) % groups.size()));
        roots.add(put(writer, node(dependencies, List.of(leaves.get(root)))));
      }
      writer.seal();
    }
    var compression = new CompressionServiceImpl();
    try (var store = PackedFingerprintValueStore.open(packs, id)) {
      for (int round = -2; round < 5; round++) {
        // Reverse concurrency order on alternate rounds to reduce warmup/order bias.
        int[] widths = round % 2 == 0 ? new int[] {1, 2, 4, 8} : new int[] {8, 4, 2, 1};
        for (int width : widths) {
          for (int position = 0; position < 2; position++) {
            boolean serial = Math.floorMod(round + position, 2) == 0;
            var inputs = new LocalInputSnapshot(workspace, output, Map.of());
            var baseline =
                new SerialDependencyValidatorBenchmarkBaseline(
                    store, inputs, NONPROD_FINGERPRINTER, compression);
            var parallel =
                new LocalDependencyValidator(store, inputs, NONPROD_FINGERPRINTER, compression);
            long start;
            long elapsed;
            try (var pool = Executors.newFixedThreadPool(width)) {
              // Start workers before timing, then partition roots without per-root task allocation.
              var ready = new java.util.concurrent.CountDownLatch(width);
              var go = new java.util.concurrent.CountDownLatch(1);
              var tasks = new ArrayList<java.util.concurrent.Future<Void>>();
              for (int worker = 0; worker < width; worker++) {
                final int offset = worker;
                tasks.add(
                    pool.submit(
                        (Callable<Void>)
                            () -> {
                              ready.countDown();
                              go.await();
                              for (int i = offset; i < roots.size(); i += width) {
                                boolean valid =
                                    serial
                                        ? baseline.validate(roots.get(i))
                                        : parallel.validate(roots.get(i));
                                if (!valid) throw new AssertionError("root failed: " + i);
                              }
                              return null;
                            }));
              }
              ready.await();
              start = System.nanoTime();
              go.countDown();
              for (var task : tasks) task.get();
              elapsed = System.nanoTime() - start;
            }
            if (!inputs.isUnchanged()) throw new AssertionError("final revalidation failed");
            if (round >= 0)
              System.out.printf(
                  Locale.ROOT,
                  "{\"round\":%d,\"implementation\":\"%s\",\"threads\":%d,\"roots\":%d,\"validation_ms\":%.3f,\"statistics\":%s}%n",
                  round,
                  serial ? "serial_baseline" : "root_stripes",
                  width,
                  roots.size(),
                  elapsed / 1e6,
                  serial ? baseline.statisticsJson() : parallel.statisticsJson());
          }
        }
      }
    }
  }
}
