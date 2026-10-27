// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.remote;

import build.bazel.remote.execution.v2.BatchUpdateBlobsRequest;
import build.bazel.remote.execution.v2.ContentAddressableStorageGrpc;
import build.bazel.remote.execution.v2.Digest;
import build.bazel.remote.execution.v2.DigestFunction;
import com.google.bytestream.ByteStreamGrpc;
import com.google.bytestream.ByteStreamProto.ReadRequest;
import com.google.devtools.build.lib.remote.util.TracingMetadataUtils;
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
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.ScheduledThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.function.LongSupplier;

/**
 * Bounded blocking transport with 100 concurrent RPCs by default. {@link #beginTransfer} gives an
 * export one absolute deadline. {@link #beginReadSession} charges a demand-driven reader only for
 * cumulative RPC time, excluding computation between requests. Both modes share a configured byte
 * budget. {@code grpc://} sends plaintext and is intended for trusted local development; {@code
 * grpcs://} uses the platform's normal TLS trust roots.
 */
public final class SkyCacheTransport implements AutoCloseable {
  private static final int MAX_PAGE_TOKEN_BYTES = 16 * 1024;
  private static final long DEFAULT_TRANSFER_BUDGET = 257L * 1024 * 1024;
  private static final int DEFAULT_MAX_CONCURRENT_RPCS = 100;
  private final String instanceName;
  private final long timeoutNanos;
  private final long transferBudget;
  private final int maxConcurrentRpcs;
  private final LongSupplier nanoTime;
  private final ManagedChannel channel;
  private final SkyCacheCatalogGrpc.SkyCacheCatalogBlockingStub catalog;
  private final ContentAddressableStorageGrpc.ContentAddressableStorageBlockingStub cas;
  private final ByteStreamGrpc.ByteStreamBlockingStub byteStream;
  private final ScheduledThreadPoolExecutor budgetTimer;
  private final Set<RpcBudget> activeRpcs = new HashSet<>();
  private ScheduledFuture<?> budgetTask;
  private long timerGeneration;
  private long accountedAt;
  private int queuedRequests;
  private Deadline deadline;
  private boolean readSession;
  private long remainingRpcNanos;
  private boolean rpcBudgetExceeded;
  private long transferredBytes;
  private volatile boolean closed;

  public SkyCacheTransport(String endpoint, String instanceName, String tokenFile, Duration timeout)
      throws IOException {
    this(
        endpoint,
        instanceName,
        tokenFile,
        timeout,
        DEFAULT_TRANSFER_BUDGET,
        DEFAULT_MAX_CONCURRENT_RPCS,
        System::nanoTime);
  }

  /** Configures attempted blob bytes independently of the logical publication size. */
  public SkyCacheTransport(
      String endpoint, String instanceName, String tokenFile, Duration timeout, long transferBudget)
      throws IOException {
    this(
        endpoint,
        instanceName,
        tokenFile,
        timeout,
        transferBudget,
        DEFAULT_MAX_CONCURRENT_RPCS,
        System::nanoTime);
  }

  /** Configures a shared limit of 1 through 1000 concurrent metadata and blob RPCs. */
  public SkyCacheTransport(
      String endpoint,
      String instanceName,
      String tokenFile,
      Duration timeout,
      long transferBudget,
      int maxConcurrentRpcs)
      throws IOException {
    this(
        endpoint,
        instanceName,
        tokenFile,
        timeout,
        transferBudget,
        maxConcurrentRpcs,
        System::nanoTime);
  }

  // A monotonic elapsed-time seam keeps cumulative-budget tests independent of scheduler timing.
  SkyCacheTransport(
      String endpoint,
      String instanceName,
      String tokenFile,
      Duration timeout,
      LongSupplier nanoTime)
      throws IOException {
    this(
        endpoint,
        instanceName,
        tokenFile,
        timeout,
        DEFAULT_TRANSFER_BUDGET,
        DEFAULT_MAX_CONCURRENT_RPCS,
        nanoTime);
  }

