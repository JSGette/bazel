// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import java.nio.file.Path;

/** Runs one collection with optional live pins on a dedicated, disposable measurement fixture. */
public final class LocalCacheCollectionBenchmark {
  public static void main(String[] args) throws Exception {
    if (args.length < 3) {
      throw new IllegalArgumentException(
          "usage: fixture-root max-bytes max-age-ms [pinned-id ...]");
    }
    Path cache = Path.of(args[0]);
    try (var reader =
        LocalCacheLeaseManager.open(cache.resolve(LocalCacheSession.MANAGED_DIRECTORY))) {
      try (var catalog = reader.trySharedCatalog()) {
        if (catalog == null) {
          throw new IllegalStateException("benchmark catalog unexpectedly busy");
        }
        for (int i = 3; i < args.length; i++) {
          reader.pinPublication(args[i]);
        }
      }
      var policy =
          new LocalCacheGarbageCollector.Policy(Long.parseLong(args[1]), Long.parseLong(args[2]));
      var stats = new LocalCacheGarbageCollector(cache, policy).run();
      System.out.println(stats.json());
      if (!stats.status().equals("complete")) {
        throw new IllegalStateException(stats.status());
      }
    }
  }

  private LocalCacheCollectionBenchmark() {}
}
