// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.CREATE;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import java.io.IOException;
import java.io.InterruptedIOException;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import javax.annotation.Nullable;

/**
 * Process-shared pins for the lease-aware cache layout on a trusted local POSIX filesystem.
 *
 * <p>The permanent lock file uses byte zero for catalog coordination and 4096 hash slots for
 * publication pins. Collisions only retain extra publications. The file must never be unlinked,
 * replaced, or collected: locks on an old inode would not protect clients opening its replacement.
 *
 * <p>One channel per canonical root and reference-counted shared locks support multiple sessions in
 * the same JVM. Closing an unrelated channel for the same inode can release process-scoped POSIX
 * locks, so channels are closed only after the last owner releases all its leases. Every operation
 * is nonblocking; a busy catalog is a cache miss or a skipped publication/collection, not a build
 * failure. No lifetime catalog lock serializes ordinary writers.
 */
final class LocalCacheLeaseManager implements AutoCloseable {
  private static final int CATALOG_SLOT = 0;
  private static final Map<Path, State> REGISTRY = new HashMap<>();

  // All state, including per-owner token sets, is protected by REGISTRY's monitor. No blocking
  // lock acquisition, content I/O, callbacks, or caller work occurs while holding that monitor.
  private static final class State {
    final Path root;
    final FileChannel channel;
    final Map<Integer, Held> held = new HashMap<>();
    int owners;
    @Nullable IOException failure;

    State(Path root, FileChannel channel) {
      this.root = root;
      this.channel = channel;
    }
  }

  private static final class Held {
    final FileLock lock;
    final boolean shared;
    int references = 1;

    Held(FileLock lock, boolean shared) {
      this.lock = lock;
      this.shared = shared;
    }
  }

  final class Lease implements AutoCloseable {
    private final int slot;
    private final Held held;
    private boolean released;

    private Lease(int slot, Held held) {
      this.slot = slot;
      this.held = held;
    }

    @Override
    public void close() throws IOException {
      synchronized (REGISTRY) {
        if (released) {
          return;
        }
        released = true;
        leases.remove(this);
        if (--held.references == 0) {
          try {
            held.lock.release();
          } catch (IOException e) {
            state.failure = e;
            throw e;
          } finally {
            state.held.remove(slot);
          }
        }
      }
    }
  }

  private final State state;
  private final Set<Lease> leases = new HashSet<>();
  private boolean closed;

  static LocalCacheLeaseManager open(Path managedRoot) throws IOException {
    Path root =
        LocalCacheSession.durableDirectory(managedRoot.toAbsolutePath().normalize()).toRealPath();
    synchronized (REGISTRY) {
      State state = REGISTRY.get(root);
      if (state == null) {
        Path lockPath = root.resolve("leases.lock");
        var channel =
            FileChannel.open(
                lockPath,
                Set.of(CREATE, READ, WRITE, NOFOLLOW_LINKS),
                PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")));
        try {
          if (!Files.isRegularFile(lockPath, NOFOLLOW_LINKS)) {
            throw new IOException("cache lease path is not a regular file");
          }
          state = new State(root, channel);
          REGISTRY.put(root, state);
        } catch (IOException | RuntimeException e) {
          channel.close();
          throw e;
        }
      }
      state.owners++;
      return new LocalCacheLeaseManager(state);
    }
  }

  private LocalCacheLeaseManager(State state) {
    this.state = state;
  }

  @Nullable
  Lease trySharedCatalog() throws IOException {
    synchronized (REGISTRY) {
      return tryAcquire(CATALOG_SLOT, true);
    }
  }

  @Nullable
  Lease tryExclusiveCatalog() throws IOException {
    synchronized (REGISTRY) {
      return tryAcquire(CATALOG_SLOT, false);
    }
  }

  Lease pinPublication(String id) throws IOException {
    synchronized (REGISTRY) {
      requireCatalog(true);
      Lease lease = tryAcquire(publicationSlot(id), true);
      if (lease == null) {
        throw new IOException("publication lock conflicts with shared catalog ownership");
      }
      return lease;
    }
  }

  @Nullable
  Lease tryCollectPublication(String id) throws IOException {
    synchronized (REGISTRY) {
      requireCatalog(false);
      return tryAcquire(publicationSlot(id), false);
    }
  }

  private void requireCatalog(boolean shared) throws IOException {
    checkOpen();
    if (leases.stream()
        .noneMatch(lease -> lease.slot == CATALOG_SLOT && lease.held.shared == shared)) {
      throw new IOException("publication lease requires this owner's catalog lock");
    }
  }

  @Nullable
  private Lease tryAcquire(int slot, boolean shared) throws IOException {
    checkOpen();
    Held held = state.held.get(slot);
    if (held != null) {
      if (!shared || !held.shared) {
        return null;
      }
      if (!held.lock.isValid()) {
        throw new IOException("cache lease unexpectedly lost");
      }
      held.references++;
    } else {
      FileLock lock = state.channel.tryLock(slot, 1, shared);
      if (lock == null) {
        return null;
      }
      if (shared && !lock.isShared()) {
        lock.release();
        throw new IOException("filesystem does not support shared cache leases");
      }
      held = new Held(lock, shared);
      state.held.put(slot, held);
    }
    var lease = new Lease(slot, held);
    leases.add(lease);
    return lease;
  }

  private void checkOpen() throws IOException {
    if (closed || !state.channel.isOpen()) {
      throw new IOException("cache lease manager is closed");
    }
    if (state.failure != null) {
      throw new IOException("cache lease manager failed", state.failure);
    }
    if (Thread.currentThread().isInterrupted()) {
      throw new InterruptedIOException("cache lease acquisition interrupted");
    }
  }

  private static int publicationSlot(String id) {
    if (id.length() != 64) {
      throw new IllegalArgumentException("invalid publication identity");
    }
    for (int i = 0; i < id.length(); i++) {
      char c = id.charAt(i);
      if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) {
        throw new IllegalArgumentException("invalid publication identity");
      }
    }
    return 1 + Integer.parseInt(id.substring(0, 3), 16);
  }

  @Override
  public void close() throws IOException {
    synchronized (REGISTRY) {
      if (closed) {
        return;
      }
      closed = true;
      IOException failure = null;
      for (Lease lease : new ArrayList<>(leases)) {
        try {
          lease.close();
        } catch (IOException e) {
          if (failure == null) {
            failure = e;
          } else {
            failure.addSuppressed(e);
          }
        }
      }
      if (--state.owners == 0) {
        REGISTRY.remove(state.root);
        try {
          state.channel.close();
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
  }
}
