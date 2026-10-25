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
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.ReentrantReadWriteLock;

/** One publication, bounded mutable segments, and compact indexes opened only when used. */
final class SegmentedFingerprintValueStore implements FingerprintValueStore, AutoCloseable {
  /** Authenticated immutable files; lifetime and downloaded-byte retention belong to the caller. */
  interface FileSource {
    /** Returns the immutable logical length without downloading file contents. */
    long size(String name) throws IOException;

    /** Reads the complete file, rejecting lengths exceeding the supplied allocation bound. */
    byte[] read(String name, long maxBytes) throws IOException;

    /** Fills the target's remaining bytes from this offset, or throws on an incomplete read. */
    void read(String name, ByteBuffer target, long offset) throws IOException;

    /** Checks availability without downloading data or changing cache state. */
    default boolean isCached(String name, long offset, long length) throws IOException {
      return false;
    }

    /** Reads cached bytes only; unavailable data must never trigger a download. */
    default void readCached(String name, ByteBuffer target, long offset) throws IOException {
      throw new NotCachedException();
    }
  }

  private static final int MAGIC = 0x53475331;
  private static final int BUCKETS = 16;
  private static final int MAX_SEGMENTS = 4096;
  private static final int MAX_ROOT_BYTES = 1024 * 1024;
  private static final int SEGMENT_ENTRIES = 16384;
  private static final long SEGMENT_BYTES = 32L * 1024 * 1024;
  private static final long WRITER_INDEX_BUDGET = 256L * 1024 * 1024;

  static final class BudgetExceededException extends IOException {
    BudgetExceededException() {
      super("segmented metadata exceeds admission budget");
    }
  }

  static final class NotCachedException extends IOException {
    NotCachedException() {
      super("requested cache data is not available locally");
    }
  }

  private static final class Segment {
    final int number;
    final int bucket;
    PackedFingerprintValueStore store;
    int entries;
    int indexBytes;
    long dataBytes;
    byte[] indexDigest;

    Segment(int number, int bucket, PackedFingerprintValueStore store) {
      this.number = number;
      this.bucket = bucket;
      this.store = store;
    }

    String prefix() {
      return String.format(java.util.Locale.ROOT, "segment-%05d", number);
    }
  }

  private static final class Bucket {
    final List<Segment> segments = new ArrayList<>();
    Segment active;
  }

  private final Path directory;
  private final FileSource source;
  private final Bucket[] buckets = new Bucket[BUCKETS];
  private final ReentrantReadWriteLock lifecycle = new ReentrantReadWriteLock();
  private final AtomicLong entries = new AtomicLong();
  private final int segmentEntries;
  private final long segmentBytes;
  private int nextSegment;
  private long metadataBytes;
  private boolean sealed;
  private boolean closed;

  private SegmentedFingerprintValueStore(Path directory, int segmentEntries, long segmentBytes) {
    this(directory, null, segmentEntries, segmentBytes);
  }

  private SegmentedFingerprintValueStore(
      Path directory, FileSource source, int segmentEntries, long segmentBytes) {
    this.directory = directory;
    this.source = source;
    this.segmentEntries = segmentEntries;
    this.segmentBytes = segmentBytes;
    for (int i = 0; i < BUCKETS; i++) {
      buckets[i] = new Bucket();
    }
  }

  static SegmentedFingerprintValueStore create(Path format, String id) throws IOException {
    return create(format, id, SEGMENT_ENTRIES, SEGMENT_BYTES);
  }

