// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.StringKey;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
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

  @Test
  public void cachedLookupStopsAtUnavailableNewerIndexBeforeReadingOlderValue() throws Exception {
    var older = new StringKey("key-000");
    var newer = otherKeyInSameBucket(older);
    var directory = temporary.newFolder().toPath();
    String id = "3".repeat(64);
    try (var writer = SegmentedFingerprintValueStore.create(directory, id, 1, 1024)) {
      writer.put(older, new byte[] {1}).get();
      writer.put(newer, new byte[] {2}).get();
      writer.seal();
    }
    var source = new CachedSource(directory.resolve(id));
    source.cachedPrefixes.put("segment-00001.index", 0L);
    // The newer value is not needed once its index proves the requested key is absent.
    source.cachedPrefixes.put("segment-00001.pack", 0L);
    try (var reader = SegmentedFingerprintValueStore.open(source, 1000000)) {
      assertThat(reader.getIfPresentCached(older)).isNull();
      assertThat(source.cachedReads).isEmpty();
      source.cacheWholeFile("segment-00001.index");
      assertThat(reader.getIfPresentCached(older)).isEqualTo(new byte[] {1});
      assertThat(source.cachedReads)
          .containsExactly("segment-00001.index", "segment-00000.index", "segment-00000.pack")
          .inOrder();
      assertThat(source.normalReads).isEqualTo(0);
    }
  }

  @Test
  public void partialCachedRecordIsRejectedBeforeReadingItsHeader() throws Exception {
    var key = new StringKey("large");
    byte[] value = new byte[128 * 1024];
    var source = singleRecordSource(key, value);
    source.cachedPrefixes.put("segment-00000.pack", 64 * 1024L);
    try (var reader = SegmentedFingerprintValueStore.open(source, 1000000)) {
      assertThat(reader.getIfPresentCached(key)).isNull();
      assertThat(source.cachedReads).containsExactly("segment-00000.index");
      assertThat(source.availabilityChecks)
          .contains(new Range("segment-00000.pack", 0, source.size("segment-00000.pack")));

      source.cacheWholeFile("segment-00000.pack");
      assertThat(reader.getIfPresentCached(key)).isEqualTo(value);
      // Large records keep the normal split header/value path after the whole-range preflight.
      assertThat(source.cachedReads)
          .containsExactly("segment-00000.index", "segment-00000.pack", "segment-00000.pack")
          .inOrder();
      assertThat(source.normalReads).isEqualTo(0);
    }
  }

  @Test
  public void cachedFileDisappearanceBetweenCheckAndReadNeverDownloads() throws Exception {
    var key = new StringKey("one");
    for (String file : List.of("segment-00000.index", "segment-00000.pack")) {
      var source = singleRecordSource(key, new byte[] {1});
      source.disappearOnRead = file;
      try (var reader = SegmentedFingerprintValueStore.open(source, 1000000)) {
        assertThat(reader.getIfPresentCached(key)).isNull();
        assertThat(source.cachedReads).contains(file);
        assertThat(source.normalReads).isEqualTo(0);
      }
    }
  }

  @Test
  public void cachedFileReadFailureNeverDownloads() throws Exception {
    var key = new StringKey("one");
    for (String file : List.of("segment-00000.index", "segment-00000.pack")) {
      var source = singleRecordSource(key, new byte[] {1});
      source.failOnRead = file;
      try (var reader = SegmentedFingerprintValueStore.open(source, 1000000)) {
        var failure = assertThrows(IOException.class, () -> reader.getIfPresentCached(key));
        assertThat(failure).hasMessageThat().isEqualTo("cached file read failed");
        assertThat(source.cachedReads).contains(file);
        assertThat(source.normalReads).isEqualTo(0);
      }
    }
  }

  @Test
  public void cachedRecordStillValidatesValueChecksum() throws Exception {
    var key = new StringKey("one");
    var source = singleRecordSource(key, new byte[] {1});
    byte[] pack = source.files.get("segment-00000.pack");
    pack[pack.length - 1] ^= 1;
    try (var reader = SegmentedFingerprintValueStore.open(source, 1000000)) {
      var failure = assertThrows(IOException.class, () -> reader.getIfPresentCached(key));
      assertThat(failure).hasMessageThat().isEqualTo("packed cache value checksum mismatch");
      assertThat(source.normalReads).isEqualTo(0);
    }
  }

  private CachedSource singleRecordSource(StringKey key, byte[] value) throws Exception {
    var directory = temporary.newFolder().toPath();
    String id = "4".repeat(64);
    try (var writer = SegmentedFingerprintValueStore.create(directory, id)) {
      writer.put(key, value).get();
      writer.seal();
    }
    return new CachedSource(directory.resolve(id));
  }

  private static StringKey otherKeyInSameBucket(StringKey key) throws Exception {
    var digest = MessageDigest.getInstance("SHA-256");
    int bucket = digest.digest(key.toBytes())[0] & 15;
    for (int i = 1; i < 1000; i++) {
      var other = new StringKey(String.format(java.util.Locale.ROOT, "key-%03d", i));
      if ((digest.digest(other.toBytes())[0] & 15) == bucket) {
        return other;
      }
    }
    throw new AssertionError("no second key in fixture bucket");
  }

  private record Range(String name, long offset, long length) {}

  /** Allows the initial root index read; every later ordinary read is an unexpected download. */
  private static final class CachedSource implements SegmentedFingerprintValueStore.FileSource {
    final Map<String, byte[]> files = new HashMap<>();
    final Map<String, Long> cachedPrefixes = new HashMap<>();
    final List<String> cachedReads = new ArrayList<>();
    final List<Range> availabilityChecks = new ArrayList<>();
    String disappearOnRead = "";
    String failOnRead = "";
    int normalReads;

    CachedSource(Path directory) throws IOException {
      try (var paths = Files.list(directory)) {
        for (Path path : paths.toList()) {
          String name = path.getFileName().toString();
          files.put(name, Files.readAllBytes(path));
          cacheWholeFile(name);
        }
      }
    }

    void cacheWholeFile(String name) throws IOException {
      cachedPrefixes.put(name, size(name));
    }

    @Override
    public long size(String name) throws IOException {
      if (!files.containsKey(name)) {
        throw new IOException("missing fixture file");
      }
      return files.get(name).length;
    }

    @Override
    public byte[] read(String name, long maxBytes) throws IOException {
      if (!name.equals("index")) {
        normalReads++;
        throw new AssertionError("unexpected ordinary metadata read: " + name);
      }
      if (size(name) > maxBytes) {
        throw new IOException("fixture file exceeds budget");
      }
      return files.get(name).clone();
    }

    @Override
    public void read(String name, ByteBuffer target, long offset) {
      normalReads++;
      throw new AssertionError("unexpected ordinary data read: " + name);
    }

    @Override
    public boolean isCached(String name, long offset, long length) throws IOException {
      availabilityChecks.add(new Range(name, offset, length));
      if (offset < 0 || length < 0 || offset > size(name) - length) {
        throw new IOException("fixture range outside bounds");
      }
      return offset + length <= cachedPrefixes.getOrDefault(name, 0L);
    }

    @Override
    public void readCached(String name, ByteBuffer target, long offset) throws IOException {
      cachedReads.add(name);
      if (name.equals(disappearOnRead)) {
        cachedPrefixes.put(name, 0L);
      }
      if (name.equals(failOnRead)) {
        throw new IOException("cached file read failed");
      }
      if (!isCached(name, offset, target.remaining())) {
        throw new SegmentedFingerprintValueStore.NotCachedException();
      }
      target.put(files.get(name), (int) offset, target.remaining());
    }
  }
}
