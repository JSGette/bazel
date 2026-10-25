// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.actions.FileArtifactValue;
import com.google.devtools.build.lib.skyframe.ActionExecutionValue;
import com.google.devtools.build.lib.skyframe.TreeArtifactValue;
import com.google.devtools.build.skyframe.SkyValue;

/** Execution metadata that remains usable after the local output tree has been removed. */
final class LocalExecutionValues {
  static boolean isPortable(SkyValue value) {
    return switch (value) {
      case ActionExecutionValue action ->
          action.getRichArtifactData() == null
              && action.getAllFileValues().values().stream().allMatch(LocalExecutionValues::isRemoteFile)
              && action.getAllTreeArtifactValues().values().stream()
                  .allMatch(LocalExecutionValues::isRemoteTree);
      case TreeArtifactValue tree -> isRemoteTree(tree);
      default -> false;
    };
  }

  private static boolean isRemoteTree(TreeArtifactValue tree) {
    return tree != TreeArtifactValue.MISSING_TREE_ARTIFACT
        && tree.isEntirelyRemote()
        && tree.getResolvedPath().isEmpty()
        && tree.getChildValues().values().stream().allMatch(LocalExecutionValues::isRemoteFile)
        && tree.getArchivedRepresentation()
            .map(a -> isRemoteFile(a.archivedFileValue())).orElse(true);
  }

  private static boolean isRemoteFile(FileArtifactValue file) {
    // Resolved symlink metadata carries absolute output paths. The upstream codec only relocates
    // paths under package roots, so leave these actions to normal evaluation after clean.
    return file.isRemote() && file.getResolvedPath() == null;
  }

  private LocalExecutionValues() {}
}
