// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.analysis;

import com.google.common.util.concurrent.ListenableFuture;

/** Optional invalidation support for stores with several source revisions per logical key. */
public interface RetainedAnalysisCacheClient extends RemoteAnalysisCacheClient {
  /**
   * Checks the evidence of the value previously loaded into this server's graph. Finding another
   * valid value for the same logical key is insufficient: the graph still contains the old value.
   * Unknown previous evidence must return false. Ordinary lookup may select a newer value after the
   * old graph node has been invalidated.
   */
  ListenableFuture<Boolean> isPreviouslyLoadedValueValid(byte[] key);
}
