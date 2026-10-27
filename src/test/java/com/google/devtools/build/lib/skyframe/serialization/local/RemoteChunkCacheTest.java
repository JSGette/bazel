// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static org.junit.Assert.assertThrows;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProtocol;
import java.io.IOException;
import java.nio.file.FileAlreadyExistsException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class RemoteChunkCacheTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  @Test
  public void byteLimitEvictsOldestAndPreservesActivePrivatePin() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 8);
    Digest first = store(cache, "aaaa", 1000);
    Digest second = store(cache, "bbbb", 2000);
    Digest third = store(cache, "cccc", 3000);
    Path pin = temporary.newFolder().toPath().resolve("pin");
    assertThat(cache.link(first, pin)).isTrue();

    cache.prune();

    assertThat(cache.contains(first)).isFalse();
    assertThat(cache.contains(second)).isTrue();
    assertThat(cache.contains(third)).isTrue();
    assertThat(Files.readString(pin)).isEqualTo("aaaa");
  }

  @Test
  public void entryLimitAppliesBelowByteLimit() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 100, 2);
    Digest first = store(cache, "a", 1000);
    Digest second = store(cache, "b", 2000);
    Digest third = store(cache, "c", 3000);

    cache.prune();

    assertThat(cache.contains(first)).isFalse();
    assertThat(cache.contains(second)).isTrue();
    assertThat(cache.contains(third)).isTrue();
  }

  @Test
  public void touchingPrivatePinUpdatesLruAcrossInstances() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var writer = new RemoteChunkCache(managed, 4);
    Digest first = store(writer, "aaaa", 1000);
    Digest second = store(writer, "bbbb", 2000);
    Path pin = temporary.newFolder().toPath().resolve("pin");
    var reader = new RemoteChunkCache(managed, 4);
    assertThat(reader.link(first, pin)).isTrue();
    reader.touch(pin);

    writer.prune();

    assertThat(reader.contains(first)).isTrue();
    assertThat(reader.contains(second)).isFalse();
    assertThat(Files.getLastModifiedTime(entry(managed, first)).toMillis()).isGreaterThan(2000L);
  }

  @Test
  public void disabledCacheDoesNotCreateOrTouchAnything() throws Exception {
    Path managed = temporary.newFolder().toPath().resolve("absent");
    Path source = bytes("data");
    Files.setLastModifiedTime(source, FileTime.fromMillis(1000));
    Digest digest = digest("data");
    Path pending = source.resolveSibling("pending");
    var cache = new RemoteChunkCache(managed, 0);

    assertThat(cache.contains(digest)).isFalse();
    assertThat(cache.link(digest, pending)).isFalse();
    cache.store(digest, source);
    cache.touch(source);
    cache.invalidate(digest);
    cache.prune();

    assertThat(Files.exists(managed, NOFOLLOW_LINKS)).isFalse();
    assertThat(Files.exists(pending, NOFOLLOW_LINKS)).isFalse();
    assertThat(Files.getLastModifiedTime(source).toMillis()).isEqualTo(1000);
    assertThrows(IOException.class, () -> new RemoteChunkCache(managed, -1));
    assertThrows(IOException.class, () -> new RemoteChunkCache(managed, 1, -1));
  }

  @Test
  public void duplicatePublicationNeverReplacesOriginalInodeOrExistingPendingPath()
      throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 100);
    Digest digest = digest("data");
    Path first = bytes("data");
    Path duplicate = bytes("data");
    cache.store(digest, first);
    cache.store(digest, duplicate);

    assertThat(Files.isSameFile(entry(managed, digest), first)).isTrue();
    assertThat(Files.isSameFile(entry(managed, digest), duplicate)).isFalse();
    Path pending = bytes("keep");
    assertThrows(FileAlreadyExistsException.class, () -> cache.link(digest, pending));
    assertThat(Files.readString(pending)).isEqualTo("keep");
  }

  @Test
  public void incompleteOrSymbolicPrivateSourcesCannotBePublished() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 100);
    Digest digest = digest("complete");
    Path partial = bytes("partial");
    Path complete = bytes("complete");
    Path symbolic = Files.createSymbolicLink(complete.resolveSibling("symbolic"), complete);
    for (Path source : List.of(partial, symbolic)) {
      assertThrows(IOException.class, () -> cache.store(digest, source));
      assertThat(cache.contains(digest)).isFalse();
      assertThat(Files.exists(entry(managed, digest), NOFOLLOW_LINKS)).isFalse();
    }
    cache.store(digest, complete);
    assertThat(Files.isSameFile(entry(managed, digest), complete)).isTrue();
  }

  @Test
  public void ancestorReplacedBySymlinkAfterConstructionCannotReceiveRetainedWrites()
      throws Exception {
    for (String relative : List.of("", "remote-chunks-v1", "remote-chunks-v1/sha256")) {
      Path managed = temporary.newFolder().toPath();
      var cache = new RemoteChunkCache(managed, 100);
      Path source = bytes("complete");
      Digest digest = digest("complete");
      Path ancestor = managed.resolve(relative);
      Files.move(ancestor, ancestor.resolveSibling(ancestor.getFileName() + "-original"));
      Path outside = temporary.newFolder().toPath();
      Files.createSymbolicLink(ancestor, outside);

      assertThrows(IOException.class, () -> cache.store(digest, source));
      assertThat(cache.contains(digest)).isFalse();
      try (var paths = Files.walk(outside)) {
        assertThat(paths.count()).isEqualTo(1);
      }
      assertThat(Files.readString(source)).isEqualTo("complete");
    }
  }

  @Test
  public void containsIsReadOnlyAndMissesDoNotCreatePrefixes() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 100);
    Digest stored = store(cache, "stored", 1000);
    Digest absent = Digest.newBuilder().setHash("ff" + "0".repeat(62)).setSizeBytes(10).build();
    Path prefix = entry(managed, absent).getParent();
    assertThat(Files.exists(prefix, NOFOLLOW_LINKS)).isFalse();

    assertThat(cache.contains(stored)).isTrue();
    assertThat(cache.contains(absent)).isFalse();
    assertThat(cache.link(absent, managed.resolve("pending"))).isFalse();

    assertThat(Files.getLastModifiedTime(entry(managed, stored)).toMillis()).isEqualTo(1000);
    assertThat(Files.exists(prefix, NOFOLLOW_LINKS)).isFalse();
    assertThat(Files.exists(managed.resolve("pending"), NOFOLLOW_LINKS)).isFalse();
  }

  @Test
  public void wrongSizeEntryCanBePinnedThenInvalidatedAndRepaired() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 100);
    Digest digest = store(cache, "good", 1000);
    Files.writeString(entry(managed, digest), "truncated");
    Path pin = temporary.newFolder().toPath().resolve("pin");

    assertThat(cache.contains(digest)).isFalse();
    assertThat(cache.link(digest, pin)).isTrue();
    assertThat(Files.readString(pin)).isEqualTo("truncated");
    cache.invalidate(digest);
    cache.store(digest, bytes("good"));

    assertThat(cache.contains(digest)).isTrue();
    assertThat(Files.readString(entry(managed, digest))).isEqualTo("good");
    assertThat(Files.readString(pin)).isEqualTo("truncated");
  }

  @Test
  public void symlinkEntryIsUnlinkedWithoutFollowingAndCanBeRepaired() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 100);
    Digest digest = store(cache, "good", 1000);
    cache.invalidate(digest);
    Path outside = bytes("keep");
    Files.createSymbolicLink(entry(managed, digest), outside);
    Path pin = temporary.newFolder().toPath().resolve("pin");

    assertThat(cache.contains(digest)).isFalse();
    assertThat(cache.link(digest, pin)).isFalse();
    assertThat(Files.exists(entry(managed, digest), NOFOLLOW_LINKS)).isFalse();
    assertThat(Files.readString(outside)).isEqualTo("keep");
    assertThat(Files.exists(pin, NOFOLLOW_LINKS)).isFalse();
    cache.store(digest, bytes("good"));
    assertThat(cache.link(digest, pin)).isTrue();
    assertThat(Files.readString(pin)).isEqualTo("good");
    assertThrows(
        IOException.class,
        () -> cache.touch(Files.createSymbolicLink(pin.resolveSibling("symlink"), outside)));
  }

  @Test
  public void pruningIgnoresUnknownFilesDirectoriesAndSymlinks() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 1);
    Digest owned = store(cache, "owned", 1000);
    Path prefix = entry(managed, owned).getParent();
    Path unknown = Files.writeString(prefix.resolve("keep.txt"), "unknown");
    Path nested = Files.createDirectory(prefix.resolve("nested"));
    Path nestedFile = Files.writeString(nested.resolve("keep"), "nested");
    Path outside = bytes("outside");
    Digest symbolic =
        Digest.newBuilder()
            .setHash(owned.getHash().substring(0, 2) + "0".repeat(62))
            .setSizeBytes(7)
            .build();
    Path symbolicEntry = Files.createSymbolicLink(entry(managed, symbolic), outside);
    Path foreign = Files.createDirectory(managed.resolve("remote-chunks-v1/sha256/foreign"));
    Path foreignFile = Files.writeString(foreign.resolve(owned.getHash() + "-5"), "foreign");
    Path outsideDirectory = temporary.newFolder().toPath();
    Digest escaped = Digest.newBuilder().setHash("ff" + "1".repeat(62)).setSizeBytes(7).build();
    Path outsideEntry =
        Files.writeString(outsideDirectory.resolve(escaped.getHash() + "-7"), "outside");
    Files.createSymbolicLink(entry(managed, escaped).getParent(), outsideDirectory);

    cache.prune();

    assertThat(cache.contains(owned)).isFalse();
    assertThat(Files.readString(unknown)).isEqualTo("unknown");
    assertThat(Files.readString(nestedFile)).isEqualTo("nested");
    assertThat(Files.readString(foreignFile)).isEqualTo("foreign");
    assertThat(Files.isSymbolicLink(symbolicEntry)).isTrue();
    assertThat(Files.readString(outside)).isEqualTo("outside");
    assertThat(Files.readString(outsideEntry)).isEqualTo("outside");
    assertThat(cache.contains(escaped)).isFalse();
    assertThat(cache.link(escaped, managed.resolve("escaped-pin"))).isFalse();
    assertThrows(IOException.class, () -> cache.store(escaped, outside));
  }

  @Test
  public void invalidDigestsAndBlockedRootsFailWithoutTraversingPaths() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 0);
    Path source = bytes("data");
    for (Digest invalid :
        List.of(
            Digest.newBuilder().setHash("../" + "a".repeat(61)).build(),
            Digest.newBuilder().setHash("A".repeat(64)).build(),
            digest("data").toBuilder().setSizeBytes(-1).build(),
            digest("data").toBuilder().setSizeBytes(SkyCacheProtocol.CHUNK_BYTES + 1).build())) {
      assertThrows(IOException.class, () -> cache.contains(invalid));
      assertThrows(IOException.class, () -> cache.link(invalid, managed.resolve("pending")));
      assertThrows(IOException.class, () -> cache.store(invalid, source));
      assertThrows(IOException.class, () -> cache.invalidate(invalid));
    }
    Files.writeString(managed.resolve("remote-chunks-v1"), "blocked");
    assertThrows(IOException.class, () -> new RemoteChunkCache(managed, 100));
  }

  @Test
  public void multipleInstancesCanPublishAndPruneWithoutLosingPrivatePins() throws Exception {
    Path managed = temporary.newFolder().toPath();
    var cache = new RemoteChunkCache(managed, 12, 3);
    Digest retained = store(cache, "pin!", 1000);
    Path pin = temporary.newFolder().toPath().resolve("pin");
    assertThat(cache.link(retained, pin)).isTrue();
    var start = new CyclicBarrier(8);
    List<Path> sources = new ArrayList<>();
    for (int i = 0; i < 8; i++) {
      sources.add(bytes("item" + i));
    }
    try (var pool = Executors.newFixedThreadPool(8)) {
      var futures = new ArrayList<java.util.concurrent.Future<?>>();
      for (int i = 0; i < sources.size(); i++) {
        Path source = sources.get(i);
        futures.add(
            pool.submit(
                () -> {
                  var other = new RemoteChunkCache(managed, 12, 3);
                  Digest digest = SkyCacheProtocol.digest(Files.readAllBytes(source));
                  start.await(20, TimeUnit.SECONDS);
                  for (int repeat = 0; repeat < 8; repeat++) {
                    other.store(digest, source);
                    other.prune();
                  }
                  return null;
                }));
      }
      for (var future : futures) {
        future.get(20, TimeUnit.SECONDS);
      }
    }
    cache.prune();
    try (var files = Files.walk(managed.resolve("remote-chunks-v1/sha256"))) {
      List<Path> entries = files.filter(path -> Files.isRegularFile(path, NOFOLLOW_LINKS)).toList();
      assertThat(entries.size()).isAtMost(3);
      long bytes = 0;
      for (Path path : entries) {
        bytes += Files.size(path);
      }
      assertThat(bytes).isAtMost(12);
    }
    assertThat(Files.readString(pin)).isEqualTo("pin!");
  }

  private Digest store(RemoteChunkCache cache, String contents, long modified) throws IOException {
    Path source = bytes(contents);
    Files.setLastModifiedTime(source, FileTime.fromMillis(modified));
    Digest digest = digest(contents);
    cache.store(digest, source);
    return digest;
  }

  private Path bytes(String contents) throws IOException {
    return Files.writeString(temporary.newFile().toPath(), contents);
  }

  private static Digest digest(String contents) {
    return SkyCacheProtocol.digest(contents.getBytes(UTF_8));
  }

  private static Path entry(Path managed, Digest digest) {
    return managed
        .resolve("remote-chunks-v1/sha256")
        .resolve(digest.getHash().substring(0, 2))
        .resolve(digest.getHash() + "-" + digest.getSizeBytes());
  }
}
