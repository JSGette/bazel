// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFuture;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.analysis.LookupResult;
import com.google.devtools.build.lib.skyframe.serialization.analysis.LookupTopLevelTargetsResult;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RetainedAnalysisCacheClient;
import com.google.protobuf.ByteString;
import com.google.protobuf.CodedInputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.HashSet;

/** Adapts upstream analysis-entry envelopes to the existing analysis-cache lookup interface. */
final class LocalAnalysisCacheClient implements RetainedAnalysisCacheClient {
  private record Result(byte[] value, byte[] invalidationFingerprint, int missReason)
      implements LookupResult {}

  private static final LookupResult MISS = new Result(new byte[0], null, 0);
  private final LocalCacheSession session;
  private final LocalDependencyValidator validator;
  private final java.util.Map<ByteString, PackedFingerprint> previousRoots;
  private final java.util.LinkedHashMap<ByteString, PackedFingerprint> acceptedRoots;
  private final int maxTrackedRoots;

  LocalAnalysisCacheClient(LocalCacheSession session, LocalDependencyValidator validator) {
    this(session, validator, java.util.Map.of());
  }

  LocalAnalysisCacheClient(
      LocalCacheSession session,
      LocalDependencyValidator validator,
      java.util.Map<ByteString, PackedFingerprint> previousRoots) {
    this(session, validator, previousRoots, 250_000);
  }

  LocalAnalysisCacheClient(
      LocalCacheSession session,
      LocalDependencyValidator validator,
      java.util.Map<ByteString, PackedFingerprint> previousRoots,
      int maxTrackedRoots) {
    if (maxTrackedRoots < 1 || previousRoots.size() > maxTrackedRoots) {
      throw new IllegalArgumentException("invalid retained analysis root capacity");
    }
    this.session = session;
    this.validator = validator;
    this.previousRoots = java.util.Map.copyOf(previousRoots);
    this.acceptedRoots = new java.util.LinkedHashMap<>(previousRoots);
    this.maxTrackedRoots = maxTrackedRoots;
  }

  synchronized java.util.Map<ByteString, PackedFingerprint> retainedRoots() {
    return java.util.Map.copyOf(acceptedRoots);
  }

  private synchronized void rememberRoot(ByteString key, PackedFingerprint root) {
    acceptedRoots.remove(key);
    acceptedRoots.put(key, root);
    if (acceptedRoots.size() > maxTrackedRoots) {
      acceptedRoots.pollFirstEntry();
    }
  }

  @Override
  public ListenableFuture<Boolean> isPreviouslyLoadedValueValid(byte[] key) {
    var identity = ByteString.copyFrom(key);
    var root = previousRoots.get(identity);
    if (root == null) {
      return immediateFuture(false);
    }
    try {
      boolean valid = session.isOpen() && validator.validate(root) && session.isOpen();
      if (!valid) {
        synchronized (this) {
          acceptedRoots.remove(identity, root);
        }
      }
      return immediateFuture(valid);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      return immediateFuture(false);
    }
  }

  String validationStatisticsJson() {
    return validator.statisticsJson();
  }

  @Override
  public ListenableFuture<LookupResult> lookup(byte[] key) {
    try {
      var attempted = new HashSet<String>();
      var fingerprint = PackedFingerprint.fromBytes(key);
      LocalCacheSession.AnalysisCandidate candidate;
      while ((candidate = session.nextAnalysisCandidate(fingerprint, attempted)) != null) {
        try {
          byte[] bytes = candidate.value();
          var input = CodedInputStream.newInstance(bytes);
          // DataType.DATA_TYPE_ANALYSIS_NODE. This module deliberately caches analysis only.
          if (input.readEnum() != 4) {
            continue;
          }
          var root = PackedFingerprint.readFrom(input);
          if (!validator.validate(root)) {
            continue;
          }
          if (!session.acceptAnalysisCandidate(candidate)) {
            return immediateFuture(MISS);
          }
          rememberRoot(ByteString.copyFrom(key), root);
          byte[] value = Arrays.copyOfRange(bytes, input.getTotalBytesRead(), bytes.length);
          return immediateFuture(new Result(value, root.toBytes(), 0));
        } catch (IOException | IllegalArgumentException e) {
          // A malformed or stale candidate must not hide a later valid revision of this key.
        }
      }
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    } catch (IOException | IllegalArgumentException e) {
      // Optional cache discovery/format failures are misses.
    }
    return immediateFuture(MISS);
  }

  @Override
  public Stats getStats() {
    return EMPTY_STATS;
  }

  @Override
  public LookupTopLevelTargetsResult lookupTopLevelTargets(
      long version, String configuration, boolean fakeStamp, String bazelVersion) {
    throw new UnsupportedOperationException("local lookup does not enable the metadata service");
  }

  @Override
  public void bailOutDueToMissingFingerprint() {}
}
