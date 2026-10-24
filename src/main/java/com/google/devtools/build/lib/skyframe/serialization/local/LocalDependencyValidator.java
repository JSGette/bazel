// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.common.util.concurrent.Striped;
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
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.atomic.LongAdder;
import java.util.concurrent.locks.Lock;
import java.util.regex.Pattern;

/**
 * Validates complete local dependency trees, retaining only bounded invocation-local success keys.
 */
final class LocalDependencyValidator {
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
      Pending pending,
      Iterator<KeyBytesProvider> children,
      List<LocalInputSnapshot.Witness> witnesses,
      ArrayList<Pending> deferred) {}

  private final FingerprintValueStore store;
  private final LocalInputSnapshot inputs;
  private final Fingerprinter fingerprinter;
  private final CompressionService compression;
  private final Limits limits;
  // One root lock per traversal, never acquired recursively. Equal roots coalesce; unrelated
  // roots can read and validate concurrently. Collisions only reduce concurrency, not correctness.
  private final Striped<Lock> rootLocks = Striped.lock(4096);
  private final Object memoInsertion = new Object();
  private final Set<ByteString> validated = ConcurrentHashMap.newKeySet();
  private final Set<LocalInputSnapshot.Witness> validatedWitnesses = ConcurrentHashMap.newKeySet();

  // Protected by ownership. An owner only retains its active DFS ancestor chain. Waiting edges
  // are checked under this same monitor, so corrupt cross-root cycles cannot deadlock callers.
  private static final class Owner {
    private Pending waitingOn;
  }

  private static final class Pending {
    private final Owner owner;
    private final CompletableFuture<Boolean> complete = new CompletableFuture<>();

    private Pending(Owner owner) {
      this.owner = owner;
    }
  }

  private final Object ownership = new Object();
  private final Map<ByteString, Pending> pendingNodes = new HashMap<>();
  private final LongAdder witnessMemoHits = new LongAdder();
  private final LongAdder admissionWitnessMemoHits = new LongAdder();
  private final LongAdder recordsRead = new LongAdder();
  private final LongAdder memoHits = new LongAdder();
  private final LongAdder witnessesChecked = new LongAdder();
  private final LongAdder rejected = new LongAdder();
  private final LongAdder subtreeWaits = new LongAdder();

  LocalDependencyValidator(
      FingerprintValueStore store,
      LocalInputSnapshot inputs,
      Fingerprinter fingerprinter,
      CompressionService compression) {
    this(store, inputs, fingerprinter, compression, Limits.DEFAULT);
  }

  LocalDependencyValidator(
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
  boolean validate(PackedFingerprint root) throws InterruptedException {
    if (Thread.interrupted()) {
      throw new InterruptedException();
    }
    if (validated.contains(ByteString.copyFrom(root.toBytes()))) {
      memoHits.increment();
      return true;
    }
    Lock lock = rootLocks.get(root);
    lock.lockInterruptibly();
    try {
      return validateRoot(root);
    } finally {
      lock.unlock();
    }
  }

  private boolean validateRoot(PackedFingerprint root) throws InterruptedException {
    var owner = new Owner();
    var active = new HashSet<ByteString>();
    var stack = new ArrayDeque<Frame>();
    long[] budget = {0, 0}; // newly read records and encoded/decompressed bytes for this lookup
    try {
      push(root, stack, active, budget, owner);
      while (!stack.isEmpty()) {
        if (Thread.interrupted()) {
          throw new InterruptedException();
        }
        Frame frame = stack.peek();
        if (frame.children().hasNext()) {
          push(frame.children().next(), stack, active, budget, owner);
          continue;
        }
        // First do independent work instead of parking at the first shared child. Every deferred
        // dependency must still succeed before this node's own witnesses or success can be reused.
        for (Pending pending : frame.deferred()) {
          await(pending, owner);
        }
        // Always visit resolution parents before reusing a witness success. Exact witnesses
        // include logical path, operation and expected digest. Memoized successes are bounded and
        // invocation-local, just like dependency records; final publication still rechecks content.
        for (var witness : frame.witnesses()) {
          validateWitness(witness);
        }
        synchronized (memoInsertion) {
          if (!validated.contains(frame.key())) {
            if (validated.size() >= limits.records()) {
              throw new IOException("dependency success memo capacity exceeded");
            }
            validated.add(frame.key());
          }
        }
        finish(frame.key(), frame.pending(), true);
        active.remove(frame.key());
        stack.pop();
      }
      return true;
    } catch (IOException | ExecutionException | IllegalArgumentException e) {
      rejected.increment();
      return false;
    } finally {
      // On rejection/interruption, wake every waiter and remove all unfinished ownership. No
      // negative result persists: another lookup can retry after a repository materializes.
      while (!stack.isEmpty()) {
        Frame frame = stack.pop();
        finish(frame.key(), frame.pending(), false);
      }
    }
  }

  private void validateWitness(LocalInputSnapshot.Witness witness) throws IOException {
    if (validatedWitnesses.contains(witness)) {
      witnessMemoHits.increment();
      return;
    }
    if (inputs.includeAdmittedWitness(witness)) {
      admissionWitnessMemoHits.increment();
    } else {
      witnessesChecked.increment();
      if (!inputs.validateAndInclude(witness)) {
        throw new IOException("dependency content mismatch");
      }
    }
    synchronized (memoInsertion) {
      if (validatedWitnesses.size() < limits.records()) {
        validatedWitnesses.add(witness);
      }
    }
  }

  private void finish(ByteString key, Pending pending, boolean success) {
    synchronized (ownership) {
      pendingNodes.remove(key, pending);
      pending.complete.complete(success);
    }
  }

  private void await(Pending pending, Owner owner)
      throws IOException, ExecutionException, InterruptedException {
    synchronized (ownership) {
      // Completed owners may already be waiting elsewhere: don't add a stale wait edge.
      if (!pending.complete.isDone()) {
        for (Pending edge = pending;
            edge != null && !edge.complete.isDone();
            edge = edge.owner.waitingOn) {
          if (edge.owner == owner) {
            throw new IOException("cyclic concurrent dependency evidence");
          }
        }
        owner.waitingOn = pending;
        subtreeWaits.increment();
      }
    }
    try {
      if (!pending.complete.get()) {
        throw new IOException("shared dependency validation failed");
      }
      memoHits.increment();
    } finally {
      synchronized (ownership) {
        owner.waitingOn = null;
      }
    }
  }

  private void push(
      KeyBytesProvider key,
      ArrayDeque<Frame> stack,
      HashSet<ByteString> active,
      long[] budget,
      Owner owner)
      throws IOException, InterruptedException, ExecutionException {
    ByteString identity = ByteString.copyFrom(key.toBytes());
    if (validated.contains(identity)) {
      memoHits.increment();
      return;
    }
    if (!active.add(identity)) {
      throw new IOException("cyclic local dependency evidence");
    }
    Pending pending;
    synchronized (ownership) {
      if (validated.contains(identity)) {
        memoHits.increment();
        active.remove(identity);
        return;
      }
      pending = pendingNodes.get(identity);
      if (pending == null) {
        if (pendingNodes.size() >= limits.records()) {
          throw new IOException("in-flight dependency capacity exceeded");
        }
        pending = new Pending(owner);
        pendingNodes.put(identity, pending);
      }
    }
    if (pending.owner != owner) {
      if (stack.isEmpty()) {
        await(pending, owner);
      } else {
        stack.peek().deferred().add(pending);
      }
      active.remove(identity);
      return;
    }
    boolean pushed = false;
    try {
      if (stack.size() >= limits.depth() || ++budget[0] > limits.records()) {
        throw new IOException("dependency traversal limit exceeded");
      }
      byte[] bytes = store.get(key).get();
      recordsRead.increment();
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
              compression.newZstdInputStream(
                  new ByteArrayInputStream(bytes, 2, bytes.length - 2))) {
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
      stack.push(new Frame(identity, pending, children.iterator(), witnesses, new ArrayList<>()));
      pushed = true;
    } finally {
      if (!pushed) {
        finish(identity, pending, false);
      }
    }
  }

  private static StringKey leafKey(String key) throws IOException {
    if (!LEAF_KEY_PATTERN.matcher(key).matches()) {
      throw new IOException("unsupported local dependency key");
    }
    return new StringKey(key);
  }

  String statisticsJson() {
    return String.format(
        java.util.Locale.ROOT,
        "{\"records_read\":%d,\"memo_hits\":%d,\"witnesses_checked\":%d,\"rejected_roots\":%d,\"memo_entries\":%d,\"witness_memo_hits\":%d,\"witness_memo_entries\":%d,\"admission_witness_memo_hits\":%d,\"subtree_waits\":%d}",
        recordsRead.sum(),
        memoHits.sum(),
        witnessesChecked.sum(),
        rejected.sum(),
        validated.size(),
        witnessMemoHits.sum(),
        validatedWitnesses.size(),
        admissionWitnessMemoHits.sum(),
        subtreeWaits.sum());
  }
}
