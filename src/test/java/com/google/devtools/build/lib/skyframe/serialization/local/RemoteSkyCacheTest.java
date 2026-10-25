// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.Assert.assertThrows;

import build.bazel.remote.execution.v2.BatchUpdateBlobsRequest;
import build.bazel.remote.execution.v2.BatchUpdateBlobsResponse;
import build.bazel.remote.execution.v2.ContentAddressableStorageGrpc.ContentAddressableStorageImplBase;
import build.bazel.remote.execution.v2.Digest;
import com.google.bytestream.ByteStreamGrpc.ByteStreamImplBase;
import com.google.bytestream.ByteStreamProto.ReadRequest;
import com.google.bytestream.ByteStreamProto.ReadResponse;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheCatalogGrpc.SkyCacheCatalogImplBase;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverResponse;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationBundle;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationFile;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProtocol;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheTransport;
import com.google.protobuf.ByteString;
import com.google.protobuf.ExtensionRegistry;
import io.grpc.Server;
import io.grpc.Status;
import io.grpc.netty.NettyServerBuilder;
import io.grpc.stub.StreamObserver;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Pattern;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class RemoteSkyCacheTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  private Path workspace;
  private Path output;
  private Path evidence;
  private String namespace;

  @Before
  public void setUp() throws Exception {
    workspace = temporary.newFolder("workspace").toPath();
    output = temporary.newFolder("output").toPath();
    Path external = Files.createDirectories(output.resolve("external/repo"));
    evidence = Files.writeString(external.resolve("source"), "unchanged");
    namespace = snapshot().cacheNamespace();
  }

  @Test
  public void completePublicationRoundTripsAndRepeatedHydrationPreservesIdentity()
      throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path manifest = publishLocal(source, new Key("entry"), new byte[] {1, 2, 3});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      assertThat(reader.hydrate()).isEqualTo(1);
      assertValue(destination, new Key("entry"), new byte[] {1, 2, 3});
      Path imported = manifestDirectory(destination).resolve(manifest.getFileName());
      byte[] before = Files.readAllBytes(imported);
      assertThat(reader.hydrate()).isEqualTo(1);
      assertThat(Files.readAllBytes(imported)).isEqualTo(before);
      assertValue(destination, new Key("entry"), new byte[] {1, 2, 3});
    }
  }

  @Test
  public void publicationImportsCompleteParentClosure() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    publishLocal(source, new Key("parent"), new byte[] {7});
    var child = new LocalCacheSession(source, snapshot());
    assertThat(child.get(new Key("parent")).get()).isEqualTo(new byte[] {7});
    child.put(new Key("child"), new byte[] {8}).get();
    assertThat(child.finish(true)).isTrue();
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(child.publicationManifest());
      PublicationBundle bundle = backend.bundle();
      assertThat(bundle.getPublicationsCount()).isEqualTo(2);
      assertThat(bundle.getPublications(1).getParentIdsList())
          .containsExactly(bundle.getPublications(0).getId());
      assertThat(reader.hydrate()).isEqualTo(2);
      assertValue(destination, new Key("parent"), new byte[] {7});
      assertValue(destination, new Key("child"), new byte[] {8});
    }
  }

  @Test
  public void corruptBundleDigestCannotExposeAnyPublication() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path manifest = publishLocal(source, new Key("entry"), new byte[] {1});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      byte[] corrupt = backend.blobs.get(backend.advertised).clone();
      corrupt[corrupt.length - 1] ^= 1;
      backend.blobs.put(backend.advertised, corrupt);
      assertThrows(IOException.class, reader::hydrate);
      assertNoPublications(destination);
    }
  }

  @Test
  public void unsafeFilenameCannotEscapeStagingOrExposePublication() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path manifest = publishLocal(source, new Key("entry"), new byte[] {1});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      var bundle = backend.bundle().toBuilder();
      var member = bundle.getPublications(0).toBuilder();
      member.setFiles(0, member.getFiles(0).toBuilder().setName("../escaped"));
      backend.advertise(bundle.setPublications(0, member).build());
      assertThrows(IOException.class, reader::hydrate);
      assertNoPublications(destination);
      try (var paths = Files.walk(destination)) {
        assertThat(paths.anyMatch(p -> p.getFileName().toString().equals("escaped"))).isFalse();
      }
    }
  }

  @Test
  public void missingChunkCannotExposePartialPublication() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path manifest = publishLocal(source, new Key("entry"), new byte[] {1});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      Digest missing = backend.bundle().getPublications(0).getFiles(0).getChunks(0);
      backend.blobs.remove(missing);
      assertThrows(IOException.class, reader::hydrate);
      assertNoPublications(destination);
    }
  }

  @Test
  public void changedWitnessStillRejectsDemandReadValue() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path manifest = publishLocal(source, new Key("entry"), new byte[] {1});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      Files.writeString(evidence, "changed");
      assertThat(reader.prepare()).isEqualTo(1);
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThrows(ExecutionException.class, () -> session.get(new Key("entry")).get());
      } finally {
        session.finish(false);
      }
      assertNoPublications(destination);
    }
  }

  @Test
  public void immutableIdentityConflictPreservesExistingBytes() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path manifest = publishLocal(source, new Key("entry"), new byte[] {1});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      String id = backend.bundle().getRootId();
      Path collision =
          Files.createDirectories(
              destination.resolve("leased-v2/objects/segmented-v1").resolve(id));
      Path index = Files.writeString(collision.resolve("index"), "existing immutable bytes");
      assertThrows(IOException.class, reader::hydrate);
      assertThat(Files.readString(index)).isEqualTo("existing immutable bytes");
      assertNoPublications(destination);
    }
  }

  @Test
  public void prepareAndRepeatedReadsFetchOnlyRequestedRecordWithoutDurableImport()
      throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    LargePublication fixture = publishLarge(source);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      PublicationFile pack = onlyFile(backend.bundle(), ".pack");
      PublicationFile index = onlyFile(backend.bundle(), ".index");
      assertThat(pack.getChunksCount()).isAtLeast(3);
      assertThat(reader.prepare()).isEqualTo(1);
      assertThat(backend.requestedDigests).containsNoneIn(pack.getChunksList());
      assertThat(backend.requestedDigests).containsNoneIn(index.getChunksList());
      assertNoPublications(destination);

      backend.requestedDigests.clear();
      long beforeBytes = backend.downloadedBytes.get();
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
        var expectedReads = new ArrayList<>(index.getChunksList());
        expectedReads.add(pack.getChunks(0));
        assertThat(backend.requestedDigests).containsExactlyElementsIn(expectedReads);
        assertThat(backend.downloadedBytes.get() - beforeBytes)
            .isEqualTo(expectedReads.stream().mapToLong(Digest::getSizeBytes).sum());
        int firstReads = backend.requestedDigests.size();
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
        var candidate = session.nextAnalysisCandidate(fixture.key(), new HashSet<>());
        assertThat(candidate).isNotNull();
        assertThat(session.acceptAnalysisCandidate(candidate)).isTrue();
        assertThat(session.importedPublicationManifest()).isNull();
        assertThat(session.importedRemoteAnalysis()).isTrue();
        assertThat(backend.requestedDigests).hasSize(firstReads);
      } finally {
        assertThat(session.finish(true)).isFalse();
      }
      assertNoPublications(destination);
      assertThat(backend.requestedDigests)
          .containsNoneIn(pack.getChunksList().subList(1, pack.getChunksCount()));
    }
  }

  @Test
  public void repeatedSmallRangesReadOnlyCoveringBlocksAndHandleBoundaries() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var fixture = prepareRanges(source, backend, reader);
      fixture.assertRead(128, 17);
      backend.requestedDigests.clear();
      long before = statistic(reader, "cached_read_bytes");
      for (int i = 0; i < 100; i++) {
        fixture.assertRead(128 + i, 17);
      }
      assertThat(statistic(reader, "cached_read_bytes") - before).isEqualTo(100L * 4096);
      assertThat(backend.requestedDigests).isEmpty();

      for (int offset : List.of(4093, SkyCacheProtocol.CHUNK_BYTES - 3)) {
        fixture.assertRead(offset, 8);
        backend.requestedDigests.clear();
        before = statistic(reader, "cached_read_bytes");
        fixture.assertRead(offset, 8);
        assertThat(statistic(reader, "cached_read_bytes") - before).isEqualTo(2L * 4096);
        assertThat(backend.requestedDigests).isEmpty();
      }
      int last = fixture.bytes().length - 7;
      fixture.assertRead(last, 7);
      backend.requestedDigests.clear();
      before = statistic(reader, "cached_read_bytes");
      fixture.assertRead(last, 7);
      assertThat(statistic(reader, "cached_read_bytes") - before)
          .isEqualTo(fixture.bytes().length % 4096);
      assertThat(backend.requestedDigests).isEmpty();
    }
  }

  @Test
  public void forgedRecordChecksumCannotAuthenticateChangedCoveredBlock() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var fixture = prepareRanges(source, backend, reader);
      fixture.assertRead(0, 1);
      byte[] forged = fixture.bytes().clone();
      int valueOffset = 44 + new Key("entry").toBytes().length;
      forged[valueOffset] ^= 1;
      byte[] checksum =
          MessageDigest.getInstance("SHA-256")
              .digest(Arrays.copyOfRange(forged, valueOffset, forged.length));
      System.arraycopy(checksum, 0, forged, 12, checksum.length);
      Files.write(
          cachedChunk(destination, fixture.pack().getChunks(0)),
          Arrays.copyOf(forged, SkyCacheProtocol.CHUNK_BYTES));
      backend.requestedDigests.clear();
      var target = ByteBuffer.allocate(1);
      var failure =
          assertThrows(
              IOException.class,
              () -> fixture.source().readCached(fixture.pack().getName(), target, valueOffset));
      assertThat(failure).hasMessageThat().contains("block checksum mismatch");
      assertThat(target.position()).isEqualTo(0);
      assertThat(backend.requestedDigests).isEmpty();
    }
  }

  @Test
  public void corruptedUnreadBlockIsRejectedOnlyWhenRequested() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var fixture = prepareRanges(source, backend, reader);
      fixture.assertRead(0, 1);
      Path cached = cachedChunk(destination, fixture.pack().getChunks(0));
      byte[] damaged = Files.readAllBytes(cached);
      damaged[8192] ^= 1;
      Files.write(cached, damaged);
      backend.requestedDigests.clear();
      fixture.assertRead(0, 1);
      var target = ByteBuffer.allocate(1);
      assertThrows(
          IOException.class, () -> fixture.source().read(fixture.pack().getName(), target, 8192));
      assertThat(target.position()).isEqualTo(0);
      assertThat(backend.requestedDigests).isEmpty();
    }
  }

  @Test
  public void missingTruncatedOrSymlinkedScratchNeverDownloadsDuringCachedRead() throws Exception {
    for (String damage : List.of("missing", "truncated", "symlink")) {
      Path source = temporary.newFolder("source-" + damage).toPath();
      Path destination = temporary.newFolder(damage).toPath();
      try (var backend = new MemoryRemote();
          var reader = remote(destination, backend)) {
        var fixture = prepareRanges(source, backend, reader);
        fixture.assertRead(0, 1);
        Path cached = cachedChunk(destination, fixture.pack().getChunks(0));
        byte[] original = Files.readAllBytes(cached);
        if (damage.equals("truncated")) {
          Files.write(cached, Arrays.copyOf(original, original.length - 1));
        } else {
          Files.delete(cached);
          if (damage.equals("symlink")) {
            Path replacement = temporary.newFolder().toPath().resolve("replacement");
            Files.write(replacement, original);
            Files.createSymbolicLink(cached, replacement);
          }
        }
        backend.requestedDigests.clear();
        assertThat(fixture.source().isCached(fixture.pack().getName(), 0, 1)).isFalse();
        var target = ByteBuffer.allocate(1);
        assertThrows(
            IOException.class,
            () -> fixture.source().readCached(fixture.pack().getName(), target, 0));
        assertThat(target.position()).isEqualTo(0);
        assertThat(backend.requestedDigests).isEmpty();
      }
    }
  }

  @Test
  public void authenticationEvictionReverifiesWholeChunkBeforeReturningRange() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    try (var backend = new MemoryRemote();
        var reader =
            new RemoteSkyCache(
                destination,
                namespace,
                32L * 1024 * 1024,
                "instance",
                new SkyCacheTransport(backend.endpoint(), "instance", "", Duration.ofSeconds(15)),
                8320)) {
      var fixture = prepareRanges(source, backend, reader);
      assertThat(fixture.pack().getChunks(0)).isNotEqualTo(fixture.pack().getChunks(1));
      fixture.assertRead(0, 1);
      long evictions = statistic(reader, "authentication_cache_evictions");
      fixture.assertRead(SkyCacheProtocol.CHUNK_BYTES, 1);
      assertThat(statistic(reader, "authentication_cache_evictions")).isGreaterThan(evictions);
      assertThat(statistic(reader, "authentication_cache_bytes")).isAtMost(8320);
      long rebuildBefore = statistic(reader, "cached_read_bytes");
      fixture.assertRead(0, 1);
      assertThat(statistic(reader, "cached_read_bytes") - rebuildBefore)
          .isEqualTo(SkyCacheProtocol.CHUNK_BYTES);
      rebuildBefore = statistic(reader, "cached_read_bytes");
      fixture.assertRead(0, 1);
      assertThat(statistic(reader, "cached_read_bytes") - rebuildBefore).isEqualTo(4096);
      fixture.assertRead(SkyCacheProtocol.CHUNK_BYTES, 1);
      Path cached = cachedChunk(destination, fixture.pack().getChunks(0));
      byte[] damaged = Files.readAllBytes(cached);
      damaged[8192] ^=
          1; // Outside the requested block; eviction requires whole-chunk verification.
      Files.write(cached, damaged);
      backend.requestedDigests.clear();
      long before = statistic(reader, "cached_read_bytes");
      var target = ByteBuffer.allocate(1);
      var failure =
          assertThrows(
              IOException.class,
              () -> fixture.source().readCached(fixture.pack().getName(), target, 0));
      assertThat(failure).hasMessageThat().contains("chunk checksum mismatch");
      assertThat(target.position()).isEqualTo(0);
      assertThat(statistic(reader, "cached_read_bytes") - before)
          .isEqualTo(SkyCacheProtocol.CHUNK_BYTES);
      assertThat(backend.requestedDigests).isEmpty();
    }
  }

  @Test
  public void blockedDownloadAllowsCachedReadsButDoesNotExposeInflightChunk() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    var pool = Executors.newFixedThreadPool(3);
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var fixture = prepareRanges(source, backend, reader);
      fixture.assertRead(0, 1);
      var gate = new ReadGate(fixture.pack().getChunks(1));
      backend.beforeRead = gate::beforeRead;
      backend.requestedDigests.clear();
      try {
        var download =
            pool.submit(
                () -> {
                  fixture.assertRead(SkyCacheProtocol.CHUNK_BYTES, 31);
                  return null;
                });
        assertThat(gate.entered.await(5, TimeUnit.SECONDS)).isTrue();
        pool.submit(
                () -> {
                  fixture.assertRead(128, 31);
                  return null;
                })
            .get(5, TimeUnit.SECONDS);
        pool.submit(
                () -> {
                  assertThat(
                          fixture
                              .source()
                              .isCached(fixture.pack().getName(), SkyCacheProtocol.CHUNK_BYTES, 1))
                      .isFalse();
                  var target = ByteBuffer.allocate(1);
                  assertThrows(
                      SegmentedFingerprintValueStore.NotCachedException.class,
                      () ->
                          fixture
                              .source()
                              .readCached(
                                  fixture.pack().getName(), target, SkyCacheProtocol.CHUNK_BYTES));
                  assertThat(target.position()).isEqualTo(0);
                  return null;
                })
            .get(5, TimeUnit.SECONDS);
        assertThat(backend.requestedDigests).containsExactly(gate.digest);
        gate.release.countDown();
        download.get(5, TimeUnit.SECONDS);
      } finally {
        gate.release.countDown();
      }
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void simultaneousReadersCoalesceOneMissingChunkDownload() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    var pool = Executors.newFixedThreadPool(4);
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var fixture = prepareRanges(source, backend, reader);
      var gate = new ReadGate(fixture.pack().getChunks(1));
      backend.beforeRead = gate::beforeRead;
      backend.requestedDigests.clear();
      var ready = new CountDownLatch(4);
      var start = new CountDownLatch(1);
      var reads = new ArrayList<Future<?>>();
      try {
        for (int i = 0; i < 4; i++) {
          int offset = SkyCacheProtocol.CHUNK_BYTES + i * 17;
          reads.add(
              pool.submit(
                  () -> {
                    ready.countDown();
                    assertThat(start.await(5, TimeUnit.SECONDS)).isTrue();
                    fixture.assertRead(offset, 31);
                    return null;
                  }));
        }
        assertThat(ready.await(5, TimeUnit.SECONDS)).isTrue();
        start.countDown();
        assertThat(gate.entered.await(5, TimeUnit.SECONDS)).isTrue();
        gate.release.countDown();
        for (var read : reads) {
          read.get(5, TimeUnit.SECONDS);
        }
        assertThat(backend.requestedDigests).containsExactly(gate.digest);
      } finally {
        start.countDown();
        gate.release.countDown();
      }
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void closeCancelsBlockedDownloadAndWaitingReadersBeforeCleaningScratch() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    var pool = Executors.newFixedThreadPool(3);
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var fixture = prepareRanges(source, backend, reader);
      fixture.assertRead(0, 1);
      var gate = new ReadGate(fixture.pack().getChunks(1));
      backend.beforeRead = gate::beforeRead;
      backend.requestedDigests.clear();
      try {
        var download =
            pool.submit(
                () -> {
                  fixture.assertRead(SkyCacheProtocol.CHUNK_BYTES, 1);
                  return null;
                });
        assertThat(gate.entered.await(5, TimeUnit.SECONDS)).isTrue();
        var waitingStarted = new CountDownLatch(1);
        var waiting =
            pool.submit(
                () -> {
                  waitingStarted.countDown();
                  fixture.assertRead(2 * SkyCacheProtocol.CHUNK_BYTES, 1);
                  return null;
                });
        assertThat(waitingStarted.await(5, TimeUnit.SECONDS)).isTrue();
        pool.submit(reader::close).get(5, TimeUnit.SECONDS);
        assertThat(assertThrows(ExecutionException.class, () -> download.get(5, TimeUnit.SECONDS)))
            .hasCauseThat()
            .isInstanceOf(IOException.class);
        assertThat(assertThrows(ExecutionException.class, () -> waiting.get(5, TimeUnit.SECONDS)))
            .hasCauseThat()
            .isInstanceOf(IOException.class);
        try (var paths = Files.list(destination.resolve("leased-v2"))) {
          assertThat(paths.anyMatch(p -> p.getFileName().toString().startsWith(".remote-")))
              .isFalse();
        }
        var target = ByteBuffer.allocate(1);
        assertThrows(
            IOException.class,
            () -> fixture.source().readCached(fixture.pack().getName(), target, 0));
        assertThat(target.position()).isEqualTo(0);
        assertThat(backend.requestedDigests).containsExactly(gate.digest);
      } finally {
        gate.release.countDown();
      }
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void interruptedDownloadWaiterPreservesInterruptWithoutAnotherRpc() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    var pool = Executors.newFixedThreadPool(2);
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var fixture = prepareRanges(source, backend, reader);
      var gate = new ReadGate(fixture.pack().getChunks(1));
      backend.beforeRead = gate::beforeRead;
      backend.requestedDigests.clear();
      try {
        var download =
            pool.submit(
                () -> {
                  fixture.assertRead(SkyCacheProtocol.CHUNK_BYTES, 1);
                  return null;
                });
        assertThat(gate.entered.await(5, TimeUnit.SECONDS)).isTrue();
        var started = new CountDownLatch(1);
        var thread = new AtomicReference<Thread>();
        var interrupted = new AtomicBoolean();
        var waiting =
            pool.submit(
                () -> {
                  thread.set(Thread.currentThread());
                  started.countDown();
                  try {
                    fixture.assertRead(2 * SkyCacheProtocol.CHUNK_BYTES, 1);
                    return null;
                  } catch (IOException e) {
                    interrupted.set(Thread.currentThread().isInterrupted());
                    return e;
                  }
                });
        assertThat(started.await(5, TimeUnit.SECONDS)).isTrue();
        thread.get().interrupt();
        assertThat(waiting.get(5, TimeUnit.SECONDS)).isInstanceOf(IOException.class);
        assertThat(interrupted.get()).isTrue();
        assertThat(backend.requestedDigests).containsExactly(gate.digest);
        gate.release.countDown();
        download.get(5, TimeUnit.SECONDS);
      } finally {
        gate.release.countDown();
      }
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void gigabyteLogicalPublicationReadsOnlyRequestedChunksWithDefaultTransferBudget()
      throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    LargePublication fixture = publishLarge(source);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      PublicationFile originalPack = onlyFile(backend.bundle(), ".pack");
      var padded = prependUnusedPackChunks(backend, backend.bundle(), 1536);
      PublicationFile paddedPack = onlyFile(padded, ".pack");
      PublicationFile index = onlyFile(padded, ".index");
      assertThat(paddedPack.getSizeBytes()).isGreaterThan(1536L * SkyCacheProtocol.CHUNK_BYTES);
      backend.advertise(padded);
      assertThat(reader.prepare()).isEqualTo(1);
      assertThat(backend.requestedDigests).containsNoneIn(paddedPack.getChunksList());

      backend.requestedDigests.clear();
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
      } finally {
        session.finish(false);
      }
      var expectedReads = new ArrayList<>(index.getChunksList());
      expectedReads.add(originalPack.getChunks(0));
      assertThat(backend.requestedDigests).containsExactlyElementsIn(expectedReads);
      assertThat(backend.downloadedBytes.get()).isLessThan(4L * SkyCacheProtocol.CHUNK_BYTES);
      assertNoPublications(destination);
    }
  }

  @Test
  public void materializationRejectsUnionOverLogicalBudgetBeforeCreatingFiles() throws Exception {
    Path destination = temporary.newFolder("destination").toPath();
    try (var backend = new MemoryRemote();
        var reader = remote(destination, backend)) {
      var bundles = new ArrayList<PublicationBundle>();
      var roots = new HashSet<String>();
      for (int i = 0; i < 3; i++) {
        Path source = temporary.newFolder("source-" + i).toPath();
        LargePublication fixture = publishLarge(source);
        try (var writer = remote(source, backend)) {
          writer.publish(fixture.manifest());
        }
        var padded = prependUnusedPackChunks(backend, backend.bundle(), 1536);
        bundles.add(padded);
        roots.add(padded.getRootId());
      }
      backend.advertiseCandidates(bundles.toArray(PublicationBundle[]::new));
      assertThat(reader.prepare()).isEqualTo(3);
      backend.requestedDigests.clear();
      List<Path> before;
      try (var paths = Files.walk(destination)) {
        before = paths.sorted().toList();
      }
      var failure = assertThrows(IOException.class, () -> reader.materialize(roots));
      assertThat(failure)
          .hasMessageThat()
          .contains("materialization exceeds its logical byte budget");
      assertThat(backend.requestedDigests).isEmpty();
      try (var paths = Files.walk(destination)) {
        assertThat(paths.sorted().toList()).containsExactlyElementsIn(before).inOrder();
      }
      assertNoPublications(destination);
    }
  }

  @Test
  public void failedMaterializationAttemptsConsumeLogicalBudget() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    LargePublication fixture = publishLarge(source);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      var padded = prependUnusedPackChunks(backend, backend.bundle(), 1536);
      Digest missing = onlyFile(padded, ".pack").getChunks(0);
      backend.advertise(padded);
      assertThat(reader.prepare()).isEqualTo(1);
      backend.blobs.remove(missing);
      var roots = java.util.Set.of(padded.getRootId());
      for (int i = 0; i < 2; i++) {
        var failure = assertThrows(IOException.class, () -> reader.materialize(roots));
        assertThat(failure).hasMessageThat().contains("NOT_FOUND");
      }
      backend.requestedDigests.clear();
      List<Path> before;
      try (var paths = Files.walk(destination)) {
        before = paths.sorted().toList();
      }
      var failure = assertThrows(IOException.class, () -> reader.materialize(roots));
      assertThat(failure)
          .hasMessageThat()
          .contains("materialization exceeds its logical byte budget");
      assertThat(backend.requestedDigests).isEmpty();
      try (var paths = Files.walk(destination)) {
        assertThat(paths.sorted().toList()).containsExactlyElementsIn(before).inOrder();
      }
      assertNoPublications(destination);
    }
  }

  @Test
  public void missingUnusedPackChunkDoesNotBlockDemandRead() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    LargePublication fixture = publishLarge(source);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      PublicationFile pack = onlyFile(backend.bundle(), ".pack");
      Digest missing = pack.getChunks(pack.getChunksCount() - 1);
      backend.blobs.remove(missing);
      assertThat(reader.prepare()).isEqualTo(1);
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
      } finally {
        session.finish(false);
      }
      assertThat(backend.requestedDigests).doesNotContain(missing);
      assertNoPublications(destination);
    }
  }

  @Test
  public void missingOrCorruptRequiredPackChunkIsACacheMiss() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    LargePublication fixture = publishLarge(source);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend)) {
      writer.publish(fixture.manifest());
      Digest required = onlyFile(backend.bundle(), ".pack").getChunks(0);
      byte[] original = backend.blobs.get(required).clone();
      for (boolean corrupt : List.of(false, true)) {
        Path destination = temporary.newFolder().toPath();
        if (corrupt) {
          byte[] altered = original.clone();
          altered[altered.length - 1] ^= 1;
          backend.blobs.put(required, altered);
        } else {
          backend.blobs.remove(required);
        }
        try (var reader = remote(destination, backend)) {
          assertThat(reader.prepare()).isEqualTo(1);
          var session = new LocalCacheSession(destination, snapshot());
          session.setRemote(reader);
          try {
            assertThrows(ExecutionException.class, () -> session.get(fixture.key()).get());
          } finally {
            session.finish(false);
          }
          assertNoPublications(destination);
        }
      }
      assertThat(backend.requestedDigests).contains(required);
    }
  }

  @Test
  public void demandReadCrossesUnequalChunkBoundaries() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    LargePublication fixture = publishLarge(source);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      var bundle = backend.bundle().toBuilder();
      var publication = bundle.getPublications(0).toBuilder();
      PublicationFile pack = onlyFile(bundle.build(), ".pack");
      var bytes = new ByteArrayOutputStream();
      for (Digest chunk : pack.getChunksList()) {
        bytes.write(backend.blobs.get(chunk));
      }
      byte[] contents = bytes.toByteArray();
      var changed = pack.toBuilder().clearChunks();
      int offset = 0;
      for (int size : new int[] {17, 39, 97}) {
        changed.addChunks(backend.putBlob(Arrays.copyOfRange(contents, offset, offset + size)));
        offset += size;
      }
      while (offset < contents.length) {
        int end = Math.min(offset + SkyCacheProtocol.CHUNK_BYTES, contents.length);
        changed.addChunks(backend.putBlob(Arrays.copyOfRange(contents, offset, end)));
        offset = end;
      }
      for (int i = 0; i < publication.getFilesCount(); i++) {
        if (publication.getFiles(i).getName().equals(pack.getName())) {
          publication.setFiles(i, changed);
        }
      }
      backend.advertise(bundle.setPublications(0, publication).build());
      assertThat(reader.prepare()).isEqualTo(1);
      backend.requestedDigests.clear();
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
      } finally {
        session.finish(false);
      }
      assertThat(backend.requestedDigests)
          .containsAtLeastElementsIn(changed.getChunksList().subList(0, 3));
      assertThat(backend.requestedDigests)
          .containsNoneIn(changed.getChunksList().subList(3, changed.getChunksCount()));
      assertNoPublications(destination);
    }
  }

  @Test
  public void childReusesOnlyUsedRemoteDescriptorWithoutMaterializingParent() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path unrelatedSource = temporary.newFolder("unrelated-source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path reopened = temporary.newFolder("reopened").toPath();
    LargePublication fixture = publishLarge(source);
    Path unrelatedManifest = publishLocal(unrelatedSource, new Key("other"), new byte[] {99});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var unrelatedWriter = remote(unrelatedSource, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      PublicationBundle used = backend.bundle();
      unrelatedWriter.publish(unrelatedManifest);
      PublicationBundle unused = backend.bundle();
      PublicationFile usedPack = onlyFile(used, ".pack");
      // The requested entry belongs to the older candidate in this first discovery page.
      backend.advertiseCandidates(unused, used);
      assertThat(reader.prepare()).isEqualTo(2);
      backend.uploadedDigests.clear();
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
        assertThat(session.importedRemoteAnalysis()).isTrue();
        assertThat(session.put(new Key("child"), new byte[] {8}).get()).isTrue();
        assertThat(session.finish(true)).isFalse();
        assertThat(session.remotePublished()).isTrue();
        assertThat(Files.exists(session.publicationManifest())).isFalse();
        assertThat(session.importedPublicationManifest()).isNull();
      } finally {
        session.finish(false);
      }
      PublicationBundle child = backend.bundle();
      assertThat(child.getPublicationsCount()).isEqualTo(2);
      assertThat(child.getPublications(0)).isEqualTo(used.getPublications(0));
      assertThat(child.getPublications(1).getId()).isEqualTo(child.getRootId());
      assertThat(child.getPublications(1).getParentIdsList()).containsExactly(used.getRootId());
      assertThat(child.getPublicationsList().stream().map(p -> p.getId()).toList())
          .doesNotContain(unused.getRootId());
      assertThat(backend.requestedDigests).contains(usedPack.getChunks(0));
      assertThat(backend.requestedDigests)
          .containsNoneIn(usedPack.getChunksList().subList(1, usedPack.getChunksCount()));
      assertThat(backend.requestedDigests)
          .containsNoneIn(onlyFile(unused, ".pack").getChunksList());
      assertThat(backend.uploadedDigests).containsNoneIn(usedPack.getChunksList());
      assertThat(backend.uploadedDigests).containsNoneIn(onlyFile(unused, ".pack").getChunksList());
      assertNoPublications(destination);

      try (var freshReader = remote(reopened, backend)) {
        assertThat(freshReader.prepare()).isEqualTo(2);
        var fresh = new LocalCacheSession(reopened, snapshot());
        fresh.setRemote(freshReader);
        try {
          assertThat(fresh.get(fixture.key()).get()).isEqualTo(fixture.value());
          assertThat(fresh.get(new Key("child")).get()).isEqualTo(new byte[] {8});
          assertThat(fresh.finish(true)).isFalse();
          assertThat(fresh.remotePublished()).isFalse();
        } finally {
          fresh.finish(false);
        }
        assertNoPublications(reopened);
      }
    }
  }

  @Test
  public void localParentAlsoAdvertisedRemotelyDoesNotRequireRemoteMaterialization()
      throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    Key parent = new Key("parent");
    Path manifest = publishLocal(cache, parent, new byte[] {7});
    try (var backend = new MemoryRemote();
        var writer = remote(cache, backend);
        var reader = remote(cache, backend)) {
      writer.publish(manifest);
      Digest missing = onlyFile(backend.bundle(), ".pack").getChunks(0);
      assertThat(reader.prepare()).isEqualTo(1);
      backend.blobs.remove(missing);
      var session = new LocalCacheSession(cache, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(parent).get()).isEqualTo(new byte[] {7});
        session.put(new Key("child"), new byte[] {8}).get();
        assertThat(session.finish(true)).isTrue();
        assertThat(Files.exists(session.publicationManifest())).isTrue();
      } finally {
        session.finish(false);
      }
      assertThat(backend.requestedDigests).doesNotContain(missing);
    }
    assertValue(cache, parent, new byte[] {7});
    assertValue(cache, new Key("child"), new byte[] {8});
  }

  @Test
  public void mixedClosureReuploadsEvictedLocalAncestorAndKeepsWriterPinnedThroughCommit()
      throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path reopened = temporary.newFolder("reopened").toPath();
    Key ancestor = new Key("ancestor");
    Key remoteParent = new Key("remote-parent");
    Key child = new Key("child");
    Path ancestorManifest = publishLocal(source, ancestor, new byte[] {1});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend)) {
      writer.publish(ancestorManifest);
      PublicationBundle ancestorBundle = backend.bundle();
      Digest ancestorPack = onlyFile(ancestorBundle, ".pack").getChunks(0);
      try (var importer = remote(destination, backend)) {
        assertThat(importer.hydrate()).isEqualTo(1);
      }
      var parentWriter = new LocalCacheSession(source, snapshot());
      try {
        assertThat(parentWriter.get(ancestor).get()).isEqualTo(new byte[] {1});
        assertThat(parentWriter.put(remoteParent, new byte[] {2}).get()).isTrue();
        assertThat(parentWriter.finish(true)).isTrue();
      } finally {
        parentWriter.finish(false);
      }
      writer.publish(parentWriter.publicationManifest());
      PublicationBundle parentBundle = backend.bundle();
      var remoteDescriptor = parentBundle.getPublications(1);
      List<Digest> remotePackChunks =
          remoteDescriptor.getFilesList().stream()
              .filter(file -> file.getName().endsWith(".pack"))
              .flatMap(file -> file.getChunksList().stream())
              .toList();

      try (var reader = remote(destination, backend)) {
        assertThat(reader.prepare()).isEqualTo(2);
        backend.blobs.remove(ancestorPack);
        backend.requestedDigests.clear();
        backend.uploadedDigests.clear();
        var collectionDuringCommit = new ConcurrentLinkedQueue<LocalCacheGarbageCollector.Stats>();
        backend.beforeCommit =
            () -> {
              try {
                collectionDuringCommit.add(
                    new LocalCacheGarbageCollector(
                            destination, new LocalCacheGarbageCollector.Policy(1, 0))
                        .run());
              } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IOException(e);
              }
            };
        var session = new LocalCacheSession(destination, snapshot());
        session.setRemote(reader);
        try {
          assertThat(session.get(ancestor).get()).isEqualTo(new byte[] {1});
          assertThat(session.get(remoteParent).get()).isEqualTo(new byte[] {2});
          assertThat(session.put(child, new byte[] {3}).get()).isTrue();
          assertThat(session.finish(true)).isFalse();
          assertThat(session.remotePublished()).isTrue();
          assertThat(Files.exists(session.publicationManifest())).isFalse();
        } finally {
          session.finish(false);
          backend.beforeCommit = null;
        }
        assertThat(collectionDuringCommit).hasSize(1);
        var during = collectionDuringCommit.element();
        assertThat(during.status()).isEqualTo("complete");
        assertThat(during.activeSlots()).isGreaterThan(0);
        assertThat(during.deletedPublications()).isEqualTo(0);
        assertThat(during.deletedPacks()).isEqualTo(0);
        assertThat(during.deletedPendingFiles()).isEqualTo(0);
        assertThat(backend.requestedDigests).doesNotContain(ancestorPack);
        assertThat(backend.uploadedDigests).contains(ancestorPack);
        assertThat(backend.uploadedDigests).containsNoneIn(remotePackChunks);
        PublicationBundle published = backend.bundle();
        assertThat(published.getPublicationsCount()).isEqualTo(3);
        assertThat(published.getPublications(0)).isEqualTo(ancestorBundle.getPublications(0));
        assertThat(published.getPublications(1)).isEqualTo(remoteDescriptor);
        assertThat(published.getPublications(2).getParentIdsList())
            .containsExactly(ancestorBundle.getRootId(), parentBundle.getRootId());
        assertThat(
                Files.exists(
                    manifestDirectory(destination).resolve(ancestorManifest.getFileName())))
            .isTrue();
        try (var manifests = Files.list(manifestDirectory(destination))) {
          assertThat(manifests.filter(path -> path.toString().endsWith(".manifest")).count())
              .isEqualTo(1);
        }

        var after =
            new LocalCacheGarbageCollector(destination, new LocalCacheGarbageCollector.Policy(1, 0))
                .run();
        assertThat(after.status()).isEqualTo("complete");
        assertThat(after.deletedPublications()).isEqualTo(1);
        assertThat(after.deletedPacks()).isEqualTo(2);
        assertNoPublications(destination);
      }

      try (var freshReader = remote(reopened, backend)) {
        assertThat(freshReader.prepare()).isEqualTo(3);
        var fresh = new LocalCacheSession(reopened, snapshot());
        fresh.setRemote(freshReader);
        try {
          assertThat(fresh.get(ancestor).get()).isEqualTo(new byte[] {1});
          assertThat(fresh.get(remoteParent).get()).isEqualTo(new byte[] {2});
          assertThat(fresh.get(child).get()).isEqualTo(new byte[] {3});
        } finally {
          fresh.finish(false);
        }
        assertNoPublications(reopened);
      }
    }
  }

  @Test
  public void missingUnusedBytesOfUsedParentSuppressesChildPublicationWithoutFetchingThem()
      throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    LargePublication fixture = publishLarge(source);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      Digest previousPublication = backend.advertised;
      PublicationFile pack = onlyFile(backend.bundle(), ".pack");
      Digest missing = pack.getChunks(pack.getChunksCount() - 1);
      backend.blobs.remove(missing);
      assertThat(reader.prepare()).isEqualTo(1);
      backend.uploadedDigests.clear();
      long commitsBefore = backend.commitRequests.get();
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
        assertThat(session.put(new Key("child"), new byte[] {8}).get()).isTrue();
        assertThrows(IOException.class, () -> session.finish(true));
        assertThat(session.remotePublished()).isFalse();
        assertThat(session.importedRemoteAnalysis()).isTrue();
      } finally {
        session.finish(false);
      }
      assertThat(backend.commitRequests.get()).isEqualTo(commitsBefore + 1);
      assertThat(backend.advertised).isEqualTo(previousPublication);
      assertThat(backend.requestedDigests).doesNotContain(missing);
      assertThat(backend.uploadedDigests).containsNoneIn(pack.getChunksList());
      assertNoPublications(destination);
    }
  }

  @Test
  public void putBeforeReadDoesNotDownloadRemoteIndexOrPack() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Key key = new Key("entry");
    byte[] value = {1, 2, 3};
    Path manifest = publishLocal(source, key, value);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      assertThat(reader.prepare()).isEqualTo(1);
      backend.requestedDigests.clear();
      backend.uploadedDigests.clear();
      long commitsBefore = backend.commitRequests.get();
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.put(key, value).get()).isTrue();
        assertThat(session.deduplicatedWrites()).isEqualTo(0);
        assertThat(session.importedRemoteAnalysis()).isFalse();
        assertThat(session.get(key).get()).isEqualTo(value);
      } finally {
        session.finish(false);
      }
      assertThat(backend.requestedDigests).isEmpty();
      assertThat(backend.uploadedDigests).isEmpty();
      assertThat(backend.commitRequests.get()).isEqualTo(commitsBefore);
      assertNoPublications(destination);
    }
  }

  @Test
  public void identicalPutAfterEarlierReadDeduplicatesWithoutPublishing() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Key key = new Key("entry");
    byte[] value = {1, 2, 3};
    Path manifest = publishLocal(source, key, value);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      assertThat(reader.prepare()).isEqualTo(1);
      var warmup = new LocalCacheSession(destination, snapshot());
      warmup.setRemote(reader);
      try {
        assertThat(warmup.get(key).get()).isEqualTo(value);
      } finally {
        warmup.finish(false);
      }
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.importedRemoteAnalysis()).isFalse();
        backend.requestedDigests.clear();
        backend.uploadedDigests.clear();
        long commitsBefore = backend.commitRequests.get();
        assertThat(session.put(key, value).get()).isFalse();
        assertThat(session.deduplicatedWrites()).isEqualTo(1);
        assertThat(session.importedRemoteAnalysis()).isTrue();
        assertThat(session.finish(true)).isFalse();
        assertThat(session.remotePublished()).isFalse();
        assertThat(backend.requestedDigests).isEmpty();
        assertThat(backend.uploadedDigests).isEmpty();
        assertThat(backend.commitRequests.get()).isEqualTo(commitsBefore);
      } finally {
        session.finish(false);
      }
      assertNoPublications(destination);
    }
  }

  @Test
  public void missingOrCorruptCachedChunkRepairsSharedPutPrivatelyWithoutNetwork()
      throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Key key = new Key("entry");
    byte[] value = {1, 2, 3};
    Path manifest = publishLocal(source, key, value);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend)) {
      writer.publish(manifest);
      Digest chunk = onlyFile(backend.bundle(), ".pack").getChunks(0);
      for (boolean corrupt : new boolean[] {false, true}) {
        Path destination = temporary.newFolder("destination-" + corrupt).toPath();
        try (var reader = remote(destination, backend)) {
          assertThat(reader.prepare()).isEqualTo(1);
          var session = new LocalCacheSession(destination, snapshot());
          session.setRemote(reader);
          try {
            assertThat(session.get(key).get()).isEqualTo(value);
            assertThat(session.put(key, value).get()).isFalse();
            Path cached = cachedChunk(destination, chunk);
            if (corrupt) {
              byte[] bytes = Files.readAllBytes(cached);
              bytes[bytes.length - 1] ^= 1;
              Files.write(cached, bytes);
            } else {
              Files.delete(cached);
            }
            backend.requestedDigests.clear();
            backend.uploadedDigests.clear();
            assertThat(session.put(key, value).get()).isTrue();
            assertThat(session.get(key).get()).isEqualTo(value);
            assertThat(session.deduplicatedWrites()).isEqualTo(1);
            assertThat(session.failureReason()).isNull();
            assertThat(backend.requestedDigests).isEmpty();
            assertThat(backend.uploadedDigests).isEmpty();
          } finally {
            session.finish(false);
          }
          assertNoPublications(destination);
        }
      }
    }
  }

  @Test
  public void conflictingRepeatedRemoteDeduplicatedPutSuppressesPublication() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Key key = new Key("entry");
    byte[] value = {1, 2, 3};
    Path manifest = publishLocal(source, key, value);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      assertThat(reader.prepare()).isEqualTo(1);
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(key).get()).isEqualTo(value);
        assertThat(session.put(key, value).get()).isFalse();
        assertThat(session.put(new Key("child"), new byte[] {8}).get()).isTrue();
        backend.requestedDigests.clear();
        backend.uploadedDigests.clear();
        long commitsBefore = backend.commitRequests.get();
        assertThat(session.put(key, new byte[] {4, 5, 6}).get()).isFalse();
        assertThat(session.failureReason()).contains("conflicting immutable shared cache value");
        assertThat(session.get(key).get()).isEqualTo(value);
        assertThat(session.finish(true)).isFalse();
        assertThat(session.remotePublished()).isFalse();
        assertThat(backend.requestedDigests).isEmpty();
        assertThat(backend.uploadedDigests).isEmpty();
        assertThat(backend.commitRequests.get()).isEqualTo(commitsBefore);
      } finally {
        session.finish(false);
      }
      assertNoPublications(destination);
    }
  }

  @Test
  public void changedInputSuppressesRemoteChildBeforeUpload() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Key key = new Key("entry");
    byte[] value = {1, 2, 3};
    Path manifest = publishLocal(source, key, value);
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      assertThat(reader.prepare()).isEqualTo(1);
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(key).get()).isEqualTo(value);
        assertThat(session.put(new Key("child"), new byte[] {8}).get()).isTrue();
        backend.requestedDigests.clear();
        backend.uploadedDigests.clear();
        long commitsBefore = backend.commitRequests.get();
        Files.writeString(evidence, "changed");
        assertThat(session.finish(true)).isFalse();
        assertThat(session.failureReason()).contains("inputs changed");
        assertThat(session.remotePublished()).isFalse();
        assertThat(backend.requestedDigests).isEmpty();
        assertThat(backend.uploadedDigests).isEmpty();
        assertThat(backend.commitRequests.get()).isEqualTo(commitsBefore);
      } finally {
        session.finish(false);
      }
      assertNoPublications(destination);
    }
  }

  private static final class ReadGate {
    final Digest digest;
    final CountDownLatch entered = new CountDownLatch(1);
    final CountDownLatch release = new CountDownLatch(1);

    ReadGate(Digest digest) {
      this.digest = digest;
    }

    void beforeRead(Digest requested) throws IOException {
      if (!digest.equals(requested)) {
        return;
      }
      entered.countDown();
      try {
        if (!release.await(30, TimeUnit.SECONDS)) {
          throw new IOException("test read gate was not released");
        }
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
        throw new IOException("test read gate interrupted", e);
      }
    }
  }

  private RangeFixture prepareRanges(Path source, MemoryRemote backend, RemoteSkyCache reader)
      throws Exception {
    byte[] value = new byte[2 * SkyCacheProtocol.CHUNK_BYTES + 117];
    new Random(90210).nextBytes(value);
    try (var writer = remote(source, backend)) {
      writer.publish(publishLocal(source, new Key("entry"), value));
    }
    PublicationFile pack = onlyFile(backend.bundle(), ".pack");
    var bytes = new ByteArrayOutputStream();
    for (Digest chunk : pack.getChunksList()) {
      bytes.write(backend.blobs.get(chunk));
    }
    assertThat(reader.prepare()).isEqualTo(1);
    return new RangeFixture(pack, bytes.toByteArray(), reader.candidates().get(0).source());
  }

  private record RangeFixture(
      PublicationFile pack, byte[] bytes, SegmentedFingerprintValueStore.FileSource source) {
    void assertRead(int offset, int length) throws IOException {
      var target = ByteBuffer.allocate(length);
      source.read(pack.getName(), target, offset);
      assertThat(target.hasRemaining()).isFalse();
      assertThat(target.array()).isEqualTo(Arrays.copyOfRange(bytes, offset, offset + length));
    }
  }

  private static long statistic(RemoteSkyCache reader, String name) {
    var match = Pattern.compile("\"" + name + "\":(\\d+)").matcher(reader.statisticsJson());
    assertThat(match.find()).isTrue();
    return Long.parseLong(match.group(1));
  }

  private static Path cachedChunk(Path cache, Digest digest) throws IOException {
    try (var paths = Files.walk(cache.resolve("leased-v2"))) {
      List<Path> matches =
          paths.filter(path -> path.getFileName().toString().equals(digest.getHash())).toList();
      assertThat(matches).hasSize(1);
      return matches.get(0);
    }
  }

  private record LargePublication(Path manifest, Key key, byte[] value) {}

  /** Inserts unindexed padding, relocating every indexed record and authenticating both indexes. */
  private static PublicationBundle prependUnusedPackChunks(
      MemoryRemote backend, PublicationBundle original, int paddingChunks) throws Exception {
    assertThat(original.getPublicationsCount()).isEqualTo(1);
    PublicationFile pack = onlyFile(original, ".pack");
    PublicationFile index = onlyFile(original, ".index");
    PublicationFile rootIndex =
        original.getPublications(0).getFilesList().stream()
            .filter(file -> file.getName().equals("index"))
            .findFirst()
            .orElseThrow();
    assertThat(index.getChunksCount()).isEqualTo(1);
    assertThat(rootIndex.getChunksCount()).isEqualTo(1);
    long paddingBytes = (long) paddingChunks * SkyCacheProtocol.CHUNK_BYTES;
    long dataBytes = pack.getSizeBytes() + paddingBytes;
    Digest zeroChunk = backend.putBlob(new byte[SkyCacheProtocol.CHUNK_BYTES]);
    var paddedPack = pack.toBuilder().setSizeBytes(dataBytes).clearChunks();
    for (int i = 0; i < paddingChunks; i++) {
      paddedPack.addChunks(zeroChunk);
    }
    paddedPack.addAllChunks(pack.getChunksList());

    byte[] indexBytes = backend.blobs.get(index.getChunks(0)).clone();
    var packedIndex = ByteBuffer.wrap(indexBytes);
    packedIndex.putLong(8, dataBytes);
    for (int i = 0; i < packedIndex.getInt(4); i++) {
      int offsetField = 16 + i * 48 + 32;
      packedIndex.putLong(offsetField, packedIndex.getLong(offsetField) + paddingBytes);
    }
    byte[] indexChecksum = refreshChecksum(indexBytes);
    byte[] rootBytes = backend.blobs.get(rootIndex.getChunks(0)).clone();
    var root = ByteBuffer.wrap(rootBytes);
    assertThat(root.getInt(4)).isEqualTo(1);
    assertThat(root.getLong(16)).isEqualTo(pack.getSizeBytes());
    root.putLong(16, dataBytes);
    root.position(28);
    root.put(indexChecksum);
    refreshChecksum(rootBytes);

    var publication = original.getPublications(0).toBuilder();
    for (int i = 0; i < publication.getFilesCount(); i++) {
      String name = publication.getFiles(i).getName();
      if (name.equals(pack.getName())) {
        publication.setFiles(i, paddedPack);
      } else if (name.equals(index.getName()) || name.equals("index")) {
        byte[] bytes = name.equals("index") ? rootBytes : indexBytes;
        publication.setFiles(
            i,
            publication.getFiles(i).toBuilder()
                .setSizeBytes(bytes.length)
                .clearChunks()
                .addChunks(backend.putBlob(bytes)));
      }
    }
    var result = original.toBuilder().setPublications(0, publication).build();
    SkyCacheProtocol.validateBundle(result, original.getNamespaceId());
    return result;
  }

  private static byte[] refreshChecksum(byte[] encoded) throws Exception {
    byte[] checksum =
        MessageDigest.getInstance("SHA-256").digest(Arrays.copyOf(encoded, encoded.length - 32));
    System.arraycopy(checksum, 0, encoded, encoded.length - 32, checksum.length);
    return checksum;
  }

  private LargePublication publishLarge(Path cache) throws Exception {
    Key requested = new Key("requested");
    Key unrelated = null;
    int bucket =
        Integer.parseInt(SkyCacheProtocol.digest(requested.toBytes()).getHash().substring(0, 2), 16)
            & 15;
    for (int i = 0; unrelated == null; i++) {
      Key candidate = new Key("unrelated-" + i);
      int otherBucket =
          Integer.parseInt(
                  SkyCacheProtocol.digest(candidate.toBytes()).getHash().substring(0, 2), 16)
              & 15;
      if (otherBucket == bucket) {
        unrelated = candidate;
      }
    }
    byte[] value = {1, 2, 3, 4, 5};
    byte[] unused = new byte[2 * SkyCacheProtocol.CHUNK_BYTES + 4096];
    new Random(12345).nextBytes(unused);
    var writer = new LocalCacheSession(cache, snapshot());
    try {
      writer.put(requested, value).get();
      writer.put(unrelated, unused).get();
      assertThat(writer.finish(true)).isTrue();
      return new LargePublication(writer.publicationManifest(), requested, value);
    } finally {
      writer.finish(false);
    }
  }

  private static PublicationFile onlyFile(PublicationBundle bundle, String suffix) {
    List<PublicationFile> files =
        bundle.getPublicationsList().stream()
            .flatMap(publication -> publication.getFilesList().stream())
            .filter(
                file -> file.getName().startsWith("segment-") && file.getName().endsWith(suffix))
            .toList();
    assertThat(files).hasSize(1);
    return files.get(0);
  }

  private LocalInputSnapshot snapshot() throws IOException {
    var inputs = new LocalInputSnapshot(workspace, output, Map.of("PATH", "/usr/bin"));
    inputs.observe(evidence, false);
    return inputs;
  }

  private Path publishLocal(Path cache, Key key, byte[] value) throws Exception {
    var writer = new LocalCacheSession(cache, snapshot());
    writer.put(key, value).get();
    assertThat(writer.finish(true)).isTrue();
    return writer.publicationManifest();
  }

  private RemoteSkyCache remote(Path cache, MemoryRemote backend) throws IOException {
    return new RemoteSkyCache(
        cache,
        namespace,
        32L * 1024 * 1024,
        "instance",
        new SkyCacheTransport(backend.endpoint(), "instance", "", Duration.ofSeconds(15)));
  }

  private void assertValue(Path cache, Key key, byte[] expected) throws Exception {
    var session = new LocalCacheSession(cache, snapshot());
    try {
      assertThat(session.get(key).get()).isEqualTo(expected);
    } finally {
      session.finish(false);
    }
  }

  private Path manifestDirectory(Path cache) {
    return cache.resolve("leased-v2/publications-v4").resolve(namespace);
  }

  private void assertNoPublications(Path cache) throws IOException {
    try (var paths = Files.list(manifestDirectory(cache))) {
      assertThat(paths.filter(p -> p.toString().endsWith(".manifest")).count()).isEqualTo(0);
    }
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

  /** Real loopback RPC transport; this fixture deliberately permits corrupt catalog candidates. */
  private static final class MemoryRemote implements AutoCloseable {
    final Map<Digest, byte[]> blobs = new ConcurrentHashMap<>();
    final ConcurrentLinkedQueue<Digest> requestedDigests = new ConcurrentLinkedQueue<>();
    final ConcurrentLinkedQueue<Digest> uploadedDigests = new ConcurrentLinkedQueue<>();
    final AtomicLong commitRequests = new AtomicLong();
    final AtomicLong downloadedBytes = new AtomicLong();
    volatile List<PublicationRef> discoveryCandidates;
    volatile Digest advertised;
    volatile CommitObserver beforeCommit;
    volatile ReadObserver beforeRead;
    private final Server server;

    @FunctionalInterface
    private interface CommitObserver {
      void run() throws IOException;
    }

    @FunctionalInterface
    private interface ReadObserver {
      void run(Digest digest) throws IOException;
    }

    MemoryRemote() throws IOException {
      server =
          NettyServerBuilder.forAddress(new InetSocketAddress("127.0.0.1", 0))
              .addService(
                  new ContentAddressableStorageImplBase() {
                    @Override
                    public void batchUpdateBlobs(
                        BatchUpdateBlobsRequest request,
                        StreamObserver<BatchUpdateBlobsResponse> observer) {
                      var response = BatchUpdateBlobsResponse.newBuilder();
                      for (var entry : request.getRequestsList()) {
                        byte[] bytes = entry.getData().toByteArray();
                        if (!SkyCacheProtocol.digest(bytes).equals(entry.getDigest())) {
                          observer.onError(Status.INVALID_ARGUMENT.asRuntimeException());
                          return;
                        }
                        blobs.put(entry.getDigest(), bytes);
                        uploadedDigests.add(entry.getDigest());
                        response.addResponses(
                            BatchUpdateBlobsResponse.Response.newBuilder()
                                .setDigest(entry.getDigest()));
                      }
                      observer.onNext(response.build());
                      observer.onCompleted();
                    }
                  })
              .addService(
                  new ByteStreamImplBase() {
                    @Override
                    public void read(ReadRequest request, StreamObserver<ReadResponse> observer) {
                      String[] path = request.getResourceName().split("/");
                      Digest digest =
                          Digest.newBuilder()
                              .setHash(path[path.length - 2])
                              .setSizeBytes(Long.parseLong(path[path.length - 1]))
                              .build();
                      requestedDigests.add(digest);
                      try {
                        if (beforeRead != null) {
                          beforeRead.run(digest);
                        }
                      } catch (IOException e) {
                        observer.onError(
                            Status.CANCELLED.withDescription(e.getMessage()).asRuntimeException());
                        return;
                      }
                      byte[] bytes = blobs.get(digest);
                      if (bytes == null) {
                        observer.onError(Status.NOT_FOUND.asRuntimeException());
                        return;
                      }
                      downloadedBytes.addAndGet(bytes.length);
                      for (int offset = 0; offset < bytes.length; offset += 16384) {
                        observer.onNext(
                            ReadResponse.newBuilder()
                                .setData(
                                    ByteString.copyFrom(
                                        bytes, offset, Math.min(16384, bytes.length - offset)))
                                .build());
                      }
                      observer.onCompleted();
                    }
                  })
              .addService(
                  new SkyCacheCatalogImplBase() {
                    @Override
                    public void commitPublication(
                        CommitRequest request, StreamObserver<PublicationRef> observer) {
                      commitRequests.incrementAndGet();
                      try {
                        if (beforeCommit != null) {
                          beforeCommit.run();
                        }
                        byte[] encoded = blobs.get(request.getManifest());
                        if (encoded == null
                            || !SkyCacheProtocol.digest(encoded).equals(request.getManifest())) {
                          throw new IOException("missing or corrupt publication bundle");
                        }
                        PublicationBundle bundle =
                            PublicationBundle.parseFrom(
                                encoded, ExtensionRegistry.getEmptyRegistry());
                        SkyCacheProtocol.validateBundle(
                            bundle, request.getScope().getNamespaceId());
                        for (var publication : bundle.getPublicationsList()) {
                          for (var file : publication.getFilesList()) {
                            for (Digest chunk : file.getChunksList()) {
                              if (!blobs.containsKey(chunk)) {
                                throw new IOException("missing referenced publication chunk");
                              }
                            }
                          }
                        }
                        advertised = request.getManifest();
                        discoveryCandidates = null;
                        observer.onNext(reference());
                        observer.onCompleted();
                      } catch (IOException e) {
                        observer.onError(
                            Status.FAILED_PRECONDITION
                                .withDescription(e.getMessage())
                                .asRuntimeException());
                      }
                    }

                    @Override
                    public void discoverPublications(
                        DiscoverRequest request, StreamObserver<DiscoverResponse> observer) {
                      var response = DiscoverResponse.newBuilder();
                      if (discoveryCandidates != null) {
                        response.addAllCandidates(discoveryCandidates);
                      } else if (advertised != null) {
                        response.addCandidates(reference());
                      }
                      observer.onNext(response.build());
                      observer.onCompleted();
                    }
                  })
              .build()
              .start();
    }

    String endpoint() {
      return "grpc://127.0.0.1:" + server.getPort();
    }

    PublicationBundle bundle() throws IOException {
      return PublicationBundle.parseFrom(
          blobs.get(advertised), ExtensionRegistry.getEmptyRegistry());
    }

    void advertise(PublicationBundle bundle) {
      byte[] bytes = bundle.toByteArray();
      Digest digest = SkyCacheProtocol.digest(bytes);
      blobs.put(digest, bytes);
      advertised = digest;
    }

    Digest putBlob(byte[] bytes) {
      Digest digest = SkyCacheProtocol.digest(bytes);
      blobs.put(digest, bytes);
      return digest;
    }

    void advertiseCandidates(PublicationBundle... bundles) {
      var references = new ArrayList<PublicationRef>();
      for (int i = 0; i < bundles.length; i++) {
        references.add(
            PublicationRef.newBuilder()
                .setManifest(putBlob(bundles[i].toByteArray()))
                .setCatalogSequence(bundles.length - i)
                .build());
      }
      discoveryCandidates = List.copyOf(references);
    }

    PublicationRef reference() {
      return PublicationRef.newBuilder().setManifest(advertised).setCatalogSequence(1).build();
    }

    @Override
    public void close() throws InterruptedException {
      server.shutdownNow();
      assertThat(server.awaitTermination(15, TimeUnit.SECONDS)).isTrue();
    }
  }
}
