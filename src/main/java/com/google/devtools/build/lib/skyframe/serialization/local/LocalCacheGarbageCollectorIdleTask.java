// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.common.flogger.GoogleLogger;
import com.google.devtools.build.lib.server.IdleTask;
import com.google.devtools.build.lib.server.IdleTaskException;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.nio.channels.ClosedByInterruptException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import javax.annotation.Nullable;

/** Runs cache collection between commands, using the server's existing interruptible idle tasks. */
final class LocalCacheGarbageCollectorIdleTask implements IdleTask {
  private static final GoogleLogger logger = GoogleLogger.forEnclosingClass();
  private final Duration delay;
  private final LocalCacheGarbageCollector collector;
  private final Path statistics;

  private LocalCacheGarbageCollectorIdleTask(
      Duration delay, LocalCacheGarbageCollector collector, Path statistics) {
    this.delay = delay;
    this.collector = collector;
    this.statistics = statistics;
  }

  @Nullable
  static LocalCacheGarbageCollectorIdleTask create(
      LocalSkycacheOptions options, Path cache, Path outputBase) {
    long age;
    try {
      age = options.getGcMaxAge().toMillis();
    } catch (ArithmeticException e) {
      throw new IllegalArgumentException("local SkyCache collection age is too large", e);
    }
    if (options.getGcIdleDelay().isNegative() || options.getGcMaxAge().isNegative()) {
      throw new IllegalArgumentException("negative local SkyCache collection duration");
    }
    var policy = new LocalCacheGarbageCollector.Policy(options.getGcMaxSize(), age);
    return policy.enabled()
        ? new LocalCacheGarbageCollectorIdleTask(
            options.getGcIdleDelay(),
            new LocalCacheGarbageCollector(cache, policy),
            outputBase.resolve("local-skycache-gc.json"))
        : null;
  }

  @Override
  public String displayName() {
    return "Local SkyCache garbage collector";
  }

  @Override
  public Duration delay() {
    return delay;
  }

  @Override
  public void run() throws IdleTaskException, InterruptedException {
    try {
      var stats = collector.run();
      // Each output base has its own report. Atomic replacement prevents observers reading a
      // partial JSON document. This diagnostic file is not cache metadata and needs no fsync.
      Path temporary = Files.createTempFile(statistics.getParent(), ".skycache-gc-", ".tmp");
      try {
        Files.writeString(temporary, stats.json() + "\n");
        Files.move(
            temporary,
            statistics,
            StandardCopyOption.ATOMIC_MOVE,
            StandardCopyOption.REPLACE_EXISTING);
      } finally {
        Files.deleteIfExists(temporary);
      }
      logger.atInfo().log("Local SkyCache collection: %s", stats.json());
    } catch (InterruptedIOException | ClosedByInterruptException e) {
      throw new InterruptedException("Local SkyCache collection interrupted: " + e);
    } catch (IOException e) {
      throw new IdleTaskException(e);
    }
  }
}
