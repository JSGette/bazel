// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.devtools.common.options.OptionsParser;
import com.google.devtools.common.options.OptionsParsingException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class AnalysisCacheIdleRetentionTaskTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  @Test
  public void optionsDefaultOffAndRejectFractionalOrNegativeRetention() throws Exception {
    var parser = OptionsParser.builder().optionsClasses(LocalSkycacheOptions.class).build();
    assertThat(parser.getOptions(LocalSkycacheOptions.class).getAnalysisCacheIdleRetention())
        .isEqualTo(Duration.ZERO);
    parser.parse("--experimental_analysis_cache_idle_retention=5m");
    assertThat(parser.getOptions(LocalSkycacheOptions.class).getAnalysisCacheIdleRetention())
        .isEqualTo(Duration.ofMinutes(5));
    for (String value : new String[] {"-1s", "1ms", "1500ms"}) {
      assertThrows(
          OptionsParsingException.class,
          () -> parser.parse("--experimental_analysis_cache_idle_retention=" + value));
    }
  }

  @Test
  public void collectedBackingDoesNotReleaseGraph() throws Exception {
    var released = new AtomicInteger();
    Path root = temporary.newFolder().toPath();
    var task =
        new AnalysisCacheIdleRetentionTask(
            Duration.ofSeconds(5),
            root.resolve("absent"),
            root.resolve("stats"),
            released::incrementAndGet);
    task.run();
    assertThat(released.get()).isEqualTo(0);
    assertThat(Files.exists(root.resolve("stats"))).isFalse();
  }

  @Test
  public void interruptBeforeReleaseKeepsGraph() throws Exception {
    var released = new AtomicInteger();
    Path root = temporary.newFolder().toPath();
    var task =
        new AnalysisCacheIdleRetentionTask(
            Duration.ofSeconds(5),
            root.resolve("absent"),
            root.resolve("stats"),
            released::incrementAndGet);
    Thread.currentThread().interrupt();
    try {
      assertThrows(InterruptedException.class, task::run);
    } finally {
      Thread.interrupted();
    }
    assertThat(released.get()).isEqualTo(0);
  }

  @Test
  public void existingBackingReleasesGraphAndWritesReceipt() throws Exception {
    Path root = temporary.newFolder().toPath();
    Path manifest =
        root.resolve("leased-v1/publications-v3/namespace/" + "a".repeat(64) + ".manifest");
    Files.createDirectories(manifest.getParent());
    Files.writeString(manifest, "already-published fixture");
    var released = new AtomicInteger();
    var task =
        new AnalysisCacheIdleRetentionTask(
            Duration.ofSeconds(5), manifest, root.resolve("stats"), released::incrementAndGet);
    assertThat(task.delay()).isEqualTo(Duration.ofSeconds(5));
    task.run();
    assertThat(released.get()).isEqualTo(1);
    assertThat(Files.readString(root.resolve("stats"))).contains("\"evicted\":true");
    assertThat(Files.exists(manifest)).isTrue();
  }
}
