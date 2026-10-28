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
package com.google.devtools.build.lib.actions;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableSet;
import com.google.devtools.build.lib.actions.Artifact.DerivedArtifact;
import com.google.devtools.build.lib.actions.ArtifactRoot.RootType;
import com.google.devtools.build.lib.actions.util.InjectedActionLookupKey;
import com.google.devtools.build.lib.actions.util.TestAction;
import com.google.devtools.build.lib.collect.nestedset.NestedSet;
import com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder;
import com.google.devtools.build.lib.collect.nestedset.Order;
import com.google.devtools.build.lib.util.Fingerprint;
import com.google.devtools.build.lib.vfs.DigestHashFunction;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.build.lib.vfs.inmemoryfs.InMemoryFileSystem;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests ownership assignment and shared-action checks within a single action lookup value. */
@RunWith(JUnit4.class)
public final class ActionsTest {
  private final ActionKeyContext actionKeyContext = new ActionKeyContext();
  private final InjectedActionLookupKey owner = new InjectedActionLookupKey("owner");
  private final ArtifactRoot root =
      ArtifactRoot.asDerivedRoot(
          new InMemoryFileSystem(DigestHashFunction.SHA256).getPath("/exec"),
          RootType.OUTPUT,
          "out");

  private DerivedArtifact output(String name) {
    return DerivedArtifact.create(root, PathFragment.create("out/" + name), owner);
  }

  private static CountingAction action(boolean shareable, String key, Artifact... outputs) {
    return new CountingAction(
        NestedSetBuilder.emptySet(Order.STABLE_ORDER),
        ImmutableSet.copyOf(outputs),
        shareable,
        key);
  }

  private void assign(CountingAction... actions) throws Exception {
    Actions.assignOwnersAndThrowIfConflictToleratingSharedActions(
        actionKeyContext, ImmutableList.copyOf(actions), owner);
  }

  @Test
  public void sameArtifactIsComparedOnlyOncePerOutput() throws Exception {
    var firstOutput = output("first");
    var secondOutput = output("second");
    var first = action(true, "same", firstOutput, secondOutput);
    var second = action(true, "same", firstOutput, secondOutput);

    assign(first, second);

    // Each output still checks the action pair once. A second check of the exact same artifact
    // adds key computation and full input/output comparison without observing a state change.
    assertThat(first.keyComputations).isEqualTo(2);
    assertThat(second.keyComputations).isEqualTo(2);
    assertThat(firstOutput.getGeneratingActionKey()).isEqualTo(ActionLookupData.create(owner, 0));
    assertThat(secondOutput.getGeneratingActionKey()).isEqualTo(ActionLookupData.create(owner, 0));
  }

  @Test
  public void distinctNonshareableOutputsAreRecheckedAfterOwnershipChanges() {
    var originalFirst = output("first");
    var originalSecond = output("second");
    var aliasFirst = output("first");
    var aliasSecond = output("second");
    var original = action(false, "same", originalFirst, originalSecond);
    var alias = action(false, "same", aliasFirst, aliasSecond);

    assertThrows(ActionConflictException.class, () -> assign(original, alias));

    // Before assignment these distinct artifacts compare equal by lookup owner. Once both have
    // generating keys, the differing action indexes matter. Reusing the first positive comparison
    // across outputs would incorrectly admit this alias.
    assertThat(originalFirst.getGeneratingActionKey()).isEqualTo(ActionLookupData.create(owner, 0));
    assertThat(aliasFirst.getGeneratingActionKey()).isEqualTo(ActionLookupData.create(owner, 1));
    assertThat(aliasFirst).isNotEqualTo(originalFirst);
    assertThat(aliasSecond.hasGeneratingActionKey()).isFalse();
  }

