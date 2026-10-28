// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.Map;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalCacheSessionTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

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

  private LocalInputSnapshot snapshot(Path workspace, Path output) throws Exception {
    return new LocalInputSnapshot(workspace, output, Map.of("PATH", "/usr/bin"));
  }

  private void gitWorktree(Path workspace, Path common, String name) throws Exception {
    Files.createDirectories(common.resolve("objects"));
    Files.writeString(common.resolve("HEAD"), "ref: refs/heads/main\n");
    Path gitDirectory = Files.createDirectories(common.resolve("worktrees/" + name));
    Files.writeString(gitDirectory.resolve("commondir"), "../..\n");
    Files.writeString(workspace.resolve(".git"), "gitdir: " + gitDirectory + "\n");
  }

  private static java.util.List<String> publicationIds(Path cache) throws Exception {
    try (var paths = Files.walk(cache.resolve("leased-v2/publications-v4"))) {
      return paths
          .filter(p -> p.toString().endsWith(".manifest"))
          .map(p -> p.getFileName().toString().substring(0, 64))
          .toList();
    }
  }

  @Test
  public void disabledFinalizationTimingDoesNotReadClock() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var clock = new AtomicLong();
    var writer = new LocalCacheSession(cache, snapshot(workspace, output), clock::getAndIncrement);
    writer.put(new Key("entry"), new byte[] {7}).get();
    long readsBeforeFinish = clock.get();
    assertThat(writer.finish(true)).isTrue();
    assertThat(writer.finalizationPhasesJson()).contains("\"enabled\":false");
    assertThat(writer.finalizationPhasesJson()).doesNotContain("\"phases\"");
    assertThat(clock.get()).isEqualTo(readsBeforeFinish);
  }

  @Test
  public void finalizationTimingSeparatesInclusivePublicationFromCleanup() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var clock = new AtomicLong();
    var writer =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            () -> clock.getAndAdd(10),
            LocalCacheSession.ReadLimits.DEFAULT,
            /* profileFinalization= */ true);
    writer.put(new Key("entry"), new byte[] {7}).get();
    clock.set(0);
    assertThat(writer.finish(true)).isTrue();
    String timings = writer.finalizationPhasesJson();
    assertThat(timings).contains("\"enabled\":true");
    assertThat(timings).contains("\"sampling\":\"boundary_command_end\"");
    assertThat(timings).contains("\"unit\":\"nanoseconds\"");
    // Publication includes the four nested phases, but closes before cleanup starts.
    assertThat(timings)
        .contains(
            "\"phases\":{\"publication\":90,\"final_validation\":10,\"witness_manifest\":10,"
                + "\"seal\":10,\"local_install\":10,\"close_stores\":10}");
    assertThat(clock.get()).isEqualTo(120);
    // A repeated finish must not replace the evidence from the original attempt.
    assertThat(writer.finish(true)).isFalse();
    assertThat(writer.finalizationPhasesJson()).isEqualTo(timings);
    assertThat(clock.get()).isEqualTo(120);
  }

  @Test
  public void finalizationTimingDistinguishesZeroDurationFromUnexecutedPhase() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            () -> 0,
            LocalCacheSession.ReadLimits.DEFAULT,
            /* profileFinalization= */ true);
    assertThat(writer.finish(false)).isFalse();
    assertThat(writer.finalizationPhasesJson())
        .contains(
            "\"phases\":{\"publication\":0,\"final_validation\":null,\"witness_manifest\":null,"
                + "\"seal\":null,\"local_install\":null,\"close_stores\":0}");
  }

  @Test
  public void leasesProtectPrivateWriterAndReaderParentClosureUntilFinish() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(new Key("parent"), new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(child.importedPublicationManifest()).isNull();
    assertThat(child.get(new Key("parent")).get()).isEqualTo(new byte[] {1});
    assertThat(child.importedPublicationManifest()).isNull();
    var candidate = child.nextAnalysisCandidate(new Key("parent"), new java.util.HashSet<>());
    assertThat(child.importedPublicationManifest()).isNull();
    assertThat(child.acceptAnalysisCandidate(candidate)).isTrue();
    assertThat(child.importedPublicationManifest()).isEqualTo(parent.publicationManifest());
    child.put(new Key("child"), new byte[] {2}).get();
    try (var collector = LocalCacheLeaseManager.open(cache.resolve("leased-v2"))) {
      try (var catalog = collector.tryExclusiveCatalog();
          var objects = Files.list(cache.resolve("leased-v2/objects/segmented-v1"))) {
        assertThat(catalog).isNotNull();
        var ids = objects.map(p -> p.getFileName().toString()).toList();
        assertThat(ids).hasSize(2);
        for (String id : ids) {
          assertThat(collector.tryCollectPublication(id)).isNull();
        }
        // Existing readers and private writers need no catalog lock for ordinary record I/O.
        assertThat(child.get(new Key("parent")).get()).isEqualTo(new byte[] {1});
        assertThat(child.put(new Key("private-later"), new byte[] {3}).get()).isTrue();
      }
      assertThat(child.finish(true)).isTrue();
      var reader = new LocalCacheSession(cache, snapshot(workspace, output));
      assertThat(reader.get(new Key("child")).get()).isEqualTo(new byte[] {2});
      var ids = publicationIds(cache);
      assertThat(ids).hasSize(2);
      try (var catalog = collector.tryExclusiveCatalog()) {
        assertThat(catalog).isNotNull();
        for (String id : ids) {
          assertThat(collector.tryCollectPublication(id)).isNull();
        }
        reader.finish(false);
        for (String id : ids) {
          try (var available = collector.tryCollectPublication(id)) {
            assertThat(available).isNotNull();
          }
        }
      }
    }
  }

  @Test
  public void leasesBusyCatalogProducesRetryableReadMissAndRejectsNewPrivateWrite()
      throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("parent");
    parent.put(key, new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    var clock = new AtomicLong();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output), clock::get);
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    try (var collector = LocalCacheLeaseManager.open(cache.resolve("leased-v2"))) {
      try (var catalog = collector.tryExclusiveCatalog()) {
        assertThat(catalog).isNotNull();
        assertThrows(ExecutionException.class, () -> reader.get(key).get());
        assertThat(reader.statisticsJson(false)).contains("\"catalog_busy_skips\":1");
        assertThat(writer.put(new Key("private"), new byte[] {2}).get()).isFalse();
        assertThat(writer.failureReason()).contains("catalog is busy");
        assertThat(writer.finish(true)).isFalse();
      }
      clock.addAndGet(2_000_000_000L);
      assertThat(reader.get(key).get()).isEqualTo(new byte[] {1});
      reader.finish(false);
    }
  }

  @Test
  public void leasesBusyCatalogAtCommitLeavesNoPublicationAndReleasesPrivatePin() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    writer.put(new Key("private"), new byte[] {2}).get();
    String id;
    try (var objects = Files.list(cache.resolve("leased-v2/objects/segmented-v1"))) {
      id = objects.findFirst().orElseThrow().getFileName().toString();
    }
    try (var collector = LocalCacheLeaseManager.open(cache.resolve("leased-v2"));
        var catalog = collector.tryExclusiveCatalog()) {
      assertThat(catalog).isNotNull();
      assertThat(collector.tryCollectPublication(id)).isNull();
      assertThrows(IOException.class, () -> writer.finish(true));
      assertThat(publicationIds(cache)).isEmpty();
      try (var available = collector.tryCollectPublication(id)) {
        assertThat(available).isNotNull();
      }
    }
  }

  @Test
  public void leasesDoNotReadOrModifyLegacyPublications() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var inputs = snapshot(workspace, output);
    Path legacy =
        Files.createDirectories(cache.resolve("publications-v3").resolve(inputs.cacheNamespace()));
    Path manifest =
        Files.writeString(legacy.resolve("1".repeat(64) + ".manifest"), "legacy-manifest");
    Path pack =
        Files.writeString(
            Files.createDirectories(cache.resolve("objects/packed-v1/" + "1".repeat(64)))
                .resolve("data.pack"),
            "legacy-pack");
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("new");
    assertThrows(ExecutionException.class, () -> writer.get(key).get());
    assertThat(writer.statisticsJson(false)).contains("\"rejected_publications\":0");
    writer.put(key, new byte[] {1}).get();
    assertThat(writer.finish(true)).isTrue();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {1});
    reader.finish(false);
    assertThat(Files.readString(manifest)).isEqualTo("legacy-manifest");
    assertThat(Files.readString(pack)).isEqualTo("legacy-pack");
    try (var entries = Files.list(legacy)) {
      assertThat(entries.toList()).containsExactly(manifest);
    }
  }

  @Test
  public void leasesCloseTheOnlyCoordinationDescriptorAfterTheLastSession() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    for (int i = 0; i < 20; i++) {
      var first = new LocalCacheSession(cache, snapshot(workspace, output));
      var second = new LocalCacheSession(cache, snapshot(workspace, output));
      assertThat(coordinationDescriptors(cache)).isEqualTo(1);
      first.finish(false);
      assertThat(coordinationDescriptors(cache)).isEqualTo(1);
      second.finish(false);
      assertThat(coordinationDescriptors(cache)).isEqualTo(0);
    }
  }

  private static long coordinationDescriptors(Path cache) throws Exception {
    Path lock = cache.resolve("leased-v2/leases.lock").toRealPath();
    try (var descriptors = Files.list(Path.of("/proc/self/fd"))) {
      return descriptors
          .filter(
              path -> {
                try {
                  return Files.readSymbolicLink(path).equals(lock);
                } catch (IOException e) {
                  return false;
                }
              })
          .count();
    }
  }

  @Test
  public void trafficCountsLogicalRequestsAndAcceptedDuplicateWrites() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var session = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("key");
    byte[] value = {1, 2, 3, 4};
    var empty = session.getStats();
    assertThrows(ExecutionException.class, () -> session.get(key).get());
    assertThat(session.put(key, value).get()).isTrue();
    assertThat(session.put(key, value).get()).isFalse();
    assertThat(session.get(key).get()).isEqualTo(value);
    var written = session.getStats();
    assertThat(written.keyBytesSent()).isEqualTo(6);
    assertThat(written.valueBytesSent()).isEqualTo(8);
    assertThat(written.entriesWritten()).isEqualTo(2);
    assertThat(written.setBatches()).isEqualTo(2);
    assertThat(written.valueBytesReceived()).isEqualTo(4);
    assertThat(written.entriesFound()).isEqualTo(1);
    assertThat(written.entriesNotFound()).isEqualTo(1);
    assertThat(written.getBatches()).isEqualTo(2);
    assertThat(written.getLatencyMicros().stream().mapToLong(b -> b.count()).sum()).isEqualTo(2);
    assertThat(written.setLatencyMicros().stream().mapToLong(b -> b.count()).sum()).isEqualTo(2);
    assertThat(written.getBatchLatencyMicros()).isEqualTo(written.getLatencyMicros());
    assertThat(empty.entriesWritten()).isEqualTo(0);
    assertThat(session.statisticsJson(false)).contains("\"writes\":1");
    assertThat(session.finish(true)).isTrue();
    assertThat(session.put(key, value).get()).isFalse();
    assertThrows(ExecutionException.class, () -> session.get(key).get());
    var closed = session.getStats();
    assertThat(closed.entriesWritten()).isEqualTo(2);
    assertThat(closed.setBatches()).isEqualTo(3);
    assertThat(closed.keyBytesSent()).isEqualTo(9);
    assertThat(closed.valueBytesSent()).isEqualTo(12);
    assertThat(closed.getBatches()).isEqualTo(3);
    assertThat(closed.entriesNotFound()).isEqualTo(2);
    assertThat(written.setBatches()).isEqualTo(2);
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    try {
      assertThat(reader.get(key).get()).isEqualTo(value);
      assertThat(reader.put(key, value).get()).isFalse();
      var stats = reader.getStats();
      assertThat(stats.entriesFound()).isEqualTo(1);
      assertThat(stats.valueBytesReceived()).isEqualTo(4);
      assertThat(stats.entriesWritten()).isEqualTo(1);
      assertThat(stats.valueBytesSent()).isEqualTo(4);
      assertThat(reader.statisticsJson(false)).contains("\"writes\":0");
      assertThat(reader.deduplicatedWrites()).isEqualTo(1);
    } finally {
      reader.finish(false);
    }
  }

  @Test
  public void trafficCountsFailedWritesWithoutClaimingAcceptance() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var session = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("key");
    assertThat(session.put(key, new byte[] {1}).get()).isTrue();
    assertThat(session.put(key, new byte[] {2}).get()).isFalse();
    assertThat(session.failureReason()).isNotNull();
    assertThat(session.put(key, new byte[] {1}).get()).isFalse();
    var stats = session.getStats();
    assertThat(stats.entriesWritten()).isEqualTo(1);
    assertThat(stats.setBatches()).isEqualTo(3);
    assertThat(stats.valueBytesSent()).isEqualTo(3);
    assertThat(stats.keyBytesSent()).isEqualTo(9);
    assertThat(stats.setLatencyMicros().stream().mapToLong(b -> b.count()).sum()).isEqualTo(3);
    assertThat(session.finish(true)).isFalse();
  }

  @Test
  public void trafficConcurrentRequestsHaveExactTotalsAndIndependentSessions() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var session = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("key");
    try (var executor = Executors.newFixedThreadPool(4)) {
      var futures = new java.util.ArrayList<java.util.concurrent.Future<?>>();
      for (int i = 0; i < 40; i++) {
        futures.add(
            executor.submit(
                () -> {
                  session.put(key, new byte[] {1}).get();
                  assertThat(session.get(key).get()).isEqualTo(new byte[] {1});
                  return null;
                }));
      }
      for (var future : futures) {
        future.get();
      }
    }
    var stats = session.getStats();
    assertThat(stats.entriesWritten()).isEqualTo(40);
    assertThat(stats.valueBytesSent()).isEqualTo(40);
    assertThat(stats.keyBytesSent()).isEqualTo(120);
    assertThat(stats.entriesFound()).isEqualTo(40);
    assertThat(stats.valueBytesReceived()).isEqualTo(40);
    assertThat(stats.getBatches()).isEqualTo(40);
    assertThat(stats.setBatches()).isEqualTo(40);
    assertThat(stats.getLatencyMicros().stream().mapToLong(b -> b.count()).sum()).isEqualTo(40);
    assertThat(stats.setLatencyMicros().stream().mapToLong(b -> b.count()).sum()).isEqualTo(40);
    session.finish(false);
    var next = new LocalCacheSession(cache, snapshot(workspace, output));
    try {
      assertThat(next.getStats().entriesWritten()).isEqualTo(0);
      assertThat(next.getStats().getLatencyMicros()).isEmpty();
    } finally {
      next.finish(false);
    }
  }

  @Test
  public void admissionHintsDoNotReplaceFreshDiscoveryOrFinalChecks() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path file = temporary.newFile("external-input").toPath();
    Files.writeString(file, "one");
    var writer = snapshot(workspace, output);
    writer.observe(file, false);
    var witness = writer.witnesses().getFirst();
    var reader = snapshot(workspace, output);
    assertThat(reader.matchesForAdmission(witness)).isTrue();
    assertThat(reader.witnesses()).isEmpty();
    assertThat(reader.includeAdmittedWitness(witness)).isTrue();
    var modified = Files.getLastModifiedTime(file);
    Files.writeString(file, "two");
    Files.setLastModifiedTime(file, modified);
    assertThat(reader.matches(witness)).isFalse();
    assertThat(reader.isUnchanged()).isFalse();
    assertThat(reader.matchesForAdmission(witness)).isFalse();
    assertThat(reader.includeAdmittedWitness(witness)).isFalse();
    assertThat(snapshot(workspace, output).includeAdmittedWitness(witness)).isFalse();
  }

  @Test
  public void admissionHintsUseExactWitnessIdentityAndRetryFailures() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path file = temporary.newFile("external-input").toPath();
    Files.writeString(file, "one");
    var writer = snapshot(workspace, output);
    writer.observe(file, false);
    var witness = writer.witnesses().getFirst();
    Files.delete(file);
    var reader = snapshot(workspace, output);
    assertThat(reader.matchesForAdmission(witness)).isFalse();
    assertThat(reader.includeAdmittedWitness(witness)).isFalse();
    Files.writeString(file, "one");
    assertThat(reader.matchesForAdmission(witness)).isTrue();
    assertThat(
            reader.includeAdmittedWitness(
                new LocalInputSnapshot.Witness(witness.path(), true, witness.digest())))
        .isFalse();
    assertThat(
            reader.includeAdmittedWitness(
                new LocalInputSnapshot.Witness(witness.path(), false, "other-digest")))
        .isFalse();
    assertThat(
            reader.includeAdmittedWitness(
                new LocalInputSnapshot.Witness("absolute/other", false, witness.digest())))
        .isFalse();
    assertThat(reader.includeAdmittedWitness(witness)).isTrue();
    assertThat(reader.isUnchanged()).isTrue();
  }

  @Test
  public void concurrentAdmissionHintsRespectCapacityAndFallBackToFreshChecks() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    var writer = snapshot(workspace, output);
    for (int i = 0; i < 12; i++) {
      Path file = temporary.newFile("external-" + i).toPath();
      Files.writeString(file, "one");
      writer.observe(file, false);
    }
    var witnesses = writer.witnesses();
    var reader = new LocalInputSnapshot(workspace, output, Map.of(), 3);
    try (var executor = Executors.newFixedThreadPool(4)) {
      var futures = new java.util.ArrayList<java.util.concurrent.Future<Boolean>>();
      for (var witness : witnesses) {
        futures.add(executor.submit(() -> reader.matchesForAdmission(witness)));
      }
      for (var future : futures) {
        assertThat(future.get()).isTrue();
      }
    }
    assertThat(reader.witnesses()).isEmpty();
    int reused = 0;
    for (var witness : witnesses) {
      if (reader.includeAdmittedWitness(witness)) {
        reused++;
      } else {
        assertThat(reader.validateAndInclude(witness)).isTrue();
      }
    }
    assertThat(reused).isEqualTo(3);
    assertThat(reader.witnesses()).containsExactlyElementsIn(witnesses);
    assertThat(reader.isUnchanged()).isTrue();
  }

  @Test
  public void closingSessionReleasesAdmissionHintsButPreservesFinalEvidence() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path file = temporary.newFile("external-input").toPath();
    Files.writeString(file, "one");
    var writer = snapshot(workspace, output);
    writer.observe(file, false);
    var witness = writer.witnesses().getFirst();
    var inputs = snapshot(workspace, output);
    var session = new LocalCacheSession(cache, inputs);
    assertThat(inputs.matchesForAdmission(witness)).isTrue();
    assertThat(inputs.includeAdmittedWitness(witness)).isTrue();
    var beforeClose = session.ownershipSnapshot();
    assertThat(beforeClose.closed()).isFalse();
    assertThat(beforeClose.inputs().admittedWitnesses()).isEqualTo(1);
    assertThat(beforeClose.inputs().observedWitnesses()).isEqualTo(1);
    session.finish(false);
    var afterClose = session.ownershipSnapshot();
    assertThat(afterClose.closed()).isTrue();
    assertThat(afterClose.inputs().admittedWitnesses()).isEqualTo(0);
    assertThat(afterClose.inputs().observedWitnesses()).isEqualTo(1);
    assertThat(beforeClose.inputs().admittedWitnesses()).isEqualTo(1);
    assertThat(inputs.includeAdmittedWitness(witness)).isFalse();
    assertThat(inputs.witnesses()).containsExactly(witness);
    assertThat(inputs.isUnchanged()).isTrue();
    Files.writeString(file, "two");
    assertThat(inputs.isUnchanged()).isFalse();
  }

  @Test
  public void unusedCandidateAdmissionDoesNotBecomePublicationEvidence() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path file = temporary.newFile("unused-input").toPath();
    Files.writeString(file, "one");
    var firstInputs = snapshot(workspace, output);
    firstInputs.observe(file, false);
    var first = new LocalCacheSession(cache, firstInputs);
    first.put(new Key("old"), new byte[] {1}).get();
    assertThat(first.finish(true)).isTrue();
    var readerInputs = snapshot(workspace, output);
    var reader = new LocalCacheSession(cache, readerInputs);
    assertThrows(ExecutionException.class, () -> reader.get(new Key("missing")).get());
    var before = reader.ownershipSnapshot();
    assertThat(before.candidates()).isEqualTo(1);
    assertThat(before.candidateWitnessReferences()).isEqualTo(1);
    assertThat(before.candidateAccountedMetadataBytes()).isGreaterThan(0);
    assertThat(before.inspectedPublications()).isEqualTo(1);
    assertThat(before.inputs().admittedWitnesses()).isEqualTo(1);
    assertThat(before.inputs().observedWitnesses()).isEqualTo(0);
    assertThat(readerInputs.witnesses()).isEmpty();
    reader.put(new Key("new"), new byte[] {2}).get();
    Files.writeString(file, "two");
    assertThat(reader.finish(true)).isTrue();
    var after = reader.ownershipSnapshot();
    assertThat(after.closed()).isTrue();
    assertThat(after.candidates()).isEqualTo(0);
    assertThat(after.candidateWitnessReferences()).isEqualTo(0);
    assertThat(after.candidateAccountedMetadataBytes()).isEqualTo(0);
    assertThat(after.inspectedPublications()).isEqualTo(0);
    assertThat(after.inputs().admittedWitnesses()).isEqualTo(0);
    // The earlier endpoint is a detached value; sampling never consumed the unused witness.
    assertThat(before.candidates()).isEqualTo(1);
    assertThat(before.inputs().observedWitnesses()).isEqualTo(0);
    var next = new LocalCacheSession(cache, snapshot(workspace, output));
    try {
      assertThat(next.get(new Key("new")).get()).isEqualTo(new byte[] {2});
      assertThrows(ExecutionException.class, () -> next.get(new Key("old")).get());
    } finally {
      next.finish(false);
    }
  }

  @Test
  public void repositoryNamespaceSpansGitWorktreesButSeparatesProjectsAndEnvironments()
      throws Exception {
    Path a = temporary.newFolder("a").toPath();
    Path b = temporary.newFolder("b").toPath();
    Path output = temporary.newFolder("output").toPath();
    assertThat(snapshot(a, output).cacheNamespace())
        .isNotEqualTo(snapshot(b, output).cacheNamespace());
    Path common = temporary.newFolder("common").toPath();
    gitWorktree(a, common, "a");
    gitWorktree(b, common, "b");
    Files.writeString(a.resolve("feature"), "one");
    Files.writeString(b.resolve("feature"), "two");
    var first = snapshot(a, output);
    assertThat(first.sourceDigest()).isNotEqualTo(snapshot(b, output).sourceDigest());
    assertThat(first.cacheNamespace()).isEqualTo(snapshot(b, output).cacheNamespace());
    assertThat(new LocalInputSnapshot(a, output, Map.of("PATH", "different")).cacheNamespace())
        .isNotEqualTo(first.cacheNamespace());
    gitWorktree(b, temporary.newFolder("other-common").toPath(), "b");
    assertThat(first.cacheNamespace()).isNotEqualTo(snapshot(b, output).cacheNamespace());
    gitWorktree(a, temporary.newFolder("changed-common").toPath(), "a");
    assertThat(first.isUnchanged()).isFalse();
  }

  @Test
  public void gitMainCheckoutAndLinkedWorktreeShareNamespaceAndMalformedPointersFailClosed()
      throws Exception {
    Path main = temporary.newFolder("main").toPath();
    Path linked = temporary.newFolder("linked").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path common = main.resolve(".git");
    gitWorktree(linked, common, "linked");
    assertThat(snapshot(main, output).cacheNamespace())
        .isEqualTo(snapshot(linked, output).cacheNamespace());
    Files.writeString(linked.resolve(".git"), "unexpected pointer");
    assertThrows(IOException.class, () -> snapshot(linked, output));
    Files.writeString(linked.resolve(".git"), "gitdir: " + "x".repeat(17000));
    assertThrows(IOException.class, () -> snapshot(linked, output));
  }

  @Test
  public void dependencyEvidenceIsPortableAndContentSensitive() throws Exception {
    Path a = temporary.newFolder("a").toPath();
    Path b = temporary.newFolder("b").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path fileA = Files.writeString(a.resolve("file"), "one");
    Path fileB = Files.writeString(b.resolve("file"), "one");
    var inputsA = snapshot(a, output);
    var inputsB = snapshot(b, output);
    byte[] evidenceA =
        inputsA.captureDependency(fileA, false, java.util.List.of("parent-b", "parent-a"));
    byte[] evidenceB =
        inputsB.captureDependency(
            fileB, false, java.util.List.of("parent-a", "parent-b", "parent-a"));
    assertThat(evidenceA).isEqualTo(evidenceB);
    var decoded = LocalDependencyEvidence.decode(evidenceA);
    assertThat(decoded.parents()).containsExactly("parent-a", "parent-b").inOrder();
    assertThat(decoded.witnesses()).hasSize(1);
    assertThat(inputsB.matches(decoded.witnesses().getFirst())).isTrue();
    FileTime modified = Files.getLastModifiedTime(fileB);
    Files.writeString(fileB, "two");
    Files.setLastModifiedTime(fileB, modified);
    assertThat(inputsB.matches(decoded.witnesses().getFirst())).isFalse();
    assertThat(inputsB.captureDependency(fileB, false, decoded.parents())).isNotEqualTo(evidenceA);
    assertThat(inputsB.isUnchanged()).isFalse();
  }

  @Test
  public void dependencyEvidenceRetainsSymlinkResolutionAndDirectoryMembership() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Files.writeString(workspace.resolve("one"), "same");
    Files.writeString(workspace.resolve("two"), "same");
    Path link = Files.createSymbolicLink(workspace.resolve("link"), Path.of("one"));
    var inputs = snapshot(workspace, output);
    var linkEvidence =
        LocalDependencyEvidence.decode(inputs.captureDependency(link, false, java.util.List.of()));
    assertThat(linkEvidence.witnesses()).hasSize(2);
    for (var witness : linkEvidence.witnesses()) {
      assertThat(inputs.matches(witness)).isTrue();
    }
    Files.delete(link);
    Files.createSymbolicLink(link, Path.of("two"));
    assertThat(
            linkEvidence.witnesses().stream()
                .allMatch(
                    w -> {
                      try {
                        return inputs.matches(w);
                      } catch (IOException e) {
                        throw new java.io.UncheckedIOException(e);
                      }
                    }))
        .isFalse();
    var listing =
        LocalDependencyEvidence.decode(
            inputs.captureDependency(workspace, true, java.util.List.of()));
    Files.writeString(workspace.resolve("added"), "new");
    assertThat(inputs.matches(listing.witnesses().getFirst())).isFalse();
  }

  @Test
  public void dependencyEvidenceRejectsMalformedAndOversizedPayloads() throws Exception {
    var witness = new LocalInputSnapshot.Witness("workspace/file", false, "digest");
    byte[] valid =
        new LocalDependencyEvidence(java.util.List.of(), java.util.List.of(witness)).encode();
    assertThrows(
        IOException.class,
        () -> LocalDependencyEvidence.decode(java.util.Arrays.copyOf(valid, valid.length - 1)));
    assertThrows(
        IOException.class,
        () -> LocalDependencyEvidence.decode(java.util.Arrays.copyOf(valid, valid.length + 1)));
    assertThrows(
        IOException.class, () -> LocalDependencyEvidence.decode(new byte[1024 * 1024 + 1]));
    byte[] badCount = valid.clone();
    ByteBuffer.wrap(badCount).putInt(4, 513);
    assertThrows(IOException.class, () -> LocalDependencyEvidence.decode(badCount));
    assertThrows(
        IOException.class,
        () -> new LocalDependencyEvidence(java.util.List.of(), java.util.List.of()).encode());
  }

  @Test
  public void concurrentInputValidationKeepsHashBuffersExclusive() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path files = temporary.newFolder("inputs").toPath();
    var inputs = snapshot(workspace, output);
    for (int index = 0; index < 32; index++) {
      byte[] bytes = new byte[131072 + index * 17];
      for (int i = 0; i < bytes.length; i++) {
        bytes[i] = (byte) (i * 73 + index);
      }
      inputs.observe(Files.write(files.resolve("input-" + index), bytes), false);
    }
    var witnesses = inputs.witnesses();
    var barrier = new CyclicBarrier(24);
    var tasks = new java.util.ArrayList<java.util.concurrent.Callable<Void>>();
    for (int worker = 0; worker < 24; worker++) {
      int offset = worker;
      tasks.add(
          () -> {
            barrier.await();
            for (int round = 0; round < 3; round++) {
              for (int index = 0; index < witnesses.size(); index++) {
                assertThat(inputs.matches(witnesses.get((index + offset) % witnesses.size())))
                    .isTrue();
              }
            }
            return null;
          });
    }
    try (var executor = Executors.newFixedThreadPool(24)) {
      for (var future : executor.invokeAll(tasks)) {
        future.get();
      }
    }
  }

  @Test
  public void inputValidationChecksPartialFinalBufferAndDoesNotReuseOldBytes() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path files = temporary.newFolder("inputs").toPath();
    var inputs = snapshot(workspace, output);
    byte[] bytes = new byte[131073];
    java.util.Arrays.fill(bytes, (byte) 37);
    Path large = Files.write(files.resolve("large"), bytes);
    Path shortFile = Files.write(files.resolve("short"), new byte[] {12});
    Path empty = Files.write(files.resolve("empty"), new byte[0]);
    for (Path file : java.util.List.of(large, shortFile, empty)) {
      inputs.observe(file, false);
    }
    var witnesses = inputs.witnesses();
    for (int round = 0; round < 3; round++) {
      for (var witness : witnesses) {
        assertThat(inputs.matches(witness)).isTrue();
      }
    }
    FileTime modified = Files.getLastModifiedTime(large);
    bytes[bytes.length - 1]++;
    Files.write(large, bytes);
    Files.setLastModifiedTime(large, modified);
    for (var witness : witnesses) {
      assertThat(inputs.matches(witness)).isEqualTo(!witness.path().endsWith("/large"));
    }
  }

  @Test
  public void identicalPathsWithDifferentDigestsAreNotInterchangeable() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path file = temporary.newFile("input").toPath();
    Files.writeString(file, "one");
    var inputsA = snapshot(workspace, output);
    inputsA.observe(file, false);
    var first = new LocalCacheSession(cache, inputsA);
    first.put(new Key("a"), new byte[] {1}).get();
    assertThat(first.finish(true)).isTrue();
    Path publications =
        cache.resolve("leased-v2/publications-v4").resolve(inputsA.cacheNamespace());
    Path manifestA;
    try (var paths = Files.list(publications)) {
      manifestA = paths.findFirst().orElseThrow();
    }
    Files.writeString(file, "two");
    var inputsB = snapshot(workspace, output);
    inputsB.observe(file, false);
    var second = new LocalCacheSession(cache, inputsB);
    second.put(new Key("b"), new byte[] {2}).get();
    assertThat(second.finish(true)).isTrue();
    Path manifestB;
    try (var paths = Files.list(publications)) {
      manifestB = paths.filter(path -> !path.equals(manifestA)).findFirst().orElseThrow();
    }
    // Make the first candidate valid, so a cache keyed only by path would incorrectly admit the
    // next.
    boolean aFirst = manifestA.compareTo(manifestB) < 0;
    Files.writeString(file, aFirst ? "one" : "two");
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    try {
      assertThat(reader.get(new Key(aFirst ? "a" : "b")).get())
          .isEqualTo(new byte[] {(byte) (aFirst ? 1 : 2)});
      assertThrows(ExecutionException.class, () -> reader.get(new Key(aFirst ? "b" : "a")).get());
    } finally {
      reader.finish(false);
    }
  }

  @Test
  public void equalWitnessListsMustBeRevalidatedOnTheNextDiscoveryPass() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path file = temporary.newFile("input").toPath();
    Files.writeString(file, "one");
    var inputs = snapshot(workspace, output);
    inputs.observe(file, false);
    var first = new LocalCacheSession(cache, inputs);
    first.put(new Key("a"), new byte[] {1}).get();
    assertThat(first.finish(true)).isTrue();
    Path publications = cache.resolve("leased-v2/publications-v4").resolve(inputs.cacheNamespace());
    Path manifestA;
    try (var paths = Files.list(publications)) {
      manifestA = paths.findFirst().orElseThrow();
    }
    var inputsB = snapshot(workspace, output);
    inputsB.observe(file, false);
    var second = new LocalCacheSession(cache, inputsB);
    second.put(new Key("b"), new byte[] {2}).get();
    assertThat(second.finish(true)).isTrue();
    Path manifestB;
    try (var paths = Files.list(publications)) {
      manifestB = paths.filter(path -> !path.equals(manifestA)).findFirst().orElseThrow();
    }
    Path hidden = temporary.getRoot().toPath().resolve("hidden-publication");
    Files.move(manifestB, hidden);
    var clock = new AtomicLong();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output), clock::get);
    try {
      assertThat(reader.get(new Key("a")).get()).isEqualTo(new byte[] {1});
      FileTime modified = Files.getLastModifiedTime(file);
      Files.writeString(file, "two");
      Files.setLastModifiedTime(file, modified);
      Files.move(hidden, manifestB);
      clock.addAndGet(2_000_000_000L);
      assertThrows(ExecutionException.class, () -> reader.get(new Key("b")).get());
    } finally {
      reader.finish(false);
    }
  }

  @Test
  public void candidateCountLimitPreservesPrivateWrites() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    for (int i = 0; i < 3; i++) {
      var writer = new LocalCacheSession(cache, snapshot(workspace, output));
      writer.put(new Key("entry-" + i), new byte[] {(byte) i}).get();
      assertThat(writer.finish(true)).isTrue();
    }
    var reader =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            System::nanoTime,
            new LocalCacheSession.ReadLimits(1, 1024 * 1024, 100));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("missing")).get());
    // A missing key may consult an empty bucket without opening a data segment.
    assertThat(openPackDescriptors(cache)).isAtMost(1);
    assertThat(reader.statisticsJson(false)).contains("\"peak_candidate_count\":1");
    assertThat(reader.put(new Key("private"), new byte[] {7}).get()).isTrue();
    assertThat(reader.get(new Key("private")).get()).isEqualTo(new byte[] {7});
    assertThat(reader.finish(true)).isTrue();
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    var later = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(later.get(new Key("private")).get()).isEqualTo(new byte[] {7});
    later.finish(false);
  }

  @Test
  public void metadataBudgetIncludesManifestAndIndexAtExactBoundary() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var inputs = snapshot(workspace, output);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(new Key("entry"), new byte[] {1}).get();
    assertThat(writer.finish(true)).isTrue();
    Path manifest;
    try (var paths =
        Files.list(cache.resolve("leased-v2/publications-v4").resolve(inputs.cacheNamespace()))) {
      manifest = paths.findFirst().orElseThrow();
    }
    String id = manifest.getFileName().toString().replace(".manifest", "");
    Path format = cache.resolve("leased-v2/objects/segmented-v1");
    Path index = format.resolve(id).resolve("index");
    long bytes;
    try (var stored = SegmentedFingerprintValueStore.open(format, id, Long.MAX_VALUE)) {
      bytes = Files.size(manifest) + stored.indexBytes();
    }
    // The store also checks the opened channel's size, independently of a caller's preflight.
    assertThrows(
        IOException.class,
        () -> SegmentedFingerprintValueStore.open(format, id, Files.size(index) - 1));
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    var tooSmall =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            System::nanoTime,
            new LocalCacheSession.ReadLimits(1, bytes - 1, 100));
    assertThrows(ExecutionException.class, () -> tooSmall.get(new Key("entry")).get());
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    assertThat(tooSmall.statisticsJson(false)).contains("\"read_limit_skips\":1");
    tooSmall.finish(false);
    var exact =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            System::nanoTime,
            new LocalCacheSession.ReadLimits(1, bytes, 100));
    assertThat(exact.get(new Key("entry")).get()).isEqualTo(new byte[] {1});
    assertThat(exact.statisticsJson(false)).contains("\"peak_candidate_metadata_bytes\":" + bytes);
    exact.finish(false);
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
  }

  @Test
  public void candidateLimitNeverAdmitsAnIncompleteParentClosure() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(new Key("parent"), new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(child.get(new Key("parent")).get()).isEqualTo(new byte[] {1});
    child.put(new Key("child"), new byte[] {2}).get();
    assertThat(child.finish(true)).isTrue();
    var limited =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            System::nanoTime,
            new LocalCacheSession.ReadLimits(1, 1024 * 1024, 100));
    assertThrows(ExecutionException.class, () -> limited.get(new Key("child")).get());
    assertThat(openPackDescriptors(cache)).isAtMost(1);
    limited.finish(false);
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    var enough =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            System::nanoTime,
            new LocalCacheSession.ReadLimits(2, 1024 * 1024, 100));
    assertThat(enough.get(new Key("child")).get()).isEqualTo(new byte[] {2});
    enough.finish(false);
  }

  @Test
  public void oversizedDiscoveryPreservesPinnedReadersAndPrivateWrites() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var first = new LocalCacheSession(cache, snapshot(workspace, output));
    first.put(new Key("first"), new byte[] {1}).get();
    assertThat(first.finish(true)).isTrue();
    var clock = new AtomicLong();
    var reader =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            clock::get,
            new LocalCacheSession.ReadLimits(10, 1024 * 1024, 2));
    assertThat(reader.get(new Key("first")).get()).isEqualTo(new byte[] {1});
    for (int i = 0; i < 2; i++) {
      var writer = new LocalCacheSession(cache, snapshot(workspace, output));
      writer.put(new Key("new-" + i), new byte[] {(byte) i}).get();
      assertThat(writer.finish(true)).isTrue();
    }
    clock.addAndGet(2_000_000_000L);
    assertThrows(ExecutionException.class, () -> reader.get(new Key("new-0")).get());
    assertThat(reader.statisticsJson(false)).contains("\"scan_limit_hits\":1");
    assertThat(reader.get(new Key("first")).get()).isEqualTo(new byte[] {1});
    assertThat(openPackDescriptors(cache)).isEqualTo(1);
    assertThat(reader.put(new Key("private"), new byte[] {4}).get()).isTrue();
    assertThat(reader.finish(true)).isTrue();
    var later = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(later.get(new Key("private")).get()).isEqualTo(new byte[] {4});
    later.finish(false);
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
  }

  @Test
  public void zeroReadBudgetAllowsPrivatePublicationAndNegativeLimitsAreRejected()
      throws Exception {
    assertThrows(IllegalArgumentException.class, () -> new LocalCacheSession.ReadLimits(-1, 1, 1));
    assertThrows(IllegalArgumentException.class, () -> new LocalCacheSession.ReadLimits(1, -1, 1));
    assertThrows(IllegalArgumentException.class, () -> new LocalCacheSession.ReadLimits(1, 1, 0));
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            System::nanoTime,
            new LocalCacheSession.ReadLimits(0, 0, 1));
    assertThat(writer.put(new Key("private"), new byte[] {5}).get()).isTrue();
    assertThat(writer.get(new Key("private")).get()).isEqualTo(new byte[] {5});
    assertThat(writer.finish(true)).isTrue();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(reader.get(new Key("private")).get()).isEqualTo(new byte[] {5});
    reader.finish(false);
  }

  @Test
  public void discardedOptionalUploadCannotPublishEvenAfterSuccessfulBuild() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("entry");
    assertThat(writer.put(key, new byte[] {7}).get()).isTrue();
    assertThat(writer.discardFailedUpload("optional serialization failure")).isTrue();
    assertThat(writer.put(new Key("later"), new byte[] {8}).get()).isFalse();
    assertThat(writer.finish(true)).isFalse();
    assertThat(writer.failureReason()).isEqualTo("optional serialization failure");
    assertThat(writer.discardFailedUpload("after close")).isFalse();
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    reader.finish(false);
  }

  @Test
  public void failedPackSealCannotPublishAndClosesItsDescriptor() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var clock = new AtomicLong();
    var writer =
        new LocalCacheSession(
            cache,
            snapshot(workspace, output),
            () -> clock.getAndAdd(10),
            LocalCacheSession.ReadLimits.DEFAULT,
            /* profileFinalization= */ true);
    writer.put(new Key("entry"), new byte[] {7}).get();
    try (var paths = Files.walk(cache.resolve("leased-v2/objects"))) {
      Path pack =
          paths.filter(p -> p.getFileName().toString().endsWith(".pack")).findFirst().orElseThrow();
      Files.createDirectory(pack.getParent().resolve("index"));
    }
    assertThat(openPackDescriptors(cache)).isGreaterThan(0);
    clock.set(0);
    assertThrows(java.io.IOException.class, () -> writer.finish(true));
    assertThat(writer.finalizationPhasesJson())
        .contains(
            "\"phases\":{\"publication\":70,\"final_validation\":10,\"witness_manifest\":10,"
                + "\"seal\":10,\"local_install\":null,\"close_stores\":10}");
    assertThat(clock.get()).isEqualTo(100);
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    try (var paths = Files.walk(cache)) {
      assertThat(paths.anyMatch(p -> p.toString().endsWith(".manifest"))).isFalse();
    }
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("entry")).get());
    reader.finish(false);
  }

  @Test
  public void completedReadersAndRejectedCandidatesDoNotLeakPackDescriptors() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path external = Files.writeString(temporary.newFile("external").toPath(), "one");
    var inputs = snapshot(workspace, output);
    inputs.observe(external, false);
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    for (int i = 0; i < 10; i++) {
      var reader = new LocalCacheSession(cache, snapshot(workspace, output));
      assertThat(reader.get(key).get()).isEqualTo(new byte[] {7});
      assertThat(openPackDescriptors(cache)).isGreaterThan(0);
      reader.finish(false);
      assertThat(openPackDescriptors(cache)).isEqualTo(0);
    }
    Files.writeString(external, "two");
    for (int i = 0; i < 10; i++) {
      var rejected = new LocalCacheSession(cache, snapshot(workspace, output));
      assertThrows(ExecutionException.class, () -> rejected.get(key).get());
      assertThat(openPackDescriptors(cache)).isEqualTo(0);
      rejected.finish(false);
    }
  }

  private static long openPackDescriptors(Path cache) throws java.io.IOException {
    Path physicalCache = cache.resolve("leased-v2/objects").toRealPath();
    try (var descriptors = Files.list(Path.of("/proc/self/fd"))) {
      return descriptors
          .filter(
              path -> {
                try {
                  return Files.readSymbolicLink(path).startsWith(physicalCache);
                } catch (java.io.IOException e) {
                  return false; // An unrelated descriptor may close while /proc is being
                  // enumerated.
                }
              })
          .count();
    }
  }

  @Test
  public void sameContentsAcrossWorktreesHaveSameIdentityAndDirtyBytesInvalidate()
      throws Exception {
    Path a = temporary.newFolder("a").toPath();
    Path b = temporary.newFolder("b").toPath();
    Path output = temporary.newFolder("out").toPath();
    Files.writeString(a.resolve("ignored.txt"), "one");
    Files.writeString(b.resolve("ignored.txt"), "one");
    var first = snapshot(a, output);
    assertThat(snapshot(b, output).sourceDigest()).isEqualTo(first.sourceDigest());
    FileTime time = Files.getLastModifiedTime(b.resolve("ignored.txt"));
    Files.writeString(b.resolve("ignored.txt"), "two");
    Files.setLastModifiedTime(b.resolve("ignored.txt"), time);
    String second = snapshot(b, output).sourceDigest();
    assertThat(second).isNotEqualTo(first.sourceDigest());
    Files.writeString(b.resolve("ignored.txt"), "six");
    Files.setLastModifiedTime(b.resolve("ignored.txt"), time);
    assertThat(snapshot(b, output).sourceDigest()).isNotEqualTo(second);
    assertThat(new LocalInputSnapshot(a, output, Map.of("PATH", "changed")).sourceDigest())
        .isNotEqualTo(first.sourceDigest());
    Files.writeString(b.resolve("ignored.txt"), "one");
    assertThat(snapshot(b, output).sourceDigest()).isEqualTo(first.sourceDigest());
    Files.createDirectory(b.resolve("new-directory"));
    assertThat(snapshot(b, output).sourceDigest()).isNotEqualTo(first.sourceDigest());
  }

  @Test
  public void snapshotMetadataBatchingPreservesExistingDigestFormat() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    gitWorktree(workspace, temporary.newFolder("common").toPath(), "main");
    Files.createSymbolicLink(workspace.resolve("bazel-out"), output);
    Path plain = Files.writeString(workspace.resolve("plain"), "hello");
    Files.setPosixFilePermissions(
        plain, java.nio.file.attribute.PosixFilePermissions.fromString("rw-r-----"));
    Path nested = Files.createDirectory(workspace.resolve("nested"));
    Files.setPosixFilePermissions(
        nested, java.nio.file.attribute.PosixFilePermissions.fromString("rwxr-x---"));
    Path child = Files.writeString(nested.resolve("child"), "world");
    Files.setPosixFilePermissions(
        child, java.nio.file.attribute.PosixFilePermissions.fromString("rwx------"));
    Files.createSymbolicLink(workspace.resolve("link-file"), Path.of("plain"));
    Files.createSymbolicLink(workspace.resolve("link-dir"), Path.of("nested"));
    Files.createSymbolicLink(workspace.resolve("link-missing"), Path.of("missing"));

    var inputs = snapshot(workspace, output);
    // Fixed-format fixtures keep existing publications usable after metadata reads are combined.
    // Include followed directory aliases, dangling links, permissions and both root exclusions.
    assertThat(inputs.sourceDigest())
        .isEqualTo("d0736b4829a4b3802a0aec60bb4c7fdd6fff16c034029f60b3a76a234f3fe9da");
    assertThat(
            inputs.matches(
                new LocalInputSnapshot.Witness(
                    "workspace/plain",
                    false,
                    "e5e98c7730de038a0a92e36e02c76129476e1ad1a336d2bc9302a4536256eb26")))
        .isTrue();
    assertThat(
            inputs.matches(
                new LocalInputSnapshot.Witness(
                    "workspace/link-file",
                    false,
                    "4f23f0e3c1f2d5429133740fbde1ed7dd71905efb1d26354aedbf3290bd6ef36")))
        .isTrue();
    var listing =
        new LocalInputSnapshot.Witness(
            "workspace/nested",
            true,
            "897b70bffef195f70ee4979ff1cae83fb59fe7ae212f5d66c599c0a4f1a2fbdc");
    assertThat(inputs.matches(listing)).isTrue();
    assertThat(
            inputs.matches(
                new LocalInputSnapshot.Witness("workspace/link-dir", true, listing.digest())))
        .isTrue();
    assertThat(inputs.isUnchanged()).isTrue();
  }

  @Test
  public void combinedMetadataStillDetectsExternalSymlinkReferentModeChanges() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = Files.writeString(temporary.newFile("outside").toPath(), "same bytes");
    Files.setPosixFilePermissions(
        outside, java.nio.file.attribute.PosixFilePermissions.fromString("rw-------"));
    Files.createSymbolicLink(workspace.resolve("link"), outside);
    var inputs = snapshot(workspace, output);
    FileTime time = Files.getLastModifiedTime(outside);
    Files.setPosixFilePermissions(
        outside, java.nio.file.attribute.PosixFilePermissions.fromString("rwx------"));
    Files.setLastModifiedTime(outside, time);
    assertThat(inputs.isUnchanged()).isFalse();
  }

  @Test
  public void sourceSymlinkReferentsAndExecutableModesArePartOfIdentity() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.newFile("outside").toPath();
    Files.writeString(outside, "first");
    Files.createSymbolicLink(workspace.resolve("link"), outside);
    String first = snapshot(workspace, output).sourceDigest();
    Files.writeString(outside, "other");
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(first);
    Path file = Files.writeString(workspace.resolve("script"), "hello");
    String beforeMode = snapshot(workspace, output).sourceDigest();
    Files.setPosixFilePermissions(
        file, java.nio.file.attribute.PosixFilePermissions.fromString("rwx------"));
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(beforeMode);
  }

  @Test
  public void directorySymlinksIncludeReferentContentsAndRejectCycles() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.newFolder("outside").toPath();
    Path nested = Files.createDirectory(outside.resolve("nested"));
    Path source = Files.writeString(nested.resolve("source"), "one");
    Files.createSymbolicLink(workspace.resolve("linked"), outside);
    String before = snapshot(workspace, output).sourceDigest();
    Files.writeString(source, "two");
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(before);
    Files.createSymbolicLink(nested.resolve("cycle"), workspace);
    assertThrows(java.io.IOException.class, () -> snapshot(workspace, output));
  }

  @Test
  public void danglingSymlinkIsDifferentFromAnEmptyReferent() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.getRoot().toPath().resolve("missing");
    Files.createSymbolicLink(workspace.resolve("link"), outside);
    String dangling = snapshot(workspace, output).sourceDigest();
    Files.createFile(outside);
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(dangling);
  }

  @Test
  public void intermediateDirectorySymlinkRetargetingRejectsPublication() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path first = temporary.newFolder("first").toPath();
    Path second = temporary.newFolder("second").toPath();
    Path intermediate = temporary.getRoot().toPath().resolve("intermediate");
    Files.createSymbolicLink(intermediate, first);
    Path outer = Files.createDirectories(output.resolve("external")).resolve("sdk");
    Files.createSymbolicLink(outer, intermediate);
    var inputs = snapshot(workspace, output);
    inputs.observe(outer, false);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(new Key("entry"), new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var before = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(before.get(new Key("entry")).get()).isEqualTo(new byte[] {7});
    before.finish(false);
    // Outer link text and the referent's kind/mode remain identical.
    Files.delete(intermediate);
    Files.createSymbolicLink(intermediate, second);
    var after = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> after.get(new Key("entry")).get());
    after.finish(false);
  }

  @Test
  public void linkResolutionPreservesParentTraversalAfterIntermediateSymlink() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.newFolder("outside").toPath();
    Path actual = Files.createDirectories(outside.resolve("target/deeper"));
    Path file = Files.writeString(actual.getParent().resolve("file"), "actual");
    Files.writeString(outside.resolve("file"), "lexically normalized, incorrect");
    Files.createSymbolicLink(outside.resolve("inner"), actual);
    Path alias = outside.resolve("alias");
    Files.createSymbolicLink(alias, Path.of("inner/../file"));
    var inputs = snapshot(workspace, output);
    inputs.observe(alias, false);
    assertThat(inputs.witnesses().stream().map(LocalInputSnapshot.Witness::path).toList())
        .contains("absolute/" + file);
    assertThat(inputs.isUnchanged()).isTrue();
    Files.writeString(file, "edited");
    assertThat(inputs.isUnchanged()).isFalse();
  }

  @Test
  public void privateWritesRemainHiddenUntilSuccessfulPublication() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("key");
    writer.put(key, "value".getBytes(UTF_8)).get();
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    assertThat(writer.finish(true)).isTrue();
    // Discovery has a bounded one-second negative refresh interval.
    Thread.sleep(1100);
    assertThat(reader.get(key).get()).isEqualTo("value".getBytes(UTF_8));
    assertThat(reader.sharedReads()).isEqualTo(1);
    assertThat(reader.finish(true)).isFalse(); // A read-only command publishes no empty session.
  }

  @Test
  public void changedInputsAndFailedBuildsNeverPublish() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Files.writeString(workspace.resolve("input"), "before");
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    writer.put(new Key("changed"), new byte[] {1}).get();
    Files.writeString(workspace.resolve("input"), "after");
    assertThat(writer.finish(true)).isFalse();
    var failed = new LocalCacheSession(cache, snapshot(workspace, output));
    failed.put(new Key("failed"), new byte[] {2}).get();
    assertThat(failed.finish(false)).isFalse();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("changed")).get());
    assertThrows(ExecutionException.class, () -> reader.get(new Key("failed")).get());
    reader.finish(false);
  }

  @Test
  public void conflictingPutReportsWhyTheOptionalPublicationWasDiscarded() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("same-key");
    assertThat(writer.put(key, new byte[] {1}).get()).isTrue();
    assertThat(writer.put(key, new byte[] {2}).get()).isFalse();
    assertThat(writer.failureReason()).contains("conflicting immutable shared cache value");
    assertThat(writer.finish(true)).isFalse();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    reader.finish(false);
  }

  @Test
  public void externalInputsAndAbsentPathsAreValidatedAtLookup() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path external = Files.createDirectories(output.resolve("external/repo"));
    Path source = Files.writeString(external.resolve("source"), "one");
    var inputs = snapshot(workspace, output);
    inputs.observe(source, false);
    inputs.observe(external.resolve("missing"), false);
    inputs.observe(external, true);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(new Key("entry"), new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(reader.get(new Key("entry")).get()).isEqualTo(new byte[] {7});
    reader.finish(false);
    Files.writeString(source, "two");
    var edited = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> edited.get(new Key("entry")).get());
    edited.finish(false);
    Files.writeString(source, "one");
    Files.writeString(external.resolve("missing"), "now exists");
    var added = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> added.get(new Key("entry")).get());
    added.finish(false);
  }

  @Test
  public void bindingFirstSkipsUnrelatedContentAndRetriesRestoredBinding() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path oldRepo = temporary.newFolder("old-repo").toPath();
    Path newRepo = temporary.newFolder("new-repo").toPath();
    Path oldFile = Files.writeString(oldRepo.resolve("input"), "expected");
    Path unrelated = Files.writeString(temporary.newFile("unrelated").toPath(), "unchanged");
    Path link = Files.createDirectories(output.resolve("external")).resolve("repo");
    Files.createSymbolicLink(link, oldRepo);
    var inputs = snapshot(workspace, output);
    inputs.observe(unrelated, false);
    inputs.observe(oldFile, false);
    var unrelatedWitness =
        inputs.witnesses().stream()
            .filter(w -> w.path().equals("absolute/" + unrelated) && !w.listing())
            .findFirst()
            .orElseThrow();
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    Files.delete(link);
    Files.createSymbolicLink(link, newRepo);
    var readerInputs = snapshot(workspace, output);
    var reader = new LocalCacheSession(cache, readerInputs, () -> 0);
    try {
      assertThrows(ExecutionException.class, () -> reader.get(key).get());
      // A successful content check creates a hint; rejecting a binding must skip this work.
      assertThat(readerInputs.includeAdmittedWitness(unrelatedWitness)).isFalse();
      Files.delete(link);
      Files.createSymbolicLink(link, oldRepo);
      // Restoring the binding must retry immediately, even with the discovery clock frozen.
      assertThat(reader.get(key).get()).isEqualTo(new byte[] {7});
      assertThat(readerInputs.includeAdmittedWitness(unrelatedWitness)).isTrue();
    } finally {
      reader.finish(false);
    }
  }

  @Test
  public void bindingFirstStillRejectsChangedContentWithMatchingBinding() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path repository = temporary.newFolder("repository").toPath();
    Path file = Files.writeString(repository.resolve("input"), "one");
    Path link = Files.createDirectories(output.resolve("external")).resolve("repo");
    Files.createSymbolicLink(link, repository);
    var inputs = snapshot(workspace, output);
    inputs.observe(file, false);
    inputs.recordRepositoryBindings();
    var binding =
        inputs.witnesses().stream()
            .filter(w -> w.path().equals("external/repo") && !w.listing())
            .findFirst()
            .orElseThrow();
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var modified = Files.getLastModifiedTime(file);
    Files.writeString(file, "two");
    Files.setLastModifiedTime(file, modified);
    var readerInputs = snapshot(workspace, output);
    var reader = new LocalCacheSession(cache, readerInputs);
    try {
      assertThat(readerInputs.matches(binding)).isTrue();
      assertThrows(ExecutionException.class, () -> reader.get(key).get());
    } finally {
      reader.finish(false);
    }
  }

  @Test
  public void bindingFirstStillRejectsChangedListingWithMatchingBinding() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path repository = temporary.newFolder("repository").toPath();
    Files.writeString(repository.resolve("original"), "one");
    Path link = Files.createDirectories(output.resolve("external")).resolve("repo");
    Files.createSymbolicLink(link, repository);
    var inputs = snapshot(workspace, output);
    inputs.observe(repository, true);
    inputs.recordRepositoryBindings();
    var binding =
        inputs.witnesses().stream()
            .filter(w -> w.path().equals("external/repo") && !w.listing())
            .findFirst()
            .orElseThrow();
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    Files.writeString(repository.resolve("added"), "two");
    var readerInputs = snapshot(workspace, output);
    var reader = new LocalCacheSession(cache, readerInputs);
    try {
      assertThat(readerInputs.matches(binding)).isTrue();
      assertThrows(ExecutionException.class, () -> reader.get(key).get());
    } finally {
      reader.finish(false);
    }
  }

  @Test
  public void repositoryBindingMustMatchEvenWhenTheOldPhysicalContentsStillExist()
      throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path oldRepo = temporary.newFolder("old-repo").toPath();
    Path newRepo = temporary.newFolder("new-repo").toPath();
    Path oldFile = Files.writeString(oldRepo.resolve("BUILD"), "old");
    Files.writeString(newRepo.resolve("BUILD"), "new");
    Path link = Files.createDirectories(output.resolve("external")).resolve("repo");
    Files.createSymbolicLink(link, oldRepo);
    var inputs = snapshot(workspace, output);
    inputs.observe(oldFile, false);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(new Key("entry"), new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    Files.delete(link);
    Files.createSymbolicLink(link, newRepo);
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("entry")).get());
    reader.finish(false);
  }

  @Test
  public void inheritedPublicationRequiresItsParentClosure() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(new Key("chunk"), new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    Path parentManifest;
    try (var files = Files.walk(cache.resolve("leased-v2/publications-v4"))) {
      parentManifest =
          files.filter(p -> p.toString().endsWith(".manifest")).findFirst().orElseThrow();
    }
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(child.get(new Key("chunk")).get()).isEqualTo(new byte[] {1});
    child.put(new Key("root"), new byte[] {2}).get();
    assertThat(child.finish(true)).isTrue();
    Files.delete(parentManifest);
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("root")).get());
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    reader.finish(false);
  }

  @Test
  public void equalInheritedInputsStillRequireEveryParent() throws Exception {
    for (int missingParent = 0; missingParent < 2; missingParent++) {
      Path workspace = temporary.newFolder().toPath();
      Path output = temporary.newFolder().toPath();
      Path cache = temporary.newFolder().toPath();
      Path input = Files.writeString(temporary.newFile().toPath(), "one");
      var parents = new java.util.ArrayList<Path>();
      Path publications = null;
      for (int i = 0; i < 2; i++) {
        var inputs = snapshot(workspace, output);
        inputs.observe(input, false);
        var parent = new LocalCacheSession(cache, inputs);
        parent.put(new Key("parent-" + i), new byte[] {(byte) i}).get();
        assertThat(parent.finish(true)).isTrue();
        publications = cache.resolve("leased-v2/publications-v4").resolve(inputs.cacheNamespace());
        try (var paths = Files.list(publications)) {
          parents.add(paths.filter(path -> !parents.contains(path)).findFirst().orElseThrow());
        }
      }
      var child = new LocalCacheSession(cache, snapshot(workspace, output));
      for (int i = 0; i < 2; i++) {
        assertThat(child.get(new Key("parent-" + i)).get()).isEqualTo(new byte[] {(byte) i});
      }
      child.put(new Key("child"), new byte[] {3}).get();
      assertThat(child.finish(true)).isTrue();
      var complete = new LocalCacheSession(cache, snapshot(workspace, output));
      assertThat(complete.get(new Key("child")).get()).isEqualTo(new byte[] {3});
      complete.finish(false);
      Files.delete(parents.get(missingParent));
      var incomplete = new LocalCacheSession(cache, snapshot(workspace, output));
      assertThrows(ExecutionException.class, () -> incomplete.get(new Key("child")).get());
      incomplete.finish(false);
      assertThat(openPackDescriptors(cache)).isEqualTo(0);
    }
  }

  @Test
  public void unchangedInheritedWitnessesReuseBlocksButAdditionalEvidenceRegenerates()
      throws Exception {
    for (boolean additionalGlobal : java.util.List.of(false, true)) {
      Path workspace = temporary.newFolder().toPath();
      Path output = temporary.newFolder().toPath();
      Path cache = temporary.newFolder().toPath();
      Path input = Files.writeString(temporary.newFile().toPath(), "parent input");
      Path source = Files.writeString(workspace.resolve("source"), "source input");
      var parentInputs = snapshot(workspace, output);
      parentInputs.observe(input, false);
      var parent = new LocalCacheSession(cache, parentInputs);
      parent.put(new Key("parent"), new byte[] {1}).get();
      assertThat(parent.finish(true)).isTrue();
      var childInputs = snapshot(workspace, output);
      var child = new LocalCacheSession(cache, childInputs);
      assertThat(child.get(new Key("parent")).get()).isEqualTo(new byte[] {1});
      Path extra =
          additionalGlobal ? Files.writeString(temporary.newFile().toPath(), "new input") : source;
      childInputs.observe(extra, false);
      child.put(new Key("child"), new byte[] {2}).get();
      assertThat(child.finish(true)).isTrue();
      assertThat(child.statisticsJson(true))
          .contains("\"reused_witness_blocks\":" + (additionalGlobal ? 0 : 1));
      var header =
          LocalCacheSession.readManifestHeader(
              child.publicationManifest(), childInputs.cacheNamespace(), Long.MAX_VALUE);
      Path directory = cache.resolve("leased-v2/objects/segmented-v1").resolve(header.id());
      assertThat(LocalWitnessTable.read(directory, header.blocks(), Long.MAX_VALUE))
          .isEqualTo(childInputs.globalWitnesses());
      var reader = new LocalCacheSession(cache, snapshot(workspace, output));
      assertThat(reader.get(new Key("child")).get()).isEqualTo(new byte[] {2});
      reader.finish(false);
    }
  }

  @Test
  public void damagedParentWitnessCopyFallsBackAfterFreshValidation() throws Exception {
    Path workspace = temporary.newFolder().toPath();
    Path output = temporary.newFolder().toPath();
    Path cache = temporary.newFolder().toPath();
    Path input = Files.writeString(temporary.newFile().toPath(), "input");
    var parentInputs = snapshot(workspace, output);
    parentInputs.observe(input, false);
    var parent = new LocalCacheSession(cache, parentInputs);
    parent.put(new Key("parent"), new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    var childInputs = snapshot(workspace, output);
    var child = new LocalCacheSession(cache, childInputs);
    assertThat(child.get(new Key("parent")).get()).isEqualTo(new byte[] {1});
    child.put(new Key("child"), new byte[] {2}).get();
    var parentHeader =
        LocalCacheSession.readManifestHeader(
            parent.publicationManifest(), parentInputs.cacheNamespace(), Long.MAX_VALUE);
    Path objects = cache.resolve("leased-v2/objects/segmented-v1");
    Files.writeString(
        objects.resolve(parentHeader.id()).resolve(LocalWitnessTable.filename(0)), "bad");
    assertThat(child.finish(true)).isTrue();
    assertThat(child.statisticsJson(true)).contains("\"reused_witness_blocks\":0");
    var childHeader =
        LocalCacheSession.readManifestHeader(
            child.publicationManifest(), childInputs.cacheNamespace(), Long.MAX_VALUE);
    assertThat(
            LocalWitnessTable.read(
                objects.resolve(childHeader.id()), childHeader.blocks(), Long.MAX_VALUE))
        .isEqualTo(childInputs.globalWitnesses());
  }

  @Test
  public void globalWitnessReuseRequiresExactUniqueTypedEvidence() throws Exception {
    var inputs = snapshot(temporary.newFolder().toPath(), temporary.newFolder().toPath());
    var a = new LocalInputSnapshot.Witness("absolute/a", false, "a".repeat(64));
    var b = new LocalInputSnapshot.Witness("absolute/b", true, "missing");
    inputs.registerInheritedForFinalValidation(java.util.List.of(a, b));
    assertThat(inputs.hasSameGlobalWitnesses(java.util.List.of(a, b))).isTrue();
    assertThat(inputs.hasSameGlobalWitnesses(java.util.List.of(a, a))).isFalse();
    assertThat(inputs.hasSameGlobalWitnesses(java.util.List.of(b, a))).isFalse();
    assertThat(inputs.hasSameGlobalWitnesses(java.util.List.of(a))).isFalse();
    assertThat(
            inputs.hasSameGlobalWitnesses(
                java.util.List.of(a, new LocalInputSnapshot.Witness(b.path(), false, b.digest()))))
        .isFalse();
    assertThat(
            inputs.hasSameGlobalWitnesses(
                java.util.List.of(
                    a, new LocalInputSnapshot.Witness(b.path(), b.listing(), "not-directory"))))
        .isFalse();
    inputs.registerInheritedForFinalValidation(
        java.util.List.of(new LocalInputSnapshot.Witness("workspace/source", false, "missing")));
    assertThat(inputs.hasSameGlobalWitnesses(java.util.List.of(a, b))).isTrue();
    assertThat(inputs.hasSameGlobalWitnesses(inputs.witnesses())).isFalse();
    inputs.registerInheritedForFinalValidation(
        java.util.List.of(new LocalInputSnapshot.Witness(a.path(), a.listing(), "b".repeat(64))));
    assertThat(inputs.hasSameGlobalWitnesses(java.util.List.of(a, b))).isFalse();
  }

  @Test
  public void inheritedInputChangeBeforePublicationIsRejectedDespiteEqualLists() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path input = Files.writeString(temporary.newFile("input").toPath(), "one");
    for (int i = 0; i < 2; i++) {
      var inputs = snapshot(workspace, output);
      inputs.observe(input, false);
      var parent = new LocalCacheSession(cache, inputs);
      parent.put(new Key("parent-" + i), new byte[] {(byte) i}).get();
      assertThat(parent.finish(true)).isTrue();
    }
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    for (int i = 0; i < 2; i++) {
      assertThat(child.get(new Key("parent-" + i)).get()).isEqualTo(new byte[] {(byte) i});
    }
    child.put(new Key("child"), new byte[] {3}).get();
    FileTime modified = Files.getLastModifiedTime(input);
    Files.writeString(input, "two");
    Files.setLastModifiedTime(input, modified);
    assertThat(child.finish(true)).isFalse();
    assertThat(child.failureReason()).isEqualTo("inputs changed during the invocation");
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    // Restore the parent evidence. The unpublished child must still remain unavailable.
    Files.writeString(input, "one");
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("child")).get());
    reader.finish(false);
  }

  @Test
  public void overlappingInheritedListsStillValidateEveryUniqueInputAndListing() throws Exception {
    for (String mutation : java.util.List.of("shared", "unique-0", "unique-1", "listing")) {
      Path workspace = temporary.newFolder().toPath();
      Path output = temporary.newFolder().toPath();
      Path cache = temporary.newFolder().toPath();
      Path external = temporary.newFolder().toPath();
      Path shared = Files.writeString(external.resolve("shared"), "one");
      Path directory = Files.createDirectory(external.resolve("directory"));
      for (int i = 0; i < 2; i++) {
        Path unique = Files.writeString(external.resolve("unique-" + i), "one");
        var inputs = snapshot(workspace, output);
        inputs.observe(shared, false);
        inputs.observe(unique, false);
        inputs.observe(directory, true);
        var parent = new LocalCacheSession(cache, inputs);
        parent.put(new Key("parent-" + i), new byte[] {(byte) i}).get();
        assertThat(parent.finish(true)).isTrue();
      }
      var child = new LocalCacheSession(cache, snapshot(workspace, output));
      for (int i = 0; i < 2; i++) {
        assertThat(child.get(new Key("parent-" + i)).get()).isEqualTo(new byte[] {(byte) i});
      }
      child.put(new Key("child"), new byte[] {3}).get();
      if (mutation.equals("listing")) {
        Files.writeString(directory.resolve("added"), "new member");
      } else {
        Path changed = external.resolve(mutation);
        FileTime modified = Files.getLastModifiedTime(changed);
        Files.writeString(changed, "two");
        Files.setLastModifiedTime(changed, modified);
      }
      assertThat(child.finish(true)).isFalse();
      assertThat(child.failureReason()).isEqualTo("inputs changed during the invocation");
      assertThat(publicationIds(cache)).hasSize(2);
      assertThat(openPackDescriptors(cache)).isEqualTo(0);
    }
  }

  @Test
  public void conflictingInheritedEvidenceCannotBeHiddenByDuplicateRegistration() throws Exception {
    Path workspace = temporary.newFolder().toPath();
    Path output = temporary.newFolder().toPath();
    Path input = Files.writeString(temporary.newFile().toPath(), "one");
    var first = snapshot(workspace, output);
    first.observe(input, false);
    Files.writeString(input, "two");
    var second = snapshot(workspace, output);
    second.observe(input, false);
    var child = snapshot(workspace, output);
    child.registerInheritedForFinalValidation(first.witnesses());
    child.registerInheritedForFinalValidation(second.witnesses());
    child.registerInheritedForFinalValidation(first.witnesses());
    Files.writeString(input, "one");
    assertThat(child.isUnchanged()).isFalse();
  }

  @Test
  public void identicalPublishedPutsDoNotCreatePrivateFilesOrEmptyPublications() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var key = new Key("chunk");
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(key, new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    long filesBefore;
    try (var files = Files.walk(cache)) {
      filesBefore = files.count();
    }
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(reader.put(key, new byte[] {1}).get()).isFalse();
    assertThat(reader.put(key, new byte[] {1}).get()).isFalse();
    assertThat(reader.deduplicatedWrites()).isEqualTo(2);
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {1});
    assertThat(reader.finish(true)).isFalse();
    assertThat(reader.failureReason()).isNull();
    try (var files = Files.walk(cache)) {
      assertThat(files.count()).isEqualTo(filesBefore);
    }
  }

  @Test
  public void deduplicatedPutRequiresParentClosureWithoutAnExplicitRead() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(new Key("chunk"), new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    Path parentManifest;
    try (var files = Files.walk(cache.resolve("leased-v2/publications-v4"))) {
      parentManifest =
          files.filter(p -> p.toString().endsWith(".manifest")).findFirst().orElseThrow();
    }
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(child.put(new Key("chunk"), new byte[] {1}).get()).isFalse();
    assertThat(child.put(new Key("root"), new byte[] {2}).get()).isTrue();
    assertThat(child.finish(true)).isTrue();
    Files.delete(parentManifest);
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("root")).get());
    assertThat(openPackDescriptors(cache)).isEqualTo(0);
    reader.finish(false);
  }

  @Test
  public void identicalBytesFromAnInvalidPublicationAreWrittenPrivately() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path source = Files.writeString(temporary.newFile("external-input").toPath(), "one");
    var inputs = snapshot(workspace, output);
    inputs.observe(source, false);
    var parent = new LocalCacheSession(cache, inputs);
    var key = new Key("chunk");
    parent.put(key, new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    Files.writeString(source, "two");
    var changedInputs = snapshot(workspace, output);
    changedInputs.observe(source, false);
    var child = new LocalCacheSession(cache, changedInputs);
    assertThat(child.put(key, new byte[] {1}).get()).isTrue();
    assertThat(child.deduplicatedWrites()).isEqualTo(0);
    assertThat(child.finish(true)).isTrue();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {1});
    reader.finish(false);
  }

  @Test
  public void conflictingPutAfterSharedPutDiscardsPublication() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var key = new Key("chunk");
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(key, new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(child.put(key, new byte[] {1}).get()).isFalse();
    assertThat(child.put(key, new byte[] {2}).get()).isFalse();
    assertThat(child.failureReason()).contains("conflicting immutable shared cache value");
    var beforeClose = child.ownershipSnapshot();
    assertThat(beforeClose.sharedPuts()).isEqualTo(1);
    assertThat(beforeClose.usedParents()).isEqualTo(1);
    assertThat(child.finish(true)).isFalse();
    var afterClose = child.ownershipSnapshot();
    assertThat(afterClose.sharedPuts()).isEqualTo(0);
    assertThat(afterClose.usedParents()).isEqualTo(0);
    assertThat(beforeClose.sharedPuts()).isEqualTo(1);
  }

  @Test
  public void concurrentSharedAndConflictingPutsNeverPublish() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var key = new Key("chunk");
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(key, new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    var barrier = new CyclicBarrier(2);
    try (var executor = Executors.newFixedThreadPool(2)) {
      var first =
          executor.submit(
              () -> {
                barrier.await();
                return child.put(key, new byte[] {1}).get();
              });
      var second =
          executor.submit(
              () -> {
                barrier.await();
                return child.put(key, new byte[] {2}).get();
              });
      first.get();
      second.get();
    }
    assertThat(child.failureReason()).contains("conflicting immutable shared cache value");
    assertThat(child.finish(true)).isFalse();
  }

  @Test
  public void sharedPutPinsTheMatchingValueWhenOtherPublicationsUseTheSameKey() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var key = new Key("chunk");
    var first = new LocalCacheSession(cache, snapshot(workspace, output));
    var second = new LocalCacheSession(cache, snapshot(workspace, output));
    first.put(key, new byte[] {1}).get();
    second.put(key, new byte[] {2}).get();
    assertThat(first.finish(true)).isTrue();
    assertThat(second.finish(true)).isTrue();
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    byte[] initial = child.get(key).get();
    byte[] wanted = new byte[] {(byte) (initial[0] == 1 ? 2 : 1)};
    assertThat(child.put(key, wanted).get()).isFalse();
    assertThat(child.deduplicatedWrites()).isEqualTo(1);
    assertThat(child.get(key).get()).isEqualTo(wanted);
    assertThat(child.finish(true)).isFalse();
  }

  @Test
  public void damagedSharedPutCanBeRepairedButCannotChangeValue() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var key = new Key("chunk");
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(key, new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(child.put(key, new byte[] {1}).get()).isFalse();
    try (var files = Files.walk(cache.resolve("leased-v2/objects"))) {
      Path entry =
          files.filter(p -> p.getFileName().toString().endsWith(".pack")).findFirst().orElseThrow();
      Files.write(entry, new byte[] {0});
    }
    assertThrows(ExecutionException.class, () -> child.get(key).get());
    assertThat(child.put(key, new byte[] {1}).get()).isTrue();
    assertThat(child.get(key).get()).isEqualTo(new byte[] {1});
    assertThat(child.put(key, new byte[] {2}).get()).isFalse();
    assertThat(child.failureReason()).contains("conflicting immutable shared cache value");
    assertThat(child.finish(true)).isFalse();
  }

  @Test
  public void repositoryMaterializationRetriesDiscoveryWithoutWaitingForTimer() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path writerOutput = temporary.newFolder("writer-output").toPath();
    Path readerOutput = temporary.newFolder("reader-output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path writerRepo = Files.createDirectories(writerOutput.resolve("external/repo"));
    Path file = Files.writeString(writerRepo.resolve("input"), "expected");
    var inputs = snapshot(workspace, writerOutput);
    inputs.observe(file, false);
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var reader = new LocalCacheSession(cache, snapshot(workspace, readerOutput), () -> 0);
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    Path readerRepo = Files.createDirectories(readerOutput.resolve("external/repo"));
    Files.writeString(readerRepo.resolve("input"), "expected");
    // The repository now exists; the clock deliberately never advances past the negative TTL.
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {7});
    assertThat(reader.sharedReads()).isEqualTo(1);
    reader.finish(false);
  }

  @Test
  public void initialDiscoverySupportsNegativeMonotonicClockValues() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output), () -> -1_000_000_000L);
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {7});
    reader.finish(false);
  }

  @Test
  public void materializedRepositoryMustStillMatchAndUnchangedMismatchesRemainThrottled()
      throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path writerOutput = temporary.newFolder("writer-output").toPath();
    Path readerOutput = temporary.newFolder("reader-output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path file =
        Files.writeString(
            Files.createDirectories(writerOutput.resolve("external/repo")).resolve("input"),
            "expected");
    var inputs = snapshot(workspace, writerOutput);
    inputs.observe(file, false);
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var clock = new AtomicLong();
    var reader = new LocalCacheSession(cache, snapshot(workspace, readerOutput), clock::get);
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    Path materialized =
        Files.writeString(
            Files.createDirectories(readerOutput.resolve("external/repo")).resolve("input"),
            "different");
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    assertThat(reader.statisticsJson(false)).contains("\"rejected_publications\":2");
    // An unchanged mismatch does not trigger full validation on every cache miss.
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    assertThat(reader.statisticsJson(false)).contains("\"rejected_publications\":2");
    Files.writeString(materialized, "expected");
    // Regeneration is also a retry hint, even though the path was already present.
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {7});
    reader.finish(false);
  }

  @Test
  public void negativeRefreshIntervalStartsAfterSlowValidationCompletes() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path file = Files.writeString(temporary.newFile("external").toPath(), "expected");
    var inputs = snapshot(workspace, output);
    inputs.observe(file, false);
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    Files.writeString(file, "mismatch");
    var clock = new AtomicLong();
    // Two seconds elapse during the first validation. No time elapses between later lookups.
    var reader =
        new LocalCacheSession(
            cache, snapshot(workspace, output), () -> clock.getAndSet(2_000_000_000L));
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    assertThat(reader.statisticsJson(false)).contains("\"rejected_publications\":1");
    var rejectedOwnership = reader.ownershipSnapshot();
    assertThat(rejectedOwnership.rejectedInputs()).isEqualTo(1);
    assertThat(rejectedOwnership.rejectedWitnesses()).isEqualTo(1);
    reader.finish(false);
    assertThat(reader.ownershipSnapshot().rejectedInputs()).isEqualTo(0);
    assertThat(reader.ownershipSnapshot().rejectedWitnesses()).isEqualTo(0);
    assertThat(rejectedOwnership.rejectedWitnesses()).isEqualTo(1);
  }

  @Test
  public void repairingPreviousMismatchStillValidatesEveryOtherWitness() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path first = Files.writeString(temporary.newFile("a-first").toPath(), "first");
    Path last = Files.writeString(temporary.newFile("z-last").toPath(), "last");
    var inputs = snapshot(workspace, output);
    inputs.observe(first, false);
    inputs.observe(last, false);
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    Files.delete(last);
    var clock = new AtomicLong();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output), clock::get);
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    Files.writeString(first, "wrong");
    Files.writeString(last, "last");
    clock.set(2_000_000_000L);
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    Files.writeString(first, "first");
    clock.set(4_000_000_000L);
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {7});
    reader.finish(false);
  }

  @Test
  public void unchangedMetadataStillGetsFullValidationAfterNegativeTtl() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path writerOutput = temporary.newFolder("writer-output").toPath();
    Path readerOutput = temporary.newFolder("reader-output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path file =
        Files.writeString(
            Files.createDirectories(writerOutput.resolve("external/repo")).resolve("input"),
            "expected");
    var inputs = snapshot(workspace, writerOutput);
    inputs.observe(file, false);
    var writer = new LocalCacheSession(cache, inputs);
    var key = new Key("entry");
    writer.put(key, new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    Path materialized =
        Files.writeString(
            Files.createDirectories(readerOutput.resolve("external/repo")).resolve("input"),
            "badvalue");
    FileTime time = Files.getLastModifiedTime(materialized);
    var clock = new AtomicLong();
    var reader = new LocalCacheSession(cache, snapshot(workspace, readerOutput), clock::get);
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    Files.writeString(materialized, "expected");
    Files.setLastModifiedTime(materialized, time);
    // Size, mtime and inode are unchanged. The periodic full check must remain available.
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    clock.set(1_000_000_001L);
    assertThat(reader.get(key).get()).isEqualTo(new byte[] {7});
    reader.finish(false);
  }

  @Test
  public void segmentedLayoutLeavesPreviousLeasedCacheUntouched() throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    Path old = Files.createDirectories(cache.resolve("leased-v1/publications-v3"));
    Path sentinel = Files.writeString(old.resolve("old.manifest"), "old-format-sentinel");
    var writer =
        new LocalCacheSession(
            cache,
            snapshot(
                temporary.newFolder("workspace").toPath(), temporary.newFolder("output").toPath()));
    writer.put(new Key("new"), new byte[] {1}).get();
    assertThat(writer.finish(true)).isTrue();
    new LocalCacheGarbageCollector(cache, new LocalCacheGarbageCollector.Policy(1, 0)).run();
    assertThat(Files.readString(sentinel)).isEqualTo("old-format-sentinel");
  }
}
