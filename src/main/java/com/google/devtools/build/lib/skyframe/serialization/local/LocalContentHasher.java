// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import java.io.IOException;
import java.io.InputStream;
import java.io.InterruptedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import javax.annotation.Nullable;

/** Thread-confined hashing with a bounded digest cache shared only within one fresh pass. */
final class LocalContentHasher {
  // Large toolchain binaries have many aliases. Do not add metadata reads for ordinary small files.
  private static final long DEFAULT_MINIMUM_BYTES = 1024 * 1024;
  private static final int DEFAULT_MAXIMUM_DIGESTS = 256;
  // At most 1 MiB of retained scratch buffers per process, independent of analysis thread count.
  private static final ArrayBlockingQueue<byte[]> HASH_BUFFERS = new ArrayBlockingQueue<>(16);
  private static final int HASH_BUFFER_BYTES = 65536;

  private record FileVersion(
      long device, long inode, FileTime changed, FileTime modified, long size) {}

  record Statistics(long filesHashed, long bytesHashed, long hits, int retainedDigests) {}

  private final long minimumBytes;
  private final int maximumDigests;
  // Guarded by itself, including content reads on a miss, so concurrent aliases are read once.
  private final Map<FileVersion, byte[]> digests;
  private long filesHashed;
  private long bytesHashed;
  private long hits;

  LocalContentHasher() {
    this(DEFAULT_MINIMUM_BYTES, DEFAULT_MAXIMUM_DIGESTS);
  }

  LocalContentHasher(long minimumBytes, int maximumDigests) {
    if (minimumBytes < 0 || maximumDigests < 1) {
      throw new IllegalArgumentException("invalid content digest limits");
    }
    this.minimumBytes = minimumBytes;
    this.maximumDigests = maximumDigests;
    this.digests = new HashMap<>();
  }

  /** Creates another thread-confined hasher sharing evidence from this same fresh pass only. */
  LocalContentHasher(LocalContentHasher samePass) {
    minimumBytes = samePass.minimumBytes;
    maximumDigests = samePass.maximumDigests;
    digests = samePass.digests;
  }

  void hashContents(Path path, MessageDigest fingerprint, long observedSize) throws IOException {
    checkInterrupted();
    FileVersion before = observedSize >= minimumBytes ? version(path) : null;
    if (before == null) {
      fingerprint.update(readDigest(path));
      return;
    }
    // Only large, versioned files take this lock. Small-file reads remain independent. Serializing
    // large misses prevents duplicate reads without adding an unbounded set of in-flight digests.
    synchronized (digests) {
      checkInterrupted();
      byte[] content = digests.get(before);
      boolean reused = content != null;
      if (!reused) {
        content = readDigest(path);
      }
      if (!before.equals(version(path))) {
        throw new IOException("input changed while hashing: " + path);
      }
      if (reused) {
        hits++;
      } else if (digests.size() < maximumDigests) {
        digests.put(before, content);
      }
      fingerprint.update(content);
    }
  }

  /** Fresh device/inode and change time are required; mtime/size alone never justify reuse. */
  @Nullable
  private static FileVersion version(Path path) throws IOException {
    try {
      var attributes = Files.readAttributes(path, "unix:dev,ino,ctime,lastModifiedTime,size");
      return new FileVersion(
          (Long) attributes.get("dev"),
          (Long) attributes.get("ino"),
          (FileTime) attributes.get("ctime"),
          (FileTime) attributes.get("lastModifiedTime"),
          (Long) attributes.get("size"));
    } catch (UnsupportedOperationException | IllegalArgumentException e) {
      // Providers without Unix identity/change time always hash the bytes again.
      return null;
    }
  }

  private byte[] readDigest(Path path) throws IOException {
    MessageDigest content;
    try {
      content = MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
    try (InputStream input = Files.newInputStream(path)) {
      byte[] buffer = HASH_BUFFERS.poll();
      if (buffer == null) {
        buffer = new byte[HASH_BUFFER_BYTES];
      }
      try {
        int count;
        while ((count = input.read(buffer)) != -1) {
          checkInterrupted();
          content.update(buffer, 0, count);
          bytesHashed += count;
        }
        filesHashed++;
        return content.digest();
      } finally {
        HASH_BUFFERS.offer(buffer);
      }
    }
  }

  private static void checkInterrupted() throws InterruptedIOException {
    if (Thread.currentThread().isInterrupted()) {
      throw new InterruptedIOException("interrupted local input hashing");
    }
  }

  Statistics statistics() {
    synchronized (digests) {
      return new Statistics(filesHashed, bytesHashed, hits, digests.size());
    }
  }
}
