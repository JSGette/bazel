// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.compress.CompressionService;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.Fingerprinter;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.StringKey;
import com.google.protobuf.ByteString;
import com.google.protobuf.CodedInputStream;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.concurrent.ExecutionException;
import java.util.regex.Pattern;

/**
 * Unmodified serial baseline from 6f451b2 (benchmark-only).
 * Validates complete local dependency trees, retaining only bounded invocation-local success keys.
 */
final class SerialDependencyValidatorBenchmarkBaseline {
  private static final Pattern LEAF_KEY_PATTERN = Pattern.compile("local-input-v1/[0-9a-f]{32}");

  record Limits(int records, long bytes, int depth, int references, int nodeBytes) {
    static final Limits DEFAULT =
        new Limits(250_000, 128L * 1024 * 1024, 4096, 100_000, 8 * 1024 * 1024);

    Limits {
      if (records < 1 || bytes < 1 || depth < 1 || references < 1 || nodeBytes < 1) {
        throw new IllegalArgumentException("invalid dependency validation limits");
      }
    }
  }

  private record Frame(
      ByteString key,
      Iterator<KeyBytesProvider> children,
      List<LocalInputSnapshot.Witness> witnesses) {}

  private final FingerprintValueStore store;
  private final LocalInputSnapshot inputs;
  private final Fingerprinter fingerprinter;
  private final CompressionService compression;
  private final Limits limits;
  private final HashSet<ByteString> validated = new HashSet<>();
  private final HashSet<LocalInputSnapshot.Witness> validatedWitnesses = new HashSet<>();
  private long witnessMemoHits;
  private long admissionWitnessMemoHits;
  private long recordsRead;
  private long memoHits;
  private long witnessesChecked;
  private long rejected;

  SerialDependencyValidatorBenchmarkBaseline(
      FingerprintValueStore store,
      LocalInputSnapshot inputs,
      Fingerprinter fingerprinter,
      CompressionService compression) {
    this(store, inputs, fingerprinter, compression, Limits.DEFAULT);
  }

  SerialDependencyValidatorBenchmarkBaseline(
      FingerprintValueStore store,
      LocalInputSnapshot inputs,
      Fingerprinter fingerprinter,
      CompressionService compression,
      Limits limits) {
    this.store = store;
    this.inputs = inputs;
    this.fingerprinter = fingerprinter;
    this.compression = compression;
    this.limits = limits;
  }

  /**
   * Successes are scoped to this invocation, under Bazel's stable-inputs-during-build assumption.
   * Every successful leaf is retained by inputs for final publication revalidation. No negative
   * result is cached: repositories and dependency packs can become available later in the command.
   */
  synchronized boolean validate(PackedFingerprint root) throws InterruptedException {
    var active = new HashSet<ByteString>();
    var stack = new ArrayDeque<Frame>();
    long[] budget = {0, 0}; // newly read records and encoded/decompressed bytes for this lookup
    try {
      push(root, stack, active, budget);
      while (!stack.isEmpty()) {
        Frame frame = stack.peek();
        if (frame.children().hasNext()) {
          push(frame.children().next(), stack, active, budget);
          continue;
        }
        // Always visit resolution parents before reusing a witness success. Exact witnesses
        // include logical path, operation and expected digest. Memoized successes are bounded and
        // invocation-local, just like dependency records; final publication still rechecks content.
        for (var witness : frame.witnesses()) {
          if (validatedWitnesses.contains(witness)) {
            witnessMemoHits++;
            continue;
          }
          if (inputs.includeAdmittedWitness(witness)) {
            admissionWitnessMemoHits++;
          } else {
            witnessesChecked++;
            if (!inputs.validateAndInclude(witness)) {
              throw new IOException("dependency content mismatch");
            }
          }
          if (validatedWitnesses.size() < limits.records()) {
            validatedWitnesses.add(witness);
          }
        }
        if (validated.size() >= limits.records()) {
          throw new IOException("dependency success memo capacity exceeded");
        }
        validated.add(frame.key());
        active.remove(frame.key());
        stack.pop();
      }
      return true;
    } catch (IOException | ExecutionException | IllegalArgumentException e) {
      rejected++;
      return false;
    }
  }

