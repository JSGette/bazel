// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService.NONPROD_FINGERPRINTER;

import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.Fingerprinter;
import com.google.devtools.build.lib.skyframe.serialization.InMemoryFingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.StringKey;
import com.google.protobuf.CodedOutputStream;
import java.io.ByteArrayOutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalDependencyValidatorTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();
  private final CompressionServiceImpl compression = new CompressionServiceImpl();
  private final InMemoryFingerprintValueStore store = new InMemoryFingerprintValueStore();
  private Path workspace;
  private Path output;
  private Path file;
  private LocalInputSnapshot writer;

  @Before
  public void setUp() throws Exception {
    workspace = temporary.newFolder("workspace").toPath();
    output = temporary.newFolder("output").toPath();
    Files.createDirectory(workspace.resolve("dir"));
    file = Files.writeString(workspace.resolve("dir/file"), "one");
    writer = snapshot();
  }

  private LocalInputSnapshot snapshot() throws Exception {
    return new LocalInputSnapshot(workspace, output, Map.of());
  }

  private LocalDependencyValidator validator() throws Exception {
    return new LocalDependencyValidator(store, snapshot(), NONPROD_FINGERPRINTER, compression);
  }

  private StringKey leaf(Path path, boolean listing, List<String> parents) throws Exception {
    byte[] bytes = writer.captureDependency(path, listing, parents);
    var key = new StringKey("local-input-v1/" + NONPROD_FINGERPRINTER.fingerprint(bytes).toHex());
    store.put(key, bytes).get();
    return key;
  }

  private byte[] bytes(
      List<PackedFingerprint> nested,
      List<StringKey> files,
      List<StringKey> listings,
      StringKey source)
      throws Exception {
    var bytes = new ByteArrayOutputStream();
    var out = CodedOutputStream.newInstance(bytes);
    out.writeInt32NoTag(nested.size());
    out.writeInt32NoTag(files.size());
    out.writeInt32NoTag(listings.size());
    out.writeBoolNoTag(source != null);
    for (var node : nested) {
      node.writeTo(out);
    }
    for (var file : files) {
      out.writeStringNoTag(file.key());
    }
    for (var listing : listings) {
      out.writeStringNoTag(listing.key());
    }
    if (source != null) {
      out.writeStringNoTag(source.key());
    }
    out.flush();
    return bytes.toByteArray();
  }

  private byte[] compressed(byte[] bytes) throws Exception {
    var output = new ByteArrayOutputStream();
    output.write(0x80);
    output.write(0);
    try (var stream = compression.newZstdOutputStream(output)) {
      stream.write(bytes);
    }
    return output.toByteArray();
  }

  private PackedFingerprint put(byte[] bytes) throws Exception {
    var key = NONPROD_FINGERPRINTER.fingerprint(bytes);
    store.put(key, bytes).get();
    return key;
  }

  private PackedFingerprint root(StringKey leaf) throws Exception {
    return put(bytes(List.of(), List.of(leaf), List.of(), null));
  }

  private void shareRepository(Path a, Path b) throws Exception {
    Path common = temporary.newFolder().toPath();
    Files.createDirectories(common.resolve("objects"));
    Files.writeString(common.resolve("HEAD"), "ref: refs/heads/main\n");
    int index = 0;
    for (Path worktree : List.of(a, b)) {
      Path git = Files.createDirectories(common.resolve("worktrees/" + index++));
      Files.writeString(git.resolve("commondir"), "../..\n");
      Files.writeString(worktree.resolve(".git"), "gitdir: " + git + "\n");
    }
  }

  private LocalAnalysisCacheClient client(LocalCacheSession session, LocalInputSnapshot inputs) {
    return new LocalAnalysisCacheClient(
        session, new LocalDependencyValidator(session, inputs, NONPROD_FINGERPRINTER, compression));
  }

  private void writeEntry(
      LocalCacheSession session,
      LocalInputSnapshot inputs,
      Path dependency,
      PackedFingerprint key,
      byte value)
      throws Exception {
    byte[] evidence = inputs.captureDependency(dependency, false, List.of());
    var leaf =
        new StringKey("local-input-v1/" + NONPROD_FINGERPRINTER.fingerprint(evidence).toHex());
    session.put(leaf, evidence).get();
    byte[] node = bytes(List.of(), List.of(leaf), List.of(), null);
    var root = NONPROD_FINGERPRINTER.fingerprint(node);
    session.put(root, node).get();
    var buffer = new ByteArrayOutputStream();
    var coded = CodedOutputStream.newInstance(buffer);
    coded.writeEnumNoTag(4);
    root.writeTo(coded);
    coded.writeRawByte(value);
    coded.flush();
    session.put(key, buffer.toByteArray()).get();
  }

  @Test
  public void newerValidRevisionCannotKeepAnOlderImportedValueInRam() throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    var key = NONPROD_FINGERPRINTER.fingerprint(new byte[] {68});
    var oldInputs = snapshot();
    var writer = new LocalCacheSession(cache, oldInputs);
    writeEntry(writer, oldInputs, file, key, (byte) 1);
    assertThat(writer.finish(true)).isTrue();
    var initialInputs = snapshot();
    var initialSession = new LocalCacheSession(cache, initialInputs);
    var initialClient = client(initialSession, initialInputs);
    assertThat(initialClient.lookup(key.toBytes()).get().value()).isEqualTo(new byte[] {1});
    var retained = initialClient.retainedRoots();
    initialSession.finish(false);
    Files.writeString(file, "two");
    var newInputs = snapshot();
    var newWriter = new LocalCacheSession(cache, newInputs);
    writeEntry(newWriter, newInputs, file, key, (byte) 2);
    assertThat(newWriter.finish(true)).isTrue();
    var nextInputs = snapshot();
    var nextSession = new LocalCacheSession(cache, nextInputs);
    var nextClient =
        new LocalAnalysisCacheClient(
            nextSession,
            new LocalDependencyValidator(
                nextSession, nextInputs, NONPROD_FINGERPRINTER, compression),
            retained);
    assertThat(nextClient.isPreviouslyLoadedValueValid(key.toBytes()).get()).isFalse();
    assertThat(nextClient.lookup(key.toBytes()).get().value()).isEqualTo(new byte[] {2});
    // Invalidation always refers to the previous command, even after a concurrent new download.
    assertThat(nextClient.isPreviouslyLoadedValueValid(key.toBytes()).get()).isFalse();
    retained = nextClient.retainedRoots();
    nextSession.finish(false);
    var finalInputs = snapshot();
    var finalSession = new LocalCacheSession(cache, finalInputs);
    var finalClient =
        new LocalAnalysisCacheClient(
            finalSession,
            new LocalDependencyValidator(
                finalSession, finalInputs, NONPROD_FINGERPRINTER, compression),
            retained);
    try {
      assertThat(finalClient.isPreviouslyLoadedValueValid(key.toBytes()).get()).isTrue();
      assertThat(finalClient.isPreviouslyLoadedValueValid(new byte[] {9}).get()).isFalse();
    } finally {
      finalSession.finish(false);
    }
    assertThat(finalClient.isPreviouslyLoadedValueValid(key.toBytes()).get()).isFalse();
  }

  @Test
  public void retainedRootCapacityEvictsToSafeInvalidationMisses() throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    var inputs = snapshot();
    var session = new LocalCacheSession(cache, inputs);
    var client =
        new LocalAnalysisCacheClient(
            session,
            new LocalDependencyValidator(session, inputs, NONPROD_FINGERPRINTER, compression),
            Map.of(),
            1);
    var first = NONPROD_FINGERPRINTER.fingerprint(new byte[] {69});
    var second = NONPROD_FINGERPRINTER.fingerprint(new byte[] {70});
    writeEntry(session, inputs, file, first, (byte) 1);
    writeEntry(session, inputs, file, second, (byte) 2);
    assertThat(client.lookup(first.toBytes()).get().value()).isEqualTo(new byte[] {1});
    assertThat(client.lookup(second.toBytes()).get().value()).isEqualTo(new byte[] {2});
    assertThat(client.retainedRoots()).hasSize(1);
    assertThat(client.retainedRoots())
        .doesNotContainKey(com.google.protobuf.ByteString.copyFrom(first.toBytes()));
    session.finish(false);
  }

  @Test
  public void analysisCandidatePrefersReadYourWritesAfterSharedPut() throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    var key = NONPROD_FINGERPRINTER.fingerprint(new byte[] {67});
    for (byte value : new byte[] {1, 2}) {
      var inputs = snapshot();
      var session = new LocalCacheSession(cache, inputs);
      session.put(key, new byte[] {value}).get();
      assertThat(session.finish(true)).isTrue();
    }
    var probe = new LocalCacheSession(cache, snapshot());
    byte first;
    try {
      first = probe.nextAnalysisCandidate(key, new java.util.HashSet<>()).value()[0];
    } finally {
      probe.finish(false);
    }
    byte wanted = (byte) (first == 1 ? 2 : 1);
    var session = new LocalCacheSession(cache, snapshot());
    try {
      assertThat(session.put(key, new byte[] {wanted}).get()).isFalse();
      assertThat(session.nextAnalysisCandidate(key, new java.util.HashSet<>()).value())
          .isEqualTo(new byte[] {wanted});
    } finally {
      session.finish(false);
    }
  }

  @Test
  public void editedWorktreeReusesOnlyUnchangedEntriesAndPublishesSelectiveSourceEvidence()
      throws Exception {
    Path other = temporary.newFolder("other-worktree").toPath();
    Files.createDirectory(other.resolve("dir"));
    Files.writeString(other.resolve("dir/file"), "one");
    Files.writeString(workspace.resolve("unrelated"), "base");
    Files.writeString(other.resolve("unrelated"), "feature");
    shareRepository(workspace, other);
    Path cache = temporary.newFolder("cache").toPath();
    var key = NONPROD_FINGERPRINTER.fingerprint(new byte[] {61});
    var extraKey = NONPROD_FINGERPRINTER.fingerprint(new byte[] {62});
    var firstInputs = snapshot();
    firstInputs.observe(workspace.resolve("unrelated"), false);
    var first = new LocalCacheSession(cache, firstInputs);
    writeEntry(first, firstInputs, file, key, (byte) 11);
    assertThat(first.finish(true)).isTrue();
    var otherInputs = new LocalInputSnapshot(other, output, Map.of());
    assertThat(otherInputs.sourceDigest()).isNotEqualTo(firstInputs.sourceDigest());
    var second = new LocalCacheSession(cache, otherInputs);
    assertThat(client(second, otherInputs).lookup(key.toBytes()).get().value())
        .isEqualTo(new byte[] {11});
    writeEntry(second, otherInputs, other.resolve("unrelated"), extraKey, (byte) 12);
    // Inheriting the first publication's unrelated source witness would wrongly reject this.
    assertThat(second.finish(true)).isTrue();
    Files.writeString(other.resolve("dir/file"), "two");
    var editedInputs = new LocalInputSnapshot(other, output, Map.of());
    var edited = new LocalCacheSession(cache, editedInputs);
    try {
      var client = client(edited, editedInputs);
      assertThat(client.lookup(key.toBytes()).get().value()).isEmpty();
      assertThat(client.lookup(extraKey.toBytes()).get().value()).isEqualTo(new byte[] {12});
    } finally {
      edited.finish(false);
    }
    Files.writeString(other.resolve("dir/file"), "one");
    var revertedInputs = new LocalInputSnapshot(other, output, Map.of());
    var reverted = new LocalCacheSession(cache, revertedInputs);
    try {
      assertThat(client(reverted, revertedInputs).lookup(key.toBytes()).get().value())
          .isEqualTo(new byte[] {11});
    } finally {
      reverted.finish(false);
    }
  }

  @Test
  public void staleFirstCandidateDoesNotHideAnotherRevisionOfTheSameAnalysisKey() throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    var key = NONPROD_FINGERPRINTER.fingerprint(new byte[] {63});
    for (int revision = 1; revision <= 2; revision++) {
      Files.writeString(file, "revision" + revision);
      var inputs = snapshot();
      var session = new LocalCacheSession(cache, inputs);
      writeEntry(session, inputs, file, key, (byte) revision);
      assertThat(session.finish(true)).isTrue();
    }
    var probe = new LocalCacheSession(cache, snapshot());
    byte[] first;
    try {
      first = probe.nextAnalysisCandidate(key, new java.util.HashSet<>()).value();
    } finally {
      probe.finish(false);
    }
    int wanted = first[first.length - 1] == 1 ? 2 : 1;
    Files.writeString(file, "revision" + wanted);
    var inputs = snapshot();
    var session = new LocalCacheSession(cache, inputs);
    try {
      var client = client(session, inputs);
      assertThat(client.lookup(key.toBytes()).get().value()).isEqualTo(new byte[] {(byte) wanted});
      assertThat(client.validationStatisticsJson()).contains("\"rejected_roots\":1");
    } finally {
      session.finish(false);
    }
  }

  @Test
  public void parallelWritersCanPublishDifferentValuesForTheSameLogicalKey() throws Exception {
    Path other = temporary.newFolder("parallel-worktree").toPath();
    Files.createDirectory(other.resolve("dir"));
    Files.writeString(other.resolve("dir/file"), "two");
    shareRepository(workspace, other);
    Path cache = temporary.newFolder("cache").toPath();
    var key = NONPROD_FINGERPRINTER.fingerprint(new byte[] {64});
    var barrier = new java.util.concurrent.CyclicBarrier(2);
    try (var executor = Executors.newFixedThreadPool(2)) {
      var tasks = new java.util.ArrayList<java.util.concurrent.Future<Boolean>>();
      for (Path tree : List.of(workspace, other)) {
        tasks.add(
            executor.submit(
                () -> {
                  var inputs = new LocalInputSnapshot(tree, output, Map.of());
                  var session = new LocalCacheSession(cache, inputs);
                  try {
                    barrier.await();
                    writeEntry(
                        session,
                        inputs,
                        tree.resolve("dir/file"),
                        key,
                        (byte) (tree.equals(workspace) ? 1 : 2));
                    return session.finish(true);
                  } finally {
                    session.finish(false);
                  }
                }));
      }
      for (var task : tasks) {
        assertThat(task.get()).isTrue();
      }
    }
    for (Path tree : List.of(workspace, other)) {
      var inputs = new LocalInputSnapshot(tree, output, Map.of());
      var session = new LocalCacheSession(cache, inputs);
      try {
        assertThat(client(session, inputs).lookup(key.toBytes()).get().value())
            .isEqualTo(new byte[] {(byte) (tree.equals(workspace) ? 1 : 2)});
      } finally {
        session.finish(false);
      }
    }
  }

  @Test
  public void acceptedSourceEvidenceIsRevalidatedAndLateCandidateAcceptanceFails()
      throws Exception {
    Path cache = temporary.newFolder("cache").toPath();
    var key = NONPROD_FINGERPRINTER.fingerprint(new byte[] {65});
    var firstInputs = snapshot();
    var first = new LocalCacheSession(cache, firstInputs);
    writeEntry(first, firstInputs, file, key, (byte) 1);
    assertThat(first.finish(true)).isTrue();
    var inputs = snapshot();
    var session = new LocalCacheSession(cache, inputs);
    var candidate = session.nextAnalysisCandidate(key, new java.util.HashSet<>());
    assertThat(client(session, inputs).lookup(key.toBytes()).get().value())
        .isEqualTo(new byte[] {1});
    session.put(NONPROD_FINGERPRINTER.fingerprint(new byte[] {66}), new byte[] {3}).get();
    Files.writeString(file, "changed after lookup");
    assertThat(session.finish(true)).isFalse();
    assertThat(session.acceptAnalysisCandidate(candidate)).isFalse();
  }

  @Test
  public void followsNestedParentsListingsAndSourceAndRejectsEachChange() throws Exception {
    var parent = leaf(file.getParent(), false, List.of());
    var input = leaf(file, false, List.of(parent.key()));
    var listing = leaf(file.getParent(), true, List.of(parent.key()));
    Path source = Files.writeString(workspace.resolve("source"), "aaa");
    var sourceKey = leaf(source, false, List.of());
    var nested = root(input);
    var root = put(bytes(List.of(nested), List.of(), List.of(listing), sourceKey));
    assertThat(validator().validate(root)).isTrue();
    var mtime = Files.getLastModifiedTime(file);
    Files.writeString(file, "two");
    Files.setLastModifiedTime(file, mtime);
    assertThat(validator().validate(root)).isFalse();
    Files.writeString(file, "one");
    Files.writeString(source, "bbb");
    assertThat(validator().validate(root)).isFalse();
    Files.writeString(source, "aaa");
    Files.writeString(file.getParent().resolve("new-member"), "new");
    assertThat(validator().validate(root)).isFalse();
  }

  @Test
  public void unrelatedSourceEditDoesNotInvalidateEntryEvidence() throws Exception {
    var root = root(leaf(file, false, List.of()));
    var originalDigest = writer.sourceDigest();
    Files.writeString(workspace.resolve("unrelated"), "another feature");
    assertThat(snapshot().sourceDigest()).isNotEqualTo(originalDigest);
    assertThat(validator().validate(root)).isTrue();
  }

  @Test
  public void parentEvidenceIsMandatoryEvenWhenLeafBytesAreUnchanged() throws Exception {
    Path target = Files.writeString(workspace.resolve("target"), "same");
    Files.writeString(workspace.resolve("other"), "same");
    Path link = Files.createSymbolicLink(workspace.resolve("link"), Path.of("target"));
    var parent = leaf(link, false, List.of());
    var root = root(leaf(target, false, List.of(parent.key())));
    assertThat(validator().validate(root)).isTrue();
    Files.delete(link);
    Files.createSymbolicLink(link, Path.of("other"));
    assertThat(validator().validate(root)).isFalse();
  }

  @Test
  public void missingRecordCanBeRetriedAndFingerprintMismatchIsRejected() throws Exception {
    byte[] evidence = writer.captureDependency(file, false, List.of());
    var leaf =
        new StringKey("local-input-v1/" + NONPROD_FINGERPRINTER.fingerprint(evidence).toHex());
    var root = root(leaf);
    var validator = validator();
    assertThat(validator.validate(root)).isFalse();
    store.put(leaf, evidence).get();
    assertThat(validator.validate(root)).isTrue();
    var wrongRoot = NONPROD_FINGERPRINTER.fingerprint(new byte[] {99});
    store.put(wrongRoot, bytes(List.of(), List.of(leaf), List.of(), null)).get();
    assertThat(validator().validate(wrongRoot)).isFalse();
    var wrongLeaf = new StringKey("local-input-v1/00000000000000000000000000000000");
    store.put(wrongLeaf, evidence).get();
    assertThat(validator().validate(root(wrongLeaf))).isFalse();
  }

  @Test
  public void rejectsMalformedCountsTrailingBytesLegacyKeysAndTruncation() throws Exception {
    byte[] valid = bytes(List.of(), List.of(leaf(file, false, List.of())), List.of(), null);
    for (byte[] malformed :
        List.of(
            new byte[] {0, 0, 0, 2}, // source count must be zero or one
            new byte[] {(byte) 0xff, (byte) 0xff, (byte) 0xff, (byte) 0xff, 7, 0, 0, 0},
            Arrays.copyOf(valid, valid.length + 1),
            Arrays.copyOf(valid, valid.length - 1),
            bytes(List.of(), List.of(new StringKey("legacy-path-key")), List.of(), null))) {
      assertThat(validator().validate(put(malformed))).isFalse();
    }
  }

  @Test
  public void compressedNodesWorkAndDecompressionIsBounded() throws Exception {
    var leaf = leaf(file, false, List.of());
    var many = java.util.Collections.nCopies(1000, leaf);
    byte[] node = bytes(List.of(), many, List.of(), null);
    var root = put(compressed(node));
    assertThat(validator().validate(root)).isTrue();
    var small =
        new LocalDependencyValidator(
            store,
            snapshot(),
            NONPROD_FINGERPRINTER,
            compression,
            new LocalDependencyValidator.Limits(100, 1024 * 1024, 100, 2000, 128));
    assertThat(small.validate(root)).isFalse();
    assertThat(validator().validate(put(new byte[] {(byte) 0x80, 0, 99}))).isFalse();
  }

  @Test
  public void deepTreesAreIterativeAndAllBudgetsFailClosed() throws Exception {
    var root = root(leaf(file, false, List.of()));
    for (int i = 0; i < 1500; i++) {
      root = put(bytes(List.of(root), List.of(), List.of(), null));
    }
    assertThat(validator().validate(root)).isTrue();
    for (var limits :
        List.of(
            new LocalDependencyValidator.Limits(100, 1024 * 1024, 4096, 100, 1024),
            new LocalDependencyValidator.Limits(2000, 100, 4096, 100, 1024),
            new LocalDependencyValidator.Limits(2000, 1024 * 1024, 100, 100, 1024))) {
      assertThat(
              new LocalDependencyValidator(
                      store, snapshot(), NONPROD_FINGERPRINTER, compression, limits)
                  .validate(root))
          .isFalse();
    }
  }

  @Test
  public void cycleIsRejectedWithoutRecursing() throws Exception {
    var fixed = PackedFingerprint.fromBytes(new byte[16]);
    var constantHash =
        new Fingerprinter() {
          @Override
          public PackedFingerprint fingerprint(byte[] ignored) {
            return fixed;
          }

          @Override
          public PackedFingerprint fingerprint(byte[] ignored, String salt) {
            return fixed;
          }
        };
    store.put(fixed, bytes(List.of(fixed), List.of(), List.of(), null)).get();
    var validator = new LocalDependencyValidator(store, snapshot(), constantHash, compression);
    assertThat(validator.validate(fixed)).isFalse();
  }

  @Test
  public void invocationMemoRetainsEvidenceForFinalRevalidation() throws Exception {
    var root = root(leaf(file, false, List.of()));
    var readerInputs = snapshot();
    var validator =
        new LocalDependencyValidator(store, readerInputs, NONPROD_FINGERPRINTER, compression);
    try (var executor = Executors.newFixedThreadPool(4)) {
      var futures = new java.util.ArrayList<java.util.concurrent.Future<Boolean>>();
      for (int i = 0; i < 20; i++) {
        futures.add(executor.submit(() -> validator.validate(root)));
      }
      for (var future : futures) {
        assertThat(future.get()).isTrue();
      }
    }
    assertThat(validator.statisticsJson()).contains("\"records_read\":2");
    assertThat(readerInputs.isUnchanged()).isTrue();
    Files.writeString(file, "two");
    assertThat(readerInputs.isUnchanged()).isFalse();
    // A new invocation has no successful checks from the previous command.
    assertThat(validator().validate(root)).isFalse();
  }

  @Test
  public void repeatedWitnessAcrossDistinctRecordsIsCheckedOnceAndFinallyRechecked()
      throws Exception {
    var parent = leaf(file.getParent(), false, List.of());
    var plain = leaf(file, false, List.of());
    var withParent = leaf(file, false, List.of(parent.key()));
    var root = put(bytes(List.of(), List.of(plain, withParent), List.of(), null));
    var inputs = snapshot();
    var validator = new LocalDependencyValidator(store, inputs, NONPROD_FINGERPRINTER, compression);
    assertThat(validator.validate(root)).isTrue();
    assertThat(validator.statisticsJson()).contains("\"witnesses_checked\":2");
    assertThat(validator.statisticsJson()).contains("\"witness_memo_hits\":1");
    assertThat(inputs.isUnchanged()).isTrue();
    var mtime = Files.getLastModifiedTime(file);
    Files.writeString(file, "two");
    Files.setLastModifiedTime(file, mtime);
    assertThat(inputs.isUnchanged()).isFalse();
    assertThat(validator().validate(root)).isFalse();
  }

  @Test
  public void witnessMemoIncludesExpectedDigestAndStaleCandidateDoesNotPoisonPublication()
      throws Exception {
    var oldRoot = root(leaf(file, false, List.of()));
    Files.writeString(file, "two");
    var newRoot = root(leaf(file, false, List.of()));
    Files.writeString(file, "one");
    var inputs = snapshot();
    var validator = new LocalDependencyValidator(store, inputs, NONPROD_FINGERPRINTER, compression);
    assertThat(validator.validate(oldRoot)).isTrue();
    assertThat(validator.validate(newRoot)).isFalse();
    assertThat(inputs.isUnchanged()).isTrue();
  }

  @Test
  public void failedWitnessIsRetriedWhenExternalRepositoryMaterializes() throws Exception {
    Path external = Files.createDirectories(output.resolve("external/repo")).resolve("file");
    Files.writeString(external, "contents");
    var root = root(leaf(external, false, List.of()));
    Files.delete(external);
    var inputs = snapshot();
    var validator = new LocalDependencyValidator(store, inputs, NONPROD_FINGERPRINTER, compression);
    assertThat(validator.validate(root)).isFalse();
    Files.writeString(external, "contents");
    assertThat(validator.validate(root)).isTrue();
    assertThat(inputs.isUnchanged()).isTrue();
  }

  @Test
  public void witnessMemoIsBoundedWithoutDroppingFinalEvidence() throws Exception {
    var witnesses = new java.util.ArrayList<LocalInputSnapshot.Witness>();
    Path last = null;
    for (int i = 0; i < 8; i++) {
      last = Files.writeString(workspace.resolve("witness-" + i), "original");
      witnesses.addAll(
          LocalDependencyEvidence.decode(writer.captureDependency(last, false, List.of()))
              .witnesses());
    }
    byte[] evidence = new LocalDependencyEvidence(List.of(), witnesses).encode();
    var key =
        new StringKey("local-input-v1/" + NONPROD_FINGERPRINTER.fingerprint(evidence).toHex());
    store.put(key, evidence).get();
    var inputs = snapshot();
    var validator =
        new LocalDependencyValidator(
            store,
            inputs,
            NONPROD_FINGERPRINTER,
            compression,
            new LocalDependencyValidator.Limits(3, 1024 * 1024, 100, 100, 1024));
    assertThat(validator.validate(root(key))).isTrue();
    assertThat(validator.statisticsJson()).contains("\"witness_memo_entries\":3");
    assertThat(inputs.witnesses()).containsAtLeastElementsIn(witnesses);
    Files.writeString(last, "modified");
    assertThat(inputs.isUnchanged()).isFalse();
  }

  @Test
  public void memoizedDirectoryMetadataDoesNotValidateChangedListing() throws Exception {
    var metadata = root(leaf(file.getParent(), false, List.of()));
    var listing = root(leaf(file.getParent(), true, List.of()));
    var validator = validator();
    assertThat(validator.validate(metadata)).isTrue();
    Files.writeString(file.getParent().resolve("new-child"), "new");
    assertThat(validator.validate(listing)).isFalse();
  }

  @Test
  public void memoizedLeafDoesNotBypassChangedResolutionParent() throws Exception {
    Path link = Files.createSymbolicLink(workspace.resolve("parent-link"), Path.of("dir/file"));
    Files.writeString(workspace.resolve("other"), "one");
    var parent = leaf(link, false, List.of());
    var plain = root(leaf(file, false, List.of()));
    var withParent = root(leaf(file, false, List.of(parent.key())));
    var validator = validator();
    assertThat(validator.validate(plain)).isTrue();
    Files.delete(link);
    Files.createSymbolicLink(link, Path.of("other"));
    assertThat(validator.validate(withParent)).isFalse();
  }

  @Test
  public void analysisClientRejectsStaleEvidenceBeforeReturningValue() throws Exception {
    var inputs = snapshot();
    var session = new LocalCacheSession(temporary.newFolder("cache").toPath(), inputs);
    try {
      byte[] evidence = writer.captureDependency(file, false, List.of());
      var leaf =
          new StringKey("local-input-v1/" + NONPROD_FINGERPRINTER.fingerprint(evidence).toHex());
      session.put(leaf, evidence).get();
      byte[] node = bytes(List.of(), List.of(leaf), List.of(), null);
      var root = NONPROD_FINGERPRINTER.fingerprint(node);
      session.put(root, node).get();
      var buffer = new ByteArrayOutputStream();
      var coded = CodedOutputStream.newInstance(buffer);
      coded.writeEnumNoTag(4);
      root.writeTo(coded);
      coded.writeRawBytes(new byte[] {11, 12});
      coded.flush();
      var key = NONPROD_FINGERPRINTER.fingerprint(new byte[] {77});
      session.put(key, buffer.toByteArray()).get();
      var client =
          new LocalAnalysisCacheClient(
              session,
              new LocalDependencyValidator(session, inputs, NONPROD_FINGERPRINTER, compression));
      assertThat(client.lookup(key.toBytes()).get().value()).isEqualTo(new byte[] {11, 12});
      Files.writeString(file, "two");
      var changedClient =
          new LocalAnalysisCacheClient(
              session,
              new LocalDependencyValidator(
                  session, snapshot(), NONPROD_FINGERPRINTER, compression));
      assertThat(changedClient.lookup(key.toBytes()).get().value()).isEmpty();
    } finally {
      session.finish(false);
    }
  }
}