  SkyCacheTransport(
      String endpoint,
      String instanceName,
      String tokenFile,
      Duration timeout,
      int maxConcurrentRpcs,
      LongSupplier nanoTime)
      throws IOException {
    this(
        endpoint,
        instanceName,
        tokenFile,
        timeout,
        DEFAULT_TRANSFER_BUDGET,
        maxConcurrentRpcs,
        nanoTime);
  }

  private SkyCacheTransport(
      String endpoint,
      String instanceName,
      String tokenFile,
      Duration timeout,
      long transferBudget,
      int maxConcurrentRpcs,
      LongSupplier nanoTime)
      throws IOException {
    this.nanoTime = java.util.Objects.requireNonNull(nanoTime);
    if (maxConcurrentRpcs < 1 || maxConcurrentRpcs > 1000) {
      throw new IOException("SkyCache maximum concurrency must be between 1 and 1000");
    }
    this.maxConcurrentRpcs = maxConcurrentRpcs;
    if (transferBudget <= 0
        || transferBudget
            > SkyCacheProtocol.MAX_PUBLICATION_BYTES + SkyCacheProtocol.MAX_BUNDLE_BYTES) {
      throw new IOException(
          "SkyCache transfer byte budget must be between 1 byte and 8 GiB + 1 MiB");
    }
    this.transferBudget = transferBudget;
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
    String invocationId = UUID.randomUUID().toString();
    Channel traced =
        ClientInterceptors.intercept(
            channel,
            TracingMetadataUtils.attachMetadataInterceptor(
                TracingMetadataUtils.buildMetadata(
                    invocationId, invocationId, "skycache-metadata")));
    Channel authenticated =
        headers == null
            ? traced
            : ClientInterceptors.intercept(
                traced, MetadataUtils.newAttachHeadersInterceptor(headers));
    catalog = SkyCacheCatalogGrpc.newBlockingStub(authenticated);
    cas = ContentAddressableStorageGrpc.newBlockingStub(authenticated);
    byteStream = ByteStreamGrpc.newBlockingStub(authenticated);
    budgetTimer =
        new ScheduledThreadPoolExecutor(
            1,
            task -> {
              var thread = new Thread(task, "skycache-rpc-budget");
              thread.setDaemon(true);
              return thread;
            });
    budgetTimer.setRemoveOnCancelPolicy(true);
  }

  /** Starts an export; resetting while any caller is active or queued is forbidden. */
  public synchronized void beginTransfer() throws IOException {
    checkResetAllowed();
    readSession = false;
    deadline = Deadline.after(timeoutNanos, TimeUnit.NANOSECONDS);
    transferredBytes = 0;
  }

  /** Starts demand-driven reads with one cumulative RPC-time budget and one byte budget. */
  public synchronized void beginReadSession() throws IOException {
    checkResetAllowed();
    readSession = true;
    deadline = null;
    remainingRpcNanos = timeoutNanos;
    rpcBudgetExceeded = false;
    transferredBytes = 0;
    accountedAt = nanoTime.getAsLong();
  }

