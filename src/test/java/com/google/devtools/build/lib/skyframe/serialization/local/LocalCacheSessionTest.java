// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import java.nio.ByteBuffer;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.Map;
import java.util.concurrent.ExecutionException;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalCacheSessionTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  private record Key(String name) implements KeyBytesProvider {
    @Override
    public byte[] toBytes() {
      return name.getBytes(UTF_8);
    }

    @Override
    public byte[] concat(byte[] suffix) {
      return ByteBuffer.allocate(toBytes().length + suffix.length)
          .put(toBytes())
          .put(suffix)
          .array();
    }
  }

  private LocalInputSnapshot snapshot(Path workspace, Path output) throws Exception {
    return new LocalInputSnapshot(workspace, output, Map.of("PATH", "/usr/bin"));
  }

  @Test
  public void sameContentsAcrossWorktreesHaveSameIdentityAndDirtyBytesInvalidate()
      throws Exception {
    Path a = temporary.newFolder("a").toPath();
    Path b = temporary.newFolder("b").toPath();
    Path output = temporary.newFolder("out").toPath();
    Files.writeString(a.resolve("ignored.txt"), "one");
    Files.writeString(b.resolve("ignored.txt"), "one");
    var first = snapshot(a, output);
    assertThat(snapshot(b, output).sourceDigest()).isEqualTo(first.sourceDigest());
    FileTime time = Files.getLastModifiedTime(b.resolve("ignored.txt"));
    Files.writeString(b.resolve("ignored.txt"), "two");
    Files.setLastModifiedTime(b.resolve("ignored.txt"), time);
    String second = snapshot(b, output).sourceDigest();
    assertThat(second).isNotEqualTo(first.sourceDigest());
    Files.writeString(b.resolve("ignored.txt"), "six");
    Files.setLastModifiedTime(b.resolve("ignored.txt"), time);
    assertThat(snapshot(b, output).sourceDigest()).isNotEqualTo(second);
    assertThat(new LocalInputSnapshot(a, output, Map.of("PATH", "changed")).sourceDigest())
        .isNotEqualTo(first.sourceDigest());
    Files.writeString(b.resolve("ignored.txt"), "one");
    assertThat(snapshot(b, output).sourceDigest()).isEqualTo(first.sourceDigest());
    Files.createDirectory(b.resolve("new-directory"));
    assertThat(snapshot(b, output).sourceDigest()).isNotEqualTo(first.sourceDigest());
  }

  @Test
  public void sourceSymlinkReferentsAndExecutableModesArePartOfIdentity() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.newFile("outside").toPath();
    Files.writeString(outside, "first");
    Files.createSymbolicLink(workspace.resolve("link"), outside);
    String first = snapshot(workspace, output).sourceDigest();
    Files.writeString(outside, "other");
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(first);
    Path file = Files.writeString(workspace.resolve("script"), "hello");
    String beforeMode = snapshot(workspace, output).sourceDigest();
    Files.setPosixFilePermissions(
        file, java.nio.file.attribute.PosixFilePermissions.fromString("rwx------"));
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(beforeMode);
  }

  @Test
  public void directorySymlinksIncludeReferentContentsAndRejectCycles() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.newFolder("outside").toPath();
    Path nested = Files.createDirectory(outside.resolve("nested"));
    Path source = Files.writeString(nested.resolve("source"), "one");
    Files.createSymbolicLink(workspace.resolve("linked"), outside);
    String before = snapshot(workspace, output).sourceDigest();
    Files.writeString(source, "two");
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(before);
    Files.createSymbolicLink(nested.resolve("cycle"), workspace);
    assertThrows(java.io.IOException.class, () -> snapshot(workspace, output));
  }

  @Test
  public void danglingSymlinkIsDifferentFromAnEmptyReferent() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.getRoot().toPath().resolve("missing");
    Files.createSymbolicLink(workspace.resolve("link"), outside);
    String dangling = snapshot(workspace, output).sourceDigest();
    Files.createFile(outside);
    assertThat(snapshot(workspace, output).sourceDigest()).isNotEqualTo(dangling);
  }

  @Test
  public void intermediateDirectorySymlinkRetargetingRejectsPublication() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path first = temporary.newFolder("first").toPath();
    Path second = temporary.newFolder("second").toPath();
    Path intermediate = temporary.getRoot().toPath().resolve("intermediate");
    Files.createSymbolicLink(intermediate, first);
    Path outer = Files.createDirectories(output.resolve("external")).resolve("sdk");
    Files.createSymbolicLink(outer, intermediate);
    var inputs = snapshot(workspace, output);
    inputs.observe(outer, false);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(new Key("entry"), new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var before = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(before.get(new Key("entry")).get()).isEqualTo(new byte[] {7});
    before.finish(false);
    // Outer link text and the referent's kind/mode remain identical.
    Files.delete(intermediate);
    Files.createSymbolicLink(intermediate, second);
    var after = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> after.get(new Key("entry")).get());
    after.finish(false);
  }

  @Test
  public void linkResolutionPreservesParentTraversalAfterIntermediateSymlink() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path outside = temporary.newFolder("outside").toPath();
    Path actual = Files.createDirectories(outside.resolve("target/deeper"));
    Path file = Files.writeString(actual.getParent().resolve("file"), "actual");
    Files.writeString(outside.resolve("file"), "lexically normalized, incorrect");
    Files.createSymbolicLink(outside.resolve("inner"), actual);
    Path alias = outside.resolve("alias");
    Files.createSymbolicLink(alias, Path.of("inner/../file"));
    var inputs = snapshot(workspace, output);
    inputs.observe(alias, false);
    assertThat(inputs.witnesses().stream().map(LocalInputSnapshot.Witness::path).toList())
        .contains("absolute/" + file);
    assertThat(inputs.isUnchanged()).isTrue();
    Files.writeString(file, "edited");
    assertThat(inputs.isUnchanged()).isFalse();
  }

  @Test
  public void privateWritesRemainHiddenUntilSuccessfulPublication() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    var key = new Key("key");
    writer.put(key, "value".getBytes(UTF_8)).get();
    assertThrows(ExecutionException.class, () -> reader.get(key).get());
    assertThat(writer.finish(true)).isTrue();
    // Discovery has a bounded one-second negative refresh interval.
    Thread.sleep(1100);
    assertThat(reader.get(key).get()).isEqualTo("value".getBytes(UTF_8));
    assertThat(reader.sharedReads()).isEqualTo(1);
    assertThat(reader.finish(true)).isFalse(); // A read-only command publishes no empty session.
  }

  @Test
  public void changedInputsAndFailedBuildsNeverPublish() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Files.writeString(workspace.resolve("input"), "before");
    var writer = new LocalCacheSession(cache, snapshot(workspace, output));
    writer.put(new Key("changed"), new byte[] {1}).get();
    Files.writeString(workspace.resolve("input"), "after");
    assertThat(writer.finish(true)).isFalse();
    var failed = new LocalCacheSession(cache, snapshot(workspace, output));
    failed.put(new Key("failed"), new byte[] {2}).get();
    assertThat(failed.finish(false)).isFalse();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("changed")).get());
    assertThrows(ExecutionException.class, () -> reader.get(new Key("failed")).get());
    reader.finish(false);
  }

  @Test
  public void externalInputsAndAbsentPathsAreValidatedAtLookup() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path external = Files.createDirectories(output.resolve("external/repo"));
    Path source = Files.writeString(external.resolve("source"), "one");
    var inputs = snapshot(workspace, output);
    inputs.observe(source, false);
    inputs.observe(external.resolve("missing"), false);
    inputs.observe(external, true);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(new Key("entry"), new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(reader.get(new Key("entry")).get()).isEqualTo(new byte[] {7});
    reader.finish(false);
    Files.writeString(source, "two");
    var edited = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> edited.get(new Key("entry")).get());
    edited.finish(false);
    Files.writeString(source, "one");
    Files.writeString(external.resolve("missing"), "now exists");
    var added = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> added.get(new Key("entry")).get());
    added.finish(false);
  }

  @Test
  public void repositoryBindingMustMatchEvenWhenTheOldPhysicalContentsStillExist()
      throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    Path oldRepo = temporary.newFolder("old-repo").toPath();
    Path newRepo = temporary.newFolder("new-repo").toPath();
    Path oldFile = Files.writeString(oldRepo.resolve("BUILD"), "old");
    Files.writeString(newRepo.resolve("BUILD"), "new");
    Path link = Files.createDirectories(output.resolve("external")).resolve("repo");
    Files.createSymbolicLink(link, oldRepo);
    var inputs = snapshot(workspace, output);
    inputs.observe(oldFile, false);
    var writer = new LocalCacheSession(cache, inputs);
    writer.put(new Key("entry"), new byte[] {7}).get();
    assertThat(writer.finish(true)).isTrue();
    Files.delete(link);
    Files.createSymbolicLink(link, newRepo);
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("entry")).get());
    reader.finish(false);
  }

  @Test
  public void inheritedPublicationRequiresItsParentClosure() throws Exception {
    Path workspace = temporary.newFolder("workspace").toPath();
    Path output = temporary.newFolder("output").toPath();
    Path cache = temporary.newFolder("cache").toPath();
    var parent = new LocalCacheSession(cache, snapshot(workspace, output));
    parent.put(new Key("chunk"), new byte[] {1}).get();
    assertThat(parent.finish(true)).isTrue();
    Path parentManifest;
    try (var files = Files.walk(cache.resolve("publications-v1"))) {
      parentManifest =
          files.filter(p -> p.toString().endsWith(".manifest")).findFirst().orElseThrow();
    }
    var child = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThat(child.get(new Key("chunk")).get()).isEqualTo(new byte[] {1});
    child.put(new Key("root"), new byte[] {2}).get();
    assertThat(child.finish(true)).isTrue();
    Files.delete(parentManifest);
    var reader = new LocalCacheSession(cache, snapshot(workspace, output));
    assertThrows(ExecutionException.class, () -> reader.get(new Key("root")).get());
    reader.finish(false);
  }
}
