// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Bounded content evidence for one file/listing operation and its path-resolution parents. */
record LocalDependencyEvidence(List<String> parents, List<LocalInputSnapshot.Witness> witnesses) {
  private static final int MAGIC = 0x534b4531;
  private static final int MAX_BYTES = 1024 * 1024;
  private static final int MAX_PARENTS = 512;
  private static final int MAX_WITNESSES = 1024;

  LocalDependencyEvidence {
    parents = parents.stream().sorted().distinct().toList();
    witnesses =
        witnesses.stream()
            .distinct()
            .sorted(
                Comparator.comparing(LocalInputSnapshot.Witness::path)
                    .thenComparing(LocalInputSnapshot.Witness::listing)
                    .thenComparing(LocalInputSnapshot.Witness::digest))
            .toList();
  }

  byte[] encode() throws IOException {
    if (parents.size() > MAX_PARENTS || witnesses.isEmpty() || witnesses.size() > MAX_WITNESSES) {
      throw new IOException("file dependency evidence count outside bounds");
    }
    var bytes = new ByteArrayOutputStream();
    try (var output = new DataOutputStream(bytes)) {
      output.writeInt(MAGIC);
      output.writeInt(parents.size());
      for (String parent : parents) {
        output.writeUTF(parent);
      }
      output.writeInt(witnesses.size());
      for (var witness : witnesses) {
        output.writeUTF(witness.path());
        output.writeBoolean(witness.listing());
        output.writeUTF(witness.digest());
      }
    }
    if (bytes.size() > MAX_BYTES) {
      throw new IOException("file dependency evidence exceeds size limit");
    }
    return bytes.toByteArray();
  }

  static LocalDependencyEvidence decode(byte[] bytes) throws IOException {
    if (bytes.length > MAX_BYTES) {
      throw new IOException("file dependency evidence exceeds size limit");
    }
    try (var input = new DataInputStream(new ByteArrayInputStream(bytes))) {
      if (input.readInt() != MAGIC) {
        throw new IOException("unsupported file dependency evidence");
      }
      int parentCount = input.readInt();
      if (parentCount < 0 || parentCount > MAX_PARENTS) {
        throw new IOException("invalid file dependency parent count");
      }
      var parents = new ArrayList<String>();
      for (int i = 0; i < parentCount; i++) {
        parents.add(input.readUTF());
      }
      int witnessCount = input.readInt();
      if (witnessCount < 1 || witnessCount > MAX_WITNESSES) {
        throw new IOException("invalid file dependency witness count");
      }
      var witnesses = new ArrayList<LocalInputSnapshot.Witness>();
      for (int i = 0; i < witnessCount; i++) {
        witnesses.add(
            new LocalInputSnapshot.Witness(input.readUTF(), input.readBoolean(), input.readUTF()));
      }
      if (input.read() != -1) {
        throw new IOException("trailing file dependency evidence bytes");
      }
      return new LocalDependencyEvidence(parents, witnesses);
    }
  }
}
