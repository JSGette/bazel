// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.analysis;

import com.google.common.collect.MapMaker;
import java.util.Objects;
import java.util.concurrent.ConcurrentMap;
import javax.annotation.Nullable;

/**
 * Creation provenance for preserving Runfiles identity across independent analysis cache writers.
 *
 * <p>This does not change Runfiles equality or intern objects. Keys are weak and use identity;
 * provenance cannot retain its Runfiles. Recording also while caching is disabled lets a later
 * command serialize values retained in the in-memory graph. Owners must be the complete configured
 * target or aspect keys, not labels or display strings.
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

  private static final ConcurrentMap<Runfiles, Origin> ORIGINS =
      new MapMaker().weakKeys().makeMap();
  private static final ThreadLocal<Scope> CURRENT = new ThreadLocal<>();

  private RunfilesOrigin() {}

  /** A rule/aspect evaluation's deterministic, thread-confined creation sequence. */
  public static final class Scope implements AutoCloseable {
    private final Object owner;
    @Nullable private final Scope previous;
    private int next;

    private Scope(Object owner) {
      this.owner = Objects.requireNonNull(owner);
      this.previous = CURRENT.get();
      CURRENT.set(this);
    }

    @Override
    public void close() {
      if (CURRENT.get() != this) {
        throw new IllegalStateException(
            "Runfiles scopes must close on their opening thread in order");
      }
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
      int ordinal = scope.next;
      scope.next = Math.incrementExact(ordinal);
      restore(value, new Origin(scope.owner, ordinal));
    }
    return value;
  }

  @Nullable
  public static Origin get(Runfiles value) {
    return ORIGINS.get(value);
  }

  /** Retains provenance if a restored graph is serialized in a later command. */
  public static void restore(Runfiles value, Origin origin) {
    Origin previous = ORIGINS.putIfAbsent(value, origin);
    if (previous != null && !previous.equals(origin)) {
      throw new IllegalStateException("Runfiles origin cannot change");
    }
  }
}
