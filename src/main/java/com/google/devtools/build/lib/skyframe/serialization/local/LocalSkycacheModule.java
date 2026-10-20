// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFuture;
import static java.util.concurrent.ForkJoinPool.commonPool;

import com.google.common.collect.ImmutableList;
import com.google.common.eventbus.Subscribe;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.analysis.BlazeDirectories;
import com.google.devtools.build.lib.buildtool.buildevent.BuildCompleteEvent;
import com.google.devtools.build.lib.concurrent.safeexecutor.SafeExecutor;
import com.google.devtools.build.lib.concurrent.safeexecutor.SafeExecutorOwner;
import com.google.devtools.build.lib.events.Event;
import com.google.devtools.build.lib.runtime.BlazeRuntime;
import com.google.devtools.build.lib.runtime.CommandEnvironment;
import com.google.devtools.build.lib.skyframe.RemoteConfiguredTargetValue;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.InMemoryFingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecRegistry;
import com.google.devtools.build.lib.skyframe.serialization.SerializationModule;
import com.google.devtools.build.lib.skyframe.serialization.SerializationRegistrySetupHelpers;
import com.google.devtools.build.lib.skyframe.serialization.ValueSharingAdapter;
import com.google.devtools.build.lib.skyframe.serialization.analysis.ClientId;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCacheClient;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCachingOptions;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCachingServicesSupplier;
import com.google.devtools.common.options.OptionPriority.PriorityCategory;
import com.google.devtools.common.options.OptionsBase;
import com.google.devtools.common.options.OptionsParser;
import com.google.devtools.common.options.OptionsParsingException;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.function.Supplier;
import javax.annotation.Nullable;

/** Installs a concurrent local backend at the upstream analysis serialization/service boundary. */
public final class LocalSkycacheModule extends SerializationModule {
  private final Services services = new Services();
  @Nullable private CommandEnvironment environment;
  private boolean buildSucceeded;

  @Override
  protected Supplier<ObjectCodecRegistry> getAnalysisCodecRegistrySupplier(
      BlazeRuntime runtime, BlazeDirectories directories) {
    var fallback = super.getAnalysisCodecRegistrySupplier(runtime, directories);
    return () -> {
      if (services.session == null) {
        return fallback.get();
      }
      return SerializationRegistrySetupHelpers.initializeAnalysisCodecRegistryBuilder(
              runtime.getRuleClassProvider(),
              SerializationRegistrySetupHelpers.makeReferenceConstants(
                  directories,
                  runtime.getRuleClassProvider(),
                  runtime.getRuleClassProvider().getRunfilesPrefix()))
          .add(new LocalPathCodec(directories.getWorkspace(), directories.getOutputBase()))
          .add(new ValueSharingAdapter<>(new LocalSourceArtifactCodec()))
          .add(RemoteConfiguredTargetValue.codecWithTransitivePackages())
          .build();
    };
  }

  @Override
  protected RemoteAnalysisCachingServicesSupplier getAnalysisCachingServicesSupplier(
      BlazeRuntime runtime) {
    return services;
  }

  @Override
  public Iterable<Class<? extends OptionsBase>> getCommonCommandOptions() {
    return ImmutableList.of(LocalSkycacheOptions.class);
  }