  private void push(
      KeyBytesProvider key, ArrayDeque<Frame> stack, HashSet<ByteString> active, long[] budget)
      throws IOException, InterruptedException, ExecutionException {
    ByteString identity = ByteString.copyFrom(key.toBytes());
    if (validated.contains(identity)) {
      memoHits++;
      return;
    }
    if (!active.add(identity)) {
      throw new IOException("cyclic local dependency evidence");
    }
    if (stack.size() >= limits.depth() || ++budget[0] > limits.records()) {
      throw new IOException("dependency traversal limit exceeded");
    }
    byte[] bytes = store.get(key).get();
    recordsRead++;
    if (bytes == null || (budget[1] += bytes.length) > limits.bytes()) {
      throw new IOException("missing or oversized dependency evidence");
    }
    var children = new ArrayList<KeyBytesProvider>();
    List<LocalInputSnapshot.Witness> witnesses = List.of();
    if (key instanceof StringKey leaf) {
      if (!leaf.key().equals("local-input-v1/" + fingerprinter.fingerprint(bytes).toHex())) {
        throw new IOException("file evidence fingerprint mismatch");
      }
      var evidence = LocalDependencyEvidence.decode(bytes);
      for (String parent : evidence.parents()) {
        children.add(leafKey(parent));
      }
      witnesses = evidence.witnesses();
    } else {
      if (!fingerprinter.fingerprint(bytes).equals(key)) {
        throw new IOException("nested dependency fingerprint mismatch");
      }
      if (bytes.length >= 2 && bytes[0] == (byte) 0x80 && bytes[1] == 0) {
        try (var decoded =
            compression.newZstdInputStream(new ByteArrayInputStream(bytes, 2, bytes.length - 2))) {
          bytes = decoded.readNBytes(limits.nodeBytes() + 1);
        }
        if ((budget[1] += bytes.length) > limits.bytes()) {
          throw new IOException("decompressed dependency budget exceeded");
        }
      }
      if (bytes.length > limits.nodeBytes()) {
        throw new IOException("dependency node exceeds size limit");
      }
      var input = CodedInputStream.newInstance(bytes);
      int nodes = input.readInt32();
      int files = input.readInt32();
      int listings = input.readInt32();
      int source = input.readInt32();
      if (nodes < 0
          || files < 0
          || listings < 0
          || source < 0
          || source > 1
          || (long) nodes + files + listings + source > limits.references()) {
        throw new IOException("invalid dependency reference count");
      }
      for (int i = 0; i < nodes; i++) {
        children.add(PackedFingerprint.readFrom(input));
      }
      for (int i = 0; i < files + listings + source; i++) {
        children.add(leafKey(input.readStringRequireUtf8()));
      }
      if (!input.isAtEnd()) {
        throw new IOException("trailing dependency node bytes");
      }
    }
    stack.push(new Frame(identity, children.iterator(), witnesses));
  }

  private static StringKey leafKey(String key) throws IOException {
    if (!LEAF_KEY_PATTERN.matcher(key).matches()) {
      throw new IOException("unsupported local dependency key");
    }
    return new StringKey(key);
  }

  synchronized String statisticsJson() {
    return String.format(
        java.util.Locale.ROOT,
        "{\"records_read\":%d,\"memo_hits\":%d,\"witnesses_checked\":%d,\"rejected_roots\":%d,\"memo_entries\":%d,\"witness_memo_hits\":%d,\"witness_memo_entries\":%d,\"admission_witness_memo_hits\":%d}",
        recordsRead,
        memoHits,
        witnessesChecked,
        rejected,
        validated.size(),
        witnessMemoHits,
        validatedWitnesses.size(),
        admissionWitnessMemoHits);
  }
}
