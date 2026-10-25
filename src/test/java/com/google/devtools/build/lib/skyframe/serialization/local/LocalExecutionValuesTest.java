// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;

import com.google.common.collect.ImmutableMap;
import com.google.devtools.build.lib.actions.Artifact;
import com.google.devtools.build.lib.actions.ArtifactRoot;
import com.google.devtools.build.lib.actions.FileArtifactValue;
import com.google.devtools.build.lib.actions.util.ActionsTestUtil;
import com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder;
import com.google.devtools.build.lib.collect.nestedset.Order;
import com.google.devtools.build.lib.skyframe.ActionExecutionValue;
import com.google.devtools.build.lib.skyframe.TreeArtifactValue;
import com.google.devtools.build.lib.testutil.Scratch;
import com.google.devtools.build.lib.vfs.PathFragment;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalExecutionValuesTest {
  private static Artifact output(String name) {
    var root = ArtifactRoot.asDerivedRoot(
        new Scratch().resolve("/execroot"), ArtifactRoot.RootType.OUTPUT, "out");
    return ActionsTestUtil.createArtifact(root, name);
  }

  private static ActionExecutionValue action(ImmutableMap<Artifact, FileArtifactValue> files) {
    return ActionExecutionValue.create(
        files, ImmutableMap.of(), null, NestedSetBuilder.emptySet(Order.STABLE_ORDER));
  }

  @Test
  public void remoteMetadataSurvivesCleanButLocalOrMixedOutputsDoNot() {
    var remote = FileArtifactValue.createForRemoteFile(new byte[] {1}, 1, 1);
    var local = FileArtifactValue.createForNormalFile(new byte[] {1}, null, 1);
    assertThat(LocalExecutionValues.isPortable(action(ImmutableMap.of(output("remote"), remote))))
        .isTrue();
    assertThat(LocalExecutionValues.isPortable(action(ImmutableMap.of(output("local"), local))))
        .isFalse();
    assertThat(LocalExecutionValues.isPortable(action(
        ImmutableMap.of(output("remote"), remote, output("local"), local)))).isFalse();
  }

  @Test
  public void missingTreeIsNeverAReusableExecutionResult() {
    assertThat(LocalExecutionValues.isPortable(TreeArtifactValue.MISSING_TREE_ARTIFACT)).isFalse();
  }

  @Test
  public void resolvedOutputSymlinkIsNotPortableAcrossOutputBases() {
    var remote = FileArtifactValue.createForRemoteFile(new byte[] {1}, 1, 1);
    var symlink = FileArtifactValue.createFromExistingWithResolvedPath(
        remote, PathFragment.create("/old-output/execroot/main/bazel-out/bin/file"));
    assertThat(LocalExecutionValues.isPortable(action(ImmutableMap.of(output("link"), symlink))))
        .isFalse();
  }
}
