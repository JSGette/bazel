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
import static java.lang.Math.max;
import static java.nio.file.StandardOpenOption.APPEND;
import static java.nio.file.StandardOpenOption.CREATE;
import static java.nio.file.StandardOpenOption.READ;

import com.google.common.annotations.VisibleForTesting;
import com.google.common.flogger.GoogleLogger;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.WriteStatus;
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.LongAdder;

/**
 * A {@link FingerprintValueStore} that appends entries to pack files and keeps their offsets in
 * memory.
 *
 * <p>An analysis upload writes hundreds of thousands of entries averaging under two kilobytes, so
 * giving each one its own file spends nearly all of the time in the filesystem rather than on the
 * data. Entries are instead appended to {@code <root>/<id>.pack} and located through
 * {@code <root>/<id>.index}, which records the key, offset and length of each entry.
 *
 * <p>A store claims an unused id on startup and only ever appends to that pack, so several servers
 * can share a directory without coordinating. The price is that a server does not see packs written
 * after it started.
 *
 * <p>Indexes are read into memory on startup and never re-read, which costs roughly a hundred bytes
 * of heap per entry. Reads that fail for any reason are reported as cache misses: a local analysis
 * cache must never be able to break a build.
 */
public final class PackedFingerprintValueStore implements FingerprintValueStore {
  private static final GoogleLogger logger = GoogleLogger.forEnclosingClass();

  private static final String PACK_SUFFIX = ".pack";
  private static final String INDEX_SUFFIX = ".index";

  /** Cap on pack size, which bounds how much a single eviction can throw away. */
  @VisibleForTesting static final long MAX_PACK_BYTES = 512L << 20;

  private static final int WRITE_BUFFER_BYTES = 1 << 20;

  private final Path root;
  private final ConcurrentHashMap<Key, Location> locations;
  private final ConcurrentHashMap<Integer, FileChannel> readChannels = new ConcurrentHashMap<>();

  private final Object writeLock = new Object();
  private volatile int writePackId;
  private OutputStream packOut;
  private DataOutputStream indexOut;

  /** Logical end of the current pack, including bytes still sitting in {@link #packOut}. */
  private long writeOffset;

  /** Prefix of the current pack that has reached the filesystem and can therefore be read. */
  private long flushedOffset;

  private final LongAdder valueBytesReceived = new LongAdder();
  private final LongAdder valueBytesSent = new LongAdder();
  private final LongAdder keyBytesSent = new LongAdder();
  private final LongAdder entriesWritten = new LongAdder();
  private final LongAdder entriesFound = new LongAdder();
  private final LongAdder entriesNotFound = new LongAdder();

  /** Outcome of a {@link #sweep} call. */
  public record SweepResult(int packsScanned, int packsDeleted, long bytesBefore, long bytesAfter) {}

  private record Location(int packId, long offset, int length) {}

  /** A map key over raw key bytes, which may come from a fingerprint or an arbitrary string. */
  private static final class Key {
    private final byte[] bytes;
    private final int hash;

    Key(byte[] bytes) {
      this.bytes = bytes;
      this.hash = Arrays.hashCode(bytes);
    }

    @Override
    public boolean equals(Object o) {
      return o instanceof Key key && Arrays.equals(bytes, key.bytes);
    }

    @Override
    public int hashCode() {
      return hash;
    }
  }

  private PackedFingerprintValueStore(Path root, ConcurrentHashMap<Key, Location> locations) {
    this.root = root;
    this.locations = locations;
  }

  /** Creates a store rooted at {@code root}, loading the indexes of any packs already present. */
  public static PackedFingerprintValueStore create(Path root) throws IOException {
    Files.createDirectories(root);
    var locations = new ConcurrentHashMap<Key, Location>();
    int highestPackId = -1;
    // Ascending order means a later pack's entry wins, which matters only for the few keys that
    // are not content addressed.
    for (int packId : packIds(root)) {
      highestPackId = max(highestPackId, packId);
      loadIndex(root, packId, locations);
    }
    var store = new PackedFingerprintValueStore(root, locations);
    store.startPack(highestPackId + 1);
    return store;
  }

