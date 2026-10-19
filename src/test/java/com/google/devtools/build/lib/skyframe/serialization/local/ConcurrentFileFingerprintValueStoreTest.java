// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore.MissingFingerprintValueException;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.io.IOException;
import java.io.RandomAccessFile;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class ConcurrentFileFingerprintValueStoreTest {
  private static final String NAMESPACE = "ab".repeat(32);
  @Rule public final TemporaryFolder directory = new TemporaryFolder();

  private record Key(String text) implements KeyBytesProvider {
    @Override
    public byte[] toBytes() {
      return text.getBytes(StandardCharsets.UTF_8);
    }

    @Override
    public byte[] concat(byte[] suffix) {
      byte[] key = toBytes();
      return ByteBuffer.allocate(key.length + suffix.length).put(key).put(suffix).array();
    }
  }

  private static byte[] value(String value) {
    return value.getBytes(StandardCharsets.UTF_8);
  }

  @Test
  public void liveReaderSeesPublicationAndNamespacesStaySeparate() throws Exception {
    Path root = directory.newFolder().toPath();
    var reader = ConcurrentFileFingerprintValueStore.create(root, NAMESPACE);
    var writer = ConcurrentFileFingerprintValueStore.create(root, NAMESPACE);
    var key = new Key("a logical key, not necessarily a content digest");
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    assertThat(writer.put(key, value("first")).get()).isTrue();
    assertThat(reader.get(key).get()).isEqualTo(value("first"));
    assertThat(reader.put(key, value("first")).get()).isFalse();
    assertThrows(ExecutionException.class, () -> writer.put(key, value("conflict")).get());
    assertThat(reader.get(key).get()).isEqualTo(value("first"));
    var other = ConcurrentFileFingerprintValueStore.create(root, "cd".repeat(32));
    assertThrows(ExecutionException.class, () -> other.get(key).get());
    other.put(key, value("isolated")).get();
    assertThat(other.get(key).get()).isEqualTo(value("isolated"));
    assertThat(reader.get(key).get()).isEqualTo(value("first"));
    var empty = new Key("empty");
    writer.put(empty, new byte[0]).get();
    assertThat(reader.get(empty).get()).isEmpty();
  }

  @Test
  public void corruptionTruncationAndUnpublishedStagingAreMisses() throws Exception {
    Path root = directory.newFolder().toPath();
    var store = ConcurrentFileFingerprintValueStore.create(root, NAMESPACE);
    var key = new Key("key");
    store.put(key, value("payload")).get();
    Path entry;
    try (var files = Files.walk(root)) {
      entry = files.filter(p -> p.toString().endsWith(".entry")).findFirst().orElseThrow();
    }
    try (var file = new RandomAccessFile(entry.toFile(), "rw")) {
      file.seek(file.length() - 1);
      file.write('X');
    }
    var corrupt = assertThrows(ExecutionException.class, () -> store.get(key).get());
    assertThat(corrupt).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
    // An immutable completed entry is never silently replaced, even if it is damaged.
    assertThrows(ExecutionException.class, () -> store.put(key, value("payload")).get());
    try (var file = new RandomAccessFile(entry.toFile(), "rw")) {
      file.setLength(5);
    }
    assertThrows(ExecutionException.class, () -> store.get(key).get());
    Files.writeString(entry.getParent().resolve(".pending-abandoned.tmp"), "partial bytes");
    assertThrows(ExecutionException.class, () -> store.get(new Key("absent")).get());
    assertThrows(
        ExecutionException.class,
        () -> store.put(new Key("x".repeat(4097)), value("bad key")).get());
    assertThrows(
        IllegalArgumentException.class,
        () -> ConcurrentFileFingerprintValueStore.create(root, "../escape"));
  }

  @Test(timeout = 60000)
  public void independentLiveProcessesPublishDistinctAndIdenticalKeys() throws Exception {
    Path root = directory.newFolder("cache").toPath();
    Path control = directory.newFolder("control").toPath();
    var reader = ConcurrentFileFingerprintValueStore.create(root, NAMESPACE);
    assertThrows(ExecutionException.class, () -> reader.get(new Key("0:0")).get());
    List<Process> writers = new ArrayList<>();
    try {
      for (int id = 0; id < 4; id++) {
        writers.add(startChild(root, control, id, "many"));
      }
      for (int id = 0; id < 4; id++) {
        waitFor(control.resolve("ready-" + id));
      }
      Files.writeString(control.resolve("go"), "go");
      for (int id = 0; id < 4; id++) {
        waitFor(control.resolve("done-" + id));
      }
      // Every writer is still alive here, and this reader predates all of them.
      for (int id = 0; id < 4; id++) {
        assertThat(writers.get(id).isAlive()).isTrue();
        for (int i = 0; i < 25; i++) {
          assertThat(reader.get(new Key(id + ":" + i)).get()).isEqualTo(value(id + ":" + i));
          assertThat(reader.get(new Key("shared:" + i)).get()).isEqualTo(value("shared:" + i));
        }
      }
      Files.writeString(control.resolve("exit"), "exit");
      for (Process writer : writers) {
        assertThat(writer.waitFor(10, TimeUnit.SECONDS)).isTrue();
        assertThat(writer.exitValue()).isEqualTo(0);
      }
    } finally {
      for (Process writer : writers) {
        writer.destroyForcibly();
      }
    }
  }

  @Test(timeout = 60000)
  public void conflictingProcessesCannotOverwriteTheWinner() throws Exception {
    Path root = directory.newFolder("cache").toPath();
    Path control = directory.newFolder("control").toPath();
    List<Process> writers = new ArrayList<>();
    try {
      for (int id = 0; id < 2; id++) {
        writers.add(startChild(root, control, id, "conflict"));
      }
      for (int id = 0; id < 2; id++) {
        waitFor(control.resolve("ready-" + id));
      }
      Files.writeString(control.resolve("go"), "go");
      List<Integer> exits = new ArrayList<>();
      for (Process writer : writers) {
        assertThat(writer.waitFor(20, TimeUnit.SECONDS)).isTrue();
        exits.add(writer.exitValue());
      }
      assertThat(exits).containsExactly(0, 2);
      byte[] winner =
          ConcurrentFileFingerprintValueStore.create(root, NAMESPACE)
              .get(new Key("conflict"))
              .get();
      assertThat(new String(winner, StandardCharsets.UTF_8))
          .isEqualTo(Integer.toString(exits.indexOf(0)));
    } finally {
      for (Process writer : writers) {
        writer.destroyForcibly();
      }
    }
  }

  private static Process startChild(Path root, Path control, int id, String mode)
      throws IOException {
    return new ProcessBuilder(
            Path.of(System.getProperty("java.home"), "bin/java").toString(),
            "-Xmx64m",
            "-cp",
            System.getProperty("java.class.path"),
            ConcurrentFileFingerprintValueStoreTest.class.getName(),
            root.toString(),
            control.toString(),
            Integer.toString(id),
            mode)
        .redirectErrorStream(true)
        .redirectOutput(control.resolve("child-" + id + ".log").toFile())
        .start();
  }

  private static void waitFor(Path path) throws Exception {
    long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(20);
    while (!Files.exists(path)) {
      if (System.nanoTime() > deadline) {
        throw new IOException("Timed out waiting for " + path);
      }
      Thread.sleep(5);
    }
  }

  /** Subprocess entry point: exercises the same public store API from independent JVMs. */
  public static void main(String[] args) throws Exception {
    Path root = Path.of(args[0]);
    Path control = Path.of(args[1]);
    String id = args[2];
    var store = ConcurrentFileFingerprintValueStore.create(root, NAMESPACE);
    Files.writeString(control.resolve("ready-" + id), "ready");
    waitFor(control.resolve("go"));
    if (args[3].equals("conflict")) {
      try {
        store.put(new Key("conflict"), value(id)).get();
      } catch (ExecutionException expected) {
        if (!(expected.getCause() instanceof IOException)) {
          throw expected;
        }
        System.exit(2);
      }
      return;
    }
    for (int i = 0; i < 25; i++) {
      String key = id + ":" + i;
      store.put(new Key(key), value(key)).get();
      key = "shared:" + i;
      store.put(new Key(key), value(key)).get();
    }
    Files.writeString(control.resolve("done-" + id), "done");
    waitFor(control.resolve("exit"));
  }
}
