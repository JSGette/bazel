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
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Random;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
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
  public void childMaterializesOnlyUsedRemoteCandidateAndSurvivesLocalOnlyReopen()
      throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path unrelatedSource = temporary.newFolder("unrelated-source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    LargePublication fixture = publishLarge(source);
    Path unrelatedManifest = publishLocal(unrelatedSource, new Key("other"), new byte[] {99});
    Path childManifest;
    String usedId;
    String unusedId;
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var unrelatedWriter = remote(unrelatedSource, backend);
        var reader = remote(destination, backend)) {
      writer.publish(fixture.manifest());
      PublicationBundle used = backend.bundle();
      unrelatedWriter.publish(unrelatedManifest);
      PublicationBundle unused = backend.bundle();
      usedId = used.getRootId();
      unusedId = unused.getRootId();
      // The requested entry belongs to the older candidate in this first discovery page.
      backend.advertiseCandidates(unused, used);
      assertThat(reader.prepare()).isEqualTo(2);
      var session = new LocalCacheSession(destination, snapshot());
      session.setRemote(reader);
      try {
        assertThat(session.get(fixture.key()).get()).isEqualTo(fixture.value());
        session.put(new Key("child"), new byte[] {8}).get();
        assertThat(session.finish(true)).isTrue();
        childManifest = session.publicationManifest();
      } finally {
        session.finish(false);
      }
      assertThat(Files.exists(manifestDirectory(destination).resolve(usedId + ".manifest")))
          .isTrue();
      assertThat(Files.exists(manifestDirectory(destination).resolve(unusedId + ".manifest")))
          .isFalse();
      assertThat(Files.exists(childManifest)).isTrue();
      assertThat(backend.requestedDigests)
          .containsAtLeastElementsIn(onlyFile(used, ".pack").getChunksList());
      assertThat(backend.requestedDigests)
          .containsNoneIn(onlyFile(unused, ".pack").getChunksList());
    }
    assertValue(destination, fixture.key(), fixture.value());
    assertValue(destination, new Key("child"), new byte[] {8});
    var header = LocalCacheSession.readManifestHeader(childManifest, namespace, 32L * 1024 * 1024);
    assertThat(header.parents()).containsExactly(usedId);
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
  public void missingUnusedBytesOfUsedParentSuppressesChildPublication() throws Exception {
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
        session.put(new Key("child"), new byte[] {8}).get();
        assertThat(backend.requestedDigests).doesNotContain(missing);
        assertThrows(IOException.class, () -> session.finish(true));
      } finally {
        session.finish(false);
      }
      assertThat(backend.requestedDigests).contains(missing);
      assertNoPublications(destination);
    }
  }

  private record LargePublication(Path manifest, Key key, byte[] value) {}

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
    final AtomicLong downloadedBytes = new AtomicLong();
    volatile List<PublicationRef> discoveryCandidates;
    volatile Digest advertised;
    private final Server server;

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
                      advertised = request.getManifest();
                      observer.onNext(reference());
                      observer.onCompleted();
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
