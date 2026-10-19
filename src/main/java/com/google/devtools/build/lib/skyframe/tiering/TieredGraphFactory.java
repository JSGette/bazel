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

import com.google.common.collect.ImmutableClassToInstanceMap;
import com.google.common.collect.ImmutableList;
import com.google.common.flogger.GoogleLogger;
import com.google.common.hash.Hashing;
import com.google.devtools.build.lib.analysis.BlazeDirectories;
import com.google.devtools.build.lib.analysis.ConfiguredRuleClassProvider;
import com.google.devtools.build.lib.compress.CompressionService;
import com.google.devtools.build.lib.concurrent.safeexecutor.SafeExecutorOwner;
import com.google.devtools.build.lib.packages.RuleClassProvider;
import com.google.devtools.build.lib.pkgcache.PackagePathCodecDependencies;
import com.google.devtools.build.lib.runtime.CommandEnvironment;
import com.google.devtools.build.lib.skyframe.SkyframeExecutor;
import com.google.devtools.build.lib.skyframe.serialization.AutoRegistry;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueCache;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.Fingerprinter;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.SerializationRegistrySetupHelpers;
import com.google.devtools.build.lib.vfs.Root;
import com.google.devtools.build.lib.vfs.Root.RootCodecDependencies;
import com.google.devtools.build.skyframe.DiskBackedGraph;
import com.google.devtools.build.skyframe.InMemoryGraph;
import java.io.Closeable;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Arrays;
import java.util.concurrent.Executors;
import java.util.function.Function;

/** Owns a separate ephemeral store and codec identity scope for each evaluator generation. */
final class TieredGraphFactory implements Function<Boolean, InMemoryGraph>, Closeable {
  private static final GoogleLogger logger = GoogleLogger.forEnclosingClass();
  private final Path binary;
  private final Path outputBase;
  private final int residentLimit;
  private final boolean goActions;
  private final BlazeDirectories directories;
  private final ConfiguredRuleClassProvider ruleClassProvider;
  private final SkyframeExecutor skyframeExecutor;
  private final String workspaceName;
  private final CompressionService compression;
  private SkystoreProcess process;
  private SafeExecutorOwner executor;
  private DiskBackedGraph graph;
  private LocalActionValueCodec codec;
  private SkystoreFingerprintStore chunks;
  private String disabledReason = "not_initialized";

  // Retain stable server services, never the command environment or its analysis result.
  TieredGraphFactory(CommandEnvironment env, String binary, int residentLimit, boolean goActions) {
    this.goActions = goActions;
    this.binary = Path.of(binary);
    this.outputBase = Path.of(env.getOutputBase().getPathString());
    this.residentLimit = residentLimit;
    this.directories = env.getDirectories();
    this.ruleClassProvider = env.getRuntime().getRuleClassProvider();
    this.skyframeExecutor = env.getSkyframeExecutor();
    this.workspaceName = env.getWorkspaceName();
    this.compression = env.getRuntime().getBlazeService(CompressionService.class);
  }

  boolean matches(String binary, int residentLimit, boolean goActions) {
    return this.binary.equals(Path.of(binary))
        && this.residentLimit == residentLimit
        && this.goActions == goActions;
  }

  boolean healthy() {
    return "edgeless".equals(disabledReason) || (process != null && process.isAlive());
  }

