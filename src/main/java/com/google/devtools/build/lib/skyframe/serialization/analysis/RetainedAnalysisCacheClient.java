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

  /**
   * Local stores whose retained-value validation performs synchronous filesystem work.
   *
   * <p>This work must run interruptibly in the command's invalidation stage, rather than inside the
   * remote request dispatcher and its submission/response deadlines. Implementations must validate
   * the original value's evidence, retain invocation-local success bounds, and return false for
   * missing or unsupported evidence. This interface does not authorize skipping input checks or
   * caching validation across commands.
   */
  interface Synchronous extends RetainedAnalysisCacheClient {
    boolean validatePreviouslyLoadedValue(byte[] key) throws InterruptedException;
  }
}
