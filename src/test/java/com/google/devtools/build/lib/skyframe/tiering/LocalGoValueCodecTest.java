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

import static com.google.common.truth.Truth.assertThat;
import static com.google.devtools.build.lib.skyframe.BzlLoadValue.keyForBuild;
import static org.junit.Assert.assertThrows;

import com.google.common.collect.ImmutableClassToInstanceMap;
import com.google.common.collect.ImmutableList;
import com.google.devtools.build.lib.actions.ActionKeyContext;
import com.google.devtools.build.lib.analysis.actions.SpawnAction;
import com.google.devtools.build.lib.analysis.util.BuildViewTestCase;
import com.google.devtools.build.lib.cmdline.Label;
import com.google.devtools.build.lib.collect.nestedset.Depset;
import com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.concurrent.safeexecutor.SafeExecutorOwner;
import com.google.devtools.build.lib.packages.RuleClassProvider;
import com.google.devtools.build.lib.packages.StarlarkInfo;
import com.google.devtools.build.lib.packages.StarlarkProvider;
import com.google.devtools.build.lib.packages.StructImpl;
import com.google.devtools.build.lib.pkgcache.PackagePathCodecDependencies;
import com.google.devtools.build.lib.skyframe.ConfiguredTargetKey;
import com.google.devtools.build.lib.skyframe.RuleConfiguredTargetValue;
import com.google.devtools.build.lib.skyframe.serialization.AutoRegistry;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueCache;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.SerializationRegistrySetupHelpers;
import com.google.devtools.build.lib.vfs.Root;
import com.google.devtools.build.lib.vfs.Root.RootCodecDependencies;
import com.google.devtools.build.runfiles.Runfiles;
import com.google.devtools.build.skyframe.SkyValue;
import java.io.IOException;
import java.nio.file.Path;
import java.time.Duration;
import java.util.concurrent.Executors;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Audits Starlark spawn round trips independently of the node's weak identity fast path. */
@RunWith(JUnit4.class)
public final class LocalGoValueCodecTest extends BuildViewTestCase {
  @Rule public final TemporaryFolder directory = new TemporaryFolder();

