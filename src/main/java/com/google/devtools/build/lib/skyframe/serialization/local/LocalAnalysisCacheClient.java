// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFuture;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.analysis.LookupResult;
import com.google.devtools.build.lib.skyframe.serialization.analysis.LookupTopLevelTargetsResult;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCacheClient;
import com.google.protobuf.CodedInputStream;
import java.io.IOException;
import java.util.Arrays;
import java.util.concurrent.ExecutionException;

/** Adapts upstream analysis-entry envelopes to the existing analysis-cache lookup interface. */
final class LocalAnalysisCacheClient implements RemoteAnalysisCacheClient {
  private record Result(byte[] value, byte[] invalidationFingerprint, int missReason)
      implements LookupResult {}

  private static final LookupResult MISS = new Result(new byte[0], null, 0);
  private final LocalCacheSession session;

  LocalAnalysisCacheClient(LocalCacheSession session) {
    this.session = session;
  }

  @Override
  public ListenableFuture<LookupResult> lookup(byte[] key) {
    try {
      byte[] bytes = session.get(PackedFingerprint.fromBytes(key)).get();
      var input = CodedInputStream.newInstance(bytes);
      int type = input.readEnum();
      // DataType.DATA_TYPE_ANALYSIS_NODE. This module deliberately caches analysis only.
      if (type != 4) {
        return immediateFuture(MISS);
      }
      byte[] invalidation = PackedFingerprint.readFrom(input).toBytes();
      byte[] value = Arrays.copyOfRange(bytes, input.getTotalBytesRead(), bytes.length);
      return immediateFuture(new Result(value, invalidation, 0));
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      return immediateFuture(MISS);
    } catch (IOException | ExecutionException | IllegalArgumentException e) {
      return immediateFuture(MISS);
    }
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
