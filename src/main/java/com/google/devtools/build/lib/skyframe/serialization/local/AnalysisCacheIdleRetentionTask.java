// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;

import com.google.common.flogger.GoogleLogger;
import com.google.devtools.build.lib.server.IdleTask;
import com.google.devtools.build.lib.server.IdleTaskException;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.Locale;

/**
 * Optional coherent graph reset; IdleTaskManager excludes subsequent commands until it finishes.
 */
final class AnalysisCacheIdleRetentionTask implements IdleTask {
  private static final GoogleLogger logger = GoogleLogger.forEnclosingClass();
  private final Duration retention;
  private final Path manifest;
  private final Path statistics;
  private final Runnable release;

  AnalysisCacheIdleRetentionTask(
      Duration retention, Path manifest, Path statistics, Runnable release) {
    this.retention = retention;
    this.manifest = manifest;
    this.statistics = statistics;
    this.release = release;
  }

  @Override
  public String displayName() {
    return "SkyCache analysis idle retention";
  }

  @Override
  public Duration delay() {
    return retention;
  }

  @Override
  public void run() throws IdleTaskException, InterruptedException {
    if (Thread.interrupted()) {
      throw new InterruptedException();
    }
    // Publication layout: leased-v2/publications-v4/<namespace>/<id>.manifest.
    if (!Files.isRegularFile(manifest, NOFOLLOW_LINKS)) {
      logger.atInfo().log("SkyCache idle retention skipped: backing publication was collected");
      return;
    }
    long started = System.nanoTime();
    var memory = ManagementFactory.getMemoryMXBean();
    long before = memory.getHeapMemoryUsage().getUsed();
    String id = manifest.getFileName().toString().replaceFirst("\\.manifest$", "");
    try (var leases = LocalCacheLeaseManager.open(manifest.getParent().getParent().getParent())) {
      try (var catalog = leases.trySharedCatalog()) {
        if (catalog == null || !Files.isRegularFile(manifest, NOFOLLOW_LINKS)) {
          return;
        }
        leases.pinPublication(id);
      }
      if (Thread.interrupted()) {
        throw new InterruptedException();
      }
      // Once begun, finish the coherent reset even if a command arrives. Never expose a half-reset
      // graph. The publication pin protects its transitive closure during this decision/reset.
      release.run();
      long released = System.nanoTime();
      // GC is best-effort, may delay an arriving command, and does not promise OS memory release.
      if (!Thread.currentThread().isInterrupted()) {
        System.gc();
      }
      String json =
          String.format(
              Locale.ROOT,
              "{\"evicted\":true,\"heap_before_bytes\":%d,\"heap_after_bytes\":%d,"
                  + "\"reset_ms\":%.3f,\"total_ms\":%.3f,\"loading_state_dropped\":true}",
              before,
              memory.getHeapMemoryUsage().getUsed(),
              (released - started) / 1e6,
              (System.nanoTime() - started) / 1e6);
      logger.atInfo().log("SkyCache idle retention: %s", json);
      Path temporary = Files.createTempFile(statistics.getParent(), ".idle-retention-", ".tmp");
      try {
        Files.writeString(temporary, json + "\n");
        Files.move(
            temporary,
            statistics,
            StandardCopyOption.ATOMIC_MOVE,
            StandardCopyOption.REPLACE_EXISTING);
      } finally {
        Files.deleteIfExists(temporary);
      }
    } catch (IOException e) {
      throw new IdleTaskException(e);
    }
  }
}