  public DiscoverResponse discover(Scope scope, ByteString pageToken) throws IOException {
    validateScope(scope);
    if (pageToken.size() > MAX_PAGE_TOKEN_BYTES) {
      throw new IOException("SkyCache page token is too large");
    }
    try (RpcBudget rpc = startRpc(0)) {
      try {
        DiscoverResponse result =
            catalog
                .withDeadline(rpc.deadline)
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
          if (publication.getCatalogSequence() < 1
              || publication.getCatalogSequence() >= previous) {
            throw new IOException("SkyCache discovery sequence is invalid");
          }
          previous = publication.getCatalogSequence();
        }
        return result;
      } catch (StatusRuntimeException e) {
        throw rpc.failure(e);
      }
    }
  }

  /**
   * Retries an uncertain UNAVAILABLE result once with the identical idempotency key and deadline.
   */
  public PublicationRef commit(CommitRequest request) throws IOException {
    validateScope(request.getScope());
    SkyCacheProtocol.validateDigest(request.getManifest(), SkyCacheProtocol.MAX_BUNDLE_BYTES);
    if (request.getIdempotencyKey().isEmpty() || request.getIdempotencyKey().length() > 4096) {
      throw new IOException("Invalid SkyCache idempotency key");
    }
    try (RpcBudget rpc = startRpc(0)) {
      Deadline transferDeadline = rpc.deadline;
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
              && !rpc.exhausted
              && !closed
              && !transferDeadline.isExpired()) {
            continue;
          }
          throw rpc.failure(e);
        }
      }
    }
  }

  public Digest upload(byte[] bytes) throws IOException {
    if (bytes.length > SkyCacheProtocol.CHUNK_BYTES) {
      throw new IOException("SkyCache blob exceeds chunk size");
    }
    try (RpcBudget rpc = startRpc(bytes.length)) {
      Deadline transferDeadline = rpc.deadline;
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
        throw rpc.failure(e);
      }
    }
  }

  public byte[] download(Digest digest) throws IOException {
    SkyCacheProtocol.validateDigest(digest, SkyCacheProtocol.CHUNK_BYTES);
    try (RpcBudget rpc = startRpc(digest.getSizeBytes())) {
      Deadline transferDeadline = rpc.deadline;
      String resourceName =
          (instanceName.isEmpty() ? "" : instanceName + "/")
              + "blobs/"
              + digest.getHash()
              + "/"
              + digest.getSizeBytes();
      ByteArrayOutputStream bytes = new ByteArrayOutputStream((int) digest.getSizeBytes());
      try {
        var responses =
            byteStream
                .withDeadline(transferDeadline)
                .read(ReadRequest.newBuilder().setResourceName(resourceName).build());
        while (responses.hasNext()) {
          ByteString chunk = responses.next().getData();
          if (chunk.size() > digest.getSizeBytes() - bytes.size()) {
            rpc.cancellation.cancel(null);
            throw new IOException("SkyCache download exceeds its declared size");
          }
          chunk.writeTo(bytes);
        }
      } catch (StatusRuntimeException e) {
        throw rpc.failure(e);
      }
      byte[] result = bytes.toByteArray();
      if (!sameDigest(digest, SkyCacheProtocol.digest(result))) {
        throw new IOException("SkyCache download digest or length mismatch");
      }
      return result;
    }
  }

  private void validateScope(Scope scope) throws IOException {
    SkyCacheProtocol.validateScope(scope);
    if (!scope.getInstanceName().equals(instanceName)) {
      throw new IOException("SkyCache scope differs from the configured CAS instance");
    }
  }

  private void charge(long size) throws IOException {
    if (size > transferBudget - transferredBytes) {
      throw new IOException("SkyCache transfer exceeds its byte budget");
    }
    transferredBytes += size;
  }

  private Deadline activeDeadline() throws IOException {
    checkOpen();
    if (readSession) {
      if (remainingRpcNanos <= 0) {
        throw deadlineExceeded();
      }
      return Deadline.after(remainingRpcNanos, TimeUnit.NANOSECONDS);
    }
    if (deadline == null) {
      throw new IOException("SkyCache transfer has not started");
    }
    if (deadline.isExpired()) {
      throw deadlineExceeded();
    }
    return deadline;
  }

  private synchronized RpcBudget startRpc(long bytes) throws IOException {
    checkOpen();
    queuedRequests++;
    try {
      while (true) {
        accountElapsed();
        Deadline nextDeadline = activeDeadline();
        if (activeRpcs.size() < maxConcurrentRpcs) {
          charge(bytes);
          var rpc = new RpcBudget(nextDeadline);
          activeRpcs.add(rpc);
          return rpc;
        }
        try {
          if (readSession) {
            wait();
          } else {
            TimeUnit.NANOSECONDS.timedWait(
                this, Math.max(1, nextDeadline.timeRemaining(TimeUnit.NANOSECONDS)));
          }
        } catch (InterruptedException e) {
          Thread.currentThread().interrupt();
          throw new IOException("Interrupted waiting for SkyCache RPC");
        }
      }
    } finally {
      queuedRequests--;
      // A fake elapsed-time clock may exhaust the budget while a new caller is being admitted.
      scheduleBudgetCheck();
      notifyAll();
    }
  }

  private void checkResetAllowed() throws IOException {
    checkOpen();
    if (!activeRpcs.isEmpty() || queuedRequests != 0) {
      throw new IOException("Cannot reset SkyCache transfer while requests are active or queued");
    }
    cancelBudgetCheck();
  }

  /**
   * All accounting runs under this monitor; multiplication cannot overflow the remaining budget.
   */
  private void accountElapsed() {
    long now = nanoTime.getAsLong();
    long elapsed = Math.max(0, now - accountedAt);
    int count = activeRpcs.size();
    if (readSession && count != 0) {
      if (elapsed > remainingRpcNanos / count) {
        // Preserve strict overspend even if another caller accounts for it before completion.
        rpcBudgetExceeded = true;
        remainingRpcNanos = 0;
      } else {
        remainingRpcNanos -= elapsed * count;
      }
    }
    accountedAt = now;
  }

  private void cancelBudgetCheck() {
    timerGeneration++;
    if (budgetTask != null) {
      budgetTask.cancel(false);
      budgetTask = null;
    }
  }

  private void scheduleBudgetCheck() {
    cancelBudgetCheck();
    if (closed
        || !readSession
        || activeRpcs.isEmpty()
        || (remainingRpcNanos == 0 && activeRpcs.stream().allMatch(rpc -> rpc.exhausted))) {
      return;
    }
    int count = activeRpcs.size();
    long delay = remainingRpcNanos / count + (remainingRpcNanos % count == 0 ? 0 : 1);
    long generation = timerGeneration;
    budgetTask = budgetTimer.schedule(() -> checkBudget(generation), delay, TimeUnit.NANOSECONDS);
  }

  private void checkBudget(long generation) {
    List<RpcBudget> exhausted;
    synchronized (this) {
      if (closed || generation != timerGeneration) {
        return;
      }
      accountElapsed();
      if (remainingRpcNanos != 0) {
        scheduleBudgetCheck();
        return;
      }
      exhausted = List.copyOf(activeRpcs);
      exhausted.forEach(rpc -> rpc.exhausted = true);
      budgetTask = null;
      notifyAll();
    }
    // Cancellation invokes gRPC listeners: never hold the accounting monitor here.
    exhausted.forEach(rpc -> rpc.cancellation.cancel(null));
  }

  private static IOException deadlineExceeded() {
    return new IOException("SkyCache remote RPC failed: DEADLINE_EXCEEDED");
  }

  private final class RpcBudget implements AutoCloseable {
    private final Deadline deadline;
    private final Context.CancellableContext cancellation;
    private final Context previous;
    private volatile boolean exhausted;

    private RpcBudget(Deadline deadline) {
      this.deadline = deadline;
      cancellation = Context.current().withCancellation();
      previous = cancellation.attach();
    }

    private IOException failure(StatusRuntimeException failure) {
      return exhausted ? deadlineExceeded() : rpcFailure(failure);
    }

    @Override
    public void close() throws IOException {
      cancellation.detach(previous);
      cancellation.close();
      List<RpcBudget> pending = List.of();
      boolean rejectCompletion;
      synchronized (SkyCacheTransport.this) {
        accountElapsed();
        rejectCompletion = exhausted || (readSession && rpcBudgetExceeded);
        activeRpcs.remove(this);
        if (readSession && remainingRpcNanos == 0) {
          pending = List.copyOf(activeRpcs);
          pending.forEach(rpc -> rpc.exhausted = true);
        }
        scheduleBudgetCheck();
        SkyCacheTransport.this.notifyAll();
      }
      pending.forEach(rpc -> rpc.cancellation.cancel(null));
      // A reply may beat the timer. Only exact-boundary completion may consume the last budget;
      // strict aggregate overspend and a previously timer-cancelled RPC cannot return success.
      if (rejectCompletion) {
        throw deadlineExceeded();
      }
    }
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
  public void close() {
    List<RpcBudget> pending;
    synchronized (this) {
      if (closed) {
        return;
      }
      closed = true;
      cancelBudgetCheck();
      pending = List.copyOf(activeRpcs);
      notifyAll();
    }
    pending.forEach(rpc -> rpc.cancellation.cancel(null));
    budgetTimer.shutdownNow();
    channel.shutdownNow();
    try {
      channel.awaitTermination(5, TimeUnit.SECONDS);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    }
  }
}
