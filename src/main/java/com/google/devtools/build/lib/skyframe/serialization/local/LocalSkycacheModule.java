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

import com.google.common.collect.ImmutableList;
import com.google.common.flogger.GoogleLogger;
import com.google.devtools.build.lib.runtime.CommandEnvironment;
import com.google.devtools.build.lib.server.FailureDetails.FailureDetail;
import com.google.devtools.build.lib.server.FailureDetails.RemoteAnalysisCaching;
import com.google.devtools.build.lib.server.IdleTask;
import com.google.devtools.build.lib.server.IdleTaskException;
import com.google.devtools.build.lib.skyframe.serialization.SerializationModule;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCachingServicesSupplier;
import com.google.devtools.build.lib.util.AbruptExitException;
import com.google.devtools.build.lib.util.DetailedExitCode;
import com.google.devtools.common.options.OptionsBase;
import java.io.IOException;
import java.nio.file.Path;
import java.time.Duration;
import javax.annotation.Nullable;

/**
 * Gives remote analysis caching a local backend: a bounded in-memory tier over a directory on disk.
 *
 * <p>Stock Bazel only ships an in-memory store, so no analysis data survives a server restart and a
 * server can only hold the configuration currently in its Skyframe graph. With this module a single
 * server can restore any configuration it has previously analyzed.
 *
 * <p>The store is opened at most once per server, so the flags describing it are fixed for the
 * server's lifetime. A command asking for a different store fails rather than silently getting the
 * existing one.
 */
public final class LocalSkycacheModule extends SerializationModule {
  private static final GoogleLogger logger = GoogleLogger.forEnclosingClass();

  private static final String DEFAULT_SUBDIRECTORY = "skycache";
  private static final Duration SWEEP_IDLE_DELAY = Duration.ofMinutes(5);

  /** Store properties that cannot change without restarting the server. */
  private record StoreParams(Path root, long memoryBytes) {}

  private final LocalAnalysisCachingServicesSupplier supplier =
      new LocalAnalysisCachingServicesSupplier();

  @Nullable private StoreParams activeParams;
  @Nullable private PackedFingerprintValueStore diskStore;

  @Override
  protected RemoteAnalysisCachingServicesSupplier getAnalysisCachingServicesSupplier() {
    return supplier;
  }

  @Override
  public Iterable<Class<? extends OptionsBase>> getCommonCommandOptions() {
    return ImmutableList.of(LocalAnalysisCachingOptions.class);
  }

  @Override
  public void beforeCommand(CommandEnvironment env) throws AbruptExitException {
    var options = env.getOptions().getOptions(LocalAnalysisCachingOptions.class);
    if (options == null || !options.enabled) {
      return;
    }

    var rootPath =
        options.path != null
            ? env.getWorkingDirectory().getRelative(options.path)
            : env.getDirectories().getOutputBase().getRelative(DEFAULT_SUBDIRECTORY);
    var params = new StoreParams(Path.of(rootPath.getPathString()), options.memorySize);
    if (activeParams == null) {
      openStore(params);
    } else if (!activeParams.equals(params)) {
      throw abruptExit(
          String.format(
              "The local analysis cache is already open with %s. Run 'bazel shutdown' before using"
                  + " %s.",
              activeParams, params),
          RemoteAnalysisCaching.Code.INCOMPATIBLE_OPTIONS,
          /* cause= */ null);
    }

    // Stock Bazel leaves this unset, which makes uploads fail outside of tests.
    env.setVersionGetter(MinimalVersionGetter.INSTANCE);

    if (options.maxSize > 0) {
      env.addIdleTask(new SweepIdleTask(diskStore, options.maxSize));
    }
  }

  private void openStore(StoreParams params) throws AbruptExitException {
    try {
      diskStore = PackedFingerprintValueStore.create(params.root());
    } catch (IOException e) {
      throw abruptExit(
          "Failed to open the local analysis cache at " + params.root(),
          RemoteAnalysisCaching.Code.REMOTE_ANALYSIS_CACHING_UNKNOWN,
          e);
    }
    supplier.setStore(LayeredFingerprintValueStore.create(diskStore, params.memoryBytes()));
    activeParams = params;
    logger.atInfo().log("Opened local analysis cache with %s", params);
  }

  @Override
  public void afterCommand() {
    if (diskStore == null) {
      return;
    }
    try {
      diskStore.flush();
    } catch (IOException e) {
      logger.atWarning().withCause(e).log("Failed to flush the local analysis cache");
    }
  }

  private static AbruptExitException abruptExit(
      String message, RemoteAnalysisCaching.Code code, @Nullable Exception cause) {
    var exitCode =
        DetailedExitCode.of(
            FailureDetail.newBuilder()
                .setMessage(message)
                .setRemoteAnalysisCaching(RemoteAnalysisCaching.newBuilder().setCode(code))
                .build());
    return cause == null
        ? new AbruptExitException(exitCode)
        : new AbruptExitException(exitCode, cause);
  }

  /** Sweeps the on-disk cache back under its size cap once the server goes idle. */
  private static final class SweepIdleTask implements IdleTask {
    private final PackedFingerprintValueStore store;
    private final long maxBytes;

    SweepIdleTask(PackedFingerprintValueStore store, long maxBytes) {
      this.store = store;
      this.maxBytes = maxBytes;
    }

    @Override
    public String displayName() {
      return "Local analysis cache sweep";
    }

    @Override
    public Duration delay() {
      return SWEEP_IDLE_DELAY;
    }

    @Override
    public void run() throws IdleTaskException {
      try {
        logger.atInfo().log("Local analysis cache sweep: %s", store.sweep(maxBytes));
      } catch (IOException e) {
        throw new IdleTaskException(e);
      }
    }
  }
}