  @Test
  public void distinctAlreadyOwnedOutputStillChecksItsOwnGeneratingAction() {
    var originalOutput = output("same");
    var separatelyOwnedOutput = output("same");
    separatelyOwnedOutput.setGeneratingActionKey(ActionLookupData.create(owner, 2));
    var original = action(true, "matching", originalOutput);
    var incoming = action(true, "matching", separatelyOwnedOutput);
    var existingOwner = action(true, "different", separatelyOwnedOutput);

    assertThrows(ActionConflictException.class, () -> assign(original, incoming, existingOwner));
    // The prior path's producer matched, but the incoming artifact has a different producer.
    assertThat(existingOwner.keyComputations).isGreaterThan(0);
  }

  @Test
  public void distinctShareableArtifactsKeepTheirOwnGeneratingKeys() throws Exception {
    var originalFirst = output("first");
    var originalSecond = output("second");
    var restoredFirst = output("first");
    var restoredSecond = output("second");
    assign(
        action(true, "same", originalFirst, originalSecond),
        action(true, "same", restoredFirst, restoredSecond));
    assertThat(originalFirst.getGeneratingActionKey()).isEqualTo(ActionLookupData.create(owner, 0));
    assertThat(restoredFirst.getGeneratingActionKey()).isEqualTo(ActionLookupData.create(owner, 1));
    assertThat(restoredSecond.getGeneratingActionKey())
        .isEqualTo(ActionLookupData.create(owner, 1));
  }

  @Test
  public void sameArtifactWithDifferentMandatoryInputsStillConflicts() {
    var sharedOutput = output("shared");
    var first =
        new CountingAction(
            NestedSetBuilder.create(Order.STABLE_ORDER, output("input-a")),
            ImmutableSet.of(sharedOutput),
            true,
            "same");
    var second =
        new CountingAction(
            NestedSetBuilder.create(Order.STABLE_ORDER, output("input-b")),
            ImmutableSet.of(sharedOutput),
            true,
            "same");
    assertThrows(ActionConflictException.class, () -> assign(first, second));
  }

  @Test
  public void samePrimaryArtifactWithDifferentOutputsStillConflicts() {
    var sharedOutput = output("shared");
    assertThrows(
        ActionConflictException.class,
        () ->
            assign(
                action(true, "same", sharedOutput, output("other-a")),
                action(true, "same", sharedOutput, output("other-b"))));
  }

  @Test
  public void outputAlreadyGeneratedByAnotherRuleStillFails() {
    var artifact = output("shared");
    artifact.setGeneratingActionKey(ActionLookupData.create(owner, 0));
    var otherOwner = new InjectedActionLookupKey("other");
    assertThrows(
        Actions.ArtifactGeneratedByOtherRuleException.class,
        () ->
            Actions.assignOwnersAndThrowIfConflictToleratingSharedActions(
                actionKeyContext, ImmutableList.of(action(true, "same", artifact)), otherOwner));
  }

  @Test
  public void sharedArtifactStillConflictsWhenSharingIsDisallowed() {
    var artifact = output("shared");
    assertThrows(
        ActionConflictException.class,
        () ->
            Actions.assignOwnersAndThrowIfConflict(
                actionKeyContext,
                ImmutableList.of(action(true, "same", artifact), action(true, "same", artifact)),
                owner));
  }

  private static final class CountingAction extends TestAction {
    private final boolean shareable;
    private final String key;
    private int keyComputations;

    CountingAction(
        NestedSet<Artifact> inputs, ImmutableSet<Artifact> outputs, boolean shareable, String key) {
      super(NO_EFFECT, inputs, outputs);
      this.shareable = shareable;
      this.key = key;
    }

    @Override
    public boolean isShareable() {
      return shareable;
    }

    @Override
    protected void computeKey(
        ActionKeyContext actionKeyContext,
        InputMetadataProvider inputMetadataProvider,
        Fingerprint fingerprint) {
      keyComputations++;
      // Deliberately independent of inputs/outputs to exercise their separate equality checks.
      fingerprint.addString(key);
    }
  }
}
