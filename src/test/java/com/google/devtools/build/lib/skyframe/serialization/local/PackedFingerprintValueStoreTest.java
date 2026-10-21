// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore.MissingFingerprintValueException;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class PackedFingerprintValueStoreTest {
  private static final String ID = "ab".repeat(32);
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  private record Key(String value) implements KeyBytesProvider {
    @Override
    public byte[] toBytes() {
      return value.getBytes(UTF_8);
    }

    @Override
    public byte[] concat(byte[] suffix) {
      byte[] bytes = toBytes();
      return ByteBuffer.allocate(bytes.length + suffix.length).put(bytes).put(suffix).array();
    }
  }

  @Test
  public void privateWritesBecomeDiscoverableOnlyAfterSealing() throws Exception {
    Path root = temporary.newFolder().toPath();
    var key = new Key("a logical key");
    try (var writer = PackedFingerprintValueStore.create(root, ID)) {
      assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, ID));
      assertThat(writer.put(key, "payload".getBytes(UTF_8)).get()).isTrue();
      assertThat(writer.get(key).get()).isEqualTo("payload".getBytes(UTF_8));
      assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, ID));
      assertThat(writer.put(key, "payload".getBytes(UTF_8)).get()).isFalse();
      assertThrows(ExecutionException.class, () -> writer.put(key, "wrong".getBytes(UTF_8)).get());
      writer.put(new Key("empty"), new byte[0]).get();
      writer.seal();
      writer.seal();
      assertThrows(
          ExecutionException.class, () -> writer.put(key, "payload".getBytes(UTF_8)).get());
      try (var reader = PackedFingerprintValueStore.open(root, ID)) {
        assertThat(reader.get(key).get()).isEqualTo("payload".getBytes(UTF_8));
        assertThat(reader.get(new Key("empty")).get()).isEmpty();
        var absent =
            assertThrows(ExecutionException.class, () -> reader.get(new Key("missing")).get());
        assertThat(absent).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
      }
    }
    try (var files = Files.list(root.resolve(ID))) {
      assertThat(files.map(p -> p.getFileName().toString()).toList())
          .containsExactly("data.pack", "index");
    }
  }

  @Test
  public void recordsAcrossCoalescingBoundaryRoundTripWithoutAliasing() throws Exception {
    Path root = temporary.newFolder().toPath();
    // With the three-byte keys below, these straddle the complete 64 KiB record boundary.
    int[] lengths = {0, 1, 65488, 65489, 65490, 1024 * 1024};
    try (var writer = PackedFingerprintValueStore.create(root, ID)) {
      for (int i = 0; i < lengths.length; i++) {
        byte[] value = new byte[lengths[i]];
        Arrays.fill(value, (byte) (i + 1));
        var key = new Key("k:" + i);
        assertThat(writer.put(key, value).get()).isTrue();
        assertThat(writer.put(key, value).get()).isFalse();
        Arrays.fill(value, (byte) 0);
        byte[] expected = new byte[lengths[i]];
        Arrays.fill(expected, (byte) (i + 1));
        assertThat(writer.get(key).get()).isEqualTo(expected);
      }
      writer.seal();
    }
    try (var reader = PackedFingerprintValueStore.open(root, ID)) {
      for (int i = 0; i < lengths.length; i++) {
        byte[] expected = new byte[lengths[i]];
        Arrays.fill(expected, (byte) (i + 1));
        var key = new Key("k:" + i);
        byte[] firstRead = reader.get(key).get();
        assertThat(firstRead).isEqualTo(expected);
        Arrays.fill(firstRead, (byte) 0);
        assertThat(reader.get(key).get()).isEqualTo(expected);
      }
    }
  }

  @Test
  public void damagedHeadersKeysAndTruncatedRecordsMissAcrossReadPaths() throws Exception {
    var key = new Key("key");
    for (int valueLength : List.of(1024, 128 * 1024)) {
      Path root = temporary.newFolder().toPath();
      try (var writer = PackedFingerprintValueStore.create(root, ID)) {
        writer.put(key, new byte[valueLength]).get();
        writer.seal();
      }
      Path pack = root.resolve(ID).resolve("data.pack");
      byte[] original = Files.readAllBytes(pack);
      try (var reader = PackedFingerprintValueStore.open(root, ID)) {
        // Corrupt magic, key length, value length, checksum, key and value independently.
        for (int offset : List.of(0, 4, 8, 12, 44, original.length - 1)) {
          byte[] corrupt = original.clone();
          corrupt[offset] ^= 1;
          Files.write(pack, corrupt);
          var failure = assertThrows(ExecutionException.class, () -> reader.get(key).get());
          assertThat(failure).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
        }
        Files.write(pack, original);
        try (var file = new RandomAccessFile(pack.toFile(), "rw")) {
          file.setLength(file.length() - 1);
        }
        var failure = assertThrows(ExecutionException.class, () -> reader.get(key).get());
        assertThat(failure).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
      }
    }
  }

  @Test
  public void abandonedWriterAndOutOfBoundsInputsAreNeverPublished() throws Exception {
    Path root = temporary.newFolder().toPath();
    try (var writer = PackedFingerprintValueStore.create(root, ID)) {
      writer.put(new Key("valid"), "value".getBytes(UTF_8)).get();
      assertThrows(ExecutionException.class, () -> writer.put(new Key(""), new byte[0]).get());
      assertThrows(
          ExecutionException.class, () -> writer.put(new Key("x".repeat(4097)), new byte[0]).get());
      assertThrows(
          ExecutionException.class,
          () -> writer.put(new Key("oversized"), new byte[64 * 1024 * 1024 + 1]).get());
      assertThrows(IOException.class, () -> PackedFingerprintValueStore.create(root, ID));
    }
    assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, ID));
    assertThrows(
        IllegalArgumentException.class,
        () -> PackedFingerprintValueStore.create(root, "../escape"));
  }

  @Test
  public void concurrentPutsAndGetsKeepRecordsSeparateAndImmutable() throws Exception {
    Path root = temporary.newFolder().toPath();
    try (var writer = PackedFingerprintValueStore.create(root, ID);
        var pool = Executors.newFixedThreadPool(8)) {
      var tasks = new ArrayList<java.util.concurrent.Callable<Void>>();
      for (int thread = 0; thread < 8; thread++) {
        int id = thread;
        tasks.add(
            () -> {
              for (int i = 0; i < 100; i++) {
                var key = new Key(id + ":" + i);
                byte[] bytes = (id + ":" + i).repeat(i + 1).getBytes(UTF_8);
                writer.put(key, bytes).get();
                assertThat(writer.get(key).get()).isEqualTo(bytes);
                writer.put(new Key("shared:" + i), Integer.toString(i).getBytes(UTF_8)).get();
              }
              return null;
            });
      }
      for (var task : pool.invokeAll(tasks)) {
        task.get();
      }
      writer.seal();
    }
    try (var reader = PackedFingerprintValueStore.open(root, ID)) {
      for (int id = 0; id < 8; id++) {
        for (int i = 0; i < 100; i++) {
          assertThat(reader.get(new Key(id + ":" + i)).get())
              .isEqualTo((id + ":" + i).repeat(i + 1).getBytes(UTF_8));
          assertThat(reader.get(new Key("shared:" + i)).get())
              .isEqualTo(Integer.toString(i).getBytes(UTF_8));
        }
      }
    }
  }

  @Test
  public void damagedRecordsMissAndTruncatedPacksCannotOpen() throws Exception {
    Path root = temporary.newFolder().toPath();
    var key = new Key("key");
    try (var writer = PackedFingerprintValueStore.create(root, ID)) {
      writer.put(key, "payload".getBytes(UTF_8)).get();
      writer.seal();
    }
    Path pack = root.resolve(ID).resolve("data.pack");
    try (var file = new RandomAccessFile(pack.toFile(), "rw")) {
      file.seek(file.length() - 1);
      file.write('X');
    }
    try (var reader = PackedFingerprintValueStore.open(root, ID)) {
      var failure = assertThrows(ExecutionException.class, () -> reader.get(key).get());
      assertThat(failure).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
    }
    try (var file = new RandomAccessFile(pack.toFile(), "rw")) {
      file.setLength(5);
    }
    assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, ID));
  }

  @Test
  public void corruptAndStructurallyInvalidIndexesCannotOpen() throws Exception {
    Path root = temporary.newFolder().toPath();
    try (var writer = PackedFingerprintValueStore.create(root, ID)) {
      writer.put(new Key("key"), "payload".getBytes(UTF_8)).get();
      writer.seal();
    }
    Path index = root.resolve(ID).resolve("index");
    byte[] original = Files.readAllBytes(index);
    byte[] corrupt = original.clone();
    corrupt[20] ^= 1;
    Files.write(index, corrupt);
    assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, ID));
    for (int field : List.of(4, 48, 56)) {
      byte[] invalid = original.clone();
      // Count, record offset and record length must remain bounded even with a valid checksum.
      if (field == 4) {
        ByteBuffer.wrap(invalid).putInt(field, Integer.MAX_VALUE);
      } else {
        ByteBuffer.wrap(invalid).putLong(field, Long.MAX_VALUE);
      }
      MessageDigest hash = MessageDigest.getInstance("SHA-256");
      hash.update(invalid, 0, invalid.length - 32);
      System.arraycopy(hash.digest(), 0, invalid, invalid.length - 32, 32);
      Files.write(index, invalid);
      assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, ID));
    }
  }

  @Test
  public void checksummedDuplicateIndexKeysAndSymlinkNamespacesCannotOpen() throws Exception {
    Path root = temporary.newFolder().toPath();
    try (var writer = PackedFingerprintValueStore.create(root, ID)) {
      writer.put(new Key("first"), new byte[] {1}).get();
      writer.put(new Key("second"), new byte[] {2}).get();
      writer.seal();
    }
    String alias = "cd".repeat(32);
    Files.createSymbolicLink(root.resolve(alias), root.resolve(ID));
    assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, alias));
    Path index = root.resolve(ID).resolve("index");
    byte[] invalid = Files.readAllBytes(index);
    System.arraycopy(invalid, 16, invalid, 16 + 48, 32);
    MessageDigest hash = MessageDigest.getInstance("SHA-256");
    hash.update(invalid, 0, invalid.length - 32);
    System.arraycopy(hash.digest(), 0, invalid, invalid.length - 32, 32);
    Files.write(index, invalid);
    assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, ID));
  }

  @Test(timeout = 60000)
  public void killedWriterRemainsUnpublishedWithoutBlockingOtherWriters() throws Exception {
    Path root = temporary.newFolder().toPath();
    Path control = temporary.newFolder().toPath();
    Process child = startWriter(root, control, "0");
    try {
      waitFor(control.resolve("written-0"));
      child.destroyForcibly();
      assertThat(child.waitFor(10, TimeUnit.SECONDS)).isTrue();
      assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, "0".repeat(64)));
      try (var writer = PackedFingerprintValueStore.create(root, ID)) {
        writer.put(new Key("same key"), new byte[] {7}).get();
        writer.seal();
      }
      try (var reader = PackedFingerprintValueStore.open(root, ID)) {
        assertThat(reader.get(new Key("same key")).get()).isEqualTo(new byte[] {7});
      }
    } finally {
      child.destroyForcibly();
    }
  }

  private static Process startWriter(Path root, Path control, String id) throws IOException {
    return new ProcessBuilder(
            Path.of(System.getProperty("java.home"), "bin/java").toString(),
            "-Xmx64m",
            "-cp",
            System.getProperty("java.class.path"),
            PackedFingerprintValueStoreTest.class.getName(),
            root.toString(),
            control.toString(),
            id)
        .redirectErrorStream(true)
        .redirectOutput(control.resolve("child-" + id + ".log").toFile())
        .start();
  }

  @Test(timeout = 60000)
  public void independentWriterProcessesSealWithoutACommonWriterLock() throws Exception {
    Path root = temporary.newFolder().toPath();
    Path control = temporary.newFolder().toPath();
    List<Process> children = new ArrayList<>();
    try {
      for (int i = 0; i < 2; i++) {
        children.add(startWriter(root, control, Integer.toString(i)));
      }
      for (int i = 0; i < 2; i++) {
        waitFor(control.resolve("written-" + i));
        String id = Integer.toString(i).repeat(64);
        assertThrows(IOException.class, () -> PackedFingerprintValueStore.open(root, id));
      }
      Files.writeString(control.resolve("seal"), "");
      for (int i = 0; i < 2; i++) {
        waitFor(control.resolve("sealed-" + i));
        assertThat(children.get(i).isAlive()).isTrue();
        try (var reader = PackedFingerprintValueStore.open(root, Integer.toString(i).repeat(64))) {
          assertThat(reader.get(new Key("same key")).get())
              .isEqualTo(Integer.toString(i).getBytes(UTF_8));
        }
      }
      Files.writeString(control.resolve("exit"), "");
      for (Process child : children) {
        assertThat(child.waitFor(10, TimeUnit.SECONDS)).isTrue();
        assertThat(child.exitValue()).isEqualTo(0);
      }
    } finally {
      children.forEach(Process::destroyForcibly);
    }
  }

  private static void waitFor(Path file) throws Exception {
    long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
    while (!Files.exists(file)) {
      if (System.nanoTime() - deadline > 0) {
        throw new IOException("timed out waiting for " + file);
      }
      Thread.sleep(5);
    }
  }

  /** Separate JVM entry point for simultaneous publication and visibility checks. */
  public static void main(String[] args) throws Exception {
    Path root = Path.of(args[0]);
    Path control = Path.of(args[1]);
    String id = args[2];
    try (var writer = PackedFingerprintValueStore.create(root, id.repeat(64))) {
      writer.put(new Key("same key"), id.getBytes(UTF_8)).get();
      Files.writeString(control.resolve("written-" + id), "");
      waitFor(control.resolve("seal"));
      writer.seal();
      Files.writeString(control.resolve("sealed-" + id), "");
      waitFor(control.resolve("exit"));
    }
  }
}
