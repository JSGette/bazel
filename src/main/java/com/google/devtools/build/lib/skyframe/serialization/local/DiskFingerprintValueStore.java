// Copyright 2026 The Bazel Authors. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFailedFuture;
import static com.google.common.util.concurrent.Futures.immediateFuture;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateFailedWriteStatus;
import static com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.immediateWriteStatus;
import static java.nio.file.StandardCopyOption.ATOMIC_MOVE;
import static java.nio.file.StandardCopyOption.REPLACE_EXISTING;
import static java.util.Comparator.comparingLong;

import com.google.common.annotations.VisibleForTesting;
import com.google.common.flogger.GoogleLogger;
import com.google.common.hash.Hashing;
import com.google.common.io.BaseEncoding;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.WriteStatus;
import java.io.IOException;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.LongAdder;

/**
 * A {@link FingerprintValueStore} backed by a directory on the local filesystem.
 *
 * <p>Each entry is a single file at {@code <root>/<2 hex>/<62 hex>}, named after the SHA-256 of the
 * key bytes. The key is hashed rather than used directly because keys are either 16-byte
 * fingerprints or arbitrary strings, and the latter are neither bounded in length nor guaranteed to
 * be valid file names.
 *
 * <p>Reads that fail for any reason are reported as cache misses: a local analysis cache must never
 * be able to break a build.
 */
public final class DiskFingerprintValueStore implements FingerprintValueStore {
  private static final GoogleLogger logger = GoogleLogger.forEnclosingClass();

  /** Number of leading hex characters of the entry name used as a subdirectory. */
  private static final int SHARD_LENGTH = 2;

  private static final String TMP_SUBDIR = "tmp";

  private final Path root;
  private final Path tmpDir;

  private final LongAdder valueBytesReceived = new LongAdder();
  private final LongAdder valueBytesSent = new LongAdder();
  private final LongAdder keyBytesSent = new LongAdder();
  private final LongAdder entriesWritten = new LongAdder();
  private final LongAdder entriesFound = new LongAdder();
  private final LongAdder entriesNotFound = new LongAdder();

  /** Outcome of a {@link #sweep} call. */
  public record SweepResult(
      int entriesScanned, int entriesDeleted, long bytesBefore, long bytesAfter) {}

  private DiskFingerprintValueStore(Path root, Path tmpDir) {
    this.root = root;
    this.tmpDir = tmpDir;
  }

  /**
   * Creates a store rooted at {@code root}, creating the directory layout if it does not exist.
   *
   * <p>All shard directories are created eagerly so that {@link #put} never has to.
   */
  public static DiskFingerprintValueStore create(Path root) throws IOException {
    Path tmpDir = root.resolve(TMP_SUBDIR);
    Files.createDirectories(tmpDir);
    for (int i = 0; i < 1 << (4 * SHARD_LENGTH); i++) {
      Files.createDirectories(root.resolve(String.format("%02x", i)));
    }
    return new DiskFingerprintValueStore(root, tmpDir);
  }

  @Override
  public WriteStatus put(KeyBytesProvider fingerprint, byte[] serializedBytes) {
    byte[] keyBytes = fingerprint.toBytes();
    Path target = pathFor(keyBytes);
    try {
      // Written to a temporary file first so that a concurrent or subsequent reader never observes
      // a partially written entry.
      Path tmp = Files.createTempFile(tmpDir, "put", null);
      try {
        Files.write(tmp, serializedBytes);
        Files.move(tmp, target, REPLACE_EXISTING, ATOMIC_MOVE);
      } catch (IOException e) {
        Files.deleteIfExists(tmp);
        throw e;
      }
    } catch (IOException e) {
      return immediateFailedWriteStatus(e);
    }
    keyBytesSent.add(keyBytes.length);
    valueBytesSent.add(serializedBytes.length);
    entriesWritten.increment();
    return immediateWriteStatus();
  }

  @Override
  public ListenableFuture<byte[]> get(KeyBytesProvider fingerprint) {
    byte[] serializedBytes;
    try {
      serializedBytes = Files.readAllBytes(pathFor(fingerprint.toBytes()));
    } catch (NoSuchFileException e) {
      entriesNotFound.increment();
      return immediateFailedFuture(new MissingFingerprintValueException(fingerprint, e));
    } catch (IOException e) {
      logger.atWarning().withCause(e).log(
          "Treating unreadable entry for %s as a miss", fingerprint);
      entriesNotFound.increment();
      return immediateFailedFuture(new MissingFingerprintValueException(fingerprint, e));
    }
    entriesFound.increment();
    valueBytesReceived.add(serializedBytes.length);
    return immediateFuture(serializedBytes);
  }

  @Override
  public Stats getStats() {
    return new Stats(
        valueBytesReceived.sum(),
        valueBytesSent.sum(),
        keyBytesSent.sum(),
        entriesWritten.sum(),
        entriesFound.sum(),
        entriesNotFound.sum(),
        entriesFound.sum() + entriesNotFound.sum(),
        entriesWritten.sum());
  }

  /**
   * Deletes entries until the store holds at most {@code maxBytes}.
   *
   * <p>Eviction order is by last modification time, which approximates insertion order rather than
   * true LRU: reads deliberately do not touch the file to keep the read path free of writes. Hot
   * entries are expected to be served by an in-memory tier layered on top of this store.
   */
  public SweepResult sweep(long maxBytes) throws IOException {
    List<Entry> entries = new ArrayList<>();
    long totalBytes = 0;
    var visitor =
        new SimpleFileVisitor<Path>() {
          @Override
          public FileVisitResult preVisitDirectory(Path dir, BasicFileAttributes attrs) {
            return dir.equals(tmpDir) ? FileVisitResult.SKIP_SUBTREE : FileVisitResult.CONTINUE;
          }

          @Override
          public FileVisitResult visitFile(Path file, BasicFileAttributes attrs) {
            entries.add(new Entry(file, attrs.size(), attrs.lastModifiedTime().toMillis()));
            return FileVisitResult.CONTINUE;
          }
        };
    Files.walkFileTree(root, visitor);
    for (Entry entry : entries) {
      totalBytes += entry.sizeBytes();
    }
    if (totalBytes <= maxBytes) {
      return new SweepResult(entries.size(), 0, totalBytes, totalBytes);
    }

    entries.sort(comparingLong(Entry::lastModifiedMillis));
    long remainingBytes = totalBytes;
    int deleted = 0;
    for (Entry entry : entries) {
      if (remainingBytes <= maxBytes) {
        break;
      }
      try {
        if (Files.deleteIfExists(entry.path())) {
          remainingBytes -= entry.sizeBytes();
          deleted++;
        }
      } catch (IOException e) {
        logger.atWarning().withCause(e).log("Failed to evict %s", entry.path());
      }
    }
    return new SweepResult(entries.size(), deleted, totalBytes, remainingBytes);
  }

  @VisibleForTesting
  Path pathFor(KeyBytesProvider key) {
    return pathFor(key.toBytes());
  }

  private Path pathFor(byte[] keyBytes) {
    String name =
        BaseEncoding.base16().lowerCase().encode(Hashing.sha256().hashBytes(keyBytes).asBytes());
    return root.resolve(name.substring(0, SHARD_LENGTH)).resolve(name.substring(SHARD_LENGTH));
  }

  private record Entry(Path path, long sizeBytes, long lastModifiedMillis) {}
}
