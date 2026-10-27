// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.CREATE;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProtocol;
import java.io.IOException;
import java.nio.channels.FileChannel;
import java.nio.channels.OverlappingFileLockException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributeView;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.FileTime;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.locks.ReentrantLock;
import java.util.regex.Pattern;

/**
 * Advisory persistent chunks, installed atomically without synchronous durability. Callers
 * authenticate a private pin before consuming its bytes; crash loss or damage is a cache miss.
 */
final class RemoteChunkCache {
  private static final int MAX_ENTRIES = 65536;
  private static final String HEX = "0123456789abcdef";
  private static final Pattern ENTRY = Pattern.compile("([0-9a-f]{64})-(0|[1-9][0-9]{0,6})");
  private static final ReentrantLock[] PRUNE_LOCKS = createLocks();

  private record Entry(Path path, long size, FileTime modified, Object key) {
    boolean unchanged(BasicFileAttributes attributes) {
      return attributes != null
          && attributes.isRegularFile()
          && attributes.size() == size
          && attributes.lastModifiedTime().equals(modified)
          && Objects.equals(attributes.fileKey(), key);
    }
  }

  private final Path root;
  private final Path algorithm;
  private final long maxBytes;
  private final int maxEntries;

  RemoteChunkCache(Path managed, long maxBytes) throws IOException {
    this(managed, maxBytes, MAX_ENTRIES);
  }

  RemoteChunkCache(Path managed, long maxBytes, int maxEntries) throws IOException {
    if (maxBytes < 0 || maxEntries < 0 || maxEntries > MAX_ENTRIES) {
      throw new IOException("invalid remote chunk cache limits");
    }
    this.maxBytes = maxBytes;
    this.maxEntries = maxEntries;
    Path requested = managed.toAbsolutePath().normalize().resolve("remote-chunks-v1");
    this.root =
        maxBytes == 0 ? requested : LocalCacheSession.durableDirectory(requested).toRealPath();
    this.algorithm = root.resolve("sha256");
    if (maxBytes > 0) {
      LocalCacheSession.durableDirectory(algorithm);
    }
  }

  /**
   * A size-only hint. Does not create directories, touch timestamps, or trust the file contents.
   */
  boolean contains(Digest digest) throws IOException {
    Path path = entry(digest);
    var attributes = maxBytes == 0 ? null : attributes(path);
    return attributes != null
        && attributes.isRegularFile()
        && attributes.size() == digest.getSizeBytes();
  }

  /** Pins a regular entry without authenticating it, including entries with an incorrect size. */
  boolean link(Digest digest, Path privatePending) throws IOException {
    Path path = entry(digest);
    var attributes = maxBytes == 0 ? null : attributes(path);
    if (attributes == null) {
      return false;
    }
    if (attributes.isSymbolicLink()) {
      invalidate(digest);
      return false;
    }
    if (!attributes.isRegularFile()) {
      return false;
    }
    try {
      Files.createLink(privatePending, path);
    } catch (NoSuchFileException e) {
      return false; // A concurrent collector removed the entry before it was pinned.
    }
    if (!Files.isRegularFile(privatePending, NOFOLLOW_LINKS)) {
      Files.deleteIfExists(privatePending);
      invalidate(digest);
      return false;
    }
    return true;
  }

  /** Publishes a complete caller-authenticated file atomically, without replacing any entry. */
  void store(Digest digest, Path verifiedPrivate) throws IOException {
    Path path = entry(digest);
    if (maxBytes == 0) {
      return;
    }
    var attributes =
        Files.readAttributes(verifiedPrivate, BasicFileAttributes.class, NOFOLLOW_LINKS);
    if (!attributes.isRegularFile() || attributes.size() != digest.getSizeBytes()) {
      throw new IOException("verified remote chunk is not a regular file of the declared size");
    }
    ensureDirectory(path.getParent());
    // Retention is advisory: kernel writeback may finish after this read or command. A crash may
    // lose or damage an entry, but every new private pin is authenticated before use. Complete
    // graph publications keep their separate durable installation path.
    try {
      Files.createLink(path, verifiedPrivate);
    } catch (FileAlreadyExistsException e) {
      return;
    }
  }

