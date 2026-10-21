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
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalCacheGarbageCollectorIdleTaskTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  private static LocalSkycacheOptions options(String... arguments) throws Exception {
    var parser = OptionsParser.builder().optionsClasses(LocalSkycacheOptions.class).build();
    parser.parse(arguments);
    return parser.getOptions(LocalSkycacheOptions.class);
  }

  @Test
  public void defaultsEnableBoundedIdleCollection() throws Exception {
    var options = options();
    assertThat(options.getGcMaxSize()).isEqualTo(5L * 1024 * 1024 * 1024);
    assertThat(options.getGcMaxAge()).isEqualTo(Duration.ofDays(7));
    var task =
        LocalCacheGarbageCollectorIdleTask.create(options, Path.of("cache"), Path.of("output"));
    assertThat(task).isNotNull();
    assertThat(task.delay()).isEqualTo(Duration.ofSeconds(30));
  }

  @Test
  public void bothZeroDisableCollectionAndIndividualZeroOnlyDisablesThatLimit() throws Exception {
    Path root = temporary.newFolder().toPath();
    var disabled =
        options(
            "--experimental_local_skycache_gc_max_size=0",
            "--experimental_local_skycache_gc_max_age=0");
    assertThat(LocalCacheGarbageCollectorIdleTask.create(disabled, root, root)).isNull();
    assertThat(
            LocalCacheGarbageCollectorIdleTask.create(
                options("--experimental_local_skycache_gc_max_size=0"), root, root))
        .isNotNull();
    assertThat(
            LocalCacheGarbageCollectorIdleTask.create(
                options("--experimental_local_skycache_gc_max_age=0"), root, root))
        .isNotNull();
  }

  @Test
  public void negativeDurationsAndSizeAreRejected() throws Exception {
    for (String flag : new String[] {"max_size", "max_age", "idle_delay"}) {
      assertThrows(
          OptionsParsingException.class,
          () -> options("--experimental_local_skycache_gc_" + flag + "=-1"));
    }
  }

  @Test
  public void idleCollectionWritesCompleteJsonAndAtomicallyReplacesPriorReport() throws Exception {
    Path cache = temporary.newFolder().toPath();
    Path output = temporary.newFolder().toPath();
    Files.createDirectories(cache.resolve("leased-v1/publications-v3"));
    Path pack =
        Files.createDirectories(cache.resolve("leased-v1/objects/packed-v1/" + "1".repeat(64)));
    Files.writeString(pack.resolve("data.pack"), "abandoned private write");
    var task =
        LocalCacheGarbageCollectorIdleTask.create(
            options(
                "--experimental_local_skycache_gc_max_size=1",
                "--experimental_local_skycache_gc_max_age=0",
                "--experimental_local_skycache_gc_idle_delay=0"),
            cache,
            output);
    assertThat(task.delay()).isEqualTo(Duration.ZERO);
    task.run();
    String first = Files.readString(output.resolve("local-skycache-gc.json"));
    assertThat(first).startsWith("{\"status\":\"complete\"");
    assertThat(first).contains("\"deleted_packs\":1");
    assertThat(first).endsWith("}\n");
    assertThat(Files.exists(pack)).isFalse();
    task.run();
    assertThat(Files.readString(output.resolve("local-skycache-gc.json")))
        .contains("\"deleted_packs\":0");
    try (var files = Files.list(output)) {
      assertThat(files.toList()).hasSize(1);
    }
  }
}