  @Override
  public InMemoryGraph apply(Boolean keepEdges) {
    close();
    if (!keepEdges) {
      disabledReason = "edgeless";
      return InMemoryGraph.createEdgeless(/* usePooledInterning= */ true);
    }
    // Resets also run during interrupted command cleanup. Do not start a child process when its
    // startup wait would immediately fail, and leave the interrupt set for the command dispatcher.
    if (Thread.currentThread().isInterrupted()) {
      disabledReason = "interrupted";
      logger.atWarning().log("Disk-backed Skyframe reset interrupted; using inmemory");
      return InMemoryGraph.create(/* usePooledInterning= */ true);
    }
    try {
      process = new SkystoreProcess(binary, outputBase);
      chunks = new SkystoreFingerprintStore(process);
      executor = new SafeExecutorOwner(Executors.newVirtualThreadPerTaskExecutor());
      var fingerprints =
          new FingerprintValueService(
              executor,
              chunks,
              new FingerprintValueCache(),
              new Fingerprinter() {
                @Override
                public PackedFingerprint fingerprint(byte[] bytes) {
                  return PackedFingerprint.fromBytes(
                      Arrays.copyOf(Hashing.sha256().hashBytes(bytes).asBytes(), 16));
                }

                @Override
                public PackedFingerprint fingerprint(byte[] bytes, String salt) {
                  return PackedFingerprint.fromBytes(
                      Arrays.copyOf(
                          Hashing.sha256()
                              .newHasher()
                              .putInt(salt.length())
                              .putUnencodedChars(salt)
                              .putBytes(bytes)
                              .hash()
                              .asBytes(),
                          16));
                }
              });
      var roots = ImmutableList.<Root>builder().add(Root.fromPath(directories.getWorkspace()));
      if (directories.isBlaze()) {
        roots.add(Root.fromPath(directories.getBlazeExecRoot()));
      }
      var dependencies =
          ImmutableClassToInstanceMap.<Object>builder()
              .put(RuleClassProvider.class, ruleClassProvider)
              .put(RootCodecDependencies.class, new RootCodecDependencies(roots.build()))
              .put(PackagePathCodecDependencies.class, skyframeExecutor::getPackagePathEntries)
              .build();
      var registry =
          SerializationRegistrySetupHelpers.addStarlarkFunctionality(
              AutoRegistry.get()
                  .getBuilder()
                  .addReferenceConstants(
                      SerializationRegistrySetupHelpers.makeReferenceConstants(
                          directories, ruleClassProvider, workspaceName)),
              ruleClassProvider);
      codec =
          new LocalActionValueCodec(
              registry,
              dependencies,
              skyframeExecutor.getSkyframeBuildView().getArtifactFactory(),
              chunks,
              fingerprints,
              compression,
              goActions);
      graph = new DiskBackedGraph(process, codec, residentLimit);
      disabledReason = "";
      return graph;
    } catch (IOException | RuntimeException e) {
      // A reset can happen after beforeCommand, including during clean and command teardown.
      // Return a complete replacement graph here rather than leaving the old evaluator attached
      // to a store that close() has already destroyed. The next command retries this factory.
      boolean interrupted = Thread.currentThread().isInterrupted();
      close();
      if (interrupted) {
        Thread.currentThread().interrupt();
      }
      disabledReason = interrupted ? "interrupted" : "startup_failure";
      logger.atWarning().withCause(e).log("Disk-backed Skyframe unavailable; using inmemory");
      return InMemoryGraph.create(/* usePooledInterning= */ true);
    }
  }

  void writeStats() throws IOException {
    if (graph == null) {
      Files.writeString(
          outputBase.resolve("skyframe-tiering.json"),
          "{\"enabled\":false,\"engine\":\""
              + (goActions ? "tiered_go" : "tiered_genrule")
              + "\",\"reason\":\""
              + disabledReason
              + "\",\"sidecar_pid\":0}\n");
      return;
    }
    var stats = graph.statistics();
    Files.writeString(
        outputBase.resolve("skyframe-tiering.json"),
        "{\"enabled\":true,\"engine\":\""
            + (goActions ? "tiered_go" : "tiered_genrule")
            + "\""
            + ",\"resident_limit_nodes\":"
            + residentLimit
            + ",\"nodes\":"
            + stats.nodes()
            + ",\"resident_nodes\":"
            + stats.resident()
            + ",\"pinned_nodes\":"
            + stats.pinned()
            + ",\"cold_nodes\":"
            + stats.cold()
            + ",\"page_ins\":"
            + stats.pageIns()
            + ",\"evictions\":"
            + stats.evictions()
            + ",\"node_bytes_read\":"
            + stats.bytesRead()
            + ",\"node_bytes_written\":"
            + stats.bytesWritten()
            + ",\"chunk_reads\":"
            + chunks.reads()
            + ",\"chunk_writes\":"
            + chunks.writes()
            + ",\"pinned_configurations\":"
            + codec.pinnedConfigurations()
            + ",\"pinned_starlark_definitions\":"
            + codec.pinnedStarlarkDefinitions()
            + ",\"pinned_runfiles\":"
            + codec.pinnedRunfiles()
            + ",\"sidecar_pid\":"
            + process.pid()
            + "}\n");
  }

  @Override
  public void close() {
    if (executor != null) {
      executor.shutdownNow();
      executor.awaitTermination(Duration.ofSeconds(5));
    }
    if (process != null) {
      process.close();
    }
    executor = null;
    process = null;
    graph = null;
    codec = null;
    chunks = null;
    disabledReason = "closed";
  }
}
