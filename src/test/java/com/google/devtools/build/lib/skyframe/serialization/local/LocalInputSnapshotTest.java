// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import java.io.IOException;
import java.io.InterruptedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalInputSnapshotTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  private LocalInputSnapshot snapshot() throws Exception {
    return new LocalInputSnapshot(
        temporary.newFolder().toPath(), temporary.newFolder().toPath(), Map.of());
  }

  private static List<Thread> validationWorkers() {
    return Thread.getAllStackTraces().keySet().stream()
        .filter(t -> t.isAlive() && t.getName().startsWith("skycache-final-validation-"))
        .toList();
  }

  private static void addMissingWitnesses(LocalInputSnapshot inputs) {
    var witnesses = new ArrayList<LocalInputSnapshot.Witness>();
    for (int i = 0; i < 50_000; i++) {
      witnesses.add(new LocalInputSnapshot.Witness("external/missing-" + i, false, "missing"));
    }
    inputs.registerInheritedForFinalValidation(witnesses);
  }

  @Test
  public void parallelValidationPreservesTypedChecksAndFreshPasses() throws Exception {
    for (String mutation : List.of("content", "listing", "missing", "symlink", "mode", "type")) {
      Path directory = temporary.newFolder().toPath();
      Path file = Files.write(directory.resolve("file"), new byte[1024 * 1024]);
      Files.setPosixFilePermissions(file, PosixFilePermissions.fromString("rw-r--r--"));
      Path target = Files.write(directory.resolve("target"), new byte[1024 * 1024]);
      Path link = Files.createSymbolicLink(directory.resolve("link"), file.getFileName());
      Path missing = directory.resolve("missing");
      var inputs = snapshot();
      inputs.observe(file, false);
      inputs.observe(directory, true);
      inputs.observe(missing, false);
      inputs.observe(link, false);
      var evidence = inputs.witnesses();
      assertThat(inputs.isUnchanged(1)).isTrue();
      assertThat(inputs.isUnchanged(4)).isTrue();
      switch (mutation) {
        case "content" -> {
          var modified = Files.getLastModifiedTime(file);
          byte[] bytes = Files.readAllBytes(file);
          bytes[0] = 1;
          Files.write(file, bytes);
          Files.setLastModifiedTime(file, modified);
        }
        case "listing" -> Files.writeString(directory.resolve("added"), "new");
        case "missing" -> Files.writeString(missing, "now exists");
        case "symlink" -> {
          Files.delete(link);
          Files.createSymbolicLink(link, target.getFileName());
        }
        case "mode" ->
            Files.setPosixFilePermissions(file, PosixFilePermissions.fromString("rwxr-xr-x"));
        case "type" -> {
          Files.delete(file);
          Files.createDirectory(file);
        }
        default -> throw new AssertionError(mutation);
      }
      assertThat(inputs.isUnchanged(4)).isFalse();
      assertThat(inputs.isUnchanged(1)).isFalse();
      assertThat(inputs.witnesses()).containsExactlyElementsIn(evidence);
      assertThat(validationWorkers()).isEmpty();
    }
  }

  @Test
  public void parallelValidationChecksSourceAndConflictingEvidence() throws Exception {
    Path workspace = temporary.newFolder().toPath();
    Path source = Files.writeString(workspace.resolve("input"), "one");
    var inputs = new LocalInputSnapshot(workspace, temporary.newFolder().toPath(), Map.of());
    assertThat(inputs.isUnchanged(4)).isTrue();
    Files.writeString(source, "two");
    assertThat(inputs.isUnchanged(4)).isFalse();
    var conflict = snapshot();
    conflict.registerInheritedForFinalValidation(
        List.of(
            new LocalInputSnapshot.Witness("external/file", false, "first"),
            new LocalInputSnapshot.Witness("external/file", false, "second")));
    assertThat(conflict.isUnchanged(4)).isFalse();
    assertThat(validationWorkers()).isEmpty();
  }

  @Test
  public void parallelValidationPropagatesIoFailureAndJoinsWorkers() throws Exception {
    var inputs = snapshot();
    addMissingWitnesses(inputs);
    inputs.registerInheritedForFinalValidation(
        List.of(new LocalInputSnapshot.Witness("invalid-root", false, "missing")));
    var error = assertThrows(IOException.class, () -> inputs.isUnchanged(4));
    assertThat(error).hasMessageThat().contains("unknown witness root");
    assertThat(validationWorkers()).isEmpty();
  }

  @Test
  public void parallelValidationJoinsWorkersAfterInterruption() throws Exception {
    var inputs = snapshot();
    addMissingWitnesses(inputs);
    var failure = new AtomicReference<Throwable>();
    var interrupted = new AtomicBoolean();
    Thread caller =
        new Thread(
            () -> {
              try {
                inputs.isUnchanged(4);
              } catch (Throwable error) {
                failure.set(error);
                interrupted.set(Thread.currentThread().isInterrupted());
              }
            });
    caller.start();
    try {
      long deadline = System.nanoTime() + 10_000_000_000L;
      while (validationWorkers().isEmpty() && caller.isAlive() && System.nanoTime() < deadline) {
        Thread.sleep(1);
      }
      assertThat(validationWorkers()).isNotEmpty();
      caller.interrupt();
      caller.join(10_000);
      assertThat(caller.isAlive()).isFalse();
      assertThat(failure.get()).isInstanceOf(InterruptedIOException.class);
      assertThat(interrupted.get()).isTrue();
      assertThat(validationWorkers()).isEmpty();
    } finally {
      caller.interrupt();
      caller.join(10_000);
    }
  }

  @Test
  public void alreadyInterruptedValidationPreservesFlag() throws Exception {
    var inputs = snapshot();
    try {
      Thread.currentThread().interrupt();
      assertThrows(InterruptedIOException.class, () -> inputs.isUnchanged(4));
      assertThat(Thread.currentThread().isInterrupted()).isTrue();
      assertThat(validationWorkers()).isEmpty();
    } finally {
      Thread.interrupted();
    }
  }
}
