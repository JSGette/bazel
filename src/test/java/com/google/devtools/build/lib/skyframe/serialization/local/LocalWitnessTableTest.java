// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalWitnessTableTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  @Test
  public void losslessBlocksExceedOldManifestLimitWithoutOneLargeAllocation() throws Exception {
    Path directory = temporary.newFolder().toPath();
    var witnesses = new ArrayList<LocalInputSnapshot.Witness>();
    String prefix = "external/repository/" + "path/".repeat(210);
    long oldBytes = 0;
    for (int i = 0; i < 70000; i++) {
      String path = prefix + i;
      String digest =
          switch (i % 3) {
            case 0 -> "a".repeat(64);
            case 1 -> "missing";
            default -> "not-directory";
          };
      witnesses.add(new LocalInputSnapshot.Witness(path, i % 2 == 0, digest));
      oldBytes += 5 + path.length() + digest.length();
    }
    assertThat(oldBytes).isGreaterThan(64L * 1024 * 1024);
    var blocks = LocalWitnessTable.write(directory, witnesses);
    assertThat(blocks.size()).isGreaterThan(1);
    var header = new ByteArrayOutputStream();
    LocalWitnessTable.writeDescriptors(new DataOutputStream(header), blocks);
    assertThat(header.size()).isLessThan(4096);
    var decoded =
        LocalWitnessTable.readDescriptors(
            new DataInputStream(new ByteArrayInputStream(header.toByteArray())));
    assertThat(decoded).isEqualTo(blocks);
    assertThat(
            LocalWitnessTable.read(directory, decoded, LocalWitnessTable.metadataBytes(blocks))
                .equals(witnesses))
        .isTrue();
    assertThrows(
        IOException.class,
        () ->
            LocalWitnessTable.read(directory, blocks, LocalWitnessTable.metadataBytes(blocks) - 1));
  }

  @Test
  public void unicodeAndNegativeWitnessesSurviveButCorruptionAndSymlinksAreRejected()
      throws Exception {
    Path directory = temporary.newFolder().toPath();
    var witnesses =
        List.of(
            new LocalInputSnapshot.Witness("external/r/é/日本", false, "b".repeat(64)),
            new LocalInputSnapshot.Witness("external/r/é/日本2", true, "missing"),
            new LocalInputSnapshot.Witness("absolute/link", true, "not-directory"));
    var blocks = LocalWitnessTable.write(directory, witnesses);
    assertThat(LocalWitnessTable.read(directory, blocks, 10000)).isEqualTo(witnesses);
    var descriptor = blocks.get(0);
    var understated =
        List.of(
            new LocalWitnessTable.Block(
                descriptor.count(),
                descriptor.encodedBytes(),
                descriptor.decodedBytes(),
                descriptor.expandedBytes() - 1,
                descriptor.digest()));
    assertThrows(IOException.class, () -> LocalWitnessTable.read(directory, understated, 10000));
    Path block = directory.resolve(LocalWitnessTable.filename(0));
    byte[] original = Files.readAllBytes(block);
    byte[] damaged = original.clone();
    damaged[0] ^= 1;
    Files.write(block, damaged);
    assertThrows(IOException.class, () -> LocalWitnessTable.read(directory, blocks, 10000));
    Path outside = temporary.newFile().toPath();
    Files.write(outside, original);
    Files.delete(block);
    Files.createSymbolicLink(block, outside);
    assertThrows(IOException.class, () -> LocalWitnessTable.read(directory, blocks, 10000));
  }
}
