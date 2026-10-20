// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFailedFuture;
import static com.google.common.util.concurrent.Futures.immediateFuture;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateWriteStatus;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatus;
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
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.ReentrantReadWriteLock;

/**
 * One invocation's private writes and immutable, validated publications from other invocations.
 *
 * <p>Records become discoverable only after successful completion and input validation. A
 * publication lists every older publication from which it read chunks, keeping that closure
 * explicit for later garbage collection. There is no deletion in this initial session format.
 */
final class LocalCacheSession implements FingerprintValueStore {
  private static final int MAGIC = 0x534b5331;
  private static final int MAX_MANIFEST_BYTES = 64 * 1024 * 1024;
  private static final int MAX_WITNESSES = 1_000_000;

  private record Candidate(
      String id,
      ConcurrentFileFingerprintValueStore store,
      List<LocalInputSnapshot.Witness> witnesses,
      List<String> parents) {}

  private final LocalInputSnapshot inputs;
  private final Path objects;
  private final Path publications;
  private final String id;
  private final ConcurrentFileFingerprintValueStore privateStore;
  private final ReentrantReadWriteLock lifecycle = new ReentrantReadWriteLock();
  private final Set<String> usedParents = ConcurrentHashMap.newKeySet();
  private final Set<String> inspected = new HashSet<>();
  private volatile List<Candidate> candidates = List.of();
  private long nextRefresh;
  private volatile boolean closed;
  private volatile String failure;
  private final AtomicLong writes = new AtomicLong();
  private final AtomicLong reads = new AtomicLong();
  private final AtomicLong sharedReads = new AtomicLong();
  private final AtomicLong rejected = new AtomicLong();

  LocalCacheSession(Path root, LocalInputSnapshot inputs) throws IOException {
    this.inputs = inputs;
    durableDirectory(root.toAbsolutePath().normalize());
    this.objects = durableDirectory(root.resolve("objects"));
    this.publications =
        durableDirectory(root.resolve("publications-v1").resolve(inputs.sourceDigest()));
    this.id =
        sha256(UUID.randomUUID().toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
    this.privateStore = ConcurrentFileFingerprintValueStore.create(objects, id);
  }

  String sourceDigest() {
    return inputs.sourceDigest();
  }

  long sharedReads() {
    return sharedReads.get();
  }

  String statisticsJson(boolean published) {
    return String.format(
        java.util.Locale.ROOT,
        "{\"writes\":%d,\"reads\":%d,\"shared_reads\":%d,\"rejected_publications\":%d,"
            + "\"published\":%s,\"failed\":%s}",
        writes.get(),
        reads.get(),
        sharedReads.get(),
        rejected.get(),
        published,
        failure != null);
  }

  @Override
  public WriteStatus put(KeyBytesProvider key, byte[] bytes) {
    lifecycle.readLock().lock();
    try {
      if (closed || failure != null) {
        return immediateWriteStatus(false);
      }
      boolean novel = privateStore.put(key, bytes).get();
      if (novel) {
        writes.incrementAndGet();
      }
      return immediateWriteStatus(novel);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      failure = "interrupted cache write";
      return immediateWriteStatus(false);
    } catch (ExecutionException e) {
      // Optional-cache failures discard this publication, not the user's build.
      failure = e.getCause().toString();
      return immediateWriteStatus(false);
    } finally {
      lifecycle.readLock().unlock();
    }
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

  private static byte[] read(ConcurrentFileFingerprintValueStore store, KeyBytesProvider key)
      throws InterruptedException {
    try {
      return store.get(key).get();
    } catch (ExecutionException e) {
      return null;
    }
  }

  private synchronized void refresh() throws IOException {
    long now = System.nanoTime();
    if (now < nextRefresh) {
      return;
    }
    nextRefresh = now + 1_000_000_000L;
    var available = new HashMap<String, Candidate>();
    for (Candidate candidate : candidates) {
      available.put(candidate.id(), candidate);
    }
    List<Path> paths;
    try (var stream = Files.list(publications)) {
      paths =
          stream.filter(p -> p.getFileName().toString().endsWith(".manifest")).sorted().toList();
    }
    for (Path path : paths) {
      String filename = path.getFileName().toString();
      if (!inspected.add(filename)) {
        continue;
      }
      try {
        Candidate candidate = readManifest(path);
        boolean matches = true;
        for (var witness : candidate.witnesses()) {
          if (!inputs.matches(witness)) {
            matches = false;
            break;
          }
        }
        if (matches) {
          available.put(candidate.id(), candidate);
        } else {
          rejected.incrementAndGet();
          // Repositories may be materialized later in this invocation. A mismatch is a miss now,
          // but it must not permanently hide an otherwise valid publication from this reader.
          inspected.remove(filename);
        }
      } catch (IOException e) {
        rejected.incrementAndGet();
      }
    }
    // Reject incomplete transitive publication closures, including missing/corrupt parents.
    boolean removed;
    do {
      removed = available.values().removeIf(c -> !available.keySet().containsAll(c.parents()));
    } while (removed);
    candidates = List.copyOf(available.values());
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
      for (Candidate candidate : candidates) {
        if (usedParents.contains(candidate.id())) {
          inputs.include(candidate.witnesses());
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
      lifecycle.writeLock().unlock();
    }
  }

  private Candidate readManifest(Path path) throws IOException {
    long size = Files.size(path);
    if (size < 32 || size > MAX_MANIFEST_BYTES + 32) {
      throw new IOException("invalid manifest length");
    }
    byte[] bytes = Files.readAllBytes(path);
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
          || !Files.isDirectory(
              objects.resolve("shared-v1").resolve(namespace), LinkOption.NOFOLLOW_LINKS)) {
        throw new IOException("incomplete publication");
      }
      return new Candidate(
          namespace,
          ConcurrentFileFingerprintValueStore.create(objects, namespace),
          List.copyOf(witnesses),
          List.copyOf(parents));
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
