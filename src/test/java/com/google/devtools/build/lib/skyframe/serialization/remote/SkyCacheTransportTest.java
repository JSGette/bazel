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
import io.grpc.Context;
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
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.LongSupplier;
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
  public void readSessionExcludesIdleTimeLongerThanItsTimeout() throws Exception {
    AtomicLong clock = new AtomicLong();
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(10), clock::get)) {
      transport.beginReadSession();
      transport.discover(SCOPE, ByteString.EMPTY);
      // Only RPC duration consumes the budget, even if build computation takes a full minute.
      clock.addAndGet(Duration.ofMinutes(1).toNanos());
      assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
      assertThat(backend.readCalls.get()).isEqualTo(1);
    }
  }

  @Test
  public void readSessionSlowRpcsShareDecreasingDeadlinesAndExhaustOneBudget() throws Exception {
    AtomicLong clock = new AtomicLong();
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(10), clock::get)) {
      // Advance the elapsed-time clock inside real RPC handling instead of sleeping in the test.
      backend.duringRpc = () -> clock.addAndGet(Duration.ofSeconds(5).toNanos());
      transport.beginReadSession();
      assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
      clock.addAndGet(Duration.ofMinutes(1).toNanos());
      assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
      assertThat(backend.observedReadDeadlines).hasSize(2);
      assertThat(backend.observedReadDeadlines.get(0) - backend.observedReadDeadlines.get(1))
          .isGreaterThan(Duration.ofSeconds(4).toNanos());
      assertThat(assertThrows(IOException.class, () -> transport.discover(SCOPE, ByteString.EMPTY)))
          .hasMessageThat()
          .contains("DEADLINE_EXCEEDED");
      assertThat(backend.lastDiscovery).isNull();
      assertThat(backend.readCalls.get()).isEqualTo(2);
    }
  }

  @Test
  public void readSessionFailedRpcsSpendTimeAndCannotRetryWithoutBound() throws Exception {
    AtomicLong clock = new AtomicLong();
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(10), clock::get)) {
      backend.duringRpc = () -> clock.addAndGet(Duration.ofMillis(2500).toNanos());
      backend.readMode = "missing";
      transport.beginReadSession();
      for (int i = 0; i < 4; i++) {
        assertThat(assertThrows(IOException.class, () -> transport.download(DIGEST)))
            .hasMessageThat()
            .contains("NOT_FOUND");
        clock.addAndGet(Duration.ofMinutes(1).toNanos());
      }
      assertThat(assertThrows(IOException.class, () -> transport.download(DIGEST)))
          .hasMessageThat()
          .contains("DEADLINE_EXCEEDED");
      assertThat(backend.readCalls.get()).isEqualTo(4);
      transport.beginReadSession();
      assertThat(assertThrows(IOException.class, () -> transport.download(DIGEST)))
          .hasMessageThat()
          .contains("NOT_FOUND");
      assertThat(backend.readCalls.get()).isEqualTo(5);
    }
  }

  @Test
  public void configuredAndDefaultRpcSlotsAreSharedAcrossServicesAndResetsRejectQueuedCallers()
      throws Exception {
    for (int concurrency : new int[] {4, 100}) {
      var clock = new ObservedClock();
      var pool = Executors.newFixedThreadPool(concurrency + 1);
      try (Backend backend = new Backend();
          SkyCacheTransport transport =
              concurrency == 100
                  ? new SkyCacheTransport(
                      backend.endpoint(), INSTANCE, "", Duration.ofSeconds(60), clock)
                  : new SkyCacheTransport(
                      backend.endpoint(),
                      INSTANCE,
                      "",
                      Duration.ofSeconds(60),
                      concurrency,
                      clock)) {
        transport.beginReadSession();
        backend.readMode = "held";
        var downloads = new ArrayList<Future<byte[]>>();
        var held = new ArrayList<HeldRead>();
        for (int i = 0; i < concurrency; i++) {
          downloads.add(pool.submit(() -> transport.download(DIGEST)));
          held.add(backend.takeRead());
        }
        var upload =
            pool.submit(
                () -> {
                  clock.watchCurrentThread();
                  return transport.upload(CONTENT);
                });
        assertThat(clock.observed.await(5, TimeUnit.SECONDS)).isTrue();
        // The clock callback runs under the admission monitor. These calls can acquire it only
        // after the extra caller has entered the queue, while every download reply is still held.
        assertThat(assertThrows(IOException.class, transport::beginReadSession))
            .hasMessageThat()
            .contains("active or queued");
        assertThat(assertThrows(IOException.class, transport::beginTransfer))
            .hasMessageThat()
            .contains("active or queued");
        assertThat(backend.uploadCalls.get()).isEqualTo(0);
        assertThat(backend.readCalls.get()).isEqualTo(concurrency);
        held.get(0).reply();
        assertThat(upload.get(5, TimeUnit.SECONDS)).isEqualTo(DIGEST);
        for (int i = 1; i < concurrency; i++) {
          held.get(i).reply();
        }
        for (var download : downloads) {
          assertThat(download.get(5, TimeUnit.SECONDS)).isEqualTo(CONTENT);
        }
        transport.beginTransfer();
        transport.beginReadSession();
      } finally {
        pool.shutdownNow();
      }
    }
  }

  @Test
  public void concurrentRpcBudgetCountsEachActiveCallAndFailuresButNotQueuedTime()
      throws Exception {
    var clock = new ObservedClock();
    var pool = Executors.newFixedThreadPool(5);
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(10), 4, clock)) {
      transport.beginReadSession();
      backend.readMode = "held";
      var downloads = new ArrayList<Future<byte[]>>();
      var held = new ArrayList<HeldRead>();
      for (int i = 0; i < 4; i++) {
        downloads.add(pool.submit(() -> transport.download(DIGEST)));
        held.add(backend.takeRead());
      }
      downloads.add(
          pool.submit(
              () -> {
                clock.watchCurrentThread();
                return transport.download(DIGEST);
              }));
      assertThat(clock.observed.await(5, TimeUnit.SECONDS)).isTrue();
      assertThrows(IOException.class, transport::beginReadSession);
      clock.now.addAndGet(Duration.ofSeconds(1).toNanos());
      held.get(0).observer.onError(Status.NOT_FOUND.asRuntimeException());
      backend.takeRead();
      // Four active calls consumed four seconds; the fifth call's queue time consumed nothing.
      assertThat(backend.observedReadDeadlines.get(4))
          .isGreaterThan(Duration.ofSeconds(5).toNanos());
      assertThat(backend.observedReadDeadlines.get(4)).isAtMost(Duration.ofSeconds(6).toNanos());
      clock.now.addAndGet(Duration.ofMillis(1500).toNanos());
      // Another four concurrent calls consume the remaining six seconds. This admission observes
      // the fake clock and wakes the budget timer, which must cancel already-running RPCs too.
      assertThat(assertThrows(IOException.class, () -> transport.discover(SCOPE, ByteString.EMPTY)))
          .hasMessageThat()
          .contains("DEADLINE_EXCEEDED");
      int notFound = 0;
      for (var download : downloads) {
        IOException failure = failedDownload(download);
        if (failure.getMessage().contains("NOT_FOUND")) {
          notFound++;
        } else {
          assertThat(failure).hasMessageThat().contains("DEADLINE_EXCEEDED");
        }
      }
      assertThat(notFound).isEqualTo(1);
      assertThat(backend.readCalls.get()).isEqualTo(5);
      assertThat(backend.lastDiscovery).isNull();
      transport.beginReadSession();
      backend.readMode = "normal";
      assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void successfulReplyCannotBeatAggregateBudgetEnforcementAtCompletion() throws Exception {
    var clock = new ObservedClock();
    var pool = Executors.newFixedThreadPool(4);
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(10), 4, clock)) {
      transport.beginReadSession();
      backend.readMode = "held";
      var downloads = new ArrayList<Future<byte[]>>();
      var held = new ArrayList<HeldRead>();
      for (int i = 0; i < 4; i++) {
        downloads.add(pool.submit(() -> transport.download(DIGEST)));
        held.add(backend.takeRead());
      }
      // Twelve aggregate seconds have elapsed. There is no new admission to notice exhaustion:
      // returning one valid reply must reject that completion and cancel the other three calls.
      clock.now.addAndGet(Duration.ofSeconds(3).toNanos());
      held.get(0).reply();
      for (var download : downloads) {
        assertThat(failedDownload(download)).hasMessageThat().contains("DEADLINE_EXCEEDED");
      }
      for (int i = 1; i < held.size(); i++) {
        assertThat(held.get(i).cancelled.await(5, TimeUnit.SECONDS)).isTrue();
      }
      assertThat(backend.readCalls.get()).isEqualTo(4);
      transport.beginReadSession();
      backend.readMode = "normal";
      assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void budgetTimerCancelsConcurrentCallsBeforeTheirIndividualDeadlines() throws Exception {
    var pool = Executors.newFixedThreadPool(4);
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(8), 4, System::nanoTime)) {
      transport.beginReadSession();
      backend.readMode = "held";
      var downloads = new ArrayList<Future<byte[]>>();
      var held = new ArrayList<HeldRead>();
      for (int i = 0; i < 4; i++) {
        downloads.add(pool.submit(() -> transport.download(DIGEST)));
        held.add(backend.takeRead());
      }
      for (var download : downloads) {
        assertThat(failedDownload(download)).hasMessageThat().contains("DEADLINE_EXCEEDED");
      }
      for (var read : held) {
        assertThat(read.cancelled.await(5, TimeUnit.SECONDS)).isTrue();
        assertThat(read.nanosRemainingAtCancellation).isGreaterThan(0L);
      }
      assertThat(backend.readCalls.get()).isEqualTo(4);
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void interruptedQueuedCallPreservesInterruptAndDoesNotAttemptRpc() throws Exception {
    var clock = new ObservedClock();
    var pool = Executors.newFixedThreadPool(5);
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(30), 4, clock)) {
      transport.beginReadSession();
      backend.readMode = "held";
      var downloads = new ArrayList<Future<byte[]>>();
      var held = new ArrayList<HeldRead>();
      for (int i = 0; i < 4; i++) {
        downloads.add(pool.submit(() -> transport.download(DIGEST)));
        held.add(backend.takeRead());
      }
      var interrupted = new AtomicBoolean();
      var queued =
          pool.submit(
              () -> {
                clock.watchCurrentThread();
                try {
                  return transport.upload(CONTENT);
                } finally {
                  interrupted.set(Thread.currentThread().isInterrupted());
                }
              });
      assertThat(clock.observed.await(5, TimeUnit.SECONDS)).isTrue();
      clock.watched.interrupt();
      assertThat(assertThrows(ExecutionException.class, () -> queued.get(5, TimeUnit.SECONDS)))
          .hasCauseThat()
          .hasMessageThat()
          .contains("Interrupted waiting");
      assertThat(interrupted.get()).isTrue();
      assertThat(backend.uploadCalls.get()).isEqualTo(0);
      for (var read : held) {
        read.reply();
      }
      for (var download : downloads) {
        assertThat(download.get(5, TimeUnit.SECONDS)).isEqualTo(CONTENT);
      }
      transport.beginReadSession();
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void closeCancelsActiveCallsAndReleasesQueuedCallersWithoutRpc() throws Exception {
    var clock = new ObservedClock();
    var pool = Executors.newFixedThreadPool(5);
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(30), 4, clock)) {
      transport.beginReadSession();
      backend.readMode = "held";
      var downloads = new ArrayList<Future<byte[]>>();
      var held = new ArrayList<HeldRead>();
      for (int i = 0; i < 4; i++) {
        downloads.add(pool.submit(() -> transport.download(DIGEST)));
        held.add(backend.takeRead());
      }
      downloads.add(
          pool.submit(
              () -> {
                clock.watchCurrentThread();
                return transport.download(DIGEST);
              }));
      assertThat(clock.observed.await(5, TimeUnit.SECONDS)).isTrue();
      transport.close();
      for (var download : downloads) {
        failedDownload(download);
      }
      for (var read : held) {
        assertThat(read.cancelled.await(5, TimeUnit.SECONDS)).isTrue();
      }
      assertThat(backend.readCalls.get()).isEqualTo(4);
      assertThat(assertThrows(IOException.class, () -> transport.download(DIGEST)))
          .hasMessageThat()
          .contains("closed");
      assertThrows(IOException.class, transport::beginTransfer);
      assertThrows(IOException.class, transport::beginReadSession);
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void concurrentAttemptsChargeOneAtomicByteBudgetEvenWhenTheyFail() throws Exception {
    var pool = Executors.newFixedThreadPool(4);
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(30), 2L * CONTENT.length)) {
      transport.beginReadSession();
      backend.readMode = "held";
      var downloads = new ArrayList<Future<byte[]>>();
      for (int i = 0; i < 4; i++) {
        downloads.add(pool.submit(() -> transport.download(DIGEST)));
      }
      backend.takeRead().observer.onError(Status.NOT_FOUND.asRuntimeException());
      backend.takeRead().observer.onError(Status.NOT_FOUND.asRuntimeException());
      int rejected = 0;
      for (var download : downloads) {
        IOException failure = failedDownload(download);
        if (failure.getMessage().contains("byte budget")) {
          rejected++;
        } else {
          assertThat(failure).hasMessageThat().contains("NOT_FOUND");
        }
      }
      assertThat(rejected).isEqualTo(2);
      assertThat(backend.readCalls.get()).isEqualTo(2);
      assertThat(assertThrows(IOException.class, () -> transport.upload(CONTENT)))
          .hasMessageThat()
          .contains("byte budget");
      assertThat(backend.uploadCalls.get()).isEqualTo(0);
    } finally {
      pool.shutdownNow();
    }
  }

  @Test
  public void configuredConcurrencyRejectsOutOfBoundsAndAcceptsBothEndpoints() throws Exception {
    try (Backend backend = new Backend()) {
      for (int limit : new int[] {-1, 0, 1001, Integer.MAX_VALUE}) {
        assertThat(
                assertThrows(
                    IOException.class,
                    () ->
                        new SkyCacheTransport(
                            backend.endpoint(),
                            INSTANCE,
                            "",
                            Duration.ofSeconds(5),
                            CONTENT.length,
                            limit)))
            .hasMessageThat()
            .contains("between 1 and 1000");
      }
      assertThat(backend.uploadCalls.get()).isEqualTo(0);
      for (int limit : new int[] {1, 1000}) {
        try (var transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(5), CONTENT.length, limit)) {
          transport.beginTransfer();
          assertThat(transport.upload(CONTENT)).isEqualTo(DIGEST);
        }
      }
      assertThat(backend.uploadCalls.get()).isEqualTo(2);
    }
  }

  @Test
  public void explicitByteBudgetIsCumulativeAcrossUploadsAndDownloadsAndResets() throws Exception {
    for (boolean readSession : new boolean[] {false, true}) {
      try (Backend backend = new Backend();
          SkyCacheTransport transport =
              new SkyCacheTransport(
                  backend.endpoint(), INSTANCE, "", Duration.ofSeconds(5), 2L * CONTENT.length)) {
        if (readSession) {
          transport.beginReadSession();
        } else {
          transport.beginTransfer();
        }
        assertThat(transport.upload(CONTENT)).isEqualTo(DIGEST);
        assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
        assertThat(assertThrows(IOException.class, () -> transport.upload(new byte[] {1})))
            .hasMessageThat()
            .contains("byte budget");
        assertThat(assertThrows(IOException.class, () -> transport.download(DIGEST)))
            .hasMessageThat()
            .contains("byte budget");
        assertThat(backend.uploadCalls.get()).isEqualTo(1);
        assertThat(backend.readCalls.get()).isEqualTo(1);

        // Switching modes also resets the same configured byte budget, not the default budget.
        if (readSession) {
          transport.beginTransfer();
        } else {
          transport.beginReadSession();
        }
        assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
        assertThat(transport.upload(CONTENT)).isEqualTo(DIGEST);
        assertThat(assertThrows(IOException.class, () -> transport.upload(new byte[] {1})))
            .hasMessageThat()
            .contains("byte budget");
        assertThat(backend.uploadCalls.get()).isEqualTo(2);
        assertThat(backend.readCalls.get()).isEqualTo(2);
      }
    }
  }

  @Test
  public void explicitByteBudgetChargesFailedDownloadsBeforeRpc() throws Exception {
    try (Backend backend = new Backend();
        SkyCacheTransport transport =
            new SkyCacheTransport(
                backend.endpoint(), INSTANCE, "", Duration.ofSeconds(5), CONTENT.length)) {
      transport.beginReadSession();
      backend.readMode = "missing";
      assertThat(assertThrows(IOException.class, () -> transport.download(DIGEST)))
          .hasMessageThat()
          .contains("NOT_FOUND");
      assertThat(assertThrows(IOException.class, () -> transport.upload(new byte[] {1})))
          .hasMessageThat()
          .contains("byte budget");
      assertThat(backend.readCalls.get()).isEqualTo(1);
      assertThat(backend.uploadCalls.get()).isEqualTo(0);
    }
  }

  @Test
  public void explicitByteBudgetRejectsInvalidValuesBeforeRpc() throws Exception {
    long maximum = SkyCacheProtocol.MAX_PUBLICATION_BYTES + SkyCacheProtocol.MAX_BUNDLE_BYTES;
    try (Backend backend = new Backend()) {
      for (long budget : new long[] {0, -1, maximum + 1, Long.MAX_VALUE}) {
        assertThrows(
            IOException.class,
            () ->
                new SkyCacheTransport(
                    backend.endpoint(), INSTANCE, "", Duration.ofSeconds(5), budget));
      }
      assertThat(backend.uploadCalls.get()).isEqualTo(0);
      assertThat(backend.readCalls.get()).isEqualTo(0);
      assertThat(backend.lastDiscovery).isNull();
      assertThat(backend.commits).isEmpty();
    }
  }

  @Test
  public void explicitByteBudgetAboveTwoGiBAndAtMaximumDoesNotOverflow() throws Exception {
    for (long budget :
        new long[] {
          (1L << 31) + 1, SkyCacheProtocol.MAX_PUBLICATION_BYTES + SkyCacheProtocol.MAX_BUNDLE_BYTES
        }) {
      try (Backend backend = new Backend();
          SkyCacheTransport transport =
              new SkyCacheTransport(
                  backend.endpoint(), INSTANCE, "", Duration.ofSeconds(5), budget)) {
        transport.beginTransfer();
        assertThat(transport.upload(CONTENT)).isEqualTo(DIGEST);
        assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
        transport.beginReadSession();
        assertThat(transport.download(DIGEST)).isEqualTo(CONTENT);
      }
    }
  }

  @Test
  public void attemptedDownloadsConsumeSharedTransferBudget() throws Exception {
    Digest large = DIGEST.toBuilder().setSizeBytes(SkyCacheProtocol.CHUNK_BYTES).build();
    for (boolean readSession : new boolean[] {false, true}) {
      try (Backend backend = new Backend();
          SkyCacheTransport transport = backend.transport(Duration.ofSeconds(30))) {
        if (readSession) {
          transport.beginReadSession();
        } else {
          transport.beginTransfer();
        }
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
        if (readSession) {
          transport.beginReadSession();
        } else {
          transport.beginTransfer();
        }
        assertThat(assertThrows(IOException.class, () -> transport.download(large)))
            .hasMessageThat()
            .contains("NOT_FOUND");
        assertThat(backend.readCalls.get()).isEqualTo(258);
      }
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

  private static IOException failedDownload(Future<byte[]> download) throws Exception {
    var failure = assertThrows(ExecutionException.class, () -> download.get(10, TimeUnit.SECONDS));
    assertThat(failure.getCause()).isInstanceOf(IOException.class);
    return (IOException) failure.getCause();
  }

  /**
   * The observed callback is inside admission, so tests need no sleeps to identify a queued call.
   */
  private static final class ObservedClock implements LongSupplier {
    final AtomicLong now = new AtomicLong();
    final CountDownLatch observed = new CountDownLatch(1);
    volatile Thread watched;

    void watchCurrentThread() {
      watched = Thread.currentThread();
    }

    @Override
    public long getAsLong() {
      if (Thread.currentThread() == watched) {
        observed.countDown();
      }
      return now.get();
    }
  }

  private static final class HeldRead {
    final StreamObserver<ReadResponse> observer;
    final CountDownLatch cancelled = new CountDownLatch(1);
    volatile long nanosRemainingAtCancellation;

    HeldRead(StreamObserver<ReadResponse> observer) {
      this.observer = observer;
      Context context = Context.current();
      context.addListener(
          ignored -> {
            nanosRemainingAtCancellation =
                context.getDeadline().timeRemaining(TimeUnit.NANOSECONDS);
            cancelled.countDown();
          },
          Runnable::run);
    }

    void reply() {
      Backend.reply(observer, CONTENT);
    }
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
    private volatile Runnable duringRpc = () -> {};
    private final List<Long> observedReadDeadlines = new CopyOnWriteArrayList<>();
    private final AtomicInteger uploadCalls = new AtomicInteger();
    private final AtomicInteger readCalls = new AtomicInteger();
    private final AtomicInteger unavailableCommits = new AtomicInteger();
    private final BlockingQueue<HeldRead> heldReads = new LinkedBlockingQueue<>();
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
                      duringRpc.run();
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
                      observedReadDeadlines.add(
                          Context.current().getDeadline().timeRemaining(TimeUnit.NANOSECONDS));
                      readCalls.incrementAndGet();
                      switch (readMode) {
                        case "held" -> heldReads.add(new HeldRead(observer));
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

    private HeldRead takeRead() throws InterruptedException {
      HeldRead read = heldReads.poll(5, TimeUnit.SECONDS);
      assertThat(read).isNotNull();
      return read;
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
