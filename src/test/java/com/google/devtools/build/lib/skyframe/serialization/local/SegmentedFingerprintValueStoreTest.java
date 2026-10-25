// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.StringKey;
import java.io.IOException;
import java.nio.file.Files;
import java.util.ArrayList;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class SegmentedFingerprintValueStoreTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  @Test
  public void concurrentRotationRetainsValuesAndDeduplicationAcrossSealedSegments()
      throws Exception {
    var directory = temporary.newFolder().toPath();
    String id = "1".repeat(64);
    try (var writer = SegmentedFingerprintValueStore.create(directory, id, 8, 1024);
        var executor = Executors.newFixedThreadPool(8)) {
      var tasks = new ArrayList<Callable<Void>>();
      for (int n = 0; n < 512; n++) {
        int value = n;
        tasks.add(
            () -> {
              var key = new StringKey("key-" + value);
              byte[] bytes = ("value-" + value).getBytes(java.nio.charset.StandardCharsets.UTF_8);
              assertThat(writer.put(key, bytes).get()).isTrue();
              assertThat(writer.get(key).get()).isEqualTo(bytes);
              return null;
            });
      }
      for (var done : executor.invokeAll(tasks)) {
        done.get();
      }
      var first = new StringKey("key-0");
      assertThat(
              writer.put(first, "value-0".getBytes(java.nio.charset.StandardCharsets.UTF_8)).get())
          .isFalse();
      assertThrows(ExecutionException.class, () -> writer.put(first, new byte[] {99}).get());
      assertThrows(
          IOException.class, () -> SegmentedFingerprintValueStore.open(directory, id, 1000000));
      writer.seal();
      long metadata = writer.indexBytes();
      assertThrows(
          IOException.class,
          () -> SegmentedFingerprintValueStore.open(directory, id, metadata - 1));
      try (var reader = SegmentedFingerprintValueStore.open(directory, id, metadata)) {
        for (int n = 0; n < 512; n++) {
          assertThat(reader.get(new StringKey("key-" + n)).get())
              .isEqualTo(("value-" + n).getBytes(java.nio.charset.StandardCharsets.UTF_8));
        }
      }
    }
    try (var files = Files.list(directory.resolve(id))) {
      assertThat(files.filter(f -> f.toString().endsWith(".pack")).count()).isGreaterThan(16);
    }
  }

  @Test
  public void indexIsAuthenticatedLazilyAgainstThePublicationDescriptor() throws Exception {
    var directory = temporary.newFolder().toPath();
    String id = "2".repeat(64);
    var key = new StringKey("one");
    try (var writer = SegmentedFingerprintValueStore.create(directory, id)) {
      writer.put(key, new byte[] {1}).get();
      writer.seal();
    }
    var index = directory.resolve(id).resolve("segment-00000.index");
    byte[] damaged = Files.readAllBytes(index);
    damaged[20] ^= 1;
    Files.write(index, damaged);
    try (var reader = SegmentedFingerprintValueStore.open(directory, id, 100000)) {
      assertThrows(ExecutionException.class, () -> reader.get(key).get());
    }
  }
}