  @Override
  public void beforeCommand(CommandEnvironment env) {
    environment = null;
    buildSucceeded = false;
    services.session = null;
    services.client = null;
    services.clientId = null;
    var options = env.getOptions().getOptions(LocalSkycacheOptions.class);
    if (options == null
        || options.getDirectory().isEmpty()
        || env.getWorkspace() == null
        || !env.getCommand().buildPhase().executes()) {
      return;
    }
    var caching = env.getOptions().getOptions(RemoteAnalysisCachingOptions.class);
    try {
      if (caching.getSkycacheMinimizeMemory()) {
        throw new IOException("local SkyCache requires the in-memory graph to be retained");
      }
      Path workspace = Path.of(env.getWorkspace().getPathString()).toRealPath();
      Path outputBase = Path.of(env.getOutputBase().getPathString()).toRealPath();
      Path root =
          Path.of(env.getWorkingDirectory().getRelative(options.getDirectory()).getPathString())
              .toAbsolutePath()
              .normalize();
      if (root.startsWith(workspace)) {
        throw new IOException("the shared cache must be outside the source workspace");
      }
      LocalCacheSession.durableDirectory(root);
      if (root.toRealPath().startsWith(workspace)) {
        throw new IOException("the shared cache resolves inside the source workspace");
      }
      var inputs = new LocalInputSnapshot(workspace, outputBase, env.getClientEnv());
      services.session = new LocalCacheSession(root, inputs);
      services.client = new LocalAnalysisCacheClient(services.session);
      services.clientId =
          new ClientId.SnapshotClientId(inputs.sourceDigest() + "/" + env.getCommandId(), 0);
      env.setVersionGetter(inputs);
      setMode(env, "async_bidi");
      environment = env;
      env.getEventBus().register(this);
    } catch (IOException e) {
      setMode(env, "off");
      env.getReporter().handle(Event.warn("Local SkyCache disabled: " + e.getMessage()));
    }
  }

  private static void setMode(CommandEnvironment env, String mode) {
    // OptionsParser creates a fresh options instance on every getOptions call. Change the parser,
    // so the factory and the analysis phase observe the same mode.
    try {
      ((OptionsParser) env.getOptions())
          .parse(
              PriorityCategory.SOFTWARE_REQUIREMENT,
              "local SkyCache",
              ImmutableList.of(
                  "--experimental_remote_analysis_cache_mode=" + mode,
                  "--experimental_skycache_analysis_only=false"));
    } catch (OptionsParsingException e) {
      throw new IllegalStateException("invalid local SkyCache options", e);
    }
  }

  @Subscribe
  public void buildComplete(BuildCompleteEvent event) {
    buildSucceeded = event.getResult().getSuccess();
  }

  @Override
  public void afterCommand() {
    var env = environment;
    var session = services.session;
    if (env != null && session != null) {
      boolean published = false;
      try {
        published = session.finish(buildSucceeded);
      } catch (IOException e) {
        env.getReporter()
            .handle(Event.warn("Local SkyCache publication skipped: " + e.getMessage()));
      }
      String statistics = session.statisticsJson(published);
      env.getReporter().handle(Event.info("Local SkyCache: " + statistics));
      try {
        Files.writeString(
            Path.of(env.getOutputBase().getPathString()).resolve("local-skycache.json"),
            statistics);
      } catch (IOException e) {
        env.getReporter().handle(Event.warn("Could not write local SkyCache statistics"));
      }
    }
    environment = null;
    super.afterCommand();
  }

  private static final class Services implements RemoteAnalysisCachingServicesSupplier {
    private final SafeExecutorOwner executor = new SafeExecutorOwner(commonPool());
    private final FingerprintValueStore fallback = new InMemoryFingerprintValueStore();
    @Nullable private LocalCacheSession session;
    @Nullable private LocalAnalysisCacheClient client;
    @Nullable private ClientId clientId;

    @Override
    public ListenableFuture<? extends FingerprintValueStore> getFingerprintValueStore() {
      return immediateFuture(session == null ? fallback : session);
    }

    @Override
    public ListenableFuture<? extends RemoteAnalysisCacheClient> getAnalysisCacheClient() {
      return client == null ? null : immediateFuture(client);
    }

    @Override
    public SafeExecutor getCommandExecutor() {
      return executor;
    }

    @Override
    public String getCacheKeyDistinguisher() {
      return session == null ? "" : session.sourceDigest();
    }

    @Override
    public ClientId getClientIdOverride() {
      return clientId;
    }

    @Override
    public void resetCommandState() {
      session = null;
      client = null;
      clientId = null;
    }
  }
}
