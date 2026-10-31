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

package com.google.devtools.build.lib.server;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;
import static org.junit.Assume.assumeTrue;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import org.junit.Before;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class EngineDirectoriesTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();
  private Path workspace;
  private Path output;

  @Before
  public void setUp() throws Exception {
    assumeTrue(System.getProperty("os.name").equals("Linux"));
    workspace = temporary.newFolder("workspace").toPath();
    output = temporary.newFolder("output").toPath();
  }

  @Test
  public void childEditsDoNotChangeDirectoryIdentity() throws Exception {
    try (EngineDirectories directories = EngineDirectories.open(workspace, output)) {
      Files.writeString(workspace.resolve("source"), "new source");
      Files.createDirectory(output.resolve("execroot"));
      Files.writeString(output.resolve("log"), "new log");
      assertThat(directories.isValid()).isTrue();
      assertThat(directories.canShutdown()).isTrue();
    }
  }

  @Test
  public void outputReplacementPermanentlyFencesOrdinaryCommands() throws Exception {
    try (EngineDirectories directories = EngineDirectories.open(workspace, output)) {
      Path original = output.resolveSibling("original-output");
      Files.move(output, original);
      Files.createDirectory(output);
      assertThat(directories.isValid()).isFalse();
      assertThat(directories.canShutdown()).isFalse();
      Files.delete(output);
      Files.move(original, output);
      assertThat(directories.isValid()).isFalse();
      assertThat(directories.canShutdown()).isTrue();
      assertThat(directories.isValid()).isFalse();
    }
    try (EngineDirectories replacement = EngineDirectories.open(workspace, output)) {
      assertThat(replacement.isValid()).isTrue();
    }
  }

  @Test
  public void workspaceReplacementIsNotTheSameEngine() throws Exception {
    try (EngineDirectories directories = EngineDirectories.open(workspace, output)) {
      Files.move(workspace, workspace.resolveSibling("original-workspace"));
      Files.createDirectory(workspace);
      assertThat(directories.isValid()).isFalse();
      assertThat(directories.canShutdown()).isFalse();
    }
  }

  @Test
  public void deletionAndRecreationCannotReusePinnedIdentity() throws Exception {
    try (EngineDirectories directories = EngineDirectories.open(workspace, output)) {
      Files.delete(output);
      Files.createDirectory(output);
      assertThat(directories.isValid()).isFalse();
      assertThat(directories.canShutdown()).isFalse();
    }
  }

  @Test
  public void missingPathFencesEvenIfRestoredBeforeNextCommand() throws Exception {
    try (EngineDirectories directories = EngineDirectories.open(workspace, output)) {
      Path original = output.resolveSibling("original");
      Files.move(output, original);
      assertThat(directories.isValid()).isFalse();
      Files.move(original, output);
      assertThat(directories.isValid()).isFalse();
      assertThat(directories.canShutdown()).isTrue();
    }
  }

  @Test
  public void symlinkToOriginalDirectoryDoesNotRemoveFence() throws Exception {
    try (EngineDirectories directories = EngineDirectories.open(workspace, output)) {
      Path original = output.resolveSibling("original");
      Files.move(output, original);
      Files.createSymbolicLink(output, original);
      assertThat(directories.isValid()).isFalse();
      assertThat(directories.canShutdown()).isFalse();
    }
  }

  @Test
  public void cannotCaptureMissingOrNoncanonicalDirectory() throws Exception {
    assertThrows(
        IOException.class, () -> EngineDirectories.open(workspace, output.resolve("missing")));
    Path alias = output.resolveSibling("alias");
    Files.createSymbolicLink(alias, output);
    assertThrows(IOException.class, () -> EngineDirectories.open(workspace, alias));
    assertThrows(IOException.class, () -> EngineDirectories.open(Path.of("relative"), output));
  }

  @Test
  public void closeIsIdempotentAndDisallowsDispatchAndCleanup() throws Exception {
    EngineDirectories directories = EngineDirectories.open(workspace, output);
    directories.close();
    directories.close();
    assertThat(directories.isValid()).isFalse();
    assertThat(directories.canShutdown()).isFalse();
  }
}
