// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;

import com.google.common.testing.GcFinalization;
import com.google.devtools.build.lib.actions.ActionLookupData;
import com.google.devtools.build.lib.actions.Artifact;
import com.google.devtools.build.lib.actions.ArtifactRoot;
import com.google.devtools.build.lib.actions.util.ActionsTestUtil;
import com.google.devtools.build.lib.analysis.Runfiles;
import com.google.devtools.build.lib.analysis.RunfilesOrigin;
import com.google.devtools.build.lib.analysis.SymlinkEntry;
import com.google.devtools.build.lib.collect.nestedset.DeferredNestedSetCodec;
import com.google.devtools.build.lib.collect.nestedset.NestedSet;
import com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.AutoRegistry;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.InMemoryFingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecs;
import com.google.devtools.build.lib.skyframe.serialization.ValueSharingAdapter;
import com.google.devtools.build.lib.vfs.DigestHashFunction;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.build.lib.vfs.Root;
import com.google.devtools.build.lib.vfs.inmemoryfs.InMemoryFileSystem;
import com.google.protobuf.ByteString;
import java.lang.ref.WeakReference;
import java.util.ArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalRunfilesCodecTest {
  private final CompressionServiceImpl compression = new CompressionServiceImpl();
  private final InMemoryFingerprintValueStore store = new InMemoryFingerprintValueStore();
  private final FingerprintValueService writer = FingerprintValueService.createForTesting(store);
  private final FingerprintValueService reader = FingerprintValueService.createForTesting(store);
  private final InMemoryFileSystem fs = new InMemoryFileSystem(DigestHashFunction.SHA256);
  private final ArtifactRoot artifactRoot =
      ArtifactRoot.asSourceRoot(Root.fromPath(fs.getPath("/source")));
  private final Artifact artifactA = ActionsTestUtil.createArtifact(artifactRoot, "a");
  private final Artifact artifactB = ActionsTestUtil.createArtifact(artifactRoot, "b");
  private final ObjectCodecs codecs =
      new ObjectCodecs(
          AutoRegistry.get()
              .getBuilder()
              .add(new DeferredNestedSetCodec())
              .add(new ValueSharingAdapter<>(new LocalRunfilesCodec()))
              .add(new ValueSharingAdapter<>(new LocalSymlinkEntryCodec()))
              .addReferenceConstant(artifactA)
              .addReferenceConstant(artifactB)
              .addReferenceConstant(Runfiles.EMPTY)
              .build());

  @After
  public void disableConstructionSharing() {
    RunfilesOrigin.setSharingEnabled(false);
  }

  @Test
  public void sharedRunfilesLeafRemainsUniqueAcrossNestedSetBranches() throws Exception {
    Runfiles leaf = scopedRunfiles("diamond-leaf", "leaf");
    var left =
        NestedSetBuilder.<Runfiles>stableOrder()
            .add(leaf)
            .add(scopedRunfiles("diamond-left", "left"))
            .build();
    var right =
        NestedSetBuilder.<Runfiles>stableOrder()
            .add(leaf)
            .add(scopedRunfiles("diamond-right", "right"))
            .build();
    var top =
        NestedSetBuilder.<Runfiles>stableOrder().addTransitive(left).addTransitive(right).build();
    assertThat(top.toList()).hasSize(3);
    NestedSet<?> restored = (NestedSet<?>) decode(encode(top));
    assertThat(restored.toList()).hasSize(3);
  }

  @Test
  public void distinctRunfilesWithIdenticalFieldsAreNotMerged() throws Exception {
    Runfiles first;
    Runfiles second;
    try (var scope = RunfilesOrigin.enter("distinct-originals")) {
      first = new Runfiles.Builder("same").build();
      second = new Runfiles.Builder("same").build();
    }
    assertThat(first).isNotSameInstanceAs(second);
    Runfiles restoredFirst = (Runfiles) decode(encode(first));
    Runfiles restoredSecond = (Runfiles) decode(encode(second));
    assertThat(restoredFirst).isNotSameInstanceAs(restoredSecond);
    assertThat(
            NestedSetBuilder.stableOrder().add(restoredFirst).add(restoredSecond).build().toList())
        .hasSize(2);
  }

  @Test
  public void oneOriginalObjectHasOneReaderIdentityAcrossSeparateRoots() throws Exception {
    Runfiles original = scopedRunfiles("single-original", "shared");
    ByteString first = encode(original);
    ByteString second = encode(original);
    Object restoredFirst = decode(first);
    assertThat(decode(second)).isSameInstanceAs(restoredFirst);
    assertThat(restoredFirst).isSameInstanceAs(original);
  }

  @Test
  public void emptySingletonRemainsTheReaderSingleton() throws Exception {
    assertThat(decode(encode(Runfiles.EMPTY))).isSameInstanceAs(Runfiles.EMPTY);
  }

  @Test
  public void independentWritersAgreeOnOneProducersIdentity() throws Exception {
    Runfiles first;
    Runfiles second;
    try (var scope = RunfilesOrigin.enter("producer")) {
      first = new Runfiles.Builder("same").build();
    }
    try (var scope = RunfilesOrigin.enter("producer")) {
      second = new Runfiles.Builder("same").build();
    }
    assertThat(first).isNotSameInstanceAs(second);
    assertThat(first.equals(second)).isFalse();
    Object restored = decode(encode(first));
    assertThat(decode(encode(FingerprintValueService.createForTesting(store), second)))
        .isSameInstanceAs(restored);
    assertThat(RunfilesOrigin.get((Runfiles) restored)).isEqualTo(RunfilesOrigin.get(first));
  }

  @Test
  public void sameProducerDifferentOrdinalsRemainDistinct() throws Exception {
    Runfiles first;
    Runfiles second;
    try (var scope = RunfilesOrigin.enter("producer")) {
      first = new Runfiles.Builder("same").build();
      second = new Runfiles.Builder("same").build();
    }
    assertThat(RunfilesOrigin.get(first).ordinal()).isEqualTo(0);
    assertThat(RunfilesOrigin.get(second).ordinal()).isEqualTo(1);
    assertThat(decode(encode(first))).isNotSameInstanceAs(decode(encode(second)));
  }

  @Test
  public void differentProducersWithIdenticalFieldsRemainDistinct() throws Exception {
    Runfiles first;
    Runfiles second;
    try (var scope = RunfilesOrigin.enter("producer-a")) {
      first = new Runfiles.Builder("same").build();
    }
    try (var scope = RunfilesOrigin.enter("producer-b")) {
      second = new Runfiles.Builder("same").build();
    }
    assertThat(decode(encode(first))).isNotSameInstanceAs(decode(encode(second)));
  }

  @Test
  public void changedContentsArePartOfTheSharedPayloadIdentity() throws Exception {
    Runfiles first;
    Runfiles second;
    try (var scope = RunfilesOrigin.enter("producer")) {
      first = new Runfiles.Builder("before").build();
    }
    try (var scope = RunfilesOrigin.enter("producer")) {
      second = new Runfiles.Builder("after").build();
    }
    assertThat(RunfilesOrigin.get(first)).isEqualTo(RunfilesOrigin.get(second));
    assertThat(decode(encode(first)))
        .isNotSameInstanceAs(
            decode(encode(FingerprintValueService.createForTesting(store), second)));
  }

  @Test
  public void nestedScopesRestoreTheirParentsAndDoNotLeak() throws Exception {
    Runfiles first;
    Runfiles inner;
    Runfiles second;
    try (var outerScope = RunfilesOrigin.enter("outer")) {
      first = new Runfiles.Builder("same").build();
      try (var innerScope = RunfilesOrigin.enter("inner")) {
        inner = new Runfiles.Builder("same").build();
      }
      second = new Runfiles.Builder("same").build();
    }
    assertThat(RunfilesOrigin.get(first)).isEqualTo(new RunfilesOrigin.Origin("outer", 0));
    assertThat(RunfilesOrigin.get(inner)).isEqualTo(new RunfilesOrigin.Origin("inner", 0));
    assertThat(RunfilesOrigin.get(second)).isEqualTo(new RunfilesOrigin.Origin("outer", 1));
    assertThat(RunfilesOrigin.get(new Runfiles.Builder("unscoped").build())).isNull();
  }

  @Test
  public void restoredOriginsSurviveSerializationByAnotherWriter() throws Exception {
    Runfiles original;
    try (var scope = RunfilesOrigin.enter("producer")) {
      original = new Runfiles.Builder("same").build();
    }
    ByteString first = encode(original);
    Runfiles restored = (Runfiles) decode(first);
    ByteString second = encode(FingerprintValueService.createForTesting(store), restored);
    assertThat(second).isEqualTo(first);
    assertThat(decode(second)).isSameInstanceAs(restored);
  }

  @Test
  public void restoredBranchReconnectsToExistingOriginal() throws Exception {
    Runfiles original;
    try (var scope = RunfilesOrigin.enter("live-original")) {
      original = new Runfiles.Builder("same").build();
    }
    assertThat(decode(encode(original))).isSameInstanceAs(original);
    assertThat(
            NestedSetBuilder.stableOrder()
                .add(original)
                .add((Runfiles) decode(encode(original)))
                .build()
                .toList())
        .hasSize(1);
  }

  @Test
  public void constructionAfterRestoreUsesTheSameProducerObject() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    Runfiles restored =
        RunfilesOrigin.restore(
            new Runfiles.Builder("same").build(), new RunfilesOrigin.Origin("restored-first", 0));
    RunfilesOrigin.setSharingEnabled(true);
    try (var scope = RunfilesOrigin.enter("restored-first")) {
      assertThat(new Runfiles.Builder("same").build()).isSameInstanceAs(restored);
      assertThat(new Runfiles.Builder("same").build()).isNotSameInstanceAs(restored);
    }
  }

  @Test
  public void disabledConstructionKeepsReferenceIdentity() throws Exception {
    Runfiles first;
    Runfiles second;
    try (var scope = RunfilesOrigin.enter("disabled-construction")) {
      first = new Runfiles.Builder("same").build();
    }
    try (var scope = RunfilesOrigin.enter("disabled-construction")) {
      second = new Runfiles.Builder("same").build();
    }
    assertThat(first).isNotSameInstanceAs(second);
  }

  @Test
  public void differingConflictPoliciesAreNeverAliased() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    Runfiles existing =
        RunfilesOrigin.restore(
            new Runfiles.Builder("same").build().setConflictPolicy(Runfiles.ConflictPolicy.ERROR),
            new RunfilesOrigin.Origin("different-policy", 0));
    RunfilesOrigin.setSharingEnabled(true);
    try (var scope = RunfilesOrigin.enter("different-policy")) {
      assertThat(new Runfiles.Builder("same").build()).isNotSameInstanceAs(existing);
    }
  }

  @Test
  public void emptySingletonHasNoProducerOrigin() throws Exception {
    assertThat(RunfilesOrigin.get(Runfiles.EMPTY)).isNull();
  }

  @Test
  public void symlinkEntriesReconnectBeforeTheirContainingRunfiles() throws Exception {
    Runfiles original;
    try (var scope = RunfilesOrigin.enter("symlinks-original")) {
      original =
          new Runfiles.Builder("same")
              .addSymlink(PathFragment.create("alias"), artifactA)
              .addRootSymlink(PathFragment.create("root-alias"), artifactB)
              .setConflictPolicy(Runfiles.ConflictPolicy.ERROR)
              .build();
    }
    original.getAllArtifacts(); // This derived memo must not affect value identity.
    assertThat(decode(encode(original))).isSameInstanceAs(original);
  }

  @Test
  public void restoredSymlinksReconnectToLaterConstruction() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    Runfiles first;
    try (var scope = RunfilesOrigin.enter("symlinks-first")) {
      first =
          new Runfiles.Builder("same")
              .addSymlink(PathFragment.create("alias"), artifactA)
              .setConflictPolicy(Runfiles.ConflictPolicy.ERROR)
              .build();
    }
    Runfiles restored = (Runfiles) decode(encode(first));
    try (var scope = RunfilesOrigin.enter("symlinks-first")) {
      assertThat(
              new Runfiles.Builder("same")
                  .addSymlink(PathFragment.create("alias"), artifactA)
                  .setConflictPolicy(Runfiles.ConflictPolicy.ERROR)
                  .build())
          .isSameInstanceAs(restored);
    }
  }

  @Test
  public void separateIdenticalSymlinksAreNotCollapsed() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    Runfiles original;
    try (var scope = RunfilesOrigin.enter("distinct-symlinks")) {
      original =
          new Runfiles.Builder("same")
              .addSymlink(PathFragment.create("alias"), artifactA)
              .addSymlink(PathFragment.create("alias"), artifactB)
              .addSymlink(PathFragment.create("alias"), artifactA)
              .build();
    }
    var links = original.getSymlinks().toList();
    assertThat(links).hasSize(3);
    assertThat(links.get(0)).isNotSameInstanceAs(links.get(2));
    Runfiles restored = (Runfiles) decode(encode(original));
    assertThat(restored.getSymlinks().toList()).containsExactlyElementsIn(links).inOrder();
  }

  @Test
  public void unscopedSymlinksCannotBePublished() throws Exception {
    Runfiles original =
        new Runfiles.Builder("same").addSymlink(PathFragment.create("alias"), artifactA).build();
    var failure =
        org.junit.Assert.assertThrows(
            Exception.class, () -> encode(original.getSymlinks().toList().get(0)));
    assertThat(rootCause(failure))
        .hasMessageThat()
        .contains("missing creator provenance for SymlinkEntry");
  }

  @Test
  public void unscopedRunfilesCannotBePublished() throws Exception {
    var failure =
        org.junit.Assert.assertThrows(
            Exception.class, () -> encode(new Runfiles.Builder("unscoped").build()));
    assertThat(rootCause(failure))
        .hasMessageThat()
        .contains("missing creator provenance for Runfiles");
  }

  private static Throwable rootCause(Throwable failure) {
    while (failure.getCause() != null) {
      failure = failure.getCause();
    }
    return failure;
  }

  private static Runfiles scopedRunfiles(String owner, String prefix) {
    try (var scope = RunfilesOrigin.enter(owner)) {
      return new Runfiles.Builder(prefix).build();
    }
  }

  @Test
  public void changedSymlinkContentsAreNotAliased() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    SymlinkEntry first;
    try (var scope = RunfilesOrigin.enter("changed-symlink")) {
      first =
          new Runfiles.Builder("same")
              .addSymlink(PathFragment.create("alias"), artifactA)
              .build()
              .getSymlinks()
              .toList()
              .get(0);
    }
    try (var scope = RunfilesOrigin.enter("changed-symlink")) {
      SymlinkEntry second =
          new Runfiles.Builder("same")
              .addSymlink(PathFragment.create("alias"), artifactB)
              .build()
              .getSymlinks()
              .toList()
              .get(0);
      assertThat(second).isNotSameInstanceAs(first);
    }
  }

  @Test(timeout = 30000)
  public void provenanceAndCanonicalIndexDoNotKeepRunfilesAlive() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    WeakReference<Runfiles> value = weakRunfiles();
    GcFinalization.awaitClear(value);
    RunfilesOrigin.setSharingEnabled(false); // Drain the typed weak-entry queues.
  }

  private WeakReference<Runfiles> weakRunfiles() {
    try (var scope = RunfilesOrigin.enter("weak-runfiles")) {
      return new WeakReference<>(new Runfiles.Builder("weak").build());
    }
  }

  @Test(timeout = 30000)
  public void parallelEquivalentRestoresAndConstructionChooseOneIdentity() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    Runfiles validated =
        RunfilesOrigin.restore(
            new Runfiles.Builder("same").build(),
            new RunfilesOrigin.Origin("concurrent-origin", 0));
    try (var executor = Executors.newFixedThreadPool(8)) {
      var ready = new CountDownLatch(8);
      var futures = new ArrayList<Future<Runfiles>>();
      for (int i = 0; i < 8; i++) {
        futures.add(
            executor.submit(
                () -> {
                  ready.countDown();
                  ready.await();
                  try (var scope = RunfilesOrigin.enter("concurrent-origin")) {
                    return new Runfiles.Builder("same").build();
                  }
                }));
      }
      Runfiles first = futures.get(0).get();
      assertThat(first).isSameInstanceAs(validated);
      for (var future : futures) {
        assertThat(future.get()).isSameInstanceAs(first);
      }
    }
  }

  @Test
  public void activeProducerRequiresLocalEvaluationInsteadOfExposingUnfinishedValues()
      throws Exception {
    Runfiles original;
    try (var scope = RunfilesOrigin.enter("active-producer")) {
      original = new Runfiles.Builder("same").build();
      assertThat(
              RunfilesOrigin.restore(
                  new Runfiles.Builder("same").build(),
                  new RunfilesOrigin.Origin("active-producer", 0)))
          .isNull();
      org.junit.Assert.assertThrows(
          com.google.devtools.build.lib.skyframe.serialization.SerializationException.class,
          () -> decode(encode(original)));
    }
    assertThat(
            RunfilesOrigin.restore(
                new Runfiles.Builder("same").build(),
                new RunfilesOrigin.Origin("active-producer", 0)))
        .isSameInstanceAs(original);
  }

  @Test
  public void constructionDoesNotReuseUnvalidatedValuesFromEarlierCommands() throws Exception {
    RunfilesOrigin.setSharingEnabled(true);
    Runfiles previous =
        RunfilesOrigin.restore(
            new Runfiles.Builder("same").build(), new RunfilesOrigin.Origin("previous-command", 0));
    RunfilesOrigin.setSharingEnabled(false);
    RunfilesOrigin.setSharingEnabled(true);
    try (var scope = RunfilesOrigin.enter("previous-command")) {
      assertThat(new Runfiles.Builder("same").build()).isNotSameInstanceAs(previous);
    }
  }

  private Artifact.DerivedArtifact derived(String path) {
    ArtifactRoot root =
        ArtifactRoot.asDerivedRoot(fs.getPath("/exec"), ArtifactRoot.RootType.OUTPUT, "bazel-out");
    return (Artifact.DerivedArtifact) ActionsTestUtil.createArtifact(root, path);
  }

  @Test
  public void laterCommandCannotReuseAnOlderGeneratingActionIndexDuringConstruction() {
    RunfilesOrigin.setSharingEnabled(true);
    var oldArtifact = derived("result");
    oldArtifact.setGeneratingActionKey(
        ActionLookupData.create(ActionsTestUtil.NULL_ARTIFACT_OWNER, 0));
    Runfiles old =
        RunfilesOrigin.restore(
            new Runfiles.Builder("same").addArtifact(oldArtifact).build(),
            new RunfilesOrigin.Origin("action-order-change", 0));
    RunfilesOrigin.setSharingEnabled(false);
    RunfilesOrigin.setSharingEnabled(true);
    var newArtifact = derived("result");
    assertThat(newArtifact).isEqualTo(oldArtifact); // Artifact.equals tolerates the pending index.
    Runfiles current;
    try (var scope = RunfilesOrigin.enter("action-order-change")) {
      current = new Runfiles.Builder("same").addArtifact(newArtifact).build();
    }
    assertThat(current).isNotSameInstanceAs(old);
    newArtifact.setGeneratingActionKey(
        ActionLookupData.create(ActionsTestUtil.NULL_ARTIFACT_OWNER, 1));
    assertThat(current.getArtifacts().toList().get(0)).isSameInstanceAs(newArtifact);
    assertThat(
            ((Artifact.DerivedArtifact) current.getArtifacts().toList().get(0))
                .getGeneratingActionKey()
                .getActionIndex())
        .isEqualTo(1);
  }

  @Test
  public void restoreNeverSelectsAnUnfinishedArtifactFromAnAbandonedEvaluation() {
    var pending = derived("abandoned");
    Runfiles abandoned;
    try (var scope = RunfilesOrigin.enter("abandoned-evaluation")) {
      abandoned = new Runfiles.Builder("same").addArtifact(pending).build();
    }
    var completed = derived("abandoned");
    completed.setGeneratingActionKey(
        ActionLookupData.create(ActionsTestUtil.NULL_ARTIFACT_OWNER, 1));
    assertThat(pending).isEqualTo(completed);
    Runfiles restored =
        RunfilesOrigin.restore(
            new Runfiles.Builder("same").addArtifact(completed).build(),
            new RunfilesOrigin.Origin("abandoned-evaluation", 0));
    assertThat(restored).isNotSameInstanceAs(abandoned);
    assertThat(restored.getArtifacts().toList().get(0)).isSameInstanceAs(completed);
  }

  private ByteString encode(Object value) throws Exception {
    return encode(writer, value);
  }

  private ByteString encode(FingerprintValueService service, Object value) throws Exception {
    var serialized = codecs.serializeMemoizedAndBlocking(compression, service, value);
    if (serialized.getFutureToBlockWritesOn() != null) {
      serialized.getFutureToBlockWritesOn().get();
    }
    return serialized.getObject();
  }

  private Object decode(ByteString value) throws Exception {
    return codecs.deserializeMemoizedAndBlocking(compression, reader, value);
  }
}
