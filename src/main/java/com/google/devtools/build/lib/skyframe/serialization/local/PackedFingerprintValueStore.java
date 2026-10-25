// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFailedFuture;
import static com.google.common.util.concurrent.Futures.immediateFuture;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateFailedWriteStatus;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateWriteStatus;
import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.CREATE_NEW;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatus;
import com.google.protobuf.ByteString;
import java.io.EOFException;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.ReentrantReadWriteLock;

/**
 * One writer invocation's append-only pack, sealed before publication to other processes.
 *
 * <p>Concurrent threads reserve disjoint ranges and use positional I/O. A put completes when its
 * private bytes are written, not when they are durable: callers MUST call {@link #seal} before
 * publishing a reference to this store. Seal syncs the data and a checksummed sorted index before
 * making the index discoverable. A crashed, unsealed writer is not readable through {@link #open}.
 * Each invocation owns a separate namespace, so independent Bazel servers need no global writer
 * lock. The publication layer owns input validation, dependency closure and retention.
 *
 * <p>Readers retain only a compact index and a file descriptor, never cached record values. This
 * format requires a trusted local POSIX filesystem with hard links and directory fsync.
 */
final class PackedFingerprintValueStore implements FingerprintValueStore, AutoCloseable {
  private static final int RECORD_MAGIC = 0x534b5001;
  private static final int INDEX_MAGIC = 0x534b4901;
  private static final int RECORD_HEADER_BYTES = 44;
  // Coalesce the common small records without copying a maximum-sized payload twice.
  private static final int MAX_COALESCED_RECORD_BYTES = 64 * 1024;
  private static final int INDEX_HEADER_BYTES = 16;
  private static final int INDEX_ENTRY_BYTES = 48;
  private static final int MAX_KEY_BYTES = 4096;
  private static final int MAX_VALUE_BYTES = 64 * 1024 * 1024;
  private static final int MAX_ENTRIES = 1_000_000;
  private static final int MAX_INDEX_BYTES =
      INDEX_HEADER_BYTES + MAX_ENTRIES * INDEX_ENTRY_BYTES + 32;

  private record Location(long offset, int length) {}

  private final Path namespace;
  private final FileChannel data;
  private final SegmentedFingerprintValueStore.FileSource source;
  private final String dataName;
  // Only writers need a mutable map. Published readers use fixed-width binary search.
  private Map<ByteString, Location> pending;
  private byte[] index;
  private final String indexName;
  private final AtomicLong end = new AtomicLong();
  private final ReentrantReadWriteLock lifecycle = new ReentrantReadWriteLock();
  private boolean sealed;
  private boolean closed;

