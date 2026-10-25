// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.remote.worker;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverResponse;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import com.google.protobuf.ByteString;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.concurrent.Callable;
import java.util.concurrent.Executors;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class SkyCacheCatalogStoreTest {
  @Rule public final TemporaryFolder temporaryFolder = new TemporaryFolder();

  private static final Scope SCOPE = scope("instance", "namespace");

  @Test
  public void restartPreservesIdempotencyAliasesAndConflicts() throws Exception {
    Path directory = temporaryFolder.newFolder().toPath();
    CommitRequest original = request(SCOPE, 1, "original");
    CommitRequest alias = request(SCOPE, 1, "alias");
    PublicationRef publication;
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      publication = store.commit(original);
      assertThat(store.lookup(original)).isEqualTo(publication);
      assertThat(store.lookup(alias)).isNull();
      assertThat(store.commit(alias)).isEqualTo(publication);
      assertThat(store.discover(query(SCOPE, 16)).getCandidatesList()).containsExactly(publication);
    }
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      assertThat(store.lookup(original)).isEqualTo(publication);
      assertThat(store.lookup(alias)).isEqualTo(publication);
      assertThat(store.commit(original)).isEqualTo(publication);
      assertThat(store.commit(alias)).isEqualTo(publication);
      assertStatus(Status.Code.ALREADY_EXISTS, () -> store.lookup(request(SCOPE, 2, "original")));
      assertStatus(Status.Code.ALREADY_EXISTS, () -> store.commit(request(SCOPE, 2, "alias")));
      assertThat(store.commit(request(SCOPE, 3, "new")).getCatalogSequence())
          .isGreaterThan(publication.getCatalogSequence());
    }
  }

  @Test
  public void scopesSeparateBothInstanceAndNamespace() throws Exception {
    Scope otherInstance = scope("other-instance", "namespace");
    Scope otherNamespace = scope("instance", "other-namespace");
    try (SkyCacheCatalogStore store =
        new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      PublicationRef one = store.commit(request(SCOPE, 1, "same-key"));
      PublicationRef two = store.commit(request(otherInstance, 2, "same-key"));
      PublicationRef three = store.commit(request(otherNamespace, 3, "same-key"));
      assertThat(store.discover(query(SCOPE, 16)).getCandidatesList()).containsExactly(one);
      assertThat(store.discover(query(otherInstance, 16)).getCandidatesList()).containsExactly(two);
      assertThat(store.discover(query(otherNamespace, 16)).getCandidatesList())
          .containsExactly(three);
      assertThat(store.discover(query(scope("missing", "namespace"), 16)).getCandidatesList())
          .isEmpty();
    }
  }

  @Test
  public void paginationSurvivesInsertionsAndRestartWithoutDuplicates() throws Exception {
    Path directory = temporaryFolder.newFolder().toPath();
    List<PublicationRef> expected = new ArrayList<>();
    DiscoverResponse first;
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      for (int i = 1; i <= 5; i++) {
        expected.add(0, store.commit(request(SCOPE, i, "request-" + i)));
      }
      first = store.discover(query(SCOPE, 2));
      assertThat(first.getCandidatesList())
          .containsExactlyElementsIn(expected.subList(0, 2))
          .inOrder();
      assertThat(first.getNextPageToken().isEmpty()).isFalse();
      store.commit(request(SCOPE, 6, "new-after-first-page"));
    }
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      List<PublicationRef> actual = new ArrayList<>(first.getCandidatesList());
      ByteString token = first.getNextPageToken();
      while (!token.isEmpty()) {
        DiscoverResponse page =
            store.discover(query(SCOPE, 2).toBuilder().setPageToken(token).build());
        actual.addAll(page.getCandidatesList());
        token = page.getNextPageToken();
      }
      assertThat(actual).containsExactlyElementsIn(expected).inOrder();
      assertThat(store.discover(query(SCOPE, 16)).getCandidatesCount()).isEqualTo(6);
    }
  }

  @Test
  public void tokensRejectTamperingScopeAndQueryChanges() throws Exception {
    try (SkyCacheCatalogStore store =
        new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      store.commit(request(SCOPE, 1, "one"));
      store.commit(request(SCOPE, 2, "two"));
      ByteString token = store.discover(query(SCOPE, 1)).getNextPageToken();
      byte[] damaged = token.toByteArray();
      damaged[damaged.length - 1] ^= 1;
      assertStatus(
          Status.Code.INVALID_ARGUMENT,
          () ->
              store.discover(
                  query(SCOPE, 1).toBuilder().setPageToken(ByteString.copyFrom(damaged)).build()));
      assertStatus(
          Status.Code.INVALID_ARGUMENT,
          () ->
              store.discover(
                  query(scope("elsewhere", "namespace"), 1).toBuilder()
                      .setPageToken(token)
                      .build()));
      assertStatus(
          Status.Code.INVALID_ARGUMENT,
          () -> store.discover(query(SCOPE, 2).toBuilder().setPageToken(token).build()));
      assertStatus(
          Status.Code.INVALID_ARGUMENT,
          () ->
              store.discover(
                  query(SCOPE, 1).toBuilder()
                      .setPageToken(ByteString.copyFromUtf8("garbage"))
                      .build()));
      assertStatus(Status.Code.INVALID_ARGUMENT, () -> store.discover(query(SCOPE, 65)));
      assertStatus(Status.Code.INVALID_ARGUMENT, () -> store.discover(query(SCOPE, -1)));
    }
  }

  @Test
  public void zeroPageSizeUsesSixteen() throws Exception {
    try (SkyCacheCatalogStore store =
        new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      for (int i = 1; i <= 17; i++) {
        store.commit(request(SCOPE, i, "request-" + i));
      }
      DiscoverResponse first = store.discover(query(SCOPE, 0));
      assertThat(first.getCandidatesCount()).isEqualTo(16);
      assertThat(
              store
                  .discover(
                      query(SCOPE, 16).toBuilder().setPageToken(first.getNextPageToken()).build())
                  .getCandidatesCount())
          .isEqualTo(1);
    }
  }

  @Test
  public void concurrentCommitsAreDurableUniqueAndIdempotent() throws Exception {
    Path directory = temporaryFolder.newFolder().toPath();
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory);
        var executor = Executors.newFixedThreadPool(8)) {
      List<Callable<PublicationRef>> calls = new ArrayList<>();
      for (int i = 0; i < 48; i++) {
        int value = i % 12 + 1;
        calls.add(() -> store.commit(request(SCOPE, value, "request-" + value)));
      }
      var unique = new HashSet<PublicationRef>();
      for (var result : executor.invokeAll(calls)) {
        unique.add(result.get());
      }
      assertThat(unique).hasSize(12);
      assertThat(store.discover(query(SCOPE, 64)).getCandidatesList())
          .containsExactlyElementsIn(unique);
    }
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      assertThat(store.discover(query(SCOPE, 64)).getCandidatesCount()).isEqualTo(12);
      for (int i = 1; i <= 12; i++) {
        assertThat(store.lookup(request(SCOPE, i, "request-" + i))).isNotNull();
      }
    }
  }

  @Test
  public void capacityRejectsNewReceiptsButRetainsRetries() throws Exception {
    try (SkyCacheCatalogStore store =
        new SkyCacheCatalogStore(temporaryFolder.newFolder().toPath())) {
      CommitRequest first = request(SCOPE, 1, "alias-0");
      PublicationRef publication = store.commit(first);
      for (int i = 1; i < 4096; i++) {
        assertThat(store.commit(request(SCOPE, 1, "alias-" + i))).isEqualTo(publication);
      }
      assertThat(store.commit(first)).isEqualTo(publication);
      assertStatus(
          Status.Code.RESOURCE_EXHAUSTED, () -> store.commit(request(SCOPE, 1, "one-too-many")));
      assertStatus(
          Status.Code.RESOURCE_EXHAUSTED, () -> store.commit(request(SCOPE, 2, "new-manifest")));
    }
  }

  @Test
  public void secondWriterCannotOpenUntilFirstCloses() throws Exception {
    Path directory = temporaryFolder.newFolder().toPath();
    try (SkyCacheCatalogStore first = new SkyCacheCatalogStore(directory)) {
      assertThrows(IOException.class, () -> new SkyCacheCatalogStore(directory));
      Process other =
          new ProcessBuilder(
                  Path.of(System.getProperty("java.home"), "bin", "java").toString(),
                  "-cp",
                  System.getProperty("java.class.path"),
                  LockProbe.class.getName(),
                  directory.toString())
              .redirectErrorStream(true)
              .start();
      String output =
          new String(
              other.getInputStream().readAllBytes(), java.nio.charset.StandardCharsets.UTF_8);
      assertThat(other.waitFor()).isEqualTo(0);
      assertThat(output).contains("catalog-locked");
      first.commit(request(SCOPE, 1, "one"));
    }
    try (SkyCacheCatalogStore reopened = new SkyCacheCatalogStore(directory)) {
      assertThat(reopened.lookup(request(SCOPE, 1, "one"))).isNotNull();
    }
  }

  /** A separate JVM is necessary to exercise the OS lock rather than JVM overlap detection. */
  public static final class LockProbe {
    public static void main(String[] args) throws Exception {
      try (SkyCacheCatalogStore ignored = new SkyCacheCatalogStore(Path.of(args[0]))) {
        throw new AssertionError("A second process acquired the catalog lock");
      } catch (IOException expected) {
        if (!expected.getMessage().contains("already has a writer")) {
          throw expected;
        }
        System.out.println("catalog-locked");
      }
    }
  }

  @Test
  public void corruptReceiptFailsClosedAndReleasesWriterLock() throws Exception {
    Path directory = temporaryFolder.newFolder().toPath();
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      store.commit(request(SCOPE, 1, "one"));
    }
    Path receipt = directory.resolve("receipt-00000001.pb");
    byte[] valid = Files.readAllBytes(receipt);
    byte[] corrupt = valid.clone();
    corrupt[corrupt.length / 2] ^= 1;
    Files.write(receipt, corrupt);
    assertThrows(IOException.class, () -> new SkyCacheCatalogStore(directory));
    Files.write(receipt, valid);
    try (SkyCacheCatalogStore recovered = new SkyCacheCatalogStore(directory)) {
      assertThat(recovered.lookup(request(SCOPE, 1, "one"))).isNotNull();
    }
  }

  @Test
  public void missingSigningKeyCannotSilentlyReplaceTokenIdentity() throws Exception {
    Path directory = temporaryFolder.newFolder().toPath();
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      store.commit(request(SCOPE, 1, "one"));
    }
    Files.delete(directory.resolve("token.key"));
    assertThrows(IOException.class, () -> new SkyCacheCatalogStore(directory));
  }

  @Test
  public void incompleteTemporaryReceiptIsIgnoredButNumberingGapFailsClosed() throws Exception {
    Path directory = temporaryFolder.newFolder().toPath();
    try (SkyCacheCatalogStore store = new SkyCacheCatalogStore(directory)) {
      store.commit(request(SCOPE, 1, "one"));
    }
    Files.writeString(directory.resolve("receipt-00000002.pb.tmp"), "interrupted upload");
    try (SkyCacheCatalogStore recovered = new SkyCacheCatalogStore(directory)) {
      assertThat(recovered.discover(query(SCOPE, 16)).getCandidatesCount()).isEqualTo(1);
      recovered.commit(request(SCOPE, 2, "two"));
    }
    Files.delete(directory.resolve("receipt-00000001.pb"));
    assertThrows(IOException.class, () -> new SkyCacheCatalogStore(directory));
  }

  private static Scope scope(String instance, String namespace) {
    return Scope.newBuilder().setInstanceName(instance).setNamespaceId(namespace).build();
  }

  private static CommitRequest request(Scope scope, int manifest, String key) {
    return CommitRequest.newBuilder()
        .setScope(scope)
        .setManifest(
            Digest.newBuilder()
                .setHash(String.format(java.util.Locale.ROOT, "%064x", manifest))
                .setSizeBytes(manifest))
        .setIdempotencyKey(key)
        .build();
  }

  private static DiscoverRequest query(Scope scope, int size) {
    return DiscoverRequest.newBuilder().setScope(scope).setPageSize(size).build();
  }

  private static void assertStatus(Status.Code expected, ThrowingRunnable operation) {
    StatusRuntimeException failure = assertThrows(StatusRuntimeException.class, operation::run);
    assertThat(failure.getStatus().getCode()).isEqualTo(expected);
  }

  @FunctionalInterface
  private interface ThrowingRunnable {
    void run() throws Exception;
  }
}
