// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFailedFuture;
import static com.google.common.util.concurrent.Futures.immediateFuture;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateWriteStatus;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.common.util.concurrent.Striped;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatus;
import com.google.protobuf.ByteString;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.Lock;
import java.util.concurrent.locks.ReentrantReadWriteLock;
import java.util.function.LongSupplier;

/**
 * One invocation's private writes and immutable, validated publications from other invocations.
 *
 * <p>Records become discoverable only after successful completion and input validation. A
 * publication lists every older publication from which it read chunks, keeping that closure
 * explicit for later garbage collection. There is no deletion in this initial session format.
 */
final class LocalCacheSession implements FingerprintValueStore {
  private static final int MAGIC = 0x534b5332;
  private static final int MAX_MANIFEST_BYTES = 64 * 1024 * 1024;
  private static final int MAX_WITNESSES = 1_000_000;
  private static final int MAX_VALIDATED_WITNESS_LISTS = 32;

  record ReadLimits(int publications, long metadataBytes, int scanEntries) {
    static final ReadLimits DEFAULT = new ReadLimits(64, 256L * 1024 * 1024, 4096);

    ReadLimits {
      if (publications < 0 || metadataBytes < 0 || scanEntries < 1) {
        throw new IllegalArgumentException("invalid local SkyCache read limits");
      }
    }
  }

  private record Manifest(
      String id,
      List<LocalInputSnapshot.Witness> witnesses,
      List<String> parents,
      long encodedBytes) {}

  private record Candidate(
      String id,
      PackedFingerprintValueStore store,
      List<LocalInputSnapshot.Witness> witnesses,
      List<String> parents,
      long metadataBytes) {}

  private record SharedPut(Candidate candidate, String valueDigest) {}

  private final LocalInputSnapshot inputs;
  private final LongSupplier nanoTime;
  private final ReadLimits limits;
  private final Path objects;
  private final Path publications;
  private final String id;
  private volatile PackedFingerprintValueStore privateStore;
  // Puts hold locks across private writes and shared reads. Keep more stripes than writers
  // so unrelated keys rarely wait behind another record's I/O.
  private final Striped<Lock> putLocks = Striped.lock(4096);
  // Only skipped puts need bookkeeping. Ordinary writes remain indexed by the private store.
  private final ConcurrentHashMap<ByteString, SharedPut> sharedPuts = new ConcurrentHashMap<>();
  private final ReentrantReadWriteLock lifecycle = new ReentrantReadWriteLock();
  private final Set<String> usedParents = ConcurrentHashMap.newKeySet();
  private final Set<String> inspected = new HashSet<>();
  // Protected by refresh's monitor. Input metadata is only a hint to retry full validation.
  // Unchanged mismatches retain the negative TTL, avoiding full scans on every cache miss.
  private final Map<Path, LocalInputSnapshot.InputStamp> rejectedInputs = new HashMap<>();
  // Retry the previously failing witness first. A match is only permission to validate the
  // entire manifest again, never proof that its other inputs still match.
  private final Map<String, LocalInputSnapshot.Witness> rejectedWitnesses = new HashMap<>();
  private volatile List<Candidate> candidates = List.of();
  private boolean hasRefreshed;
  private long nextRefresh;
  private volatile boolean closed;
  private volatile String failure;
  private final AtomicLong writes = new AtomicLong();
  private final AtomicLong reads = new AtomicLong();
  private final AtomicLong sharedReads = new AtomicLong();
  private final AtomicLong rejected = new AtomicLong();
  private final AtomicLong deduplicatedWrites = new AtomicLong();
  private volatile long readLimitSkips;
  private volatile long scanLimitHits;
  private volatile int peakCandidateCount;
  private volatile long peakCandidateMetadataBytes;

  LocalCacheSession(Path root, LocalInputSnapshot inputs) throws IOException {
    this(root, inputs, System::nanoTime);
  }

