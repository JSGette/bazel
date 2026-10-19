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

package com.google.devtools.build.lib.skyframe.tiering;

import com.google.devtools.build.lib.analysis.AnalysisOptions;
import com.google.devtools.build.lib.events.Event;
import com.google.devtools.build.lib.runtime.BlazeModule;
import com.google.devtools.build.lib.runtime.CommandEnvironment;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCacheMode;
import com.google.devtools.build.lib.skyframe.serialization.analysis.RemoteAnalysisCachingOptions;
import com.google.devtools.build.skyframe.InMemoryGraph;
import com.google.devtools.common.options.EnumConverter;
import com.google.devtools.common.options.Option;
import com.google.devtools.common.options.OptionDocumentationCategory;
import com.google.devtools.common.options.OptionEffectTag;
import com.google.devtools.common.options.OptionMetadataTag;
import com.google.devtools.common.options.OptionsBase;
import com.google.devtools.common.options.OptionsClass;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Experimental Rust-backed idle and demand-loaded graph tiers. Disabled by default. */
public final class SkyframeTieringModule extends BlazeModule {
  public enum Engine {
    INMEMORY,
    TIERED_IDLE,
    TIERED_GENRULE,
    TIERED_GO
  }

  public static final class EngineConverter extends EnumConverter<Engine> {
    public EngineConverter() {
      super(Engine.class, "Skyframe engine");
    }
  }

  @OptionsClass
  public abstract static class Options extends OptionsBase {
    @Option(
        name = "experimental_skyframe_engine",
        defaultValue = "inmemory",
        converter = EngineConverter.class,
        documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
        effectTags = {OptionEffectTag.HOST_MACHINE_RESOURCE_OPTIMIZATIONS},
        metadataTags = {OptionMetadataTag.EXPERIMENTAL},
        help =
            "inmemory, tiered_idle, tiered_genrule, or tiered_go. tiered_idle restores glob values"
                + " before commands. tiered_genrule pages complete native-genrule nodes on demand;"
                + " other values remain pinned. tiered_go also pages audited Go library spawn"
                + " actions. Experimental and disabled by default.")
    public abstract Engine getEngine();

    @Option(
        name = "experimental_skystore_binary",
        defaultValue = "",
        documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
        effectTags = {OptionEffectTag.HOST_MACHINE_RESOURCE_OPTIMIZATIONS},
        metadataTags = {OptionMetadataTag.EXPERIMENTAL},
        help = "Absolute path to the optional skystore executable for the tiered engines.")
    public abstract String getBinary();

    @Option(
        name = "experimental_skyframe_resident_nodes",
        defaultValue = "128",
        documentationCategory = OptionDocumentationCategory.UNDOCUMENTED,
        effectTags = {OptionEffectTag.HOST_MACHINE_RESOURCE_OPTIMIZATIONS},
        metadataTags = {OptionMetadataTag.EXPERIMENTAL},
        help =
            "Maximum resident eligible completed nodes for tiered_genrule or tiered_go. Other nodes"
                + " are pinned.")
    public abstract int getResidentNodes();
  }

  private static final long SPILL_BUDGET = 32L * 1024 * 1024;
  private TieredGraphFactory pagedFactory;
  private SkystoreProcess process;
  private IdleValueTier tier;
  private InMemoryGraph graph;
  private String binary;
  private boolean enabled;
  private long recoveryCount;
  private long restoredValues;
  private long restoreNanos;

  @Override
  public Iterable<Class<? extends OptionsBase>> getCommandOptions(String commandName) {
    return List.of(Options.class);
  }

