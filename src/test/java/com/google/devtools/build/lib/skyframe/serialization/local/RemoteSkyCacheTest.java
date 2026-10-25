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
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProtocol;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheTransport;
import com.google.protobuf.ByteString;
import com.google.protobuf.ExtensionRegistry;
import io.grpc.Server;
import io.grpc.Status;
import io.grpc.netty.NettyServerBuilder;
import io.grpc.stub.StreamObserver;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
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
  public void changedWitnessStillRejectsHydratedValue() throws Exception {
    Path source = temporary.newFolder("source").toPath();
    Path destination = temporary.newFolder("destination").toPath();
    Path manifest = publishLocal(source, new Key("entry"), new byte[] {1});
    try (var backend = new MemoryRemote();
        var writer = remote(source, backend);
        var reader = remote(destination, backend)) {
      writer.publish(manifest);
      Files.writeString(evidence, "changed");
      assertThat(reader.hydrate()).isEqualTo(1);
      var session = new LocalCacheSession(destination, snapshot());
      try {
        assertThrows(ExecutionException.class, () -> session.get(new Key("entry")).get());
      } finally {
        session.finish(false);
      }
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
                      byte[] bytes = blobs.get(digest);
                      if (bytes == null) {
                        observer.onError(Status.NOT_FOUND.asRuntimeException());
                        return;
                      }
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
                      if (advertised != null) {
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
