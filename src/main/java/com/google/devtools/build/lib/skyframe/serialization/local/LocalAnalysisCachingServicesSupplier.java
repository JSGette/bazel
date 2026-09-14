// Copyright 2026 The Bazel Authors. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFuture;
import static java.util.concurrent.ForkJoinPool.commonPool;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.concurrent.ThreadSafety.ThreadSafe;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueCache;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.analysis.ClientId;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCachingOptions;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCachingServicesSupplier;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisJsonLogWriter;
import javax.annotation.Nullable;
import javax.annotation.concurrent.GuardedBy;

/**
 * Supplies a {@link FingerprintValueService} over a store provided by {@link LocalSkycacheModule}.
 *
 * <p>The service is created once and lives as long as the server, which is the point: its {@link
 * FingerprintValueCache} memoizes deserialized values, so a configuration the server has seen
 * before is restored without deserializing shared values again. Consequently {@link #shutdown} is a
 * no-op even though it is called after every command.
 *
 * <p>No analysis cache client is supplied. Reads go straight to the store, and invalidation is
 * whatever the cache key encodes.
 *
 * <p>Because the service outlives a single command, the {@link RemoteAnalysisJsonLogWriter} of the
 * command that created it is the one that stays attached; later values of
 * {@code --remote_analysis_json_log} have no effect until the server restarts.
 */
@ThreadSafe
public final class LocalAnalysisCachingServicesSupplier
    implements RemoteAnalysisCachingServicesSupplier {

  @GuardedBy("this")
  @Nullable
  private FingerprintValueStore store;

  @GuardedBy("this")
  @Nullable
  private ListenableFuture<FingerprintValueService> service;

  /**
   * Installs the store to serve analysis data from.
   *
   * <p>Only the first call has an effect. {@link LocalSkycacheModule} is responsible for rejecting
   * commands that ask for a different store.
   */
  public synchronized void setStore(FingerprintValueStore store) {
    if (this.store == null) {
      this.store = store;
    }
  }

  @Override
  public synchronized void configure(
      RemoteAnalysisCachingOptions cachingOptions,
      @Nullable ClientId clientId,
      String buildId,
      @Nullable RemoteAnalysisJsonLogWriter jsonLogWriter) {
    getOrCreateService(jsonLogWriter);
  }

  @Override
  public synchronized ListenableFuture<FingerprintValueService> getFingerprintValueService() {
    return getOrCreateService(/* jsonLogWriter= */ null);
  }

  /** Deliberately does not release the service: it is shared by every command on this server. */
  @Override
  public void shutdown() {}

  @GuardedBy("this")
  private ListenableFuture<FingerprintValueService> getOrCreateService(
      @Nullable RemoteAnalysisJsonLogWriter jsonLogWriter) {
    if (service == null) {
      service =
          immediateFuture(
              new FingerprintValueService(
                  commonPool(),
                  // Falls back to matching stock Bazel when the local cache is disabled.
                  store == null ? FingerprintValueStore.inMemoryStore() : store,
                  new FingerprintValueCache(FingerprintValueCache.SyncMode.NOT_LINKED),
                  FingerprintValueService.NONPROD_FINGERPRINTER,
                  jsonLogWriter));
    }
    return service;
  }
}