  /** Creates a new, exclusively owned namespace inside an existing durable format directory. */
  static PackedFingerprintValueStore create(Path format, String id) throws IOException {
    checkId(id);
    Path namespace = format.resolve(id);
    Files.createDirectory(
        namespace,
        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
    syncDirectory(format);
    return createFiles(namespace, "data.pack", "index");
  }

  static PackedFingerprintValueStore createSegment(Path namespace, String prefix)
      throws IOException {
    return createFiles(namespace, prefix + ".pack", prefix + ".index");
  }

  private static PackedFingerprintValueStore createFiles(
      Path namespace, String dataName, String indexName) throws IOException {
    FileChannel data =
        FileChannel.open(
            namespace.resolve(dataName),
            Set.of(CREATE_NEW, READ, WRITE, NOFOLLOW_LINKS),
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
    return new PackedFingerprintValueStore(
        namespace, data, new ConcurrentHashMap<>(), null, indexName);
  }

  /** Opens only a sealed namespace, verifying index bounds, checksum and data-file length. */
  static PackedFingerprintValueStore open(Path format, String id) throws IOException {
    return open(format, id, MAX_INDEX_BYTES);
  }

  /** Enforces the caller's remaining metadata budget against the opened file before allocation. */
  static PackedFingerprintValueStore open(Path format, String id, long maxIndexBytes)
      throws IOException {
    checkId(id);
    return openFiles(format.resolve(id), "data.pack", "index", maxIndexBytes);
  }

  static PackedFingerprintValueStore openSegment(Path namespace, String prefix, long maxIndexBytes)
      throws IOException {
    return openFiles(namespace, prefix + ".pack", prefix + ".index", maxIndexBytes);
  }

  private static PackedFingerprintValueStore openFiles(
      Path namespace, String dataName, String indexName, long maxIndexBytes) throws IOException {
    byte[] index;
    if (!Files.isDirectory(namespace, NOFOLLOW_LINKS)) {
      throw new IOException("packed cache namespace is not a directory");
    }
    try (FileChannel channel =
        FileChannel.open(namespace.resolve(indexName), READ, NOFOLLOW_LINKS)) {
      long size = channel.size();
      checkIndexLength(size, maxIndexBytes);
      index = new byte[(int) size];
      read(channel, ByteBuffer.wrap(index), 0);
    }
    long dataSize = checkIndex(index, maxIndexBytes);
    FileChannel data = FileChannel.open(namespace.resolve(dataName), READ, NOFOLLOW_LINKS);
    try {
      if (data.size() != dataSize) {
        throw new IOException("packed cache data length mismatch");
      }
      return new PackedFingerprintValueStore(namespace, data, null, index, indexName);
    } catch (IOException | RuntimeException e) {
      data.close();
      throw e;
    }
  }

  static PackedFingerprintValueStore openSegment(
      SegmentedFingerprintValueStore.FileSource source, String prefix, long maxIndexBytes)
      throws IOException {
    String indexName = prefix + ".index";
    String dataName = prefix + ".pack";
    long size = source.size(indexName);
    checkIndexLength(size, maxIndexBytes);
    byte[] index = source.read(indexName, Math.min(maxIndexBytes, MAX_INDEX_BYTES)).clone();
    if (index.length != size) {
      throw new IOException("packed cache index length mismatch");
    }
    long dataSize = checkIndex(index, maxIndexBytes);
    if (source.size(dataName) != dataSize) {
      throw new IOException("packed cache data length mismatch");
    }
    return new PackedFingerprintValueStore(source, dataName, index, indexName);
  }

  private static void checkIndexLength(long size, long maxIndexBytes) throws IOException {
    if (size < INDEX_HEADER_BYTES + 32 || size > MAX_INDEX_BYTES || size > maxIndexBytes) {
      throw new IOException("packed cache index size outside bounds");
    }
  }

  private static long checkIndex(byte[] index, long maxIndexBytes) throws IOException {
    checkIndexLength(index.length, maxIndexBytes);
    int payloadSize = index.length - 32;
    if (!MessageDigest.isEqual(
        digest(index, payloadSize), Arrays.copyOfRange(index, payloadSize, index.length))) {
      throw new IOException("packed cache index checksum mismatch");
    }
    ByteBuffer header = ByteBuffer.wrap(index);
    int magic = header.getInt();
    int count = header.getInt();
    long dataSize = header.getLong();
    if (magic != INDEX_MAGIC
        || count < 0
        || count > MAX_ENTRIES
        || dataSize < 0
        || payloadSize != INDEX_HEADER_BYTES + (long) count * INDEX_ENTRY_BYTES) {
      throw new IOException("invalid packed cache index header");
    }
    for (int i = 0; i < count; i++) {
      int at = INDEX_HEADER_BYTES + i * INDEX_ENTRY_BYTES;
      if (i > 0
          && Arrays.compareUnsigned(
                  index, at - INDEX_ENTRY_BYTES, at - INDEX_ENTRY_BYTES + 32, index, at, at + 32)
              >= 0) {
        throw new IOException("packed cache index keys are not strictly ordered");
      }
      long offset = header.getLong(at + 32);
      long length = header.getLong(at + 40);
      if (offset < 0
          || length < RECORD_HEADER_BYTES + 1
          || length > (long) RECORD_HEADER_BYTES + MAX_KEY_BYTES + MAX_VALUE_BYTES
          || offset > dataSize - length) {
        throw new IOException("packed cache record location outside bounds");
      }
    }
    return dataSize;
  }

  int indexBytes() {
    return index == null ? 0 : index.length;
  }

  private PackedFingerprintValueStore(
      Path namespace,
      FileChannel data,
      Map<ByteString, Location> pending,
      byte[] index,
      String indexName) {
    this.namespace = namespace;
    this.indexName = indexName;
    this.data = data;
    this.source = null;
    this.dataName = "";
    this.pending = pending;
    this.index = index;
    this.sealed = pending == null;
  }

  private PackedFingerprintValueStore(
      SegmentedFingerprintValueStore.FileSource source,
      String dataName,
      byte[] index,
      String indexName) {
    this.namespace = null;
    this.data = null;
    this.source = source;
    this.dataName = dataName;
    this.pending = null;
    this.index = index;
    this.indexName = indexName;
    this.sealed = true;
  }

  @Override
  public WriteStatus put(KeyBytesProvider fingerprint, byte[] serializedBytes) {
    lifecycle.readLock().lock();
    try {
      if (closed || sealed) {
        throw new IOException("packed cache writer is closed or sealed");
      }
      byte[] key = checkedKey(fingerprint);
      if (serializedBytes.length > MAX_VALUE_BYTES) {
        throw new IOException("packed cache value exceeds size limit");
      }
      byte[] value = serializedBytes.clone();
      boolean[] novel = {false};
      pending.compute(
          ByteString.copyFrom(digest(key, key.length)),
          (hash, previous) -> {
            try {
              if (previous != null) {
                if (!Arrays.equals(readRecord(previous, key), value)) {
                  throw new IOException("conflicting immutable shared cache value");
                }
                return previous;
              }
              int length = RECORD_HEADER_BYTES + key.length + value.length;
              long offset = end.getAndAdd(length);
              boolean coalesced = length <= MAX_COALESCED_RECORD_BYTES;
              ByteBuffer record =
                  ByteBuffer.allocate(coalesced ? length : RECORD_HEADER_BYTES + key.length)
                      .putInt(RECORD_MAGIC)
                      .putInt(key.length)
                      .putInt(value.length)
                      .put(digest(value, value.length))
                      .put(key);
              if (coalesced) {
                record.put(value);
              }
              write(Objects.requireNonNull(data), record.flip(), offset);
              if (!coalesced) {
                write(
                    Objects.requireNonNull(data),
                    ByteBuffer.wrap(value),
                    offset + RECORD_HEADER_BYTES + key.length);
              }
              novel[0] = true;
              return new Location(offset, length);
            } catch (IOException e) {
              throw new UncheckedIOException(e);
            }
          });
      return immediateWriteStatus(novel[0]);
    } catch (IOException e) {
      return immediateFailedWriteStatus(e);
    } catch (UncheckedIOException e) {
      return immediateFailedWriteStatus(e.getCause());
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  @Override
  public ListenableFuture<byte[]> get(KeyBytesProvider fingerprint) {
    lifecycle.readLock().lock();
    try {
      if (closed) {
        throw new IOException("packed cache is closed");
      }
      byte[] value = getIfPresent(fingerprint);
      if (value == null) {
        throw new IOException("packed cache key is absent");
      }
      return immediateFuture(value);
    } catch (IOException e) {
      return immediateFailedFuture(new MissingFingerprintValueException(fingerprint, e));
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  byte[] getIfPresent(KeyBytesProvider fingerprint) throws IOException {
    byte[] key = checkedKey(fingerprint);
    return getIfPresent(key, digest(key, key.length));
  }

  // A segmented lookup computes and owns these bytes once, across all of its segment probes.
  byte[] getIfPresent(byte[] key, byte[] hash) throws IOException {
    lifecycle.readLock().lock();
    try {
      if (closed) {
        throw new IOException("packed cache is closed");
      }
      Location location = pending == null ? find(hash) : pending.get(ByteString.copyFrom(hash));
      return location == null ? null : readRecord(location, key);
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  long dataBytes() throws IOException {
    return source == null ? Objects.requireNonNull(data).size() : source.size(dataName);
  }

  byte[] indexDigest() {
    return Arrays.copyOfRange(index, index.length - 32, index.length);
  }

  /** Makes all completed writes durable before the caller publishes the enclosing manifest. */
  void seal() throws IOException {
    lifecycle.writeLock().lock();
    try {
      if (closed) {
        throw new IOException("packed cache is closed");
      }
      if (sealed) {
        return;
      }
      FileChannel localData = Objects.requireNonNull(data);
      Path localNamespace = Objects.requireNonNull(namespace);
      if (pending.size() > MAX_ENTRIES) {
        throw new IOException("packed cache entry count exceeds size limit");
      }
      var entries =
          pending.entrySet().stream()
              .sorted(Map.Entry.comparingByKey(ByteString.unsignedLexicographicalComparator()))
              .toList();
      int payloadSize = INDEX_HEADER_BYTES + entries.size() * INDEX_ENTRY_BYTES;
      ByteBuffer buffer =
          ByteBuffer.allocate(payloadSize + 32)
              .putInt(INDEX_MAGIC)
              .putInt(entries.size())
              .putLong(localData.size());
      for (var entry : entries) {
        buffer
            .put(entry.getKey().toByteArray())
            .putLong(entry.getValue().offset())
            .putLong(entry.getValue().length());
      }
      buffer.put(digest(buffer.array(), payloadSize)).flip();
      localData.force(true);
      Path temporary =
          Files.createTempFile(
              localNamespace,
              ".pending-",
              ".index",
              PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
      try {
        try (FileChannel channel = FileChannel.open(temporary, WRITE, NOFOLLOW_LINKS)) {
          write(channel, buffer, 0);
          channel.force(true);
        }
        Files.createLink(localNamespace.resolve(indexName), temporary);
        syncDirectory(localNamespace);
      } finally {
        Files.deleteIfExists(temporary);
      }
      index = buffer.array();
      pending.clear();
      pending = null;
      sealed = true;
    } finally {
      lifecycle.writeLock().unlock();
    }
  }

  private Location find(byte[] hash) {
    int low = 0;
    int high = ByteBuffer.wrap(index).getInt(4) - 1;
    while (low <= high) {
      int middle = (low + high) >>> 1;
      int at = INDEX_HEADER_BYTES + middle * INDEX_ENTRY_BYTES;
      int comparison = Arrays.compareUnsigned(index, at, at + 32, hash, 0, hash.length);
      if (comparison < 0) {
        low = middle + 1;
      } else if (comparison > 0) {
        high = middle - 1;
      } else {
        var buffer = ByteBuffer.wrap(index);
        return new Location(buffer.getLong(at + 32), (int) buffer.getLong(at + 40));
      }
    }
    return null;
  }

  private byte[] readRecord(Location location, byte[] expectedKey) throws IOException {
    int prefixLength = RECORD_HEADER_BYTES + expectedKey.length;
    if (location.length() < prefixLength
        || location.length() > (long) prefixLength + MAX_VALUE_BYTES) {
      throw new IOException("invalid packed cache record length");
    }
    boolean coalesced = location.length() <= MAX_COALESCED_RECORD_BYTES;
    ByteBuffer record = ByteBuffer.allocate(coalesced ? location.length() : prefixLength);
    readData(record, location.offset());
    record.flip();
    int magic = record.getInt();
    int keyLength = record.getInt();
    int valueLength = record.getInt();
    byte[] checksum = new byte[32];
    record.get(checksum);
    if (magic != RECORD_MAGIC
        || keyLength != expectedKey.length
        || valueLength < 0
        || valueLength > MAX_VALUE_BYTES
        || location.length() != (long) RECORD_HEADER_BYTES + keyLength + valueLength) {
      throw new IOException("invalid packed cache record header");
    }
    if (!Arrays.equals(
        record.array(), RECORD_HEADER_BYTES, prefixLength, expectedKey, 0, expectedKey.length)) {
      throw new IOException("packed cache key mismatch");
    }
    byte[] value;
    if (coalesced) {
      value = Arrays.copyOfRange(record.array(), prefixLength, location.length());
    } else {
      value = new byte[valueLength];
      readData(ByteBuffer.wrap(value), location.offset() + prefixLength);
    }
    if (!MessageDigest.isEqual(checksum, digest(value, value.length))) {
      throw new IOException("packed cache value checksum mismatch");
    }
    return value;
  }

  private void readData(ByteBuffer target, long offset) throws IOException {
    if (source == null) {
      read(Objects.requireNonNull(data), target, offset);
    } else {
      source.read(dataName, target, offset);
      if (target.hasRemaining()) {
        throw new EOFException("truncated packed cache data");
      }
    }
  }

  @Override
  public void close() throws IOException {
    lifecycle.writeLock().lock();
    try {
      closed = true;
      if (data != null) {
        data.close();
      }
      if (pending != null) {
        pending.clear();
      }
    } finally {
      lifecycle.writeLock().unlock();
    }
  }

  static byte[] checkedKey(KeyBytesProvider fingerprint) throws IOException {
    byte[] key = fingerprint.toBytes();
    if (key.length == 0 || key.length > MAX_KEY_BYTES) {
      throw new IOException("packed cache key length outside bounds");
    }
    return key.clone();
  }

  private static void checkId(String id) {
    if (!id.matches("[0-9a-f]{64}")) {
      throw new IllegalArgumentException("packed namespace must be 64 lowercase hex digits");
    }
  }

  private static byte[] digest(byte[] bytes, int length) {
    try {
      MessageDigest digest = MessageDigest.getInstance("SHA-256");
      digest.update(bytes, 0, length);
      return digest.digest();
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }

  private static void syncDirectory(Path directory) throws IOException {
    try (FileChannel channel = FileChannel.open(directory, READ)) {
      channel.force(true);
    }
  }

  private static void read(FileChannel channel, ByteBuffer buffer, long offset) throws IOException {
    while (buffer.hasRemaining()) {
      int count = channel.read(buffer, offset);
      if (count < 0) {
        throw new EOFException("truncated packed cache data");
      }
      offset += count;
    }
  }

  private static void write(FileChannel channel, ByteBuffer buffer, long offset)
      throws IOException {
    while (buffer.hasRemaining()) {
      offset += channel.write(buffer, offset);
    }
  }
}
