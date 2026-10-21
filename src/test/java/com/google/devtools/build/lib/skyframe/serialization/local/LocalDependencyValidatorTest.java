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
