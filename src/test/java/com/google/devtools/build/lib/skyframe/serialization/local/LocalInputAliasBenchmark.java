// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

/** Measures complete final content checks of large inputs reached through several aliases. */
public final class LocalInputAliasBenchmark {
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
    int aliases = Integer.parseInt(args[1]);
    int size = Integer.parseInt(args[2]);
    Path workspace = Files.createDirectory(root.resolve("workspace"));
    Path output = Files.createDirectory(root.resolve("output"));
    Path external = Files.createDirectory(root.resolve("inputs"));
    Path input = Files.write(external.resolve("library"), new byte[size]);
    Path distinct = Files.write(external.resolve("distinct"), new byte[size]);
    var snapshot = new LocalInputSnapshot(workspace, output, Map.of());
    snapshot.observe(input, false);
    snapshot.observe(distinct, false);
    for (int i = 0; i < aliases; i++) {
      Path alias = external.resolve("alias-" + i);
      if ((i & 1) == 0) {
        Files.createSymbolicLink(alias, input.getFileName());
      } else {
        Files.createLink(alias, input);
      }
      snapshot.observe(alias, false);
    }
    for (int round = -2; round < 5; round++) {
      var before = io();
      long start = System.nanoTime();
      if (!snapshot.isUnchanged()) {
        throw new AssertionError("unchanged input rejected");
      }
      long nanos = System.nanoTime() - start;
      var after = io();
      if (round >= 0) {
        System.out.printf(
            Locale.ROOT,
            "{\"round\":%d,\"aliases\":%d,\"distinct_files\":2,\"file_bytes\":%d,\"milliseconds\":%.3f,\"read_characters\":%d,\"read_syscalls\":%d,\"physical_read_bytes\":%d}%n",
            round,
            aliases,
            size,
            nanos / 1e6,
            after.get("rchar") - before.get("rchar"),
            after.get("syscr") - before.get("syscr"),
            after.get("read_bytes") - before.get("read_bytes"));
      }
    }
  }

  private LocalInputAliasBenchmark() {}
}