  @Override
  public WriteStatus put(KeyBytesProvider fingerprint, byte[] serializedBytes) {
    byte[] keyBytes = fingerprint.toBytes();
    try {
      synchronized (writeLock) {
        if (writeOffset >= MAX_PACK_BYTES) {
          rollPack();
        }
        var location = new Location(writePackId, writeOffset, serializedBytes.length);
        packOut.write(serializedBytes);
        writeOffset += serializedBytes.length;
        indexOut.writeInt(keyBytes.length);
        indexOut.write(keyBytes);
        indexOut.writeLong(location.offset());
        indexOut.writeInt(location.length());
        locations.put(new Key(keyBytes), location);
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
    Location location = locations.get(new Key(fingerprint.toBytes()));
    if (location == null) {
      entriesNotFound.increment();
      return immediateFailedFuture(new MissingFingerprintValueException(fingerprint));
    }
    byte[] serializedBytes;
    try {
      serializedBytes = read(location);
    } catch (IOException e) {
      logger.atWarning().withCause(e).log("Treating unreadable entry for %s as a miss", fingerprint);
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
   * Pushes buffered entries to the filesystem so that another server can see them.
   *
   * <p>Entries still buffered when a server dies are dropped on the next startup, which costs cache
   * hits but cannot produce a wrong answer.
   */
  public void flush() throws IOException {
    synchronized (writeLock) {
      packOut.flush();
      indexOut.flush();
      flushedOffset = writeOffset;
    }
  }

  /**
   * Deletes whole packs, oldest first, until the store holds at most {@code maxBytes}.
   *
   * <p>Eviction is coarse by design: an entry cannot be removed from the middle of a pack without
   * rewriting it. The pack being written is never deleted.
   */
  public SweepResult sweep(long maxBytes) throws IOException {
    List<Integer> packIds = packIds(root);
    long totalBytes = 0;
    for (int packId : packIds) {
      totalBytes += sizeOf(root.resolve(packId + PACK_SUFFIX)) + sizeOf(indexPath(root, packId));
    }
    if (totalBytes <= maxBytes) {
      return new SweepResult(packIds.size(), 0, totalBytes, totalBytes);
    }

    long remainingBytes = totalBytes;
    int deleted = 0;
    for (int packId : packIds) {
      if (remainingBytes <= maxBytes) {
        break;
      }
      if (packId == writePackId) {
        continue;
      }
      remainingBytes -= deletePack(packId);
      deleted++;
    }
    return new SweepResult(packIds.size(), deleted, totalBytes, remainingBytes);
  }

  private byte[] read(Location location) throws IOException {
    ensureReadable(location);
    var buffer = ByteBuffer.allocate(location.length());
    FileChannel channel = channelFor(location.packId());
    while (buffer.hasRemaining()) {
      if (channel.read(buffer, location.offset() + buffer.position()) < 0) {
        throw new EOFException(
            String.format(
                "Pack %d ended before offset %d", location.packId(), location.offset()));
      }
    }
    return buffer.array();
  }

  /** Flushes if {@code location} points into the part of the current pack still being buffered. */
  private void ensureReadable(Location location) throws IOException {
    if (location.packId() != writePackId) {
      return;
    }
    synchronized (writeLock) {
      if (location.offset() + location.length() > flushedOffset) {
        packOut.flush();
        flushedOffset = writeOffset;
      }
    }
  }

  private FileChannel channelFor(int packId) throws IOException {
    FileChannel channel = readChannels.get(packId);
    if (channel != null) {
      return channel;
    }
    FileChannel opened = FileChannel.open(root.resolve(packId + PACK_SUFFIX), READ);
    FileChannel raced = readChannels.putIfAbsent(packId, opened);
    if (raced == null) {
      return opened;
    }
    opened.close();
    return raced;
  }

  private void startPack(int candidateId) throws IOException {
    for (int packId = candidateId; ; packId++) {
      Path pack = root.resolve(packId + PACK_SUFFIX);
      try {
        Files.createFile(pack);
      } catch (FileAlreadyExistsException e) {
        continue; // Another server claimed this id.
      }
      writePackId = packId;
      packOut = new BufferedOutputStream(Files.newOutputStream(pack, APPEND), WRITE_BUFFER_BYTES);
      indexOut =
          new DataOutputStream(
              new BufferedOutputStream(
                  Files.newOutputStream(indexPath(root, packId), CREATE, APPEND),
                  WRITE_BUFFER_BYTES));
      writeOffset = 0;
      flushedOffset = 0;
      return;
    }
  }

  private void rollPack() throws IOException {
    packOut.close();
    indexOut.close();
    startPack(writePackId + 1);
  }

  private long deletePack(int packId) throws IOException {
    long freedBytes = 0;
    FileChannel channel = readChannels.remove(packId);
    if (channel != null) {
      channel.close();
    }
    for (Path path : List.of(root.resolve(packId + PACK_SUFFIX), indexPath(root, packId))) {
      freedBytes += sizeOf(path);
      Files.deleteIfExists(path);
    }
    locations.values().removeIf(location -> location.packId() == packId);
    return freedBytes;
  }

  /** Reads one pack's index, dropping records that point past the end of the pack. */
  private static void loadIndex(
      Path root, int packId, ConcurrentHashMap<Key, Location> locations) {
    long packBytes = sizeOf(root.resolve(packId + PACK_SUFFIX));
    try (var in =
        new DataInputStream(
            new BufferedInputStream(Files.newInputStream(indexPath(root, packId))))) {
      while (true) {
        byte[] keyBytes = new byte[in.readInt()];
        in.readFully(keyBytes);
        long offset = in.readLong();
        int length = in.readInt();
        if (offset + length > packBytes) {
          // The entry was indexed but its bytes never reached the pack, so the server died here.
          break;
        }
        locations.put(new Key(keyBytes), new Location(packId, offset, length));
      }
    } catch (EOFException e) {
      // A torn record at the tail, same cause as above.
    } catch (IOException e) {
      logger.atWarning().withCause(e).log("Ignoring unreadable index for pack %d", packId);
    }
  }

  private static List<Integer> packIds(Path root) throws IOException {
    var packIds = new ArrayList<Integer>();
    try (var entries = Files.list(root)) {
      entries.forEach(
          path -> {
            String name = path.getFileName().toString();
            if (name.endsWith(PACK_SUFFIX)) {
              try {
                packIds.add(
                    Integer.parseInt(name.substring(0, name.length() - PACK_SUFFIX.length())));
              } catch (NumberFormatException e) {
                logger.atWarning().log("Ignoring unrecognized cache file %s", path);
              }
            }
          });
    }
    packIds.sort(null);
    return packIds;
  }

  private static Path indexPath(Path root, int packId) {
    return root.resolve(packId + INDEX_SUFFIX);
  }

  private static long sizeOf(Path path) {
    try {
      return Files.size(path);
    } catch (IOException e) {
      return 0;
    }
  }
}
