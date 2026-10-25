// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.remote;

import build.bazel.remote.execution.v2.BatchUpdateBlobsRequest;
import build.bazel.remote.execution.v2.ContentAddressableStorageGrpc;
import build.bazel.remote.execution.v2.Digest;
import build.bazel.remote.execution.v2.DigestFunction;
import com.google.bytestream.ByteStreamGrpc;
import com.google.bytestream.ByteStreamProto.ReadRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverResponse;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import com.google.protobuf.ByteString;
import io.grpc.Channel;
import io.grpc.ClientInterceptors;
import io.grpc.Context;
import io.grpc.Deadline;
import io.grpc.ManagedChannel;
import io.grpc.Metadata;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.netty.NettyChannelBuilder;
import io.grpc.stub.MetadataUtils;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.URISyntaxException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.TimeUnit;

/**
 * Bounded blocking transport for one SkyCache transfer at a time. Call {@link #beginTransfer}
 * before each import/discovery stage or export stage. Every RPC in that stage shares one absolute
 * deadline and byte budget. {@code grpc://} sends plaintext and is intended for trusted local
 * development; {@code grpcs://} uses the platform's normal TLS trust roots.
 */
public final class SkyCacheTransport implements AutoCloseable {
  private static final int MAX_PAGE_TOKEN_BYTES = 16 * 1024;
  private static final long TRANSFER_BUDGET =
      SkyCacheProtocol.MAX_TRANSFER_BYTES + SkyCacheProtocol.MAX_BUNDLE_BYTES;
  private final String instanceName;
  private final long timeoutNanos;
  private final ManagedChannel channel;
  private final SkyCacheCatalogGrpc.SkyCacheCatalogBlockingStub catalog;
  private final ContentAddressableStorageGrpc.ContentAddressableStorageBlockingStub cas;
  private final ByteStreamGrpc.ByteStreamBlockingStub byteStream;
  private Deadline deadline;
  private long transferredBytes;
  private boolean closed;

  public SkyCacheTransport(String endpoint, String instanceName, String tokenFile, Duration timeout)
      throws IOException {
    URI uri = parseEndpoint(endpoint);
    if (instanceName == null || timeout == null) {
      throw new IOException("SkyCache instance and transfer timeout are required");
    }
    SkyCacheProtocol.validateScope(
        Scope.newBuilder().setInstanceName(instanceName).setNamespaceId("0".repeat(64)).build());
    this.instanceName = instanceName;
    try {
      timeoutNanos = timeout.toNanos();
    } catch (ArithmeticException e) {
      throw new IOException("SkyCache transfer timeout is too large");
    }
    if (timeoutNanos <= 0) {
      throw new IOException("SkyCache transfer timeout must be positive");
    }
    Metadata headers = readToken(tokenFile);
    NettyChannelBuilder builder =
        NettyChannelBuilder.forAddress(uri.getHost(), uri.getPort())
            .maxInboundMessageSize(SkyCacheProtocol.CHUNK_BYTES + 65536);
    if (uri.getScheme().equals("grpc")) {
      builder.usePlaintext();
    } else {
      builder.useTransportSecurity();
    }
    try {
      channel = builder.build();
    } catch (RuntimeException e) {
      throw new IOException("Unable to initialize SkyCache RPC channel");
    }
    Channel authenticated =
        headers == null
            ? channel
            : ClientInterceptors.intercept(
                channel, MetadataUtils.newAttachHeadersInterceptor(headers));
    catalog = SkyCacheCatalogGrpc.newBlockingStub(authenticated);
    cas = ContentAddressableStorageGrpc.newBlockingStub(authenticated);
    byteStream = ByteStreamGrpc.newBlockingStub(authenticated);
  }

  /** Starts a new transfer; this does not extend deadlines of RPCs already in progress. */
  public synchronized void beginTransfer() throws IOException {
    checkOpen();
    deadline = Deadline.after(timeoutNanos, TimeUnit.NANOSECONDS);
    transferredBytes = 0;
  }