  @Override
  public void beforeCommand(CommandEnvironment env) {
    boolean wasActive = tier != null || pagedFactory != null;
    enabled = false;
    restoredValues = 0;
    long start = System.nanoTime();
    if (tier != null) {
      try {
        // If a previous clean discarded the graph, the old references have no remaining readers.
        if (graph == env.getSkyframeExecutor().getEvaluator().getInMemoryGraph()) {
          restoredValues = tier.detachedCount();
          tier.restore();
        } else {
          stop();
        }
      } catch (IOException | RuntimeException e) {
        // Restoring is all-or-reset. Never let a detached value reach incremental evaluation.
        env.getSkyframeExecutor().resetEvaluator();
        recoveryCount++;
        env.getReporter()
            .handle(
                Event.warn("Skyframe tier restore failed; rebuilding graph: " + e.getMessage()));
        stop();
      } finally {
        if (process != null) {
          process.disconnect();
        }
      }
    }
    restoreNanos = System.nanoTime() - start;
    Options options = env.getOptions().getOptions(Options.class);
    if (pagedFactory != null
        && ((options.getEngine() != Engine.TIERED_GENRULE
                && options.getEngine() != Engine.TIERED_GO)
            || !pagedFactory.matches(
                options.getBinary(),
                options.getResidentNodes(),
                options.getEngine() == Engine.TIERED_GO)
            || !pagedFactory.healthy())) {
      stopPaged(env);
    }
    if (options.getEngine() == Engine.TIERED_GENRULE || options.getEngine() == Engine.TIERED_GO) {
      stop();
      AnalysisOptions analysis = env.getOptions().getOptions(AnalysisOptions.class);
      RemoteAnalysisCachingOptions remote =
          env.getOptions().getOptions(RemoteAnalysisCachingOptions.class);
      if (options.getResidentNodes() < 0
          || (analysis != null && analysis.getDiscardAnalysisCache())
          || (remote != null && remote.getMode() != RemoteAnalysisCacheMode.OFF)) {
        stopPaged(env);
        env.getReporter()
            .handle(
                Event.warn(
                    "Demand paging disabled: requires a nonnegative resident-node limit, "
                        + "retained analysis values, and remote analysis caching off."));
        writeStats(env, 0);
        return;
      }
      if (pagedFactory == null) {
        try {
          pagedFactory =
              new TieredGraphFactory(
                  env,
                  options.getBinary(),
                  options.getResidentNodes(),
                  options.getEngine() == Engine.TIERED_GO);
          env.getSkyframeExecutor().setExperimentalGraphFactory(pagedFactory);
        } catch (RuntimeException e) {
          stopPaged(env);
          env.getReporter()
              .handle(
                  Event.warn(
                      "Disk-backed Skyframe unavailable; using inmemory: " + e.getMessage()));
          writeStats(env, 0);
        }
      }
      return;
    }
    if (options.getEngine() != Engine.TIERED_IDLE) {
      stop();
      if (wasActive) {
        writeStats(env, 0);
      }
      return;
    }
    try {
      if (process != null && !options.getBinary().equals(binary)) {
        stop();
      }
      if (process == null) {
        process =
            new SkystoreProcess(
                Path.of(options.getBinary()), Path.of(env.getOutputBase().getPathString()));
        binary = options.getBinary();
      }
      // There are no detached values now. A new tier avoids retaining nodes from a reset graph.
      tier = new IdleValueTier(process);
      graph = env.getSkyframeExecutor().getEvaluator().getInMemoryGraph();
      enabled = true;
      process.disconnect();
    } catch (IOException | RuntimeException e) {
      stop();
      env.getReporter()
          .handle(Event.warn("Skyframe tier unavailable; using inmemory: " + e.getMessage()));
      writeStats(env, 0);
    }
  }

  @Override
  public void afterCommandMaintenance(CommandEnvironment env) {
    if (pagedFactory != null) {
      try {
        pagedFactory.writeStats();
      } catch (IOException ignored) {
        // Diagnostics do not affect graph correctness.
      }
      return;
    }
    if (!enabled) {
      return;
    }
    long start = System.nanoTime();
    try {
      graph = env.getSkyframeExecutor().getEvaluator().getInMemoryGraph();
      tier.spill(graph, SPILL_BUDGET);
    } catch (IOException | RuntimeException e) {
      // A write may have succeeded before the connection failed. Restore committed references;
      // if that fails, discard the graph rather than trusting unverified storage.
      try {
        tier.restore();
      } catch (IOException | RuntimeException restoreFailure) {
        env.getSkyframeExecutor().resetEvaluator();
        recoveryCount++;
      }
      env.getReporter()
          .handle(
              Event.warn("Skyframe tier spill failed; keeping resident state: " + e.getMessage()));
      stop();
    } finally {
      if (process != null) {
        process.disconnect();
      }
      writeStats(env, System.nanoTime() - start);
    }
  }

  private void writeStats(CommandEnvironment env, long spillNanos) {
    try {
      Files.writeString(
          Path.of(env.getOutputBase().getPathString(), "skyframe-tiering.json"),
          "{\"enabled\":"
              + (tier != null)
              + ",\"engine\":\""
              + (tier == null ? "inmemory" : "tiered_idle")
              + "\",\"detached_values\":"
              + (tier == null ? 0 : tier.detachedCount())
              + ",\"serialized_bytes\":"
              + (tier == null ? 0 : tier.serializedBytes())
              + ",\"restored_values\":"
              + restoredValues
              + ",\"restore_nanos\":"
              + restoreNanos
              + ",\"spill_nanos\":"
              + spillNanos
              + ",\"recovery_count\":"
              + recoveryCount
              + ",\"sidecar_pid\":"
              + (process == null ? 0 : process.pid())
              + "}\n");
    } catch (IOException ignored) {
      // Diagnostic output is never required for correctness.
    }
  }

  private void stopPaged(CommandEnvironment env) {
    if (pagedFactory != null) {
      env.getSkyframeExecutor().setExperimentalGraphFactory(null);
      pagedFactory.close();
      pagedFactory = null;
    }
  }

  private void stop() {
    if (process != null) {
      process.close();
    }
    process = null;
    tier = null;
    graph = null;
    enabled = false;
  }

  @Override
  public void blazeShutdown() {
    stop();
    if (pagedFactory != null) {
      pagedFactory.close();
      pagedFactory = null;
    }
  }
}