  /** Creates/checks every ancestor without forcing advisory directory entries to storage. */
  private static void ensureDirectory(Path path) throws IOException {
    Path parent = path.getParent();
    if (parent == null) {
      if (!Files.isDirectory(path, NOFOLLOW_LINKS)) {
        throw new IOException("cache directory has no parent");
      }
      return;
    }
    ensureDirectory(parent);
    try {
      Files.createDirectory(
          path, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
    } catch (FileAlreadyExistsException e) {
      if (!Files.isDirectory(path, NOFOLLOW_LINKS)) {
        throw e;
      }
    }
  }

  /** Removes only the named entry; existing private hardlinks remain readable. */
  void invalidate(Digest digest) throws IOException {
    Path path = entry(digest);
    var attributes = maxBytes == 0 ? null : attributes(path);
    if (attributes != null && (attributes.isRegularFile() || attributes.isSymbolicLink())) {
      Files.deleteIfExists(path);
    }
  }

  /** Updates LRU time through a caller-authenticated private hardlink, never a symlink target. */
  void touch(Path verifiedPin) throws IOException {
    if (maxBytes == 0) {
      return;
    }
    if (!Files.isRegularFile(verifiedPin, NOFOLLOW_LINKS)) {
      throw new IOException("verified remote chunk pin is not a regular file");
    }
    Files.getFileAttributeView(verifiedPin, BasicFileAttributeView.class, NOFOLLOW_LINKS)
        .setTimes(FileTime.fromMillis(System.currentTimeMillis()), null, null);
  }

  /** Best-effort soft limits: concurrent publications and recently touched entries may remain. */
  void prune() throws IOException {
    if (maxBytes == 0
        || !Files.isDirectory(root, NOFOLLOW_LINKS)
        || !Files.isDirectory(algorithm, NOFOLLOW_LINKS)) {
      return;
    }
    // POSIX process locks can be released when another channel for the same inode closes.
    // Guard the entire open/lock/close lifecycle, not just tryLock, across local instances.
    ReentrantLock local = PRUNE_LOCKS[Math.floorMod(root.hashCode(), PRUNE_LOCKS.length)];
    if (!local.tryLock()) {
      return;
    }
    try {
      Path lockPath = root.resolve("prune.lock");
      try (var channel =
          FileChannel.open(
              lockPath,
              Set.of(CREATE, READ, WRITE, NOFOLLOW_LINKS),
              PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))) {
        if (!Files.isRegularFile(lockPath, NOFOLLOW_LINKS)) {
          throw new IOException("remote chunk collection lock is not a regular file");
        }
        try (var lock = channel.tryLock()) {
          if (lock != null) {
            pruneLocked();
          }
        } catch (OverlappingFileLockException e) {
          // Another owner outside this helper holds the file lock. Collection is optional.
        }
      }
    } finally {
      local.unlock();
    }
  }

  private void pruneLocked() throws IOException {
    var entries = new ArrayList<Entry>();
    long bytes = 0;
    for (int i = 0; i < 256; i++) {
      String prefix = "" + HEX.charAt(i >>> 4) + HEX.charAt(i & 15);
      Path directory = algorithm.resolve(prefix);
      if (!Files.isDirectory(directory, NOFOLLOW_LINKS)) {
        continue;
      }
      try (var children = Files.newDirectoryStream(directory)) {
        for (Path path : children) {
          var match = ENTRY.matcher(path.getFileName().toString());
          if (!match.matches()
              || !match.group(1).startsWith(prefix)
              || Long.parseLong(match.group(2)) > SkyCacheProtocol.CHUNK_BYTES) {
            continue;
          }
          var attributes = attributes(path);
          if (attributes == null || !attributes.isRegularFile()) {
            continue;
          }
          entries.add(
              new Entry(
                  path, attributes.size(), attributes.lastModifiedTime(), attributes.fileKey()));
          bytes = Math.addExact(bytes, attributes.size());
        }
      } catch (NoSuchFileException e) {
        // A concurrently removed prefix has no entries to collect.
      }
    }
    entries.sort(Comparator.comparing(Entry::modified).thenComparing(e -> e.path().toString()));
    int remaining = entries.size();
    for (Entry entry : entries) {
      if (bytes <= maxBytes && remaining <= maxEntries) {
        break;
      }
      if (entry.unchanged(attributes(entry.path())) && Files.deleteIfExists(entry.path())) {
        bytes -= entry.size();
        remaining--;
      }
    }
  }

  private Path entry(Digest digest) throws IOException {
    SkyCacheProtocol.validateDigest(digest, SkyCacheProtocol.CHUNK_BYTES);
    String hash = digest.getHash();
    return algorithm.resolve(hash.substring(0, 2)).resolve(hash + "-" + digest.getSizeBytes());
  }

  private BasicFileAttributes attributes(Path path) throws IOException {
    if (!Files.isDirectory(root, NOFOLLOW_LINKS)
        || !Files.isDirectory(algorithm, NOFOLLOW_LINKS)
        || !Files.isDirectory(path.getParent(), NOFOLLOW_LINKS)) {
      return null;
    }
    try {
      return Files.readAttributes(path, BasicFileAttributes.class, NOFOLLOW_LINKS);
    } catch (NoSuchFileException e) {
      return null;
    }
  }

  private static ReentrantLock[] createLocks() {
    var locks = new ReentrantLock[64];
    for (int i = 0; i < locks.length; i++) {
      locks[i] = new ReentrantLock();
    }
    return locks;
  }
}
