// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.remote.worker;

import static com.google.common.truth.Truth.assertThat;

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
