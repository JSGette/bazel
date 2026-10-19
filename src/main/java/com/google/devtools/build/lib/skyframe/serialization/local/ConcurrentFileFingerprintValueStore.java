// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFailedFuture;
import static com.google.common.util.concurrent.Futures.immediateFuture;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateFailedWriteStatus;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateWriteStatus;
import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatus;
import java.io.EOFException;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;
import java.util.HexFormat;

/**
 * Experimental immutable store for concurrent processes on a local POSIX filesystem.
 *
 * <p>A successful put publishes a complete, fsynced entry through an atomic hard link. Writers do
 * not hold an exclusive lifetime lock, and readers use direct lookups without a startup-only index
 * or negative cache. Repeating an identical put is idempotent; a different value for an existing
 * key is an error. The caller owns semantic keying, compatibility, source validation and the
 * publication barrier for any referenced dependency closure. This class does not enable analysis
 * reuse by itself, and local graph identity IDs must never be stored here for cross-server reuse.
 *
 * <p>The root must already exist and be durably created by the caller. This first implementation
 * performs blocking I/O, uses one file per entry and deliberately has no deletion/GC. It is a
 * publication correctness foundation, not the packed, byte-budgeted production backend. Use a
 * trusted same-user directory on a filesystem supporting hard links and directory fsync (tested on
 * Linux/ext4); network filesystems and Windows are not covered by this contract.
 */
public final class ConcurrentFileFingerprintValueStore implements FingerprintValueStore {
  private static final int MAGIC = 0x534b4301;
  private static final int HEADER_BYTES = 3 * Integer.BYTES + 32;
  private static final int MAX_KEY_BYTES = 4096;
  private static final int MAX_VALUE_BYTES = 64 * 1024 * 1024;
  private final Path namespace;

  /** The namespace is a caller-supplied SHA-256 digest of the compatibility domain. */
  public static ConcurrentFileFingerprintValueStore create(Path root, String compatibilityDigest)
      throws IOException {
    if (!compatibilityDigest.matches("[0-9a-f]{64}")) {
      throw new IllegalArgumentException("compatibility namespace must be 64 lowercase hex digits");
    }
    root = root.toAbsolutePath().normalize();
    if (!Files.isDirectory(root, NOFOLLOW_LINKS)) {
      throw new IOException("cache root must be an existing directory: " + root);
    }
    Path format = directory(root, "shared-v1");
    return new ConcurrentFileFingerprintValueStore(directory(format, compatibilityDigest));
  }

  private ConcurrentFileFingerprintValueStore(Path namespace) {
    this.namespace = namespace;
  }

  @Override
  public WriteStatus put(KeyBytesProvider fingerprint, byte[] serializedBytes) {
    try {
      byte[] key = checkedKey(fingerprint);
      if (serializedBytes.length > MAX_VALUE_BYTES) {
        throw new IOException("shared cache value exceeds size limit");
      }
      byte[] value = serializedBytes.clone();
      Path destination = entry(key);
      Path shard = directory(namespace, destination.getParent().getFileName().toString());
      Path staging =
          Files.createTempFile(
              shard,
              ".pending-",
              ".tmp",
              PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
      boolean novel;
      try {
        try (FileChannel channel = FileChannel.open(staging, WRITE)) {
          ByteBuffer header =
              ByteBuffer.allocate(HEADER_BYTES)
                  .putInt(MAGIC)
                  .putInt(key.length)
                  .putInt(value.length)
                  .put(sha256(value));
          write(channel, header.flip());
          write(channel, ByteBuffer.wrap(key));
          write(channel, ByteBuffer.wrap(value));
          channel.force(true);
        }
        try {
          // ATOMIC_MOVE can replace an existing target on POSIX. A hard link is atomic and
          // fails if a competing publisher won, without exposing partial or replacing bytes.
          Files.createLink(destination, staging);
          novel = true;
        } catch (FileAlreadyExistsException e) {
          if (!Arrays.equals(read(destination, key), value)) {
            throw new IOException("conflicting immutable shared cache value for " + destination, e);
          }
          novel = false;
        }
        syncDirectory(shard);
      } finally {
        // A crash can leave an orphan staging file. Readers never consult staging files.
        Files.deleteIfExists(staging);
      }
      return immediateWriteStatus(novel);
    } catch (IOException e) {
      return immediateFailedWriteStatus(e);
    }
  }

  @Override
  public ListenableFuture<byte[]> get(KeyBytesProvider fingerprint) {
    try {
      byte[] key = checkedKey(fingerprint);
      return immediateFuture(read(entry(key), key));
    } catch (IOException e) {
      // This is optional shared cache data, never an authoritative private graph record.
      return immediateFailedFuture(new MissingFingerprintValueException(fingerprint, e));
    }
  }

  private Path entry(byte[] key) {
    String digest = HexFormat.of().formatHex(sha256(key));
    return namespace.resolve(digest.substring(0, 2)).resolve(digest.substring(2) + ".entry");
  }

  private static byte[] checkedKey(KeyBytesProvider fingerprint) throws IOException {
    byte[] key = fingerprint.toBytes();
    if (key.length == 0 || key.length > MAX_KEY_BYTES) {
      throw new IOException("shared cache key length outside bounds");
    }
    return key.clone();
  }

  private static byte[] read(Path path, byte[] expectedKey) throws IOException {
    try (FileChannel channel = FileChannel.open(path, READ, NOFOLLOW_LINKS)) {
      ByteBuffer header = ByteBuffer.allocate(HEADER_BYTES);
      read(channel, header);
      header.flip();
      int magic = header.getInt();
      int keyLength = header.getInt();
      int valueLength = header.getInt();
      byte[] checksum = new byte[32];
      header.get(checksum);
      if (magic != MAGIC
          || keyLength != expectedKey.length
          || valueLength < 0
          || valueLength > MAX_VALUE_BYTES
          || channel.size() != (long) HEADER_BYTES + keyLength + valueLength) {
        throw new IOException("invalid shared cache record header or length");
      }
      byte[] key = new byte[keyLength];
      read(channel, ByteBuffer.wrap(key));
      if (!Arrays.equals(key, expectedKey)) {
        throw new IOException("shared cache key mismatch");
      }
      byte[] value = new byte[valueLength];
      read(channel, ByteBuffer.wrap(value));
      if (!MessageDigest.isEqual(checksum, sha256(value))) {
        throw new IOException("shared cache checksum mismatch");
      }
      return value;
    }
  }

  private static Path directory(Path parent, String name) throws IOException {
    Path result = parent.resolve(name);
    try {
      Files.createDirectory(
          result,
          PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
    } catch (FileAlreadyExistsException e) {
      if (!Files.isDirectory(result, NOFOLLOW_LINKS)) {
        throw new IOException("shared cache directory is not a directory: " + result, e);
      }
    }
    // Also sync after another writer created it: that writer may not have reached its fsync yet.
    syncDirectory(parent);
    return result;
  }

  private static void syncDirectory(Path directory) throws IOException {
    try (FileChannel channel = FileChannel.open(directory, READ)) {
      channel.force(true);
    }
  }

  private static void read(FileChannel channel, ByteBuffer buffer) throws IOException {
    while (buffer.hasRemaining()) {
      if (channel.read(buffer) < 0) {
        throw new EOFException("truncated shared cache record");
      }
    }
  }

  private static void write(FileChannel channel, ByteBuffer buffer) throws IOException {
    while (buffer.hasRemaining()) {
      channel.write(buffer);
    }
  }

  private static byte[] sha256(byte[] value) {
    try {
      return MessageDigest.getInstance("SHA-256").digest(value);
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }
}
