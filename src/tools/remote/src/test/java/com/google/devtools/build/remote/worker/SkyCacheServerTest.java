// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.remote.worker;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.remote.common.CacheNotFoundException;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverResponse;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.LocalPublication;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationBundle;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationFile;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProtocol;
import io.grpc.Status;
import io.grpc.stub.StreamObserver;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class SkyCacheServerTest {
  private static final String NAMESPACE = "a".repeat(64);
  private static final String PUBLICATION = "b".repeat(64);
  private static final Scope SCOPE =
      Scope.newBuilder().setInstanceName("instance").setNamespaceId(NAMESPACE).build();

  @Rule public final TemporaryFolder temporaryFolder = new TemporaryFolder();

  @Test
  public void commitDiscoversPublicationAndRetrySurvivesCompleteBlobEviction() throws Exception {
    var blobs = new MemoryBlobs();
    CommitRequest request = request(blobs, NAMESPACE);
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      var first = commit(server, request);
      first.assertSuccess();
      assertThat(blobs.downloads).isEqualTo(1);
      assertThat(blobs.refreshes).isEqualTo(2);
      blobs.contents.clear();
      var retry = commit(server, request);
      retry.assertSuccess();
      assertThat(retry.value).isEqualTo(first.value);
      assertThat(blobs.downloads).isEqualTo(1);
      assertThat(blobs.refreshes).isEqualTo(2);
      var discovered = discover(server, SCOPE);
      discovered.assertSuccess();
      assertThat(discovered.value.getCandidatesList()).containsExactly(first.value);
    }
  }

  @Test
  public void missingManifestAndChunkNeverBecomeVisible() throws Exception {
    var blobs = new MemoryBlobs();
    CommitRequest request = request(blobs, NAMESPACE);
    byte[] manifest = blobs.contents.remove(request.getManifest());
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      commit(server, request).assertFailure(Status.Code.FAILED_PRECONDITION);
      blobs.contents.clear();
      blobs.contents.put(request.getManifest(), manifest);
      commit(server, request).assertFailure(Status.Code.FAILED_PRECONDITION);
      var discovered = discover(server, SCOPE);
      discovered.assertSuccess();
      assertThat(discovered.value.getCandidatesList()).isEmpty();
      assertThat(store.lookup(request)).isNull();
    }
  }

  @Test
  public void corruptAndOversizedActualManifestAreRejected() throws Exception {
    var blobs = new MemoryBlobs();
    CommitRequest request = request(blobs, NAMESPACE);
    byte[] valid = blobs.contents.get(request.getManifest());
    byte[] corrupt = valid.clone();
    corrupt[corrupt.length - 1] ^= 1;
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      blobs.contents.put(request.getManifest(), corrupt);
      commit(server, request).assertFailure(Status.Code.DATA_LOSS);
      blobs.contents.put(request.getManifest(), new byte[valid.length + 1]);
      commit(server, request).assertFailure(Status.Code.DATA_LOSS);
      assertThat(store.lookup(request)).isNull();
      assertThat(blobs.refreshes).isEqualTo(0);
    }
  }

  @Test
  public void wrongScopeInstanceAndDeclaredSizeFailBeforeCasAccess() throws Exception {
    var blobs = new MemoryBlobs();
    CommitRequest request = request(blobs, NAMESPACE);
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      Scope wrongInstance = SCOPE.toBuilder().setInstanceName("other").build();
      Scope wrongNamespace = SCOPE.toBuilder().setNamespaceId("bad").build();
      commit(server, request.toBuilder().setScope(wrongInstance).build())
          .assertFailure(Status.Code.INVALID_ARGUMENT);
      commit(server, request.toBuilder().setScope(wrongNamespace).build())
          .assertFailure(Status.Code.INVALID_ARGUMENT);
      commit(
              server,
              request.toBuilder()
                  .setManifest(
                      request.getManifest().toBuilder()
                          .setSizeBytes(SkyCacheProtocol.MAX_BUNDLE_BYTES + 1L))
                  .build())
          .assertFailure(Status.Code.INVALID_ARGUMENT);
      discover(server, wrongInstance).assertFailure(Status.Code.INVALID_ARGUMENT);
      discover(server, wrongNamespace).assertFailure(Status.Code.INVALID_ARGUMENT);
      assertThat(blobs.downloads).isEqualTo(0);
    }
  }

  @Test
  public void bundleNamespaceMismatchIsAnInvalidArgument() throws Exception {
    var blobs = new MemoryBlobs();
    CommitRequest request = request(blobs, "c".repeat(64));
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      commit(server, request).assertFailure(Status.Code.INVALID_ARGUMENT);
      assertThat(store.lookup(request)).isNull();
      assertThat(blobs.refreshes).isEqualTo(0);
    }
  }

  @Test
  public void malformedBundleIsAnInvalidArgument() throws Exception {
    var blobs = new MemoryBlobs();
    Digest invalid = blobs.put(new byte[] {(byte) 0xff});
    CommitRequest request =
        CommitRequest.newBuilder()
            .setScope(SCOPE)
            .setManifest(invalid)
            .setIdempotencyKey("malformed")
            .build();
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      commit(server, request).assertFailure(Status.Code.INVALID_ARGUMENT);
      assertThat(store.lookup(request)).isNull();
    }
  }

  @Test
  public void idempotencyConflictPreservesStatusAndAvoidsCasAccess() throws Exception {
    var blobs = new MemoryBlobs();
    CommitRequest request = request(blobs, NAMESPACE);
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      commit(server, request).assertSuccess();
      Digest other = SkyCacheProtocol.digest(new byte[] {1, 2, 3});
      commit(server, request.toBuilder().setManifest(other).build())
          .assertFailure(Status.Code.ALREADY_EXISTS);
      assertThat(blobs.downloads).isEqualTo(1);
    }
  }

  @Test
  public void ioFailureMapsToUnavailable() throws Exception {
    var blobs = new MemoryBlobs();
    CommitRequest request = request(blobs, NAMESPACE);
    blobs.failure = new IOException("storage unavailable");
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      commit(server, request).assertFailure(Status.Code.UNAVAILABLE);
      assertThat(store.lookup(request)).isNull();
    }
  }

  @Test
  public void commitAcceptsLargeLogicalClosureThroughExactFourGiBBoundary() throws Exception {
    assertThat(SkyCacheProtocol.MAX_PUBLICATION_BYTES).isEqualTo(4L * 1024 * 1024 * 1024);
    for (long logicalBytes :
        new long[] {256L * 1024 * 1024 + 1, SkyCacheProtocol.MAX_PUBLICATION_BYTES}) {
      var blobs = new MemoryBlobs();
      PublicationBundle bundle = largeBundle(blobs, logicalBytes);
      SkyCacheProtocol.validateBundle(bundle, NAMESPACE);
      assertThat(bundle.getSerializedSize()).isLessThan(SkyCacheProtocol.MAX_BUNDLE_BYTES);
      CommitRequest request = request(blobs, bundle);
      try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
        var server = new SkyCacheServer(store, blobs, "instance");
        var committed = commit(server, request);
        committed.assertSuccess();
        assertThat(blobs.downloads).isEqualTo(1);
        // Repeated logical references require only four distinct, small CAS objects.
        assertThat(blobs.refreshes).isEqualTo(4);
        var discovered = discover(server, SCOPE);
        discovered.assertSuccess();
        assertThat(discovered.value.getCandidatesList()).containsExactly(committed.value);
      }
    }
  }

  @Test
  public void excessiveAndOverflowingLogicalClosuresFailBeforeChunkRefresh() throws Exception {
    for (boolean overflow : new boolean[] {false, true}) {
      var blobs = new MemoryBlobs();
      PublicationBundle bundle = largeBundle(blobs, SkyCacheProtocol.MAX_PUBLICATION_BYTES + 1);
      if (overflow) {
        var forged = bundle.toBuilder();
        forged.getPublicationsBuilder(0).getFilesBuilder(2).setSizeBytes(Long.MAX_VALUE);
        bundle = forged.build();
      }
      PublicationBundle invalid = bundle;
      assertThat(
              assertThrows(
                  IOException.class, () -> SkyCacheProtocol.validateBundle(invalid, NAMESPACE)))
          .hasMessageThat()
          .contains("over-budget publication file");
      CommitRequest request = request(blobs, invalid);
      try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
        var server = new SkyCacheServer(store, blobs, "instance");
        commit(server, request).assertFailure(Status.Code.INVALID_ARGUMENT);
        assertThat(blobs.downloads).isEqualTo(1);
        assertThat(blobs.refreshes).isEqualTo(0);
        assertThat(store.lookup(request)).isNull();
        var discovered = discover(server, SCOPE);
        discovered.assertSuccess();
        assertThat(discovered.value.getCandidatesList()).isEmpty();
      }
    }
  }

  @Test
  public void largeLogicalClosureStillRequiresEveryReferencedChunk() throws Exception {
    var blobs = new MemoryBlobs();
    PublicationBundle bundle = largeBundle(blobs, SkyCacheProtocol.MAX_PUBLICATION_BYTES);
    CommitRequest request = request(blobs, bundle);
    Digest missing = bundle.getPublications(0).getFiles(2).getChunks(0);
    blobs.contents.remove(missing);
    try (var store = new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      var server = new SkyCacheServer(store, blobs, "instance");
      commit(server, request).assertFailure(Status.Code.FAILED_PRECONDITION);
      assertThat(blobs.downloads).isEqualTo(1);
      assertThat(blobs.refreshes).isEqualTo(3);
      assertThat(store.lookup(request)).isNull();
      var discovered = discover(server, SCOPE);
      discovered.assertSuccess();
      assertThat(discovered.value.getCandidatesList()).isEmpty();
    }
  }

  @Test
  public void largerLogicalClosureDoesNotRelaxChunkOrBundleSizeBounds() throws Exception {
    var blobs = new MemoryBlobs();
    PublicationBundle bundle = largeBundle(blobs, 256L * 1024 * 1024 + 1);
    Digest tooLarge =
        bundle.getPublications(0).getFiles(2).getChunks(0).toBuilder()
            .setSizeBytes(SkyCacheProtocol.CHUNK_BYTES + 1L)
            .build();
    var oversizedChunk = bundle.toBuilder();
    oversizedChunk.getPublicationsBuilder(0).setFiles(2, file("segment-00000.pack", tooLarge));
    assertThat(
            assertThrows(
                IOException.class,
                () -> SkyCacheProtocol.validateBundle(oversizedChunk.build(), NAMESPACE)))
        .hasMessageThat()
        .contains("over-budget SHA-256 digest");

    Digest tiny = blobs.put(new byte[] {1});
    var manyChunks = PublicationFile.newBuilder().setName("segment-00000.pack").setSizeBytes(16384);
    for (int i = 0; i < 16384; i++) {
      manyChunks.addChunks(tiny);
    }
    var oversizedBundle = bundle.toBuilder();
    oversizedBundle.getPublicationsBuilder(0).setFiles(2, manyChunks);
    PublicationBundle invalid = oversizedBundle.build();
    assertThat(invalid.getSerializedSize()).isGreaterThan(SkyCacheProtocol.MAX_BUNDLE_BYTES);
    assertThat(
            assertThrows(
                IOException.class, () -> SkyCacheProtocol.validateBundle(invalid, NAMESPACE)))
        .hasMessageThat()
        .contains("over-budget publication bundle");
  }

  private static PublicationBundle largeBundle(MemoryBlobs blobs, long logicalBytes) {
    Digest manifest = blobs.put("local manifest".getBytes(StandardCharsets.UTF_8));
    Digest index = blobs.put("local index".getBytes(StandardCharsets.UTF_8));
    long packBytes = logicalBytes - manifest.getSizeBytes() - index.getSizeBytes();
    var pack = PublicationFile.newBuilder().setName("segment-00000.pack").setSizeBytes(packBytes);
    // Represent multi-GiB logical files using one repeated 1 MiB payload and a final partial chunk.
    Digest fullChunk = blobs.put(new byte[SkyCacheProtocol.CHUNK_BYTES]);
    while (packBytes >= SkyCacheProtocol.CHUNK_BYTES) {
      pack.addChunks(fullChunk);
      packBytes -= SkyCacheProtocol.CHUNK_BYTES;
    }
    if (packBytes > 0) {
      pack.addChunks(blobs.put(new byte[(int) packBytes]));
    }
    return PublicationBundle.newBuilder()
        .setFormatVersion(1)
        .setNamespaceId(NAMESPACE)
        .setRootId(PUBLICATION)
        .addPublications(
            LocalPublication.newBuilder()
                .setId(PUBLICATION)
                .addFiles(file("manifest", manifest))
                .addFiles(file("index", index))
                .addFiles(pack))
        .build();
  }

  private static CommitRequest request(MemoryBlobs blobs, String namespace) {
    Digest manifestFile = blobs.put("local manifest".getBytes(StandardCharsets.UTF_8));
    Digest indexFile = blobs.put("local index".getBytes(StandardCharsets.UTF_8));
    PublicationBundle bundle =
        PublicationBundle.newBuilder()
            .setFormatVersion(1)
            .setNamespaceId(namespace)
            .setRootId(PUBLICATION)
            .addPublications(
                LocalPublication.newBuilder()
                    .setId(PUBLICATION)
                    .addFiles(file("manifest", manifestFile))
                    .addFiles(file("index", indexFile)))
            .build();
    return request(blobs, bundle);
  }

  private static CommitRequest request(MemoryBlobs blobs, PublicationBundle bundle) {
    return CommitRequest.newBuilder()
        .setScope(SCOPE)
        .setManifest(blobs.put(bundle.toByteArray()))
        .setIdempotencyKey("request")
        .build();
  }

  private static PublicationFile file(String name, Digest digest) {
    return PublicationFile.newBuilder()
        .setName(name)
        .setSizeBytes(digest.getSizeBytes())
        .addChunks(digest)
        .build();
  }

  private static Response<PublicationRef> commit(SkyCacheServer server, CommitRequest request) {
    var response = new Response<PublicationRef>();
    server.commitPublication(request, response);
    return response;
  }

  private static Response<DiscoverResponse> discover(SkyCacheServer server, Scope scope) {
    var response = new Response<DiscoverResponse>();
    server.discoverPublications(DiscoverRequest.newBuilder().setScope(scope).build(), response);
    return response;
  }

  private static final class MemoryBlobs implements SkyCacheServer.BlobAccess {
    final Map<Digest, byte[]> contents = new HashMap<>();
    int downloads;
    int refreshes;
    IOException failure;

    Digest put(byte[] bytes) {
      Digest digest = SkyCacheProtocol.digest(bytes);
      contents.put(digest, bytes);
      return digest;
    }

    @Override
    public void download(Digest digest, OutputStream output) throws IOException {
      downloads++;
      if (failure != null) {
        throw failure;
      }
      byte[] bytes = contents.get(digest);
      if (bytes == null) {
        throw new CacheNotFoundException(digest);
      }
      output.write(bytes);
    }

    @Override
    public boolean refresh(Digest digest) {
      refreshes++;
      return contents.containsKey(digest);
    }
  }

  private static final class Response<T> implements StreamObserver<T> {
    T value;
    Throwable failure;
    int values;
    boolean completed;

    @Override
    public void onNext(T value) {
      this.value = value;
      values++;
    }

    @Override
    public void onError(Throwable failure) {
      this.failure = failure;
    }

    @Override
    public void onCompleted() {
      completed = true;
    }

    void assertSuccess() {
      assertThat(failure).isNull();
      assertThat(completed).isTrue();
      assertThat(values).isEqualTo(1);
    }

    void assertFailure(Status.Code expected) {
      assertThat(failure).isNotNull();
      assertThat(Status.fromThrowable(failure).getCode()).isEqualTo(expected);
      assertThat(completed).isFalse();
      assertThat(values).isEqualTo(0);
    }
  }
}
