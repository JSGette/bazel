// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalCacheGarbageCollectorTest {
  private static final String A = "1".repeat(64);
  private static final String B = "2".repeat(64);
  private static final String C = "3".repeat(64);
  private static final String NAMESPACE = "a".repeat(64);
  private static final long NOW = 10_000;
  private static final LocalCacheGarbageCollector.Policy EVICT =
      new LocalCacheGarbageCollector.Policy(1, 0);
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  private Path cache() throws Exception {
    Path cache = temporary.newFolder().toPath();
    Files.createDirectories(managed(cache).resolve("publications-v4"));
    Files.createDirectories(managed(cache).resolve("objects/segmented-v1"));
    try (var unused = LocalCacheLeaseManager.open(managed(cache))) {}
    return cache;
  }

  private static Path managed(Path cache) {
    return cache.resolve("leased-v2");
  }

  private static Path pack(Path cache, String id) {
    return managed(cache).resolve("objects/segmented-v1/" + id);
  }

  private static Path manifest(Path cache, String id) {
    return managed(cache).resolve("publications-v4/" + NAMESPACE + "/" + id + ".manifest");
  }

  // Only manifest framing matters to collection. Live session tests below also use real packs.
  private static void publication(Path cache, String id, List<String> parents, long time)
      throws Exception {
    publication(cache, id, NAMESPACE, parents, time);
  }

  private static void publication(
      Path cache, String id, String namespace, List<String> parents, long time) throws Exception {
    Path directory = Files.createDirectories(pack(cache, id));
    Files.write(directory.resolve("data.pack"), new byte[256]);
    Files.write(directory.resolve("index"), new byte[64]);
    var bytes = new ByteArrayOutputStream();
    try (var out = new DataOutputStream(bytes)) {
      out.writeInt(0x534b5334);
      out.writeUTF(id);
      out.writeUTF(namespace);
      out.writeUTF("f".repeat(64));
      out.writeInt(parents.size());
      for (String parent : parents) {
        out.writeUTF(parent);
      }
      out.writeInt(0); // No witness blocks are needed by these GC framing fixtures.
    }
    byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes.toByteArray());
    bytes.write(digest);
    Path path = managed(cache).resolve("publications-v4/" + namespace + "/" + id + ".manifest");
    Files.createDirectories(path.getParent());
    Files.write(path, bytes.toByteArray());
    Files.setLastModifiedTime(path, FileTime.fromMillis(time));
  }

  private static LocalCacheGarbageCollector collector(
      Path cache, LocalCacheGarbageCollector.Policy policy) {
    return new LocalCacheGarbageCollector(
        cache, policy, () -> NOW, new LocalCacheGarbageCollector.Observer() {});
  }

  private static long bytes(Path cache, String id) throws Exception {
    return 320 + Files.size(manifest(cache, id));
  }

  @Test
  public void disabledAndAbsentDoNotCreateTheManagedLayout() throws Exception {
    Path cache = temporary.newFolder().toPath();
    assertThat(collector(cache, new LocalCacheGarbageCollector.Policy(0, 0)).run().status())
        .isEqualTo("disabled");
    assertThat(collector(cache, EVICT).run().status()).isEqualTo("absent");
    assertThat(Files.exists(managed(cache))).isFalse();
    assertThrows(
        IllegalArgumentException.class, () -> new LocalCacheGarbageCollector.Policy(-1, 0));
  }

  @Test
  public void exactByteBudgetKeepsNewestRootAndItsOlderParent() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    publication(cache, B, List.of(A), 3_000);
    publication(cache, C, List.of(), 2_000);
    long retained = bytes(cache, A) + bytes(cache, B);
    long removed = bytes(cache, C);
    var stats = collector(cache, new LocalCacheGarbageCollector.Policy(retained, 0)).run();
    assertThat(stats.status()).isEqualTo("complete");
    assertThat(stats.deletedPublications()).isEqualTo(1);
    assertThat(stats.deletedPacks()).isEqualTo(1);
    assertThat(stats.knownBytesBefore()).isEqualTo(retained + removed);
    assertThat(stats.knownBytesRetained()).isEqualTo(retained);
    assertThat(stats.deletedBytes()).isEqualTo(removed);
    assertThat(stats.catalogNanos()).isGreaterThan(0);
    assertThat(stats.elapsedNanos()).isAtLeast(stats.catalogNanos());
    assertThat(Files.exists(manifest(cache, A))).isTrue();
    assertThat(Files.exists(manifest(cache, B))).isTrue();
    assertThat(Files.exists(pack(cache, C))).isFalse();
  }

  @Test
  public void ageIsPublicationTimeWithInclusiveBoundaryAndParentClosure() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    publication(cache, B, List.of(A), 9_000);
    publication(cache, C, List.of(), 8_999);
    var stats = collector(cache, new LocalCacheGarbageCollector.Policy(0, 1_000)).run();
    assertThat(stats.deletedPublications()).isEqualTo(1);
    assertThat(Files.exists(manifest(cache, A))).isTrue();
    assertThat(Files.exists(manifest(cache, B))).isTrue();
    assertThat(Files.exists(manifest(cache, C))).isFalse();
  }

  @Test
  public void pinningOnlyChildProtectsUnpinnedTransitiveParentsBeyondQuota() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    publication(cache, B, List.of(A), 2_000);
    publication(cache, C, List.of(B), 3_000);
    try (var reader = LocalCacheLeaseManager.open(managed(cache))) {
      try (var catalog = reader.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        reader.pinPublication(C);
      }
      var stats = collector(cache, EVICT).run();
      assertThat(stats.activeSlots()).isEqualTo(1);
      assertThat(stats.deletedPublications()).isEqualTo(0);
      assertThat(stats.protectedBytes()).isEqualTo(stats.knownBytesBefore());
      assertThat(stats.knownBytesRetained()).isGreaterThan(1);
    }
    var stats = collector(cache, EVICT).run();
    assertThat(stats.deletedPublications()).isEqualTo(3);
    assertThat(stats.deletedPacks()).isEqualTo(3);
    assertThat(stats.knownBytesRetained()).isEqualTo(0);
  }

  @Test
  public void slotCollisionConservativelyKeepsUnrelatedPublication() throws Exception {
    Path cache = cache();
    String collision = "111" + "4".repeat(61);
    publication(cache, A, List.of(), 1_000);
    publication(cache, collision, List.of(), 2_000);
    try (var reader = LocalCacheLeaseManager.open(managed(cache))) {
      try (var catalog = reader.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        reader.pinPublication(A);
      }
      assertThat(collector(cache, EVICT).run().deletedPublications()).isEqualTo(0);
    }
    assertThat(collector(cache, EVICT).run().deletedPublications()).isEqualTo(2);
  }

  @Test
  public void unpublishedSealedAndUnsealedPacksAndRecognizedTemporariesAreCollected()
      throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), NOW);
    Path sealed = Files.createDirectories(pack(cache, B));
    Files.writeString(sealed.resolve("data.pack"), "sealed");
    Files.writeString(sealed.resolve("index"), "sealed");
    Path unsealed = Files.createDirectories(pack(cache, C));
    Files.writeString(unsealed.resolve("data.pack"), "unsealed");
    Files.writeString(unsealed.resolve(".pending-123.index"), "partial index");
    Path pending = manifest(cache, A).getParent().resolve(".pending-456.tmp");
    Files.writeString(pending, "partial manifest");
    var stats = collector(cache, new LocalCacheGarbageCollector.Policy(0, 1_000)).run();
    assertThat(stats.deletedPublications()).isEqualTo(0);
    assertThat(stats.deletedPacks()).isEqualTo(2);
    assertThat(stats.deletedPendingFiles()).isEqualTo(2);
    assertThat(Files.exists(pending)).isFalse();
    assertThat(Files.exists(pack(cache, A))).isTrue();
  }

  @Test
  public void unknownPackContentProtectsItsParentsAndLegacyDataRemainsUntouched() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    publication(cache, B, List.of(A), 2_000);
    Files.writeString(pack(cache, B).resolve("unrecognized"), "keep");
    Path legacy = Files.createDirectories(cache.resolve("objects/packed-v1/old"));
    Files.writeString(legacy.resolve("data.pack"), "legacy");
    Object lockKey =
        Files.readAttributes(
                managed(cache).resolve("leases.lock"),
                java.nio.file.attribute.BasicFileAttributes.class)
            .fileKey();
    var stats = collector(cache, EVICT).run();
    assertThat(stats.foreignEntries()).isEqualTo(1);
    assertThat(stats.deletedPublications()).isEqualTo(0);
    assertThat(Files.readString(legacy.resolve("data.pack"))).isEqualTo("legacy");
    assertThat(
            Files.readAttributes(
                    managed(cache).resolve("leases.lock"),
                    java.nio.file.attribute.BasicFileAttributes.class)
                .fileKey())
        .isEqualTo(lockKey);
  }

  @Test
  public void symlinkInPackIsPreservedWithoutFollowingIt() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    Path outside = temporary.newFile().toPath();
    Files.writeString(outside, "outside");
    Files.delete(pack(cache, A).resolve("index"));
    Files.createSymbolicLink(pack(cache, A).resolve("index"), outside);
    var stats = collector(cache, EVICT).run();
    assertThat(stats.deletedPublications()).isEqualTo(0);
    assertThat(Files.readString(outside)).isEqualTo("outside");
    assertThat(Files.isSymbolicLink(pack(cache, A).resolve("index"))).isTrue();
  }

  @Test
  public void symlinkManifestAbortsBeforeAnyDeletion() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    Path outside = temporary.newFile().toPath();
    Files.write(outside, Files.readAllBytes(manifest(cache, A)));
    Files.delete(manifest(cache, A));
    Files.createSymbolicLink(manifest(cache, A), outside);
    assertThrows(IOException.class, () -> collector(cache, EVICT).run());
    assertThat(Files.exists(pack(cache, A))).isTrue();
    assertThat(Files.exists(outside)).isTrue();
  }

  @Test
  public void corruptChecksumAbortsBeforeAnyDeletion() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    publication(cache, B, List.of(), 2_000);
    byte[] bytes = Files.readAllBytes(manifest(cache, B));
    bytes[bytes.length - 1] ^= 1;
    Files.write(manifest(cache, B), bytes);
    assertThrows(IOException.class, () -> collector(cache, EVICT).run());
    assertThat(Files.exists(manifest(cache, A))).isTrue();
    assertThat(Files.exists(pack(cache, B))).isTrue();
  }

  @Test
  public void missingParentAbortsBeforeAnyDeletion() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(B), 1_000);
    assertThat(assertThrows(IOException.class, () -> collector(cache, EVICT).run()))
        .hasMessageThat()
        .contains("incomplete");
    assertThat(Files.exists(manifest(cache, A))).isTrue();
  }

  @Test
  public void parentCycleAbortsBeforeAnyDeletion() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(B), 1_000);
    publication(cache, B, List.of(A), 2_000);
    assertThat(assertThrows(IOException.class, () -> collector(cache, EVICT).run()))
        .hasMessageThat()
        .contains("cycle");
    assertThat(Files.exists(manifest(cache, A))).isTrue();
    assertThat(Files.exists(manifest(cache, B))).isTrue();
  }

  @Test
  public void crossNamespaceParentAbortsBeforeAnyDeletion() throws Exception {
    Path cache = cache();
    publication(cache, A, "b".repeat(64), List.of(), 1_000);
    publication(cache, B, List.of(A), 2_000);
    assertThat(assertThrows(IOException.class, () -> collector(cache, EVICT).run()))
        .hasMessageThat()
        .contains("cross-namespace");
    assertThat(Files.exists(manifest(cache, B))).isTrue();
  }

  @Test
  public void collectorAndCatalogContentionSkipWithoutDeletion() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    try (var owner = LocalCacheLeaseManager.open(managed(cache))) {
      try (var coordination = owner.tryCollector()) {
        assertThat(coordination).isNotNull();
        var stats = collector(cache, EVICT).run();
        assertThat(stats.status()).isEqualTo("collector_busy");
        assertThat(stats.json()).contains("\"known_bytes_before\":null");
      }
      try (var catalog = owner.trySharedCatalog()) {
        assertThat(catalog).isNotNull();
        assertThat(collector(cache, EVICT).run().status()).isEqualTo("catalog_busy");
      }
    }
    assertThat(Files.exists(manifest(cache, A))).isTrue();
    assertThat(collector(cache, EVICT).run().deletedPublications()).isEqualTo(1);
  }

  @Test
  public void publicationAddedDuringOutsideScanIsReconciledBeforeProtectingParents()
      throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    try (var writer = LocalCacheLeaseManager.open(managed(cache))) {
      var collector =
          new LocalCacheGarbageCollector(
              cache,
              EVICT,
              () -> NOW,
              new LocalCacheGarbageCollector.Observer() {
                @Override
                public void afterScan() throws IOException {
                  try (var catalog = writer.trySharedCatalog()) {
                    assertThat(catalog).isNotNull();
                    writer.pinPublication(B);
                    try {
                      publication(cache, B, List.of(A), 2_000);
                    } catch (Exception e) {
                      throw new IOException(e);
                    }
                  }
                }
              });
      var stats = collector.run();
      assertThat(stats.publications()).isEqualTo(2);
      assertThat(stats.deletedPublications()).isEqualTo(0);
      assertThat(stats.scanBytes())
          .isEqualTo(Files.size(manifest(cache, A)) + Files.size(manifest(cache, B)));
    }
  }

  @Test
  public void changingPreviouslyScannedManifestAbortsBeforeDeletion() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    var collector =
        new LocalCacheGarbageCollector(
            cache,
            EVICT,
            () -> NOW,
            new LocalCacheGarbageCollector.Observer() {
              @Override
              public void afterScan() throws IOException {
                Files.setLastModifiedTime(manifest(cache, A), FileTime.fromMillis(2_000));
              }
            });
    assertThat(collector.run().status()).isEqualTo("catalog_changed");
    assertThat(Files.exists(pack(cache, A))).isTrue();
  }

  @Test
  public void interruptionAfterDurableChildUnlinkLeavesParentsAndReleasesLocks() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    publication(cache, B, List.of(A), 2_000);
    var collector =
        new LocalCacheGarbageCollector(
            cache,
            EVICT,
            () -> NOW,
            new LocalCacheGarbageCollector.Observer() {
              @Override
              public void afterUnpublish(Path path) throws InterruptedException {
                assertThat(path).isEqualTo(manifest(cache, B));
                Thread.currentThread().interrupt();
                throw new InterruptedException("injected interruption");
              }
            });
    try {
      assertThrows(InterruptedException.class, collector::run);
      assertThat(Thread.currentThread().isInterrupted()).isTrue();
    } finally {
      Thread.interrupted();
    }
    assertThat(Files.exists(manifest(cache, B))).isFalse();
    assertThat(Files.exists(manifest(cache, A))).isTrue();
    assertThat(Files.exists(pack(cache, B))).isTrue();
    var recovered = collector(cache, EVICT).run();
    assertThat(recovered.deletedPublications()).isEqualTo(1);
    assertThat(recovered.deletedPacks()).isEqualTo(2);
  }

  private record Key(String name) implements KeyBytesProvider {
    @Override
    public byte[] toBytes() {
      return name.getBytes(UTF_8);
    }

    @Override
    public byte[] concat(byte[] suffix) {
      return ByteBuffer.allocate(toBytes().length + suffix.length)
          .put(toBytes())
          .put(suffix)
          .array();
    }
  }

  @Test
  public void realReaderAndPrivateWriterSurviveCollectionThenBecomeCollectible() throws Exception {
    Path cache = cache();
    Path workspace = temporary.newFolder().toPath();
    Path output = temporary.newFolder().toPath();
    var seed = new LocalCacheSession(cache, new LocalInputSnapshot(workspace, output, Map.of()));
    seed.put(new Key("seed"), new byte[] {1}).get();
    assertThat(seed.finish(true)).isTrue();
    var writer = new LocalCacheSession(cache, new LocalInputSnapshot(workspace, output, Map.of()));
    try {
      assertThat(writer.get(new Key("seed")).get()).isEqualTo(new byte[] {1});
      writer.put(new Key("private"), new byte[] {2}).get();
      var stats = collector(cache, EVICT).run();
      assertThat(stats.deletedPacks()).isEqualTo(0);
      assertThat(stats.deletedPublications()).isEqualTo(0);
      assertThat(writer.get(new Key("seed")).get()).isEqualTo(new byte[] {1});
      assertThat(writer.get(new Key("private")).get()).isEqualTo(new byte[] {2});
      writer.put(new Key("later"), new byte[] {3}).get();
      assertThat(writer.finish(true)).isTrue();
    } finally {
      writer.finish(false);
    }
    var reader = new LocalCacheSession(cache, new LocalInputSnapshot(workspace, output, Map.of()));
    try {
      assertThat(reader.get(new Key("later")).get()).isEqualTo(new byte[] {3});
      assertThat(collector(cache, EVICT).run().deletedPacks()).isEqualTo(0);
      assertThat(reader.get(new Key("seed")).get()).isEqualTo(new byte[] {1});
    } finally {
      reader.finish(false);
    }
    assertThat(collector(cache, EVICT).run().deletedPacks()).isEqualTo(2);
  }

  @Test
  public void killedCollectorLeavesClosedGraphAndNextCollectorRecoversOrphans() throws Exception {
    Path cache = cache();
    publication(cache, A, List.of(), 1_000);
    publication(cache, B, List.of(A), 2_000);
    Path control = temporary.newFolder().toPath();
    Process process = child(cache, control, "collect");
    try {
      waitFor(control.resolve("ready"));
      assertThat(Files.readString(control.resolve("ready"))).isEqualTo(B);
      assertThat(collector(cache, EVICT).run().status()).isEqualTo("collector_busy");
      process.destroyForcibly();
      assertThat(process.waitFor(20, TimeUnit.SECONDS)).isTrue();
      assertThat(Files.exists(manifest(cache, A))).isTrue();
      assertThat(Files.exists(manifest(cache, B))).isFalse();
      assertThat(Files.exists(pack(cache, B))).isTrue();
      var recovered = collector(cache, EVICT).run();
      assertThat(recovered.deletedPublications()).isEqualTo(1);
      assertThat(recovered.deletedPacks()).isEqualTo(2);
    } finally {
      process.destroyForcibly();
      process.waitFor(20, TimeUnit.SECONDS);
    }
  }

  @Test
  public void killedWriterReleasesPinAndUnpublishedPackCanBeReclaimed() throws Exception {
    Path cache = cache();
    Path control = temporary.newFolder().toPath();
    Process process = child(cache, control, "write");
    try {
      waitFor(control.resolve("ready"));
      assertThat(collector(cache, EVICT).run().deletedPacks()).isEqualTo(0);
      process.destroyForcibly();
      assertThat(process.waitFor(20, TimeUnit.SECONDS)).isTrue();
      var recovered = collector(cache, EVICT).run();
      assertThat(recovered.deletedPublications()).isEqualTo(0);
      assertThat(recovered.deletedPacks()).isEqualTo(1);
    } finally {
      process.destroyForcibly();
      process.waitFor(20, TimeUnit.SECONDS);
    }
  }

  private static Process child(Path cache, Path control, String mode) throws IOException {
    return new ProcessBuilder(
            Path.of(System.getProperty("java.home"), "bin/java").toString(),
            "-Xmx64m",
            "-cp",
            System.getProperty("java.class.path"),
            LocalCacheGarbageCollectorTest.class.getName(),
            cache.toString(),
            control.toString(),
            mode)
        .redirectErrorStream(true)
        .redirectOutput(control.resolve("child.log").toFile())
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

  /** Separate JVMs exercise process death and OS lease release, not only same-JVM bookkeeping. */
  public static void main(String[] args) throws Exception {
    Path cache = Path.of(args[0]);
    Path control = Path.of(args[1]);
    if (args[2].equals("write")) {
      Path workspace = Files.createDirectory(control.resolve("workspace"));
      Path output = Files.createDirectory(control.resolve("output"));
      var writer =
          new LocalCacheSession(cache, new LocalInputSnapshot(workspace, output, Map.of()));
      try {
        writer.put(new Key("private"), new byte[] {1}).get();
        Files.writeString(control.resolve("ready"), "ready");
        waitFor(control.resolve("release"));
      } finally {
        writer.finish(false);
      }
      return;
    }
    new LocalCacheGarbageCollector(
            cache,
            EVICT,
            () -> NOW,
            new LocalCacheGarbageCollector.Observer() {
              @Override
              public void afterUnpublish(Path path) throws IOException, InterruptedException {
                Files.writeString(
                    control.resolve("ready"), path.getFileName().toString().substring(0, 64));
                try {
                  waitFor(control.resolve("release"));
                } catch (IOException | InterruptedException e) {
                  throw e;
                } catch (Exception e) {
                  throw new IOException(e);
                }
              }
            })
        .run();
  }
}