  // Small limits exercise rotation and closure in unit tests without million-entry fixtures.
  static SegmentedFingerprintValueStore create(Path format, String id, int count, long bytes)
      throws IOException {
    checkId(id);
    if (count < 1 || count > SEGMENT_ENTRIES || bytes < 1 || bytes > SEGMENT_BYTES) {
      throw new IllegalArgumentException("invalid segment limits");
    }
    Path directory = format.resolve(id);
    Files.createDirectory(
        directory,
        PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
    sync(format);
    return new SegmentedFingerprintValueStore(directory, count, bytes);
  }

  static SegmentedFingerprintValueStore open(Path format, String id, long budget)
      throws IOException {
    checkId(id);
    Path directory = format.resolve(id);
    if (!Files.isDirectory(directory, NOFOLLOW_LINKS)) {
      throw new IOException("segmented store is not a directory");
    }
    byte[] bytes;
    try (var channel = FileChannel.open(directory.resolve("index"), READ, NOFOLLOW_LINKS)) {
      long length = channel.size();
      checkRootLength(length, budget);
      bytes = new byte[(int) length];
      ByteBuffer target = ByteBuffer.wrap(bytes);
      while (target.hasRemaining()) {
        if (channel.read(target) < 0) {
          throw new IOException("truncated segmented index directory");
        }
      }
    }
    return openRoot(bytes, directory, null, budget);
  }

  /** Opens root metadata eagerly, deferring segment indexes and record ranges until lookup. */
  static SegmentedFingerprintValueStore open(FileSource source, long metadataBudget)
      throws IOException {
    long length = source.size("index");
    checkRootLength(length, metadataBudget);
    byte[] bytes = source.read("index", Math.min(metadataBudget, MAX_ROOT_BYTES)).clone();
    if (bytes.length != length) {
      throw new IOException("segmented index directory length mismatch");
    }
    return openRoot(bytes, null, source, metadataBudget);
  }

  private static void checkRootLength(long length, long budget) throws IOException {
    if (length < 40 || length > MAX_ROOT_BYTES) {
      throw new IOException("segmented index directory outside bounds");
    }
    if (length > budget) {
      throw new BudgetExceededException();
    }
  }

  private static SegmentedFingerprintValueStore openRoot(
      byte[] bytes, Path directory, FileSource source, long budget) throws IOException {
    byte[] payload = Arrays.copyOf(bytes, bytes.length - 32);
    if (!MessageDigest.isEqual(
        hash(payload), Arrays.copyOfRange(bytes, payload.length, bytes.length))) {
      throw new IOException("segmented index directory checksum mismatch");
    }
    var result =
        new SegmentedFingerprintValueStore(directory, source, SEGMENT_ENTRIES, SEGMENT_BYTES);
    result.metadataBytes = bytes.length;
    try (var in = new DataInputStream(new ByteArrayInputStream(payload))) {
      if (in.readInt() != MAGIC) {
        throw new IOException("unsupported segmented index directory");
      }
      int count = in.readInt();
      if (count < 0 || count > MAX_SEGMENTS) {
        throw new IOException("invalid segment count");
      }
      for (int i = 0; i < count; i++) {
        int bucket = in.readInt();
        if (bucket < 0 || bucket >= BUCKETS) {
          throw new IOException("invalid segment bucket");
        }
        var segment = new Segment(i, bucket, null);
        segment.entries = in.readInt();
        segment.dataBytes = in.readLong();
        segment.indexBytes = in.readInt();
        segment.indexDigest = new byte[32];
        in.readFully(segment.indexDigest);
        if (segment.entries < 1
            || segment.entries > SEGMENT_ENTRIES
            || segment.dataBytes < 0
            || segment.indexBytes != 48L + 48L * segment.entries) {
          throw new IOException("segment metadata exceeds bounds or budget");
        }
        if (segment.indexBytes > budget - result.metadataBytes) {
          throw new BudgetExceededException();
        }
        if (result.fileSize(segment.prefix() + ".index") != segment.indexBytes
            || result.fileSize(segment.prefix() + ".pack") != segment.dataBytes) {
          throw new IOException("segment file length mismatch");
        }
        result.metadataBytes += segment.indexBytes;
        result.buckets[bucket].segments.add(segment);
      }
      if (in.read() != -1) {
        throw new IOException("trailing segmented index directory bytes");
      }
    }
    result.sealed = true;
    return result;
  }

  private long fileSize(String name) throws IOException {
    return source == null
        ? Files.size(Objects.requireNonNull(directory).resolve(name))
        : source.size(name);
  }

  long indexBytes() {
    return metadataBytes;
  }

  /** Lists exactly the immutable files owned by a sealed store, excluding witness blocks. */
  List<String> sealedFiles() throws IOException {
    lifecycle.readLock().lock();
    try {
      if (!sealed || closed) {
        throw new IOException("segmented store is not open and sealed");
      }
      var segments = new ArrayList<Segment>();
      for (Bucket bucket : buckets) {
        segments.addAll(bucket.segments);
      }
      segments.sort(Comparator.comparingInt(s -> s.number));
      var files = new ArrayList<String>();
      files.add("index");
      for (Segment segment : segments) {
        files.add(segment.prefix() + ".index");
        files.add(segment.prefix() + ".pack");
      }
      return List.copyOf(files);
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  private synchronized Segment newSegment(int bucket) throws IOException {
    if (nextSegment >= MAX_SEGMENTS) {
      throw new IOException("segment count exceeds limit");
    }
    var segment = new Segment(nextSegment++, bucket, null);
    segment.store =
        PackedFingerprintValueStore.createSegment(
            Objects.requireNonNull(directory), segment.prefix());
    return segment;
  }

  private static int bucket(KeyBytesProvider key) {
    return hash(key.toBytes())[0] & (BUCKETS - 1);
  }

  private PackedFingerprintValueStore store(Segment segment, boolean cachedOnly)
      throws IOException {
    if (segment.store == null) {
      PackedFingerprintValueStore opened;
      if (source == null) {
        opened =
            PackedFingerprintValueStore.openSegment(
                Objects.requireNonNull(directory), segment.prefix(), segment.indexBytes);
      } else if (cachedOnly) {
        opened =
            PackedFingerprintValueStore.openSegmentCached(
                source, segment.prefix(), segment.indexBytes);
      } else {
        opened =
            PackedFingerprintValueStore.openSegment(source, segment.prefix(), segment.indexBytes);
      }
      if (!MessageDigest.isEqual(opened.indexDigest(), segment.indexDigest)) {
        opened.close();
        throw new IOException("segment index differs from publication descriptor");
      }
      segment.store = opened;
    }
    return segment.store;
  }

  private byte[] find(Bucket bucket, KeyBytesProvider key) throws IOException {
    return find(bucket, key, false);
  }

  private byte[] find(Bucket bucket, KeyBytesProvider key, boolean cachedOnly) throws IOException {
    for (int i = bucket.segments.size() - 1; i >= 0; i--) {
      var store = store(bucket.segments.get(i), cachedOnly);
      // An unavailable newer index or matching record stops this search. Skipping it could
      // return a different older value than the ordinary lookup would return.
      byte[] value = cachedOnly ? store.getIfPresentCached(key) : store.getIfPresent(key);
      if (value != null) {
        return value;
      }
    }
    return null;
  }

  @Override
  public WriteStatus put(KeyBytesProvider key, byte[] value) {
    lifecycle.readLock().lock();
    try {
      if (closed || sealed) {
        throw new IOException("segmented writer closed or sealed");
      }
      int number = bucket(key);
      Bucket bucket = buckets[number];
      synchronized (bucket) {
        byte[] previous = find(bucket, key);
        if (previous != null) {
          if (!Arrays.equals(previous, value)) {
            throw new IOException("conflicting immutable shared cache value");
          }
          return immediateWriteStatus(false);
        }
        if (bucket.active != null
            && (bucket.active.entries >= segmentEntries
                || bucket.active.store.dataBytes() >= segmentBytes)) {
          sealSegment(bucket.active);
          bucket.active = null;
        }
        if (bucket.active == null) {
          bucket.active = newSegment(number);
          bucket.segments.add(bucket.active);
        }
        if ((entries.incrementAndGet() * 48) > WRITER_INDEX_BUDGET) {
          throw new IOException("segmented writer index budget exceeded");
        }
        boolean novel = bucket.active.store.put(key, value).get();
        if (novel) {
          bucket.active.entries++;
        }
        return immediateWriteStatus(novel);
      }
    } catch (IOException e) {
      return immediateFailedWriteStatus(e);
    } catch (ExecutionException e) {
      return immediateFailedWriteStatus(e.getCause());
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      return immediateFailedWriteStatus(e);
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  @Override
  public ListenableFuture<byte[]> get(KeyBytesProvider key) {
    lifecycle.readLock().lock();
    try {
      if (closed) {
        throw new IOException("segmented store closed");
      }
      Bucket bucket = buckets[bucket(key)];
      synchronized (bucket) {
        byte[] value = find(bucket, key);
        if (value == null) {
          throw new IOException("segmented cache key is absent");
        }
        return immediateFuture(value);
      }
    } catch (IOException e) {
      return immediateFailedFuture(new MissingFingerprintValueException(key, e));
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  /** Returns null for absent or unavailable cached data, without requesting remote bytes. */
  byte[] getIfPresentCached(KeyBytesProvider key) throws IOException {
    lifecycle.readLock().lock();
    try {
      if (closed) {
        throw new IOException("segmented store closed");
      }
      Bucket bucket = buckets[bucket(key)];
      synchronized (bucket) {
        return find(bucket, key, true);
      }
    } catch (NotCachedException e) {
      return null;
    } finally {
      lifecycle.readLock().unlock();
    }
  }

  private void sealSegment(Segment segment) throws IOException {
    segment.store.seal();
    segment.indexBytes = segment.store.indexBytes();
    segment.dataBytes = segment.store.dataBytes();
    segment.indexDigest = segment.store.indexDigest();
  }

  void seal() throws IOException {
    lifecycle.writeLock().lock();
    try {
      if (closed) {
        throw new IOException("segmented store closed");
      }
      if (sealed) {
        return;
      }
      var segments = new ArrayList<Segment>();
      for (Bucket bucket : buckets) {
        if (bucket.active != null) {
          sealSegment(bucket.active);
          bucket.active = null;
        }
        segments.addAll(bucket.segments);
      }
      segments.sort(Comparator.comparingInt(s -> s.number));
      var bytes = new ByteArrayOutputStream();
      try (var out = new DataOutputStream(bytes)) {
        out.writeInt(MAGIC);
        out.writeInt(segments.size());
        for (Segment segment : segments) {
          out.writeInt(segment.bucket);
          out.writeInt(segment.entries);
          out.writeLong(segment.dataBytes);
          out.writeInt(segment.indexBytes);
          out.write(segment.indexDigest);
        }
      }
      byte[] payload = bytes.toByteArray();
      bytes.write(hash(payload));
      Path localDirectory = Objects.requireNonNull(directory);
      Path temporary = Files.createTempFile(localDirectory, ".pending-", ".index");
      try {
        try (var channel = FileChannel.open(temporary, WRITE, NOFOLLOW_LINKS)) {
          ByteBuffer buffer = ByteBuffer.wrap(bytes.toByteArray());
          while (buffer.hasRemaining()) {
            channel.write(buffer);
          }
          channel.force(true);
        }
        Files.createLink(localDirectory.resolve("index"), temporary);
        sync(localDirectory);
      } finally {
        Files.deleteIfExists(temporary);
      }
      metadataBytes = bytes.size() + segments.stream().mapToLong(s -> s.indexBytes).sum();
      sealed = true;
    } finally {
      lifecycle.writeLock().unlock();
    }
  }

  @Override
  public void close() throws IOException {
    lifecycle.writeLock().lock();
    try {
      closed = true;
      IOException failure = null;
      for (Bucket bucket : buckets) {
        for (Segment segment : bucket.segments) {
          if (segment.store != null) {
            try {
              segment.store.close();
            } catch (IOException e) {
              if (failure == null) {
                failure = e;
              } else {
                failure.addSuppressed(e);
              }
            }
          }
        }
        bucket.segments.clear();
        bucket.active = null;
      }
      if (failure != null) {
        throw failure;
      }
    } finally {
      lifecycle.writeLock().unlock();
    }
  }

  private static void checkId(String id) {
    if (!id.matches("[0-9a-f]{64}")) {
      throw new IllegalArgumentException("invalid segmented publication ID");
    }
  }

  private static byte[] hash(byte[] value) {
    try {
      return MessageDigest.getInstance("SHA-256").digest(value);
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }

  private static void sync(Path directory) throws IOException {
    try (var channel = FileChannel.open(directory, READ)) {
      channel.force(true);
    }
  }
}