  public synchronized DiscoverResponse discover(Scope scope, ByteString pageToken)
      throws IOException {
    validateScope(scope);
    if (pageToken.size() > MAX_PAGE_TOKEN_BYTES) {
      throw new IOException("SkyCache page token is too large");
    }
    try {
      DiscoverResponse result =
          catalog
              .withDeadline(activeDeadline())
              .discoverPublications(
                  DiscoverRequest.newBuilder()
                      .setScope(scope)
                      .setPageSize(16)
                      .setPageToken(pageToken)
                      .build());
      if (result.getCandidatesCount() > 16
          || result.getNextPageToken().size() > MAX_PAGE_TOKEN_BYTES) {
        throw new IOException("SkyCache discovery response exceeds bounds");
      }
      long previous = Long.MAX_VALUE;
      for (PublicationRef publication : result.getCandidatesList()) {
        SkyCacheProtocol.validateDigest(
            publication.getManifest(), SkyCacheProtocol.MAX_BUNDLE_BYTES);
        if (publication.getCatalogSequence() < 1 || publication.getCatalogSequence() >= previous) {
          throw new IOException("SkyCache discovery sequence is invalid");
        }
        previous = publication.getCatalogSequence();
      }
      return result;
    } catch (StatusRuntimeException e) {
      throw rpcFailure(e);
    }
  }

  /**
   * Retries an uncertain UNAVAILABLE result once with the identical idempotency key and deadline.
   */
  public synchronized PublicationRef commit(CommitRequest request) throws IOException {
    validateScope(request.getScope());
    SkyCacheProtocol.validateDigest(request.getManifest(), SkyCacheProtocol.MAX_BUNDLE_BYTES);
    if (request.getIdempotencyKey().isEmpty() || request.getIdempotencyKey().length() > 4096) {
      throw new IOException("Invalid SkyCache idempotency key");
    }
    Deadline transferDeadline = activeDeadline();
    for (int attempt = 0; ; attempt++) {
      try {
        PublicationRef publication =
            catalog.withDeadline(transferDeadline).commitPublication(request);
        if (publication.getCatalogSequence() < 1
            || !sameDigest(request.getManifest(), publication.getManifest())) {
          throw new IOException("SkyCache commit response does not match its request");
        }
        return publication;
      } catch (StatusRuntimeException e) {
        if (attempt == 0
            && e.getStatus().getCode() == Status.Code.UNAVAILABLE
            && !transferDeadline.isExpired()) {
          continue;
        }
        throw rpcFailure(e);
      }
    }
  }

  public synchronized Digest upload(byte[] bytes) throws IOException {
    if (bytes.length > SkyCacheProtocol.CHUNK_BYTES) {
      throw new IOException("SkyCache blob exceeds chunk size");
    }
    Deadline transferDeadline = activeDeadline();
    charge(bytes.length);
    Digest digest = SkyCacheProtocol.digest(bytes);
    try {
      var response =
          cas.withDeadline(transferDeadline)
              .batchUpdateBlobs(
                  BatchUpdateBlobsRequest.newBuilder()
                      .setInstanceName(instanceName)
                      .setDigestFunction(DigestFunction.Value.SHA256)
                      .addRequests(
                          BatchUpdateBlobsRequest.Request.newBuilder()
                              .setDigest(digest)
                              .setData(ByteString.copyFrom(bytes)))
                      .build());
      if (response.getResponsesCount() != 1) {
        throw new IOException("SkyCache CAS returned an invalid upload response count");
      }
      var result = response.getResponses(0);
      if (result.getStatus().getCode() != 0) {
        throw new IOException(
            "SkyCache upload failed: "
                + Status.fromCodeValue(result.getStatus().getCode()).getCode());
      }
      // Bazel's existing REAPI test worker omits the per-item digest on success.
      if (result.hasDigest() && !sameDigest(digest, result.getDigest())) {
        throw new IOException("SkyCache CAS upload digest mismatch");
      }
      return digest;
    } catch (StatusRuntimeException e) {
      throw rpcFailure(e);
    }
  }

