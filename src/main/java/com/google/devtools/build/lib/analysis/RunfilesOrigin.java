// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.analysis;

import com.google.common.collect.MapMaker;
import java.lang.ref.ReferenceQueue;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.atomic.AtomicLong;
import javax.annotation.Nullable;

/**
 * Creation provenance for preserving Runfiles and symlink-entry identity across independent
 * analysis cache writers.
 *
 * <p>Runfiles keeps reference equality. When local caching is enabled, equivalent values from the
 * same producer and ordinal reuse a live object, joining restored branches to the retained graph.
 * Different origins or contents are never merged. Both maps refer weakly to Runfiles: neither
 * provenance nor index keys may retain their value. Recording while caching is disabled allows a
 * later command to restore into a retained graph. Owners must be complete configured-target or
 * aspect keys, not labels or display strings.
 */
public final class RunfilesOrigin {
  public record Origin(Object owner, int ordinal) {
    public Origin {
      Objects.requireNonNull(owner);
      if (ordinal < 0) {
        throw new IllegalArgumentException("negative Runfiles creation ordinal");
      }
    }
  }

  private static final ThreadLocal<Scope> CURRENT = new ThreadLocal<>();
  private static final Registry<Runfiles> RUNFILES =
      new Registry<>(Runfiles::sameContentsForSharing);
  private static final Registry<SymlinkEntry> SYMLINKS =
      new Registry<>(
          (left, right, restoring) ->
              left.getPath().equals(right.getPath())
                  && Runfiles.sameArtifactForSharing(
                      left.getArtifact(), right.getArtifact(), restoring));
  private static final ConcurrentHashMap<Object, Integer> ACTIVE = new ConcurrentHashMap<>();
  private static final AtomicLong EPOCHS = new AtomicLong();
  private static volatile long epoch;
  private static volatile boolean sharingEnabled;

  /** Starts a command's cache-validation epoch without retaining objects from earlier commands. */
  public static void setSharingEnabled(boolean enabled) {
    if (enabled && !sharingEnabled) {
      epoch = EPOCHS.incrementAndGet();
    }
    sharingEnabled = enabled;
    RUNFILES.discardCollected();
    SYMLINKS.discardCollected();
  }

  @FunctionalInterface
  private interface Equality<T> {
    boolean test(T left, T right, boolean restoring);
  }

  private RunfilesOrigin() {}

  /** A rule/aspect evaluation's deterministic, thread-confined creation sequence. */
  public static final class Scope implements AutoCloseable {
    private final Object owner;
    @Nullable private final Scope previous;
    private int nextRunfiles;
    private int nextSymlink;

    private Scope(Object owner) {
      this.owner = Objects.requireNonNull(owner);
      this.previous = CURRENT.get();
      ACTIVE.merge(owner, 1, Math::addExact);
      CURRENT.set(this);
    }

    @Override
    public void close() {
      if (CURRENT.get() != this) {
        throw new IllegalStateException(
            "Runfiles scopes must close on their opening thread in order");
      }
      ACTIVE.compute(owner, (key, count) -> count == 1 ? null : count - 1);
      if (previous == null) {
        CURRENT.remove();
      } else {
        CURRENT.set(previous);
      }
    }
  }

  public static Scope enter(Object owner) {
    return new Scope(owner);
  }

  static Runfiles record(Runfiles value) {
    Scope scope = CURRENT.get();
    if (scope != null) {
      int ordinal = scope.nextRunfiles;
      scope.nextRunfiles = Math.incrementExact(ordinal);
      return RUNFILES.register(value, new Origin(scope.owner, ordinal), false);
    }
    return value;
  }

  static SymlinkEntry record(SymlinkEntry value) {
    Scope scope = CURRENT.get();
    if (scope != null) {
      int ordinal = scope.nextSymlink;
      scope.nextSymlink = Math.incrementExact(ordinal);
      return SYMLINKS.register(value, new Origin(scope.owner, ordinal), false);
    }
    return value;
  }

  @Nullable
  public static Origin get(Runfiles value) {
    return RUNFILES.origins.get(value);
  }

  @Nullable
  public static Origin get(SymlinkEntry value) {
    return SYMLINKS.origins.get(value);
  }

  /** Reconnects a completely restored value to an equivalent live object from its producer. */
  @Nullable
  public static Runfiles restore(Runfiles value, Origin origin) {
    return RUNFILES.restore(value, origin);
  }

  @Nullable
  public static SymlinkEntry restore(SymlinkEntry value, Origin origin) {
    return SYMLINKS.restore(value, origin);
  }

  private static final class Entry<T> extends WeakReference<T> {
    private final Origin origin;
    private long validatedEpoch;

    private Entry(T value, Origin origin, ReferenceQueue<T> collected, long validatedEpoch) {
      super(value, collected);
      this.origin = origin;
      this.validatedEpoch = validatedEpoch;
    }
  }

  /** Separate typed registries keep Runfiles and symlink creation sequences independent. */
  private static final class Registry<T> {
    private final ConcurrentMap<T, Origin> origins = new MapMaker().weakKeys().makeMap();
    private final ConcurrentHashMap<Origin, ArrayList<Entry<T>>> live = new ConcurrentHashMap<>();
    private final ReferenceQueue<T> collected = new ReferenceQueue<>();
    private final Equality<T> sameContents;

    private Registry(Equality<T> sameContents) {
      this.sameContents = sameContents;
    }

    @SuppressWarnings("unchecked") // Only Entry<T> instances are registered with this queue.
    private void discardCollected() {
      Entry<T> entry;
      while ((entry = (Entry<T>) collected.poll()) != null) {
        Entry<T> removed = entry;
        live.computeIfPresent(
            entry.origin,
            (origin, entries) -> {
              entries.remove(removed);
              return entries.isEmpty() ? null : entries;
            });
      }
    }

    @Nullable
    private T restore(T value, Origin origin) {
      var result = new java.util.concurrent.atomic.AtomicReference<T>();
      // Serializes the check against Scope.enter. Never expose values from an in-progress
      // producer: its artifacts and providers are not final. The codec will request a cache miss.
      ACTIVE.compute(
          origin.owner(),
          (owner, count) -> {
            if (count == null) {
              result.set(register(value, origin, true));
            }
            return count;
          });
      return result.get();
    }

    private T register(T value, Origin origin, boolean restoring) {
      Origin previous = origins.putIfAbsent(value, origin);
      if (previous != null && !previous.equals(origin)) {
        throw new IllegalStateException("Runfiles value origin cannot change");
      }
      discardCollected();
      var result = new java.util.concurrent.atomic.AtomicReference<T>(value);
      live.compute(
          origin,
          (key, entries) -> {
            if (entries == null) {
              entries = new ArrayList<>();
            }
            // Prefer a more recently created original after cache-off recomputation.
            // Older graph versions with different contents remain distinct.
            for (int i = entries.size() - 1; i >= 0; i--) {
              Entry<T> entry = entries.get(i);
              T candidate = entry.get();
              if (candidate == null) {
                entries.remove(i);
              } else if (candidate == value
                  || ((restoring || (sharingEnabled && entry.validatedEpoch == epoch))
                      && sameContents.test(value, candidate, restoring))) {
                if (restoring) {
                  entry.validatedEpoch = epoch;
                }
                result.set(candidate);
                return entries;
              }
            }
            entries.add(new Entry<>(value, origin, collected, restoring ? epoch : -1));
            return entries;
          });
      return result.get();
    }
  }
}
