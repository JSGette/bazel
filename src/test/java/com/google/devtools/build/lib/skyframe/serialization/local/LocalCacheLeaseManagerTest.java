// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import java.io.IOException;
import java.io.InterruptedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalCacheLeaseManagerTest {
  private static final String A = "1".repeat(64);
  private static final String B = "2".repeat(64);
  private static final String COLLISION = "111" + "3".repeat(61);
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  @Test
  public void sharedOwnersRemainPinnedWhenAnotherOwnerCloses() throws Exception {
    Path root = temporary.newFolder().toPath();
    try (var first = LocalCacheLeaseManager.open(root);
        var second = LocalCacheLeaseManager.open(root);
        var collector = LocalCacheLeaseManager.open(root)) {
      var firstCatalog = first.trySharedCatalog();
      var secondCatalog = second.trySharedCatalog();
      assertThat(firstCatalog).isNotNull();
      assertThat(secondCatalog).isNotNull();
      var firstPin = first.pinPublication(A);
      second.pinPublication(A);
      assertThat(collector.tryExclusiveCatalog()).isNull();
      firstCatalog.close();
      secondCatalog.close();
      firstPin.close();
      first.close();
      try (var catalog = collector.tryExclusiveCatalog()) {
        assertThat(catalog).isNotNull();
        assertThat(collector.tryCollectPublication(A)).isNull();
        try (var unrelated = collector.tryCollectPublication(B)) {
          assertThat(unrelated).isNotNull();
        }
        second.close();
        try (var available = collector.tryCollectPublication(A)) {
          assertThat(available).isNotNull();
        }
      }
    }
    assertThat(Files.isRegularFile(root.resolve("leases.lock"))).isTrue();
    try (var reopened = LocalCacheLeaseManager.open(root);
        var catalog = reopened.tryExclusiveCatalog();
        var pin = reopened.tryCollectPublication(A)) {
      assertThat(catalog).isNotNull();
      assertThat(pin).isNotNull();
    }
  }

  @Test
  public void publicationPinsRequireThisOwnersCatalogAndCollisionsRetainBoth() throws Exception {
    Path root = temporary.newFolder().toPath();
    try (var reader = LocalCacheLeaseManager.open(root);
        var collector = LocalCacheLeaseManager.open(root)) {
      assertThrows(IOException.class, () -> reader.pinPublication(A));
      assertThrows(IOException.class, () -> collector.tryCollectPublication(A));
      try (var catalog = reader.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        assertThrows(IOException.class, () -> collector.pinPublication(A));
        assertThrows(IllegalArgumentException.class, () -> reader.pinPublication("../bad"));
        assertThrows(IllegalArgumentException.class, () -> reader.pinPublication("G".repeat(64)));
        reader.pinPublication(A);
      }
      try (var catalog = collector.tryExclusiveCatalog()) {
        assertThat(catalog).isNotNull();
        assertThat(collector.tryCollectPublication(COLLISION)).isNull();
        assertThat(reader.trySharedCatalog()).isNull();
        assertThrows(IOException.class, () -> collector.pinPublication(B));
      }
    }
  }

  @Test
  public void concurrentLocalSessionsShareTheChannelAndLeaveNoPins() throws Exception {
    Path root = temporary.newFolder().toPath();
    var acquired = new CyclicBarrier(9);
    var release = new CyclicBarrier(9);
    try (var pool = Executors.newFixedThreadPool(8);
        var collector = LocalCacheLeaseManager.open(root)) {
      List<java.util.concurrent.Future<?>> futures = new ArrayList<>();
      for (int i = 0; i < 8; i++) {
        futures.add(
            pool.submit(
                () -> {
                  try (var owner = LocalCacheLeaseManager.open(root)) {
                    try (var catalog = owner.trySharedCatalog()) {
                      assertThat(catalog).isNotNull();
                      owner.pinPublication(A);
                    }
                    acquired.await(20, TimeUnit.SECONDS);
                    release.await(20, TimeUnit.SECONDS);
                  }
                  return null;
                }));
      }
      acquired.await(20, TimeUnit.SECONDS);
      try (var catalog = collector.tryExclusiveCatalog()) {
        assertThat(catalog).isNotNull();
        assertThat(collector.tryCollectPublication(A)).isNull();
      }
      release.await(20, TimeUnit.SECONDS);
      for (var future : futures) {
        future.get(20, TimeUnit.SECONDS);
      }
      try (var catalog = collector.tryExclusiveCatalog();
          var pin = collector.tryCollectPublication(A)) {
        assertThat(catalog).isNotNull();
        assertThat(pin).isNotNull();
      }
    }
  }

  @Test
  public void interruptionDoesNotCloseAnotherOwnersChannelOrDropItsPin() throws Exception {
    Path root = temporary.newFolder().toPath();
    try (var reader = LocalCacheLeaseManager.open(root);
        var other = LocalCacheLeaseManager.open(root)) {
      try (var catalog = reader.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        reader.pinPublication(A);
      }
      Thread.currentThread().interrupt();
      try {
        assertThrows(InterruptedIOException.class, other::trySharedCatalog);
        assertThat(Thread.currentThread().isInterrupted()).isTrue();
        other.close();
      } finally {
        Thread.interrupted();
      }
      assertThat(probe(root, temporary.newFolder().toPath(), "probe")).isEqualTo(3);
    }
  }

  @Test
  public void independentProcessesSharePinsAndProcessDeathReleasesOnlyTheDeadOwner()
      throws Exception {
    Path root = temporary.newFolder().toPath();
    Path control = temporary.newFolder().toPath();
    Process first = child(root, control, "first", "hold");
    Process second = child(root, control, "second", "hold");
    try {
      waitFor(control.resolve("first.ready"));
      waitFor(control.resolve("second.ready"));
      try (var collector = LocalCacheLeaseManager.open(root)) {
        try (var catalog = collector.tryExclusiveCatalog()) {
          assertThat(catalog).isNotNull();
          assertThat(collector.tryCollectPublication(A)).isNull();
          try (var unrelated = collector.tryCollectPublication(B)) {
            assertThat(unrelated).isNotNull();
          }
        }
        first.destroyForcibly();
        assertThat(first.waitFor(20, TimeUnit.SECONDS)).isTrue();
        try (var catalog = collector.tryExclusiveCatalog()) {
          assertThat(catalog).isNotNull();
          assertThat(collector.tryCollectPublication(A)).isNull();
        }
        second.destroyForcibly();
        assertThat(second.waitFor(20, TimeUnit.SECONDS)).isTrue();
        try (var catalog = collector.tryExclusiveCatalog();
            var pin = collector.tryCollectPublication(A)) {
          assertThat(catalog).isNotNull();
          assertThat(pin).isNotNull();
        }
      }
    } finally {
      first.destroyForcibly();
      second.destroyForcibly();
      first.waitFor(20, TimeUnit.SECONDS);
      second.waitFor(20, TimeUnit.SECONDS);
    }
  }

  @Test
  public void closingOneLocalOwnerDoesNotReleaseAnotherPinToAnExternalCollector() throws Exception {
    Path root = temporary.newFolder().toPath();
    Path control = temporary.newFolder().toPath();
    try (var first = LocalCacheLeaseManager.open(root);
        var second = LocalCacheLeaseManager.open(root)) {
      try (var catalog = first.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        first.pinPublication(A);
      }
      try (var catalog = second.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        second.pinPublication(A);
      }
      first.close();
      assertThat(probe(root, control, "blocked")).isEqualTo(3);
      second.close();
      assertThat(probe(root, control, "free")).isEqualTo(0);
    }
  }

  @Test
  public void symlinkLockAndSymlinkRootAreRejectedWithoutChangingTheirTargets() throws Exception {
    Path root = temporary.newFolder().toPath();
    Path target = temporary.newFile().toPath();
    Files.writeString(target, "preserved");
    Files.createSymbolicLink(root.resolve("leases.lock"), target);
    assertThrows(IOException.class, () -> LocalCacheLeaseManager.open(root));
    assertThat(Files.readString(target)).isEqualTo("preserved");
    Path linkedRoot = root.resolve("linked-root");
    Files.createSymbolicLink(linkedRoot, temporary.newFolder().toPath());
    assertThrows(IOException.class, () -> LocalCacheLeaseManager.open(linkedRoot));
  }

  private static int probe(Path root, Path control, String name) throws Exception {
    Process process = child(root, control, name, "probe");
    try {
      assertThat(process.waitFor(20, TimeUnit.SECONDS)).isTrue();
      return process.exitValue();
    } finally {
      process.destroyForcibly();
      process.waitFor(20, TimeUnit.SECONDS);
    }
  }

  @Test
  public void externalCollectorExcludesNewCatalogReadersWithoutReleasingExistingPins()
      throws Exception {
    Path root = temporary.newFolder().toPath();
    Path control = temporary.newFolder().toPath();
    try (var reader = LocalCacheLeaseManager.open(root)) {
      try (var catalog = reader.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        reader.pinPublication(A);
      }
      Process collector = child(root, control, "collector", "catalog");
      try {
        waitFor(control.resolve("collector.ready"));
        assertThat(reader.trySharedCatalog()).isNull();
        collector.destroyForcibly();
        assertThat(collector.waitFor(20, TimeUnit.SECONDS)).isTrue();
        try (var catalog = reader.trySharedCatalog()) {
          assertThat(catalog).isNotNull();
        }
        assertThat(probe(root, control, "still-pinned")).isEqualTo(3);
      } finally {
        collector.destroyForcibly();
        collector.waitFor(20, TimeUnit.SECONDS);
      }
    }
  }

  private static Process child(Path root, Path control, String name, String mode)
      throws IOException {
    return new ProcessBuilder(
            Path.of(System.getProperty("java.home"), "bin/java").toString(),
            "-Xmx64m",
            "-cp",
            System.getProperty("java.class.path"),
            LocalCacheLeaseManagerTest.class.getName(),
            root.toString(),
            control.toString(),
            name,
            mode)
        .redirectErrorStream(true)
        .redirectOutput(control.resolve(name + ".log").toFile())
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

  /** Cross-JVM protocol checks using the same lease implementation as normal cache sessions. */
  public static void main(String[] args) throws Exception {
    Path root = Path.of(args[0]);
    Path control = Path.of(args[1]);
    String name = args[2];
    try (var owner = LocalCacheLeaseManager.open(root)) {
      if (args[3].equals("probe")) {
        try (var catalog = owner.tryExclusiveCatalog()) {
          if (catalog == null) {
            System.exit(4);
          }
          try (var pin = owner.tryCollectPublication(A)) {
            if (pin == null) {
              System.exit(3);
            }
          }
        }
        return;
      }
      if (args[3].equals("catalog")) {
        try (var catalog = owner.tryExclusiveCatalog()) {
          if (catalog == null || owner.tryCollectPublication(A) != null) {
            throw new IOException("collector failed to respect active publication pin");
          }
          Files.writeString(control.resolve(name + ".ready"), "ready");
          waitFor(control.resolve(name + ".release"));
        }
        return;
      }
      try (var catalog = owner.trySharedCatalog()) {
        if (catalog == null) {
          throw new IOException("unexpected busy catalog");
        }
        owner.pinPublication(A);
      }
      Files.writeString(control.resolve(name + ".ready"), "ready");
      waitFor(control.resolve(name + ".release"));
    }
  }
}
