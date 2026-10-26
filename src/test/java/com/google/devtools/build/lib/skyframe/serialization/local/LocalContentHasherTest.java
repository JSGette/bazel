// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import java.io.InterruptedIOException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalContentHasherTest {
  @Rule public final TemporaryFolder temporary = new TemporaryFolder();

  private byte[] hash(LocalContentHasher hasher, Path path) throws Exception {
    var digest = MessageDigest.getInstance("SHA-256");
    hasher.hashContents(path, digest, Files.size(path));
    return digest.digest();
  }

  @Test
  public void aliasesReuseOneContentDigestButDistinctFilesAreRead() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "contents");
    Path link = file.resolveSibling("symbolic");
    Files.createSymbolicLink(link, file.getFileName());
    Path hard = file.resolveSibling("hard");
    Files.createLink(hard, file);
    Path distinct = Files.writeString(temporary.newFile().toPath(), "contents");
    var hasher = new LocalContentHasher(0, 8);
    byte[] expected = hash(hasher, file);
    assertThat(hash(hasher, link)).isEqualTo(expected);
    assertThat(hash(hasher, hard)).isEqualTo(expected);
    assertThat(hash(hasher, distinct)).isEqualTo(expected);
    assertThat(hasher.statistics()).isEqualTo(new LocalContentHasher.Statistics(2, 16, 2, 2));
  }

  @Test
  public void concurrentAliasesShareOneBoundedDigestCache() throws Exception {
    Path file = Files.write(temporary.newFile().toPath(), new byte[1024 * 1024]);
    Path symbolic = Files.createSymbolicLink(file.resolveSibling("symbolic"), file.getFileName());
    Path hard = Files.createLink(file.resolveSibling("hard"), file);
    var pass = new LocalContentHasher(0, 8);
    var barrier = new CyclicBarrier(4);
    var hashers = new ArrayList<LocalContentHasher>();
    var pending = new ArrayList<Future<byte[]>>();
    try (var executor = Executors.newFixedThreadPool(4)) {
      for (Path alias : new Path[] {file, symbolic, hard, file}) {
        var hasher = new LocalContentHasher(pass);
        hashers.add(hasher);
        pending.add(
            executor.submit(
                () -> {
                  barrier.await();
                  return hash(hasher, alias);
                }));
      }
      for (var result : pending) {
        assertThat(result.get()).isEqualTo(pending.getFirst().get());
      }
    }
    assertThat(hashers.stream().mapToLong(h -> h.statistics().filesHashed()).sum()).isEqualTo(1);
    assertThat(hashers.stream().mapToLong(h -> h.statistics().bytesHashed()).sum())
        .isEqualTo(1024 * 1024);
    assertThat(hashers.stream().mapToLong(h -> h.statistics().hits()).sum()).isEqualTo(3);
    assertThat(pass.statistics().retainedDigests()).isEqualTo(1);
  }

  @Test
  public void sharedCacheRechecksVersionAndDoesNotCrossPasses() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "one");
    var first = new LocalContentHasher(0, 8);
    byte[] original = hash(first, file);
    var sibling = new LocalContentHasher(first);
    assertThat(hash(sibling, file)).isEqualTo(original);
    assertThat(sibling.statistics().hits()).isEqualTo(1);
    var modified = Files.getLastModifiedTime(file);
    Thread.sleep(5);
    Files.writeString(file, "two");
    Files.setLastModifiedTime(file, modified);
    assertThat(hash(sibling, file)).isNotEqualTo(original);
    assertThat(sibling.statistics().filesHashed()).isEqualTo(1);
    var nextPass = new LocalContentHasher(0, 8);
    assertThat(hash(nextPass, file)).isEqualTo(hash(sibling, file));
    assertThat(nextPass.statistics().filesHashed()).isEqualTo(1);
    assertThat(nextPass.statistics().hits()).isEqualTo(0);
  }

  @Test
  public void concurrentDistinctFilesRespectSharedCapacity() throws Exception {
    var pass = new LocalContentHasher(0, 2);
    var barrier = new CyclicBarrier(4);
    var pending = new ArrayList<Future<LocalContentHasher.Statistics>>();
    try (var executor = Executors.newFixedThreadPool(4)) {
      for (int i = 0; i < 4; i++) {
        Path file = Files.writeString(temporary.newFile().toPath(), "contents");
        pending.add(
            executor.submit(
                () -> {
                  var hasher = new LocalContentHasher(pass);
                  barrier.await();
                  hash(hasher, file);
                  return hasher.statistics();
                }));
      }
      for (var result : pending) {
        assertThat(result.get().filesHashed()).isEqualTo(1);
        assertThat(result.get().retainedDigests()).isAtMost(2);
      }
    }
    assertThat(pass.statistics().retainedDigests()).isEqualTo(2);
  }

  @Test
  public void sameSizeContentChangeWithRestoredMtimeIsRehashed() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "one");
    var hasher = new LocalContentHasher(0, 8);
    byte[] original = hash(hasher, file);
    var modified = Files.getLastModifiedTime(file);
    Thread.sleep(5); // Separate the Unix change times even on coarse test filesystems.
    Files.writeString(file, "two");
    Files.setLastModifiedTime(file, modified);
    assertThat(hash(hasher, file)).isNotEqualTo(original);
    assertThat(hasher.statistics().filesHashed()).isEqualTo(2);
    assertThat(hasher.statistics().hits()).isEqualTo(0);
  }

  @Test
  public void replacementWithEqualSizeAndMtimeIsRehashed() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "one");
    Path replacement = Files.writeString(temporary.newFile().toPath(), "two");
    Files.setLastModifiedTime(replacement, Files.getLastModifiedTime(file));
    var hasher = new LocalContentHasher(0, 8);
    byte[] original = hash(hasher, file);
    Files.move(replacement, file, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
    assertThat(hash(hasher, file)).isNotEqualTo(original);
    assertThat(hasher.statistics().filesHashed()).isEqualTo(2);
  }

  @Test
  public void retargetedSymbolicLinkUsesNewReferent() throws Exception {
    Path first = Files.writeString(temporary.newFile().toPath(), "one");
    Path second = Files.writeString(temporary.newFile().toPath(), "two");
    Path link = first.resolveSibling("symbolic");
    Files.createSymbolicLink(link, first.getFileName());
    var hasher = new LocalContentHasher(0, 8);
    byte[] original = hash(hasher, link);
    Files.delete(link);
    Files.createSymbolicLink(link, second.getFileName());
    assertThat(hash(hasher, link)).isNotEqualTo(original);
    assertThat(hasher.statistics().filesHashed()).isEqualTo(2);
  }

  @Test
  public void eachPassStartsWithFreshContentReads() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "one");
    var first = new LocalContentHasher(0, 8);
    var second = new LocalContentHasher(0, 8);
    assertThat(hash(first, file)).isEqualTo(hash(second, file));
    assertThat(first.statistics().filesHashed()).isEqualTo(1);
    assertThat(second.statistics().filesHashed()).isEqualTo(1);
  }

  @Test
  public void capacityOverflowStillChecksContent() throws Exception {
    Path first = Files.writeString(temporary.newFile().toPath(), "one");
    Path second = Files.writeString(temporary.newFile().toPath(), "two");
    var hasher = new LocalContentHasher(0, 1);
    hash(hasher, first);
    byte[] secondDigest = hash(hasher, second);
    assertThat(hash(hasher, second)).isEqualTo(secondDigest);
    hash(hasher, first);
    assertThat(hasher.statistics()).isEqualTo(new LocalContentHasher.Statistics(3, 9, 1, 1));
  }

  @Test
  public void smallFilesDoNotRetainDigests() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "one");
    var hasher = new LocalContentHasher();
    hash(hasher, file);
    hash(hasher, file);
    assertThat(hasher.statistics()).isEqualTo(new LocalContentHasher.Statistics(2, 6, 0, 0));
  }

  @Test
  public void emptyFileUsesSha256ContentDigest() throws Exception {
    Path file = temporary.newFile().toPath();
    var sha = MessageDigest.getInstance("SHA-256");
    assertThat(hash(new LocalContentHasher(0, 1), file)).isEqualTo(sha.digest(sha.digest()));
  }

  @Test
  public void interruptionPreservesFlagAndDoesNotHash() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "one");
    var hasher = new LocalContentHasher(0, 8);
    try {
      Thread.currentThread().interrupt();
      assertThrows(InterruptedIOException.class, () -> hash(hasher, file));
      assertThat(Thread.currentThread().isInterrupted()).isTrue();
      assertThat(hasher.statistics().filesHashed()).isEqualTo(0);
    } finally {
      Thread.interrupted();
    }
  }

  @Test
  public void missingInputCannotUseCachedDigest() throws Exception {
    Path file = Files.writeString(temporary.newFile().toPath(), "one");
    var hasher = new LocalContentHasher(0, 8);
    hash(hasher, file);
    Files.delete(file);
    assertThrows(
        NoSuchFileException.class,
        () -> hasher.hashContents(file, MessageDigest.getInstance("SHA-256"), 3));
    assertThat(hasher.statistics().hits()).isEqualTo(0);
  }
}
