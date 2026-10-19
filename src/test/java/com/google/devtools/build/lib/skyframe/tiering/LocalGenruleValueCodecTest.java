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
import static org.junit.Assert.assertThrows;

import com.google.common.collect.ImmutableClassToInstanceMap;
import com.google.common.collect.ImmutableList;
import com.google.devtools.build.lib.analysis.util.BuildViewTestCase;
import com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.concurrent.safeexecutor.SafeExecutorOwner;
import com.google.devtools.build.lib.packages.RuleClassProvider;
import com.google.devtools.build.lib.pkgcache.PackagePathCodecDependencies;
import com.google.devtools.build.lib.rules.genrule.GenRuleAction;
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

/** Audits local native-action round trips independently of the node's weak identity fast path. */
@RunWith(JUnit4.class)
public final class LocalGenruleValueCodecTest extends BuildViewTestCase {
  @Rule public final TemporaryFolder directory = new TemporaryFolder();

  @Test(timeout = 30000)
  public void nativeGenrulePreservesActionAndLocalIdentity() throws Exception {
    scratch.file("a/input.txt", "input");
    scratch.file(
        "a/BUILD",
        "genrule(name='g', srcs=['input.txt'], outs=['out.txt'], cmd='cat $(SRCS) > $@')");
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
              false);

      assertThat(codec.supports(original)).isTrue();
      byte[] record = codec.encode(original);
      var restored = (RuleConfiguredTargetValue) codec.decode(record);
      assertThat(restored).isNotSameInstanceAs(original);
      assertThat(restored.getConfiguredTarget().getLabel())
          .isEqualTo(original.getConfiguredTarget().getLabel());
      assertThat(restored.getTransitivePackages())
          .isSameInstanceAs(original.getTransitivePackages());
      assertThat(restored.getActions())
          .isSameInstanceAs(restored.getConfiguredTarget().getActions());
      GenRuleAction oldAction = (GenRuleAction) original.getActions().getFirst();
      GenRuleAction newAction = (GenRuleAction) restored.getActions().getFirst();
      assertThat(newAction.getArguments())
          .containsExactlyElementsIn(oldAction.getArguments())
          .inOrder();
      assertThat(newAction.getInputs()).isSameInstanceAs(oldAction.getInputs());
      assertThat(newAction.getOutputs()).containsExactlyElementsIn(oldAction.getOutputs());
      assertThat(newAction.getPrimaryOutput()).isSameInstanceAs(oldAction.getPrimaryOutput());
      assertThat(newAction.getOwner().getBuildConfigurationInfo())
          .isSameInstanceAs(oldAction.getOwner().getBuildConfigurationInfo());
      assertThat(codec.pinnedConfigurations()).isAtLeast(1);
      assertThat(store.writes()).isGreaterThan(0);
      codec.resetNestedSetCacheForTesting(store);
      var cold = (RuleConfiguredTargetValue) codec.decode(record);
      GenRuleAction coldAction = (GenRuleAction) cold.getActions().getFirst();
      assertThat(coldAction.getInputs().toList())
          .containsExactlyElementsIn(oldAction.getInputs().toList());
      assertThat(cold.getTransitivePackages().toList())
          .containsExactlyElementsIn(original.getTransitivePackages().toList());
      assertThat(coldAction.getArguments())
          .containsExactlyElementsIn(oldAction.getArguments())
          .inOrder();
      assertThat(store.reads()).isGreaterThan(0);
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