  @Test(timeout = 30000)
  public void goLibraryPreservesProvidersCommandsAndActionIdentity() throws Exception {
    scratch.file("a/input.txt", "input");
    scratch.file("a/input2.txt", "input2");
    scratch.file("a/input3.txt", "input3");
    scratch.file("go/private/BUILD");
    scratch.file(
        "go/private/providers.bzl",
        """
        GoArchive = provider(fields = ["files", "options", "direct", "payload", "transitive"])
        GoArchiveData = provider()
        OtherInfo = provider(fields = ["value"])
        def _map(file):
            return file.path
        def _resources(os, inputs):
            return {"cpu": 1, "memory": 128}
        def _impl(ctx):
            out = ctx.actions.declare_file(ctx.label.name + ".a")
            args = ctx.actions.args()
            args.add_all(depset(ctx.files.srcs), map_each = _map)
            args.use_param_file("@%s", use_always = True)
            args.set_param_file_format("multiline")
            export = ctx.actions.declare_file(ctx.label.name + ".x")
            outputs = [out, export]
            unused = None
            if ctx.attr.prune:
                unused = ctx.actions.declare_file(ctx.label.name + ".unused")
                outputs.append(unused)
            ctx.actions.run(executable = "/bin/true", arguments = [args],
                inputs = ctx.files.srcs, outputs = outputs, mnemonic = "GoCompilePkg",
                resource_set = _resources, unused_inputs_list = unused)
            validation = ctx.actions.declare_file(ctx.label.name + ".validation")
            ctx.actions.run(executable = "/bin/true", inputs = [out],
                outputs = [validation], mnemonic = "GoValidate")
            cycle = {}
            other = OtherInfo(value = cycle)
            cycle["self"] = other
            data = GoArchiveData(file = out, runfiles = ctx.runfiles(files = ctx.files.srcs))
            return [DefaultInfo(files = depset([out])), other,
                GoArchive(files = depset([out]), options = {"importpath": "example/a"},
                    direct = [d[GoArchive] for d in ctx.attr.deps], payload = ctx.attr.payload,
                    transitive = depset([data], transitive = [d[GoArchive].transitive for d in ctx.attr.deps]))]
        go_library = rule(implementation = _impl, attrs = {
            "srcs": attr.label_list(allow_files = True), "prune": attr.bool(),
            "deps": attr.label_list(), "payload": attr.string()})
        """);
    byte[] random = new byte[32768];
    new java.util.Random(1234).nextBytes(random);
    String payload = java.util.Base64.getEncoder().encodeToString(random);
    scratch.file(
        "a/BUILD",
        """
        load("//go/private:providers.bzl", "go_library")
        go_library(name = "leaf", srcs = ["input.txt"], payload = "%s")
        go_library(name = "left", deps = [":leaf"])
        go_library(name = "right", deps = [":leaf"])
        go_library(name = "g", srcs = ["input.txt", "input2.txt", "input3.txt"], deps = [":left", ":right"])
        go_library(name = "pruned", srcs = ["input.txt"], prune = True)
        """
            .formatted(payload));
    var target = getConfiguredTarget("//a:g");
    var analyzed =
        (RuleConfiguredTargetValue)
            skyframeExecutor
                .getEvaluator()
                .getExistingValue(ConfiguredTargetKey.fromConfiguredTarget(target));
    // Ordinary builds may omit this optional field; explicitly exercise its non-null form.
    var original =
        new RuleConfiguredTargetValue(
            analyzed.getConfiguredTarget(),
            NestedSetBuilder.<com.google.devtools.build.lib.packages.Package.Metadata>stableOrder()
                .add(getTarget("//a:g").getPackageMetadata())
                .build());
    Path binary = Path.of(Runfiles.create().rlocation("_main/tools/skystore/skystore"));
    var process = new SkystoreProcess(binary.toAbsolutePath(), directory.getRoot().toPath());
    var store = new SkystoreFingerprintStore(process);
    var executor = new SafeExecutorOwner(Executors.newVirtualThreadPerTaskExecutor());
    try {
      var fingerprints =
          new FingerprintValueService(
              executor,
              store,
              new FingerprintValueCache(),
              FingerprintValueService.NONPROD_FINGERPRINTER);
      var registry =
          SerializationRegistrySetupHelpers.addStarlarkFunctionality(
              AutoRegistry.get()
                  .getBuilder()
                  .addReferenceConstants(
                      SerializationRegistrySetupHelpers.makeReferenceConstants(
                          directories, ruleClassProvider, ruleClassProvider.getRunfilesPrefix())),
              ruleClassProvider);
      var dependencies =
          ImmutableClassToInstanceMap.<Object>builder()
              .put(RuleClassProvider.class, ruleClassProvider)
              .put(
                  RootCodecDependencies.class,
                  new RootCodecDependencies(
                      ImmutableList.of(Root.fromPath(directories.getWorkspace()))))
              .put(PackagePathCodecDependencies.class, skyframeExecutor::getPackagePathEntries)
              .build();
      var codec =
          new LocalActionValueCodec(
              registry,
              dependencies,
              view.getArtifactFactory(),
              store,
              fingerprints,
              new CompressionServiceImpl(),
              true);

      assertThat(codec.supports(original)).isTrue();
      assertThat(original.getActions()).hasSize(2);
      var pruned = getConfiguredTarget("//a:pruned");
      var prunedValue =
          (RuleConfiguredTargetValue)
              skyframeExecutor
                  .getEvaluator()
                  .getExistingValue(ConfiguredTargetKey.fromConfiguredTarget(pruned));
      assertThat(codec.supports(prunedValue)).isFalse();
      byte[] record = codec.encode(original);
      // The large transitive payload belongs in a shared chunk, not every node record.
      assertThat(record.length).isLessThan(random.length);
      var restored = (RuleConfiguredTargetValue) codec.decode(record);
      assertThat(restored).isNotSameInstanceAs(original);
      assertThat(restored.getConfiguredTarget().getLabel())
          .isEqualTo(original.getConfiguredTarget().getLabel());
      assertThat(restored.getTransitivePackages())
          .isSameInstanceAs(original.getTransitivePackages());
      assertThat(restored.getActions())
          .isSameInstanceAs(restored.getConfiguredTarget().getActions());
      SpawnAction oldAction = (SpawnAction) original.getActions().getFirst();
      SpawnAction newAction = (SpawnAction) restored.getActions().getFirst();
      assertThat(newAction.getArguments())
          .containsExactlyElementsIn(oldAction.getArguments())
          .inOrder();
      assertThat(newAction.getKey(new ActionKeyContext(), null))
          .isEqualTo(oldAction.getKey(new ActionKeyContext(), null));
      assertThat(newAction.getInputs().toList())
          .containsExactlyElementsIn(oldAction.getInputs().toList());
      assertThat(newAction.getOutputs()).containsExactlyElementsIn(oldAction.getOutputs());
      assertThat(newAction.getPrimaryOutput()).isSameInstanceAs(oldAction.getPrimaryOutput());
      assertThat(newAction.getOwner().getBuildConfigurationInfo())
          .isSameInstanceAs(oldAction.getOwner().getBuildConfigurationInfo());
      assertThat(codec.pinnedConfigurations()).isAtLeast(1);
      assertThat(store.writes()).isGreaterThan(0);
      codec.resetNestedSetCacheForTesting(store);
      codec.resetFingerprintServiceForTesting(
          new FingerprintValueService(
              executor,
              store,
              new FingerprintValueCache(),
              FingerprintValueService.NONPROD_FINGERPRINTER));
      var cold = (RuleConfiguredTargetValue) codec.decode(record);
      SpawnAction coldAction = (SpawnAction) cold.getActions().getFirst();
      assertThat(coldAction.getInputs().toList())
          .containsExactlyElementsIn(oldAction.getInputs().toList());
      assertThat(cold.getTransitivePackages().toList())
          .containsExactlyElementsIn(original.getTransitivePackages().toList());
      assertThat(coldAction.getArguments())
          .containsExactlyElementsIn(oldAction.getArguments())
          .inOrder();
      assertThat(coldAction.getKey(new ActionKeyContext(), null))
          .isEqualTo(oldAction.getKey(new ActionKeyContext(), null));
      var providerKey =
          new StarlarkProvider.Key(
              keyForBuild(Label.parseCanonical("//go/private:providers.bzl")), "GoArchive");
      var oldInfo = (StructImpl) original.getConfiguredTarget().get(providerKey);
      var coldInfo = (StructImpl) cold.getConfiguredTarget().get(providerKey);
      assertThat(coldInfo.getProvider()).isSameInstanceAs(oldInfo.getProvider());
      assertThat(coldInfo.getValue("options")).isEqualTo(oldInfo.getValue("options"));
      var oldArchives =
          Depset.cast(oldInfo.getValue("transitive"), StarlarkInfo.class, "transitive");
      var coldArchives =
          Depset.cast(coldInfo.getValue("transitive"), StarlarkInfo.class, "transitive");
      assertThat(
              NestedSetBuilder.<StarlarkInfo>stableOrder()
                  .addTransitive(oldArchives)
                  .addTransitive(coldArchives)
                  .build()
                  .toList())
          .containsExactlyElementsIn(oldArchives.toList());
      assertThat(codec.pinnedRunfiles()).isGreaterThan(0);
      var direct = (java.util.List<?>) coldInfo.getValue("direct");
      var left = (StructImpl) direct.get(0);
      var right = (StructImpl) direct.get(1);
      var leftLeaf = (StructImpl) ((java.util.List<?>) left.getValue("direct")).get(0);
      var rightLeaf = (StructImpl) ((java.util.List<?>) right.getValue("direct")).get(0);
      assertThat(leftLeaf.getValue("payload")).isEqualTo(payload);
      assertThat(leftLeaf.getValue("options")).isSameInstanceAs(rightLeaf.getValue("options"));
      var otherKey =
          new StarlarkProvider.Key(
              keyForBuild(Label.parseCanonical("//go/private:providers.bzl")), "OtherInfo");
      var other = (StructImpl) cold.getConfiguredTarget().get(otherKey);
      assertThat(((java.util.Map<?, ?>) other.getValue("value")).get("self"))
          .isSameInstanceAs(other);
      for (int i = 0; i < original.getActions().size(); i++) {
        var before = (SpawnAction) original.getActions().get(i);
        var after = (SpawnAction) cold.getActions().get(i);
        assertThat(after.getKey(new ActionKeyContext(), null))
            .isEqualTo(before.getKey(new ActionKeyContext(), null));
        assertThat(after.getPrimaryOutput()).isSameInstanceAs(before.getPrimaryOutput());
      }
      assertThat(store.reads()).isGreaterThan(0);
      assertThat(codec.pinnedStarlarkDefinitions()).isGreaterThan(0);
      // A failed shared chunk must also fail every later reader of its cached future.
      codec.resetNestedSetCacheForTesting(store);
      process.clear();
      for (int i = 0; i < 2; i++) {
        assertThrows(
            Exception.class,
            () -> {
              var missing = (RuleConfiguredTargetValue) codec.decode(record);
              missing.getActions().getFirst().getInputs().toList();
            });
      }
      assertThat(codec.supports(new SkyValue() {})).isFalse();
      assertThrows(IOException.class, () -> codec.encode(new SkyValue() {}));
    } finally {
      executor.shutdownNow();
      executor.awaitTermination(Duration.ofSeconds(5));
      process.close();
    }
  }
}