  public synchronized byte[] download(Digest digest) throws IOException {
    SkyCacheProtocol.validateDigest(digest, SkyCacheProtocol.CHUNK_BYTES);
    Deadline transferDeadline = activeDeadline();
    charge(digest.getSizeBytes());
    String resourceName =
        (instanceName.isEmpty() ? "" : instanceName + "/")
            + "blobs/"
            + digest.getHash()
            + "/"
            + digest.getSizeBytes();
    ByteArrayOutputStream bytes = new ByteArrayOutputStream((int) digest.getSizeBytes());
    try (Context.CancellableContext cancellation = Context.current().withCancellation()) {
      Context previous = cancellation.attach();
      try {
        var responses =
            byteStream
                .withDeadline(transferDeadline)
                .read(ReadRequest.newBuilder().setResourceName(resourceName).build());
        while (responses.hasNext()) {
          ByteString chunk = responses.next().getData();
          if (chunk.size() > digest.getSizeBytes() - bytes.size()) {
            cancellation.cancel(null);
            throw new IOException("SkyCache download exceeds its declared size");
          }
          chunk.writeTo(bytes);
        }
      } catch (StatusRuntimeException e) {
        throw rpcFailure(e);
      } finally {
        cancellation.detach(previous);
      }
    }
    byte[] result = bytes.toByteArray();
    if (!sameDigest(digest, SkyCacheProtocol.digest(result))) {
      throw new IOException("SkyCache download digest or length mismatch");
    }
    return result;
  }

  private void validateScope(Scope scope) throws IOException {
    SkyCacheProtocol.validateScope(scope);
    if (!scope.getInstanceName().equals(instanceName)) {
      throw new IOException("SkyCache scope differs from the configured CAS instance");
    }
  }

  private void charge(long size) throws IOException {
    if (size > TRANSFER_BUDGET - transferredBytes) {
      throw new IOException("SkyCache transfer exceeds its byte budget");
    }
    transferredBytes += size;
  }

  private Deadline activeDeadline() throws IOException {
    checkOpen();
    if (deadline == null) {
      throw new IOException("SkyCache transfer has not started");
    }
    if (deadline.isExpired()) {
      throw new IOException("SkyCache remote RPC failed: DEADLINE_EXCEEDED");
    }
    return deadline;
  }

  private void checkOpen() throws IOException {
    if (closed) {
      throw new IOException("SkyCache transport is closed");
    }
  }

  private static boolean sameDigest(Digest expected, Digest actual) {
    return expected.getHash().equals(actual.getHash())
        && expected.getSizeBytes() == actual.getSizeBytes();
  }

  private static IOException rpcFailure(StatusRuntimeException failure) {
    // Server descriptions/causes can contain arbitrary data, including credentials. Never relay
    // them.
    return new IOException("SkyCache remote RPC failed: " + failure.getStatus().getCode());
  }

  private static URI parseEndpoint(String endpoint) throws IOException {
    try {
      URI uri = new URI(endpoint);
      if ((!"grpc".equals(uri.getScheme()) && !"grpcs".equals(uri.getScheme()))
          || uri.getHost() == null
          || uri.getHost().isEmpty()
          || uri.getPort() < 1
          || uri.getPort() > 65535
          || uri.getRawUserInfo() != null
          || uri.getRawQuery() != null
          || uri.getRawFragment() != null
          || (uri.getRawPath() != null && !uri.getRawPath().isEmpty())) {
        throw new IOException("SkyCache endpoint must be grpc://host:port or grpcs://host:port");
      }
      return uri;
    } catch (URISyntaxException | NullPointerException e) {
      throw new IOException("Invalid SkyCache endpoint");
    }
  }

  private static Metadata readToken(String tokenFile) throws IOException {
    if (tokenFile == null || tokenFile.isEmpty()) {
      return null;
    }
    byte[] bytes;
    try (var input = Files.newInputStream(Path.of(tokenFile))) {
      bytes = input.readNBytes(8193);
    } catch (IOException | RuntimeException e) {
      throw new IOException("Unable to read SkyCache bearer token");
    }
    String contents = new String(bytes, StandardCharsets.UTF_8);
    if (bytes.length > 8192 || contents.indexOf('\r') >= 0 || contents.indexOf('\n') >= 0) {
      throw new IOException("Invalid SkyCache bearer token file");
    }
    String token = contents.trim();
    if (token.isEmpty() || !token.chars().allMatch(c -> c >= 0x20 && c <= 0x7e)) {
      throw new IOException("Invalid SkyCache bearer token file");
    }
    Metadata metadata = new Metadata();
    metadata.put(
        Metadata.Key.of("authorization", Metadata.ASCII_STRING_MARSHALLER), "Bearer " + token);
    return metadata;
  }

  @Override
  public synchronized void close() {
    if (!closed) {
      closed = true;
      channel.shutdownNow();
      try {
        channel.awaitTermination(5, TimeUnit.SECONDS);
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
      }
    }
  }
}