  LocalCacheSession(Path root, LocalInputSnapshot inputs, LongSupplier nanoTime)
      throws IOException {
    this(root, inputs, nanoTime, ReadLimits.DEFAULT);
  }

  LocalCacheSession(Path root, LocalInputSnapshot inputs, LongSupplier nanoTime, ReadLimits limits)
      throws IOException {
    this.limits = limits;
    this.inputs = inputs;
    this.nanoTime = nanoTime;
    durableDirectory(root.toAbsolutePath().normalize());
    this.objects = durableDirectory(root.resolve("objects").resolve("packed-v1"));
    this.publications =
        durableDirectory(root.resolve("publications-v2").resolve(inputs.sourceDigest()));
    this.id =
        sha256(UUID.randomUUID().toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
  }

  String sourceDigest() {
    return inputs.sourceDigest();
  }

  long sharedReads() {
    return sharedReads.get();
  }

  long deduplicatedWrites() {
    return deduplicatedWrites.get();
  }

  String failureReason() {
    return failure;
  }

  /** Prevents any optional upload failure from exposing a partial publication. */
  boolean discardFailedUpload(String reason) {
    lifecycle.writeLock().lock();
    try {
      if (closed) {
        return false;
      }
      failure = reason;
      return true;
    } finally {
      lifecycle.writeLock().unlock();
    }
  }

  String statisticsJson(boolean published) {
    return String.format(
        java.util.Locale.ROOT,
        "{\"writes\":%d,\"reads\":%d,\"shared_reads\":%d,\"rejected_publications\":%d,"
            + "\"deduplicated_writes\":%d,\"published\":%s,\"failed\":%s,"
            + "\"read_limit_skips\":%d,\"scan_limit_hits\":%d,\"peak_candidate_count\":%d,"
            + "\"peak_candidate_metadata_bytes\":%d}",
        writes.get(),
        reads.get(),
        sharedReads.get(),
        rejected.get(),
        deduplicatedWrites.get(),
        published,
        failure != null,
        readLimitSkips,
        scanLimitHits,
        peakCandidateCount,
        peakCandidateMetadataBytes);
  }

  @Override
  public WriteStatus put(KeyBytesProvider key, byte[] bytes) {
    lifecycle.readLock().lock();
    try {
      if (closed || failure != null) {
        return immediateWriteStatus(false);
      }
      ByteString keyBytes = ByteString.copyFrom(key.toBytes());
      Lock keyLock = putLocks.get(keyBytes);
      keyLock.lock();
      try {
        SharedPut previous = sharedPuts.get(keyBytes);
        if (previous != null) {
          if (!previous.valueDigest().equals(sha256(bytes))) {
            throw new IOException("conflicting immutable shared cache value for reused key");
          }
          // Recheck the pinned record. If it disappeared or was damaged, repair with a private
          // write, preserving the first put's value and the read-your-writes guarantee.
          if (Arrays.equals(read(previous.candidate().store(), key), bytes)) {
            deduplicatedWrites.incrementAndGet();
            return immediateWriteStatus(false);
          }
        } else {
          SharedPut shared = findIdenticalCandidate(key, bytes);
          if (shared == null) {
            try {
              refresh();
            } catch (IOException ignored) {
              // Optional candidate discovery must not prevent an otherwise valid private write.
            }
            shared = findIdenticalCandidate(key, bytes);
          }
          if (shared != null && read(privateStore, key) == null) {
            sharedPuts.put(keyBytes, shared);
            usedParents.add(shared.candidate().id());
            deduplicatedWrites.incrementAndGet();
            return immediateWriteStatus(false);
          }
        }
        boolean novel = storeForWrites().put(key, bytes).get();
        if (novel) {
          writes.incrementAndGet();
        }
        return immediateWriteStatus(novel);
      } finally {
        keyLock.unlock();
      }
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      failure = "interrupted cache write";
      return immediateWriteStatus(false);
    } catch (ExecutionException e) {
      // Optional-cache failures discard this publication, not the user's build.
      failure = e.getCause().toString();
      return immediateWriteStatus(false);
    } catch (IOException e) {
      failure = e.toString();
      return immediateWriteStatus(false);
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  private synchronized PackedFingerprintValueStore storeForWrites() throws IOException {
    if (privateStore == null) {
      privateStore = PackedFingerprintValueStore.create(objects, id);
    }
    return privateStore;
  }

  private SharedPut findIdenticalCandidate(KeyBytesProvider key, byte[] expected)
      throws InterruptedException {
    for (Candidate candidate : candidates) {
      byte[] value = read(candidate.store(), key);
      if (Arrays.equals(value, expected)) {
        return new SharedPut(candidate, sha256(value));
      }
    }
    return null;
  }

  @Override
  public ListenableFuture<byte[]> get(KeyBytesProvider key) {
    lifecycle.readLock().lock();
    try {
      if (closed) {
        return immediateFailedFuture(new MissingFingerprintValueException(key));
      }
      reads.incrementAndGet();
      byte[] own = read(privateStore, key);
      if (own != null) {
        return immediateFuture(own);
      }
      if (!sharedPuts.isEmpty()) {
        SharedPut pinned = sharedPuts.get(ByteString.copyFrom(key.toBytes()));
        if (pinned != null) {
          byte[] value = read(pinned.candidate().store(), key);
          if (value == null || !pinned.valueDigest().equals(sha256(value))) {
            return immediateFailedFuture(new MissingFingerprintValueException(key));
          }
          sharedReads.incrementAndGet();
          return immediateFuture(value);
        }
      }
      byte[] shared = readCandidates(key);
      if (shared == null) {
        refresh();
        shared = readCandidates(key);
      }
      return shared == null
          ? immediateFailedFuture(new MissingFingerprintValueException(key))
          : immediateFuture(shared);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      return immediateFailedFuture(e);
    } catch (IOException e) {
      return immediateFailedFuture(new MissingFingerprintValueException(key, e));
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  private byte[] readCandidates(KeyBytesProvider key) throws InterruptedException {
    for (Candidate candidate : candidates) {
      byte[] value = read(candidate.store(), key);
      if (value != null) {
        usedParents.add(candidate.id());
        sharedReads.incrementAndGet();
        return value;
      }
    }
    return null;
  }

  private static byte[] read(PackedFingerprintValueStore store, KeyBytesProvider key)
      throws InterruptedException {
    if (store == null) {
      return null;
    }
    try {
      return store.get(key).get();
    } catch (ExecutionException e) {
      return null;
    }
  }

  private synchronized void refresh() throws IOException {
    long now = nanoTime.getAsLong();
    if (hasRefreshed && now - nextRefresh < 0 && !rejectedInputsChanged()) {
      return;
    }
    hasRefreshed = true;
    try {
      refreshCandidates();
    } finally {
      // A large validation can exceed the interval itself. Starting the interval before that
      // work allowed every queued lookup to immediately repeat the same expensive scan.
      nextRefresh = nanoTime.getAsLong() + 1_000_000_000L;
    }
  }

  // Called with refresh's monitor held.
  private void refreshCandidates() throws IOException {
    rejectedInputs.clear();
    // Equal immutable lists describe exactly the same input checks. Reuse only checks made in
    // this pass; a later discovery must observe content changes even if metadata is unchanged.
    // Keys are lists already retained by opened candidates, so the bounded map retains no
    // additional unique input objects. List equality checks paths, operation kinds and digests.
    var validatedWitnessLists =
        new HashMap<List<LocalInputSnapshot.Witness>, List<LocalInputSnapshot.Witness>>();
    var available = new HashMap<String, Candidate>();
    for (Candidate candidate : candidates) {
      available.put(candidate.id(), candidate);
    }
    long metadataBytes = candidates.stream().mapToLong(Candidate::metadataBytes).sum();
    List<Path> paths;
    try (var stream = Files.list(publications)) {
      // Bound the listing itself before sorting or retaining filenames. An overfull directory
      // admits no new candidates; already pinned readers remain usable for this invocation.
      paths = stream.limit((long) limits.scanEntries() + 1).toList();
    }
    if (paths.size() > limits.scanEntries()) {
      scanLimitHits++;
      return;
    }
    paths =
        paths.stream()
            .filter(p -> p.getFileName().toString().endsWith(".manifest"))
            .sorted()
            .toList();
    var filenames = new HashSet<String>();
    paths.forEach(path -> filenames.add(path.getFileName().toString()));
    // Deleted/replaced directory entries must not leave unbounded negative bookkeeping behind.
    inspected.retainAll(filenames);
    rejectedWitnesses.keySet().retainAll(filenames);
    List<Candidate> opened = new ArrayList<>();
    try {
      for (Path path : paths) {
        String filename = path.getFileName().toString();
        if (inspected.contains(filename)) {
          continue;
        }
        if (available.size() >= limits.publications()) {
          readLimitSkips++;
          continue;
        }
        inspected.add(filename);
        try {
          var previousMismatch = rejectedWitnesses.get(filename);
          if (previousMismatch != null && !inputs.matches(previousMismatch)) {
            rejectWitness(filename, previousMismatch);
            continue;
          }
          long remaining = limits.metadataBytes() - metadataBytes;
          if (Files.size(path) > remaining) {
            readLimitSkips++;
            inspected.remove(filename);
            continue;
          }
          Manifest manifest = readManifest(path, remaining);
          long indexBudget = remaining - manifest.encodedBytes();
          if (Files.size(objects.resolve(manifest.id()).resolve("index")) > indexBudget) {
            readLimitSkips++;
            inspected.remove(filename);
            continue;
          }
          var witnesses = validatedWitnessLists.get(manifest.witnesses());
          boolean matches = true;
          if (witnesses == null) {
            witnesses = manifest.witnesses();
            for (var witness : witnesses) {
              if (!inputs.matches(witness)) {
                rejectWitness(filename, witness);
                matches = false;
                break;
              }
            }
          }
          if (matches) {
            rejectedWitnesses.remove(filename);
            var store = PackedFingerprintValueStore.open(objects, manifest.id(), indexBudget);
            Candidate candidate =
                new Candidate(
                    manifest.id(),
                    store,
                    witnesses,
                    manifest.parents(),
                    manifest.encodedBytes() + store.indexBytes());
            opened.add(candidate);
            available.put(candidate.id(), candidate);
            metadataBytes += candidate.metadataBytes();
            peakCandidateCount = Math.max(peakCandidateCount, available.size());
            peakCandidateMetadataBytes = Math.max(peakCandidateMetadataBytes, metadataBytes);
            if (validatedWitnessLists.size() < MAX_VALIDATED_WITNESS_LISTS) {
              validatedWitnessLists.put(witnesses, witnesses);
            }
          }
        } catch (IOException e) {
          rejected.incrementAndGet();
        }
      }
      // Existing candidates already have a complete closure. Only new candidates can be pruned.
      boolean removed;
      do {
        removed = available.values().removeIf(c -> !available.keySet().containsAll(c.parents()));
      } while (removed);
      candidates = List.copyOf(available.values());
    } finally {
      // Rejected or incomplete publications must not retain a pack descriptor or index.
      // Previously published candidates stay open until finish, while other gets may use them.
      Set<String> retained = new HashSet<>();
      for (Candidate candidate : candidates) {
        retained.add(candidate.id());
      }
      IOException failure = null;
      for (Candidate candidate : opened) {
        if (!retained.contains(candidate.id())) {
          try {
            candidate.store().close();
          } catch (IOException e) {
            if (failure == null) {
              failure = e;
            } else {
              failure.addSuppressed(e);
            }
          }
        }
      }
      if (failure != null) {
        throw failure;
      }
    }
  }

  private void rejectWitness(String filename, LocalInputSnapshot.Witness witness)
      throws IOException {
    var stamp = inputs.inputStamp(witness);
    rejectedInputs.put(stamp.path(), stamp);
    rejectedWitnesses.put(filename, witness);
    rejected.incrementAndGet();
    // Repositories may materialize later in this invocation; retry these mismatches.
    inspected.remove(filename);
  }

  private boolean rejectedInputsChanged() throws IOException {
    for (var stamp : rejectedInputs.values()) {
      if (!stamp.equals(LocalInputSnapshot.inputStamp(stamp.path()))) {
        return true;
      }
    }
    return false;
  }

  boolean finish(boolean success) throws IOException {
    lifecycle.writeLock().lock();
    try {
      if (closed) {
        return false;
      }
      closed = true;
      if (!success || failure != null || writes.get() == 0) {
        return false;
      }
      inputs.recordRepositoryBindings();
      // Revalidate inherited evidence now, even when discovery already validated it. Equal
      // lists need only one include in this finish call; retain every used parent ID below.
      var inheritedWitnessLists = new HashSet<List<LocalInputSnapshot.Witness>>();
      for (Candidate candidate : candidates) {
        if (usedParents.contains(candidate.id())
            && !inheritedWitnessLists.contains(candidate.witnesses())) {
          inputs.include(candidate.witnesses());
          if (inheritedWitnessLists.size() < MAX_VALIDATED_WITNESS_LISTS) {
            inheritedWitnessLists.add(candidate.witnesses());
          }
        }
      }
      if (!inputs.isUnchanged()) {
        failure = "inputs changed during the invocation";
        return false;
      }
      var buffer = new ByteArrayOutputStream();
      try (var out = new DataOutputStream(buffer)) {
        out.writeInt(MAGIC);
        out.writeUTF(id);
        out.writeUTF(inputs.sourceDigest());
        out.writeInt(usedParents.size());
        for (String parent : usedParents.stream().sorted().toList()) {
          out.writeUTF(parent);
        }
        List<LocalInputSnapshot.Witness> witnesses = inputs.witnesses();
        out.writeInt(witnesses.size());
        for (var witness : witnesses) {
          out.writeUTF(witness.path());
          out.writeBoolean(witness.listing());
          out.writeUTF(witness.digest());
        }
      }
      byte[] payload = buffer.toByteArray();
      if (payload.length > MAX_MANIFEST_BYTES) {
        throw new IOException("input manifest exceeds size limit");
      }
      // No manifest can reference private bytes that have not reached durable storage.
      privateStore.seal();
      Path temporary =
          Files.createTempFile(
              publications,
              ".pending-",
              ".tmp",
              PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
      try {
        try (FileChannel channel = FileChannel.open(temporary, WRITE)) {
          ByteBuffer contents =
              ByteBuffer.wrap(
                  com.google.common.primitives.Bytes.concat(
                      payload, HexFormat.of().parseHex(sha256(payload))));
          while (contents.hasRemaining()) {
            channel.write(contents);
          }
          channel.force(true);
        }
        Files.createLink(publications.resolve(id + ".manifest"), temporary);
        syncDirectory(publications);
      } finally {
        Files.deleteIfExists(temporary);
      }
      return true;
    } finally {
      try {
        closeStores();
      } finally {
        lifecycle.writeLock().unlock();
      }
    }
  }

  // finish owns the lifecycle write lock, so no get or put can still be using a descriptor.
  private void closeStores() throws IOException {
    List<PackedFingerprintValueStore> stores = new ArrayList<>();
    if (privateStore != null) {
      stores.add(privateStore);
      privateStore = null;
    }
    for (Candidate candidate : candidates) {
      stores.add(candidate.store());
    }
    candidates = List.of();
    sharedPuts.clear();
    usedParents.clear();
    inspected.clear();
    rejectedInputs.clear();
    rejectedWitnesses.clear();
    IOException failure = null;
    for (PackedFingerprintValueStore store : stores) {
      try {
        store.close();
      } catch (IOException e) {
        if (failure == null) {
          failure = e;
        } else {
          failure.addSuppressed(e);
        }
      }
    }
    if (failure != null) {
      throw failure;
    }
  }

  private Manifest readManifest(Path path, long remainingBytes) throws IOException {
    byte[] bytes;
    try (var channel = FileChannel.open(path, READ, LinkOption.NOFOLLOW_LINKS)) {
      long size = channel.size();
      if (size < 32 || size > MAX_MANIFEST_BYTES + 32 || size > remainingBytes) {
        throw new IOException("invalid or over-budget manifest length");
      }
      bytes = new byte[(int) size];
      var contents = ByteBuffer.wrap(bytes);
      while (contents.hasRemaining()) {
        if (channel.read(contents) < 0) {
          throw new java.io.EOFException("truncated input manifest");
        }
      }
      if (channel.size() != size) {
        throw new IOException("manifest length changed while reading");
      }
    }
    byte[] payload = java.util.Arrays.copyOf(bytes, bytes.length - 32);
    if (!MessageDigest.isEqual(
        HexFormat.of().parseHex(sha256(payload)),
        java.util.Arrays.copyOfRange(bytes, bytes.length - 32, bytes.length))) {
      throw new IOException("manifest checksum mismatch");
    }
    try (var in = new DataInputStream(new ByteArrayInputStream(payload))) {
      if (in.readInt() != MAGIC) {
        throw new IOException("unsupported publication format");
      }
      String namespace = in.readUTF();
      if (!namespace.matches("[0-9a-f]{64}")
          || !path.getFileName().toString().equals(namespace + ".manifest")
          || !in.readUTF().equals(inputs.sourceDigest())) {
        throw new IOException("invalid publication identity");
      }
      List<String> parents = new ArrayList<>();
      int parentCount = boundedCount(in);
      for (int i = 0; i < parentCount; i++) {
        String parent = in.readUTF();
        if (!parent.matches("[0-9a-f]{64}") || parent.equals(namespace)) {
          throw new IOException("invalid parent publication");
        }
        parents.add(parent);
      }
      List<LocalInputSnapshot.Witness> witnesses = new ArrayList<>();
      int witnessCount = boundedCount(in);
      for (int i = 0; i < witnessCount; i++) {
        witnesses.add(new LocalInputSnapshot.Witness(in.readUTF(), in.readBoolean(), in.readUTF()));
      }
      if (in.read() != -1
          || !Files.isDirectory(objects.resolve(namespace), LinkOption.NOFOLLOW_LINKS)) {
        throw new IOException("incomplete publication");
      }
      return new Manifest(namespace, List.copyOf(witnesses), List.copyOf(parents), bytes.length);
    }
  }

  private static int boundedCount(DataInputStream input) throws IOException {
    int count = input.readInt();
    if (count < 0 || count > MAX_WITNESSES) {
      throw new IOException("manifest count outside bounds");
    }
    return count;
  }

  static Path durableDirectory(Path path) throws IOException {
    Path parent = path.getParent();
    if (parent == null) {
      if (!Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS)) {
        throw new IOException("cache directory has no parent");
      }
      return path;
    }
    // An existing directory may have just been created by another writer that has not synced
    // its parent yet. Ensure the complete directory chain is durable before publishing into it.
    durableDirectory(parent);
    try {
      Files.createDirectory(
          path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
    } catch (java.nio.file.FileAlreadyExistsException e) {
      if (!Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS)) {
        throw e;
      }
    }
    syncDirectory(parent);
    return path;
  }

  private static void syncDirectory(Path path) throws IOException {
    try (FileChannel channel = FileChannel.open(path, READ)) {
      channel.force(true);
    }
  }

  private static String sha256(byte[] bytes) {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }
}
