// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.remote;

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.Assert.assertThrows;

import build.bazel.remote.execution.v2.BatchUpdateBlobsRequest;
import build.bazel.remote.execution.v2.BatchUpdateBlobsResponse;
import build.bazel.remote.execution.v2.ContentAddressableStorageGrpc;
import build.bazel.remote.execution.v2.Digest;
import build.bazel.remote.execution.v2.DigestFunction;
import build.bazel.remote.execution.v2.RequestMetadata;
import com.google.bytestream.ByteStreamGrpc;
import com.google.bytestream.ByteStreamProto.ReadRequest;
import com.google.bytestream.ByteStreamProto.ReadResponse;
import com.google.devtools.build.lib.remote.util.TracingMetadataUtils;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverResponse;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import com.google.protobuf.ByteString;
import io.grpc.Metadata;
import io.grpc.Server;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.ServerInterceptor;
import io.grpc.Status;
import io.grpc.netty.NettyServerBuilder;
import io.grpc.stub.StreamObserver;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class SkyCacheTransportTest {
  @Rule public final TemporaryFolder temporaryFolder = new TemporaryFolder();
  private static final String INSTANCE = "projects/test";
  private static final Scope SCOPE =
      Scope.newBuilder().setInstanceName(INSTANCE).setNamespaceId("a".repeat(64)).build();
  private static final byte[] CONTENT = "verified skycache content".getBytes(UTF_8);
  private static final Digest DIGEST = SkyCacheProtocol.digest(CONTENT);

  @Test
  public void realGrpcRoundTripUsesCasAndByteStreamInstance() throws Exception {
    try (Backend backend = new Backend();
        SkyCacheTransport transport = backend.transport(Duration.ofSeconds(5))) {
      transport.beginTransfer();
      assertThat(transport.upload(CONTENT)).isEqualTo(DIGEST);
      assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
      assertThat(backend.lastUpload.getInstanceName()).isEqualTo(INSTANCE);
      assertThat(backend.lastUpload.getDigestFunction()).isEqualTo(DigestFunction.Value.SHA256);
      assertThat(backend.lastRead.getResourceName())
          .isEqualTo(INSTANCE + "/blobs/" + DIGEST.getHash() + "/" + CONTENT.length);
      assertThat(transport.discover(SCOPE, ByteString.EMPTY).getCandidatesCount()).isEqualTo(0);
      assertThat(backend.lastDiscovery.getPageSize()).isEqualTo(16);
      assertThat(backend.lastDiscovery.getScope()).isEqualTo(SCOPE);
    }
  }

  @Test
  public void uploadAcceptsMissingDigestButRejectsMalformedRepliesAndOversizeInput()
      throws Exception {
    try (Backend backend = new Backend();
        SkyCacheTransport transport = backend.transport(Duration.ofSeconds(5))) {
      transport.beginTransfer();
      backend.uploadMode = "omit";
      assertThat(transport.upload(CONTENT)).isEqualTo(DIGEST);
      for (String mode : List.of("wrong", "empty", "reject")) {
        backend.uploadMode = mode;
        IOException failure = assertThrows(IOException.class, () -> transport.upload(CONTENT));
        assertThat(failure).hasMessageThat().doesNotContain("private-server-detail");
      }
      assertThrows(
          IOException.class, () -> transport.upload(new byte[SkyCacheProtocol.CHUNK_BYTES + 1]));
    }
  }

  @Test
  public void downloadRejectsCorruptTruncatedOversizedAndMalformedData() throws Exception {
    // An oversized HTTP/2 response can close its connection after cancellation. Each malicious
    // response gets an independent healthy connection, so a previous failure cannot mask this one.
    for (String mode : List.of("corrupt", "truncated", "oversized", "large-message")) {
      try (Backend backend = new Backend();
          SkyCacheTransport transport = backend.transport(Duration.ofSeconds(5))) {
        transport.beginTransfer();
        backend.readMode = mode;
        IOException failure = assertThrows(IOException.class, () -> transport.download(DIGEST));
        String expected =
            switch (mode) {
              case "corrupt", "truncated" -> "digest or length mismatch";
              case "oversized" -> "exceeds its declared size";
              default -> "RESOURCE_EXHAUSTED";
            };
        assertThat(failure).hasMessageThat().contains(expected);
        assertThat(backend.readCalls.get()).isEqualTo(1);
      }
    }
  }

  @Test
  public void malformedRequestsAndMissingBlobErrorsRemainSanitized() throws Exception {
    try (Backend backend = new Backend();
        SkyCacheTransport transport = backend.transport(Duration.ofSeconds(5))) {
      transport.beginTransfer();
      assertThrows(
          IOException.class, () -> transport.download(DIGEST.toBuilder().setHash("bad").build()));
      assertThrows(
          IOException.class, () -> transport.download(DIGEST.toBuilder().setSizeBytes(-1).build()));
      assertThrows(
          IOException.class,
          () ->
              transport.download(
                  DIGEST.toBuilder().setSizeBytes(SkyCacheProtocol.CHUNK_BYTES + 1).build()));
      assertThat(backend.readCalls.get()).isEqualTo(0);
      backend.readMode = "missing";
      IOException failure = assertThrows(IOException.class, () -> transport.download(DIGEST));
      assertThat(failure).hasMessageThat().contains("NOT_FOUND");
      assertThat(failure).hasMessageThat().doesNotContain("private-server-detail");
      assertThat(failure.getCause()).isNull();
    }
  }

  @Test
  public void bearerTokenIsAttachedToEveryService() throws Exception {
    Path token = temporaryFolder.newFile().toPath();
    Files.writeString(token, "  local-test-token  ");
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, token.toString(), Duration.ofSeconds(5))) {
      transport.beginTransfer();
      transport.upload(CONTENT);
      transport.download(DIGEST);
      transport.discover(SCOPE, ByteString.EMPTY);
      transport.commit(commit());
      assertThat(backend.requestMetadata).hasSize(4);
      RequestMetadata metadata = backend.requestMetadata.get(0);
      assertThat(metadata.getToolInvocationId()).isNotEmpty();
      assertThat(metadata.getCorrelatedInvocationsId()).isEqualTo(metadata.getToolInvocationId());
      assertThat(metadata.getActionId()).isEqualTo("skycache-metadata");
      for (RequestMetadata request : backend.requestMetadata) {
        assertThat(request).isEqualTo(metadata);
      }
      assertThat(backend.authorization)
          .containsExactly(
              "Bearer local-test-token",
              "Bearer local-test-token",
              "Bearer local-test-token",
              "Bearer local-test-token");
    }
  }

  @Test
  public void tokenFilesRejectNewlinesOversizeAndUnreadablePaths() throws Exception {
    Path token = temporaryFolder.newFile().toPath();
    for (String invalid :
        List.of("token\n", "token\r", " ", "x".repeat(8193), "control\u0000character")) {
      Files.writeString(token, invalid);
      IOException failure =
          assertThrows(
              IOException.class,
              () ->
                  new SkyCacheTransport(
                      "grpc://localhost:1", INSTANCE, token.toString(), Duration.ofSeconds(5)));
      assertThat(failure).hasMessageThat().contains("bearer token");
      assertThat(failure.getCause()).isNull();
    }
    assertThrows(
        IOException.class,
        () ->
            new SkyCacheTransport(
                "grpc://localhost:1",
                INSTANCE,
                token.resolve("missing").toString(),
                Duration.ofSeconds(5)));
  }

  @Test
  public void commitRetriesIdenticalRequestExactlyOnce() throws Exception {
    try (Backend backend = new Backend();
        SkyCacheTransport transport = backend.transport(Duration.ofSeconds(5))) {
      transport.beginTransfer();
      backend.unavailableCommits.set(1);
      assertThat(transport.commit(commit()).getManifest()).isEqualTo(DIGEST);
      assertThat(backend.commits).containsExactly(commit(), commit()).inOrder();
      backend.commits.clear();
      backend.unavailableCommits.set(3);
      assertThat(assertThrows(IOException.class, () -> transport.commit(commit())))
          .hasMessageThat()
          .contains("UNAVAILABLE");
      assertThat(backend.commits).hasSize(2);
    }
  }

  @Test
  public void transferDeadlineAppliesAcrossOperationsAndCanBeReset() throws Exception {
    try (Backend backend = new Backend();
        SkyCacheTransport transport = backend.transport(Duration.ofMillis(500))) {
      transport.beginTransfer();
      backend.readMode = "hang";
      assertThat(assertThrows(IOException.class, () -> transport.download(DIGEST)))
          .hasMessageThat()
          .contains("DEADLINE_EXCEEDED");
      assertThat(assertThrows(IOException.class, () -> transport.upload(CONTENT)))
          .hasMessageThat()
          .contains("DEADLINE_EXCEEDED");
      assertThat(backend.uploadCalls.get()).isEqualTo(0);
      transport.beginTransfer();
      assertThat(transport.upload(CONTENT)).isEqualTo(DIGEST);
    }
  }

  @Test
  public void attemptedDownloadsConsumeSharedTransferBudget() throws Exception {
    Digest large = DIGEST.toBuilder().setSizeBytes(SkyCacheProtocol.CHUNK_BYTES).build();
    try (Backend backend = new Backend();
        SkyCacheTransport transport = backend.transport(Duration.ofSeconds(30))) {
      transport.beginTransfer();
      backend.readMode = "missing";
      for (int i = 0; i < 257; i++) {
        assertThat(assertThrows(IOException.class, () -> transport.download(large)))
            .hasMessageThat()
            .contains("NOT_FOUND");
      }
      assertThat(assertThrows(IOException.class, () -> transport.download(large)))
          .hasMessageThat()
          .contains("byte budget");
      assertThat(backend.readCalls.get()).isEqualTo(257);
      transport.beginTransfer();
      assertThat(assertThrows(IOException.class, () -> transport.download(large)))
          .hasMessageThat()
          .contains("NOT_FOUND");
      assertThat(backend.readCalls.get()).isEqualTo(258);
    }
  }

  @Test
  public void endpointValidationAndLifecycleFailBeforeRpc() throws Exception {
    for (String endpoint :
        List.of(
            "localhost:1234",
            "http://localhost:1234",
            "grpc://localhost",
            "grpc://localhost:0",
            "grpc://localhost:65536",
            "grpc://user:secret@localhost:1234",
            "grpc://localhost:1234/path",
            "grpc://localhost:1234/",
            "grpc://localhost:1234?x=1",
            "grpc://localhost:1234#fragment")) {
      IOException failure =
          assertThrows(
              IOException.class,
              () -> new SkyCacheTransport(endpoint, INSTANCE, "", Duration.ofSeconds(1)));
      assertThat(failure).hasMessageThat().doesNotContain("secret");
    }
    assertThrows(
        IOException.class,
        () -> new SkyCacheTransport("grpc://localhost:1", INSTANCE, "", Duration.ZERO));
    try (SkyCacheTransport tls =
        new SkyCacheTransport("grpcs://localhost:1", INSTANCE, "", Duration.ofSeconds(1))) {
      assertThrows(IOException.class, () -> tls.upload(CONTENT));
    }
    try (Backend backend = new Backend()) {
      SkyCacheTransport transport = backend.transport(Duration.ofSeconds(1));
      transport.beginTransfer();
      assertThrows(
          IOException.class,
          () ->
              transport.discover(
                  SCOPE.toBuilder().setInstanceName("other").build(), ByteString.EMPTY));
      transport.close();
      assertThrows(IOException.class, transport::beginTransfer);
      assertThrows(IOException.class, () -> transport.upload(CONTENT));
      assertThat(backend.uploadCalls.get()).isEqualTo(0);
    }
  }

  @Test
  public void discoveryAndCommitResponsesMustMatchProtocolBounds() throws Exception {
    try (Backend backend = new Backend();
        SkyCacheTransport transport = backend.transport(Duration.ofSeconds(5))) {
      transport.beginTransfer();
      backend.discovery =
          DiscoverResponse.newBuilder()
              .addCandidates(PublicationRef.newBuilder().setManifest(DIGEST).setCatalogSequence(2))
              .addCandidates(PublicationRef.newBuilder().setManifest(DIGEST).setCatalogSequence(3))
              .build();
      assertThrows(IOException.class, () -> transport.discover(SCOPE, ByteString.EMPTY));
      backend.wrongCommitDigest = true;
      assertThrows(IOException.class, () -> transport.commit(commit()));
    }
  }

  private static CommitRequest commit() {
    return CommitRequest.newBuilder()
        .setScope(SCOPE)
        .setManifest(DIGEST)
        .setIdempotencyKey("stable-key")
        .build();
  }

  /** Real loopback gRPC server: only service replies are scripted. */
  private static final class Backend implements AutoCloseable {
    private final Server server;
    private volatile String uploadMode = "normal";
    private volatile String readMode = "normal";
    private volatile BatchUpdateBlobsRequest lastUpload;
    private volatile ReadRequest lastRead;
    private volatile DiscoverRequest lastDiscovery;
    private volatile DiscoverResponse discovery = DiscoverResponse.getDefaultInstance();
    private volatile boolean wrongCommitDigest;
    private final AtomicInteger uploadCalls = new AtomicInteger();
    private final AtomicInteger readCalls = new AtomicInteger();
    private final AtomicInteger unavailableCommits = new AtomicInteger();
    private final List<CommitRequest> commits = new CopyOnWriteArrayList<>();
    private final List<String> authorization = new CopyOnWriteArrayList<>();
    private final List<RequestMetadata> requestMetadata = new CopyOnWriteArrayList<>();

    Backend() throws IOException {
      server =
          NettyServerBuilder.forAddress(new InetSocketAddress("127.0.0.1", 0))
              .directExecutor()
              .intercept(new TracingMetadataUtils.ServerHeadersInterceptor())
              .intercept(
                  new ServerInterceptor() {
                    @Override
                    public <ReqT, RespT> ServerCall.Listener<ReqT> interceptCall(
                        ServerCall<ReqT, RespT> call,
                        Metadata headers,
                        ServerCallHandler<ReqT, RespT> next) {
                      RequestMetadata metadata =
                          TracingMetadataUtils.requestMetadataFromHeaders(headers);
                      if (metadata != null) {
                        requestMetadata.add(metadata);
                      }
                      String auth =
                          headers.get(
                              Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER));
                      if (auth != null) {
                        authorization.add(auth);
                      }
                      return next.startCall(call, headers);
                    }
                  })
              .addService(
                  new ContentAddressableStorageGrpc.ContentAddressableStorageImplBase() {
                    @Override
                    public void batchUpdateBlobs(
                        BatchUpdateBlobsRequest request,
                        StreamObserver<BatchUpdateBlobsResponse> observer) {
                      lastUpload = request;
                      uploadCalls.incrementAndGet();
                      var response = BatchUpdateBlobsResponse.newBuilder();
                      if (!uploadMode.equals("empty")) {
                        var item = BatchUpdateBlobsResponse.Response.newBuilder();
                        if (uploadMode.equals("normal")) {
                          item.setDigest(request.getRequests(0).getDigest());
                        }
                        if (uploadMode.equals("wrong")) {
                          item.setDigest(DIGEST.toBuilder().setHash("b".repeat(64)));
                        }
                        item.setStatus(
                            com.google.rpc.Status.newBuilder()
                                .setCode(uploadMode.equals("reject") ? 8 : 0)
                                .setMessage("private-server-detail"));
                        response.addResponses(item);
                      }
                      observer.onNext(response.build());
                      observer.onCompleted();
                    }
                  })
              .addService(
                  new ByteStreamGrpc.ByteStreamImplBase() {
                    @Override
                    public void read(ReadRequest request, StreamObserver<ReadResponse> observer) {
                      lastRead = request;
                      readCalls.incrementAndGet();
                      switch (readMode) {
                        case "missing" ->
                            observer.onError(
                                Status.NOT_FOUND
                                    .withDescription("private-server-detail")
                                    .asRuntimeException());
                        case "hang" -> {}
                        case "corrupt" -> {
                          byte[] corrupt = CONTENT.clone();
                          corrupt[0] ^= 1;
                          reply(observer, corrupt);
                        }
                        case "truncated" ->
                            reply(observer, java.util.Arrays.copyOf(CONTENT, CONTENT.length - 1));
                        case "oversized" ->
                            reply(observer, java.util.Arrays.copyOf(CONTENT, CONTENT.length + 1));
                        case "large-message" ->
                            reply(observer, new byte[SkyCacheProtocol.CHUNK_BYTES + 65537]);
                        default -> {
                          int half = CONTENT.length / 2;
                          observer.onNext(
                              ReadResponse.newBuilder()
                                  .setData(ByteString.copyFrom(CONTENT, 0, half))
                                  .build());
                          observer.onNext(
                              ReadResponse.newBuilder()
                                  .setData(
                                      ByteString.copyFrom(CONTENT, half, CONTENT.length - half))
                                  .build());
                          observer.onCompleted();
                        }
                      }
                    }
                  })
              .addService(
                  new SkyCacheCatalogGrpc.SkyCacheCatalogImplBase() {
                    @Override
                    public void discoverPublications(
                        DiscoverRequest request, StreamObserver<DiscoverResponse> observer) {
                      lastDiscovery = request;
                      observer.onNext(discovery);
                      observer.onCompleted();
                    }

                    @Override
                    public void commitPublication(
                        CommitRequest request, StreamObserver<PublicationRef> observer) {
                      commits.add(request);
                      if (unavailableCommits.getAndDecrement() > 0) {
                        observer.onError(
                            Status.UNAVAILABLE
                                .withDescription("private-server-detail")
                                .asRuntimeException());
                        return;
                      }
                      observer.onNext(
                          PublicationRef.newBuilder()
                              .setManifest(
                                  wrongCommitDigest
                                      ? DIGEST.toBuilder().setHash("b".repeat(64)).build()
                                      : request.getManifest())
                              .setCatalogSequence(1)
                              .build());
                      observer.onCompleted();
                    }
                  })
              .build()
              .start();
    }

    private String endpoint() {
      return "grpc://127.0.0.1:" + server.getPort();
    }

    private SkyCacheTransport transport(Duration timeout) throws IOException {
      return new SkyCacheTransport(endpoint(), INSTANCE, "", timeout);
    }

    private static void reply(StreamObserver<ReadResponse> observer, byte[] bytes) {
      observer.onNext(ReadResponse.newBuilder().setData(ByteString.copyFrom(bytes)).build());
      observer.onCompleted();
    }

    @Override
    public void close() throws InterruptedException {
      server.shutdownNow().awaitTermination(5, TimeUnit.SECONDS);
    }
  }
}
