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
  // Only writers need a mutable map. Published readers use fixed-width binary search.
  private final Map<ByteString, Location> pending;
  private final byte[] index;
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
    FileChannel data =
        FileChannel.open(
            namespace.resolve("data.pack"),
            Set.of(CREATE_NEW, READ, WRITE, NOFOLLOW_LINKS),
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
    return new PackedFingerprintValueStore(namespace, data, new ConcurrentHashMap<>(), null);
  }

  /** Opens only a sealed namespace, verifying index bounds, checksum and data-file length. */
  static PackedFingerprintValueStore open(Path format, String id) throws IOException {
    checkId(id);
    Path namespace = format.resolve(id);
    byte[] index;
    if (!Files.isDirectory(namespace, NOFOLLOW_LINKS)) {
      throw new IOException("packed cache namespace is not a directory");
    }
    try (FileChannel channel = FileChannel.open(namespace.resolve("index"), READ, NOFOLLOW_LINKS)) {
      long size = channel.size();
      if (size < INDEX_HEADER_BYTES + 32 || size > MAX_INDEX_BYTES) {
        throw new IOException("packed cache index size outside bounds");
      }
      index = new byte[(int) size];
      read(channel, ByteBuffer.wrap(index), 0);
    }
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
    FileChannel data = FileChannel.open(namespace.resolve("data.pack"), READ, NOFOLLOW_LINKS);
    try {
      if (data.size() != dataSize) {
        throw new IOException("packed cache data length mismatch");
      }
      return new PackedFingerprintValueStore(namespace, data, null, index);
    } catch (IOException | RuntimeException e) {
      data.close();
      throw e;
    }
  }

  private PackedFingerprintValueStore(
      Path namespace, FileChannel data, Map<ByteString, Location> pending, byte[] index) {
    this.namespace = namespace;
    this.data = data;
    this.pending = pending;
    this.index = index;
    this.sealed = pending == null;
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
              ByteBuffer header =
                  ByteBuffer.allocate(RECORD_HEADER_BYTES)
                      .putInt(RECORD_MAGIC)
                      .putInt(key.length)
                      .putInt(value.length)
                      .put(digest(value, value.length));
              write(data, header.flip(), offset);
              write(data, ByteBuffer.wrap(key), offset + RECORD_HEADER_BYTES);
              write(data, ByteBuffer.wrap(value), offset + RECORD_HEADER_BYTES + key.length);
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
      byte[] key = checkedKey(fingerprint);
      byte[] hash = digest(key, key.length);
      Location location = pending == null ? find(hash) : pending.get(ByteString.copyFrom(hash));
      if (location == null) {
        throw new IOException("packed cache key is absent");
      }
      return immediateFuture(readRecord(location, key));
    } catch (IOException e) {
      return immediateFailedFuture(new MissingFingerprintValueException(fingerprint, e));
    } finally {
      lifecycle.readLock().unlock();
    }
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
              .putLong(data.size());
      for (var entry : entries) {
        buffer
            .put(entry.getKey().toByteArray())
            .putLong(entry.getValue().offset())
            .putLong(entry.getValue().length());
      }
      buffer.put(digest(buffer.array(), payloadSize)).flip();
      data.force(true);
      Path temporary =
          Files.createTempFile(
              namespace,
              ".pending-",
              ".index",
              PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
      try {
        try (FileChannel channel = FileChannel.open(temporary, WRITE, NOFOLLOW_LINKS)) {
          write(channel, buffer, 0);
          channel.force(true);
        }
        Files.createLink(namespace.resolve("index"), temporary);
        syncDirectory(namespace);
      } finally {
        Files.deleteIfExists(temporary);
      }
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
    ByteBuffer header = ByteBuffer.allocate(RECORD_HEADER_BYTES);
    read(data, header, location.offset());
    header.flip();
    int magic = header.getInt();
    int keyLength = header.getInt();
    int valueLength = header.getInt();
    byte[] checksum = new byte[32];
    header.get(checksum);
    if (magic != RECORD_MAGIC
        || keyLength != expectedKey.length
        || valueLength < 0
        || valueLength > MAX_VALUE_BYTES
        || location.length() != (long) RECORD_HEADER_BYTES + keyLength + valueLength) {
      throw new IOException("invalid packed cache record header");
    }
    byte[] key = new byte[keyLength];
    read(data, ByteBuffer.wrap(key), location.offset() + RECORD_HEADER_BYTES);
    if (!Arrays.equals(key, expectedKey)) {
      throw new IOException("packed cache key mismatch");
    }
    byte[] value = new byte[valueLength];
    read(data, ByteBuffer.wrap(value), location.offset() + RECORD_HEADER_BYTES + keyLength);
    if (!MessageDigest.isEqual(checksum, digest(value, value.length))) {
      throw new IOException("packed cache value checksum mismatch");
    }
    return value;
  }

  @Override
  public void close() throws IOException {
    lifecycle.writeLock().lock();
    try {
      closed = true;
      data.close();
      if (pending != null) {
        pending.clear();
      }
    } finally {
      lifecycle.writeLock().unlock();
    }
  }

  private static byte[] checkedKey(KeyBytesProvider fingerprint) throws IOException {
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
