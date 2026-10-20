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
package com.google.devtools.build.skyframe;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.common.collect.ImmutableList;
import com.google.devtools.build.skyframe.NodeEntry.DependencyState;
import com.google.devtools.build.skyframe.NodeEntry.DirtyType;
import com.google.devtools.build.skyframe.NodeEntry.LifecycleState;
import com.google.testing.junit.testparameterinjector.TestParameter;
import com.google.testing.junit.testparameterinjector.TestParameterInjector;
import org.junit.Test;
import org.junit.runner.RunWith;

/** Completion contracts for uninitialized, initialized-empty, and nonempty direct deps. */
@RunWith(TestParameterInjector.class)
public final class EmptyDepsCompletionTest {
  private static final IntVersion FIRST_VERSION = IntVersion.of(10);
  private static final IntVersion NEXT_VERSION = IntVersion.of(11);
  private static final SkyKey PARENT = new Key("parent", false);

  @TestParameter boolean skipsBatchPrefetch;

  private record Key(String name, boolean skipsBatchPrefetch) implements SkyKey {
    @Override
    public SkyFunctionName functionName() {
      return SkyFunctionName.FOR_TESTING;
    }
  }

  private record Value(int number) implements SkyValue {}

  @Test
  public void freshUninitializedCompletion(@TestParameter({"0", "1", "4"}) int parentCount)
      throws InterruptedException {
    IncrementalInMemoryNodeEntry entry = newEntry();
    assertThat(entry.addReverseDepAndCheckIfDone(null)).isEqualTo(DependencyState.NEEDS_SCHEDULING);
    ImmutableList.Builder<SkyKey> parents = ImmutableList.builder();
    for (int i = 0; i < parentCount; i++) {
      SkyKey parent = new Key("parent" + i, false);
      parents.add(parent);
      entry.addReverseDepAndCheckIfDone(parent);
    }
    ImmutableList<SkyKey> expectedParents = parents.build();
    entry.markRebuilding();
    assertThat(entry.directDeps).isNull();
    Value value = new Value(1);

    assertThat(entry.setValue(value, FIRST_VERSION, /* maxTransitiveSourceVersion= */ null))
        .containsExactlyElementsIn(expectedParents);

    assertEmptyDone(entry, value, FIRST_VERSION, FIRST_VERSION);
    assertThat(entry.getReverseDepsForDoneEntry()).containsExactlyElementsIn(expectedParents);
  }

  @Test
  public void initializedEmptyCompletionPreservesEmptyDeps() throws InterruptedException {
    IncrementalInMemoryNodeEntry entry = newEntry();
    entry.addReverseDepAndCheckIfDone(PARENT);
    entry.markRebuilding();
    GroupedDeps temporary = entry.getTemporaryDirectDeps();
    assertThat(temporary instanceof GroupedDeps.WithHashSet).isEqualTo(skipsBatchPrefetch);
    assertThat(entry.directDeps).isSameInstanceAs(temporary);
    Value value = new Value(1);

    assertThat(entry.setValue(value, FIRST_VERSION, /* maxTransitiveSourceVersion= */ null))
        .containsExactly(PARENT);

    assertEmptyDone(entry, value, FIRST_VERSION, FIRST_VERSION);
    assertThat(temporary).isEmpty();
    assertThat(entry.getReverseDepsForDoneEntry()).containsExactly(PARENT);
  }

  @Test
  public void nonemptyCompletionPreservesDependencyGroups() throws InterruptedException {
    IncrementalInMemoryNodeEntry entry = newEntry();
    entry.addReverseDepAndCheckIfDone(PARENT);
    entry.markRebuilding();
    SkyKey first = new Key("first", false);
    SkyKey second = new Key("second", false);
    SkyKey third = new Key("third", false);
    ImmutableList<SkyKey> firstGroup = ImmutableList.of(first);
    ImmutableList<SkyKey> secondGroup = ImmutableList.of(second, third);
    entry.addTemporaryDirectDepGroup(firstGroup);
    entry.addTemporaryDirectDepGroup(secondGroup);
    for (SkyKey dep : ImmutableList.of(first, second, third)) {
      entry.signalDep(FIRST_VERSION, dep);
    }
    GroupedDeps temporary = entry.getTemporaryDirectDeps();
    assertThat(temporary instanceof GroupedDeps.WithHashSet).isEqualTo(skipsBatchPrefetch);
    Value value = new Value(1);

    assertThat(entry.setValue(value, FIRST_VERSION, /* maxTransitiveSourceVersion= */ null))
        .containsExactly(PARENT);

    assertThat(entry.isDone()).isTrue();
    assertThat(entry.getValue()).isSameInstanceAs(value);
    assertThat(entry.hasAtLeastOneDep()).isTrue();
    assertThat(entry.getDirectDeps()).containsExactly(first, second, third).inOrder();
    assertThat(GroupedDeps.decompress(entry.getCompressedDirectDepsForDoneEntry()))
        .containsExactly(firstGroup, secondGroup)
        .inOrder();
    assertThat(temporary).containsExactly(firstGroup, secondGroup).inOrder();
    assertThat(entry.getReverseDepsForDoneEntry()).containsExactly(PARENT);
  }

  @Test
  public void changedRebuildToEmptyDiscardsPriorDeps(@TestParameter boolean valueChanges)
      throws InterruptedException {
    IncrementalInMemoryNodeEntry entry = newEntry();
    entry.addReverseDepAndCheckIfDone(PARENT);
    entry.markRebuilding();
    SkyKey oldDep = new Key("old", false);
    entry.addSingletonTemporaryDirectDep(oldDep);
    entry.signalDep(FIRST_VERSION, oldDep);
    Value oldValue = new Value(1);
    entry.setValue(oldValue, FIRST_VERSION, /* maxTransitiveSourceVersion= */ null);
    entry.markDirty(DirtyType.CHANGE);
    assertThat(entry.checkIfDoneForDirtyReverseDep(PARENT))
        .isEqualTo(DependencyState.NEEDS_SCHEDULING);
    entry.markRebuilding();
    assertThat(entry.directDeps).isNull();
    Value rebuiltValue = new Value(valueChanges ? 2 : 1);

    assertThat(entry.setValue(rebuiltValue, NEXT_VERSION, /* maxTransitiveSourceVersion= */ null))
        .containsExactly(PARENT);

    assertEmptyDone(
        entry,
        valueChanges ? rebuiltValue : oldValue,
        valueChanges ? NEXT_VERSION : FIRST_VERSION,
        NEXT_VERSION);
    assertThat(entry.getReverseDepsForDoneEntry()).containsExactly(PARENT);
  }

  @Test
  public void resetAfterEarlierDepsCompletesEmpty(@TestParameter boolean incremental)
      throws InterruptedException {
    IncrementalInMemoryNodeEntry entry = newEntry();
    entry.addReverseDepAndCheckIfDone(PARENT);
    entry.markRebuilding();
    Value value = new Value(1);
    if (incremental) {
      SkyKey oldDep = new Key("old-build", false);
      entry.addSingletonTemporaryDirectDep(oldDep);
      entry.signalDep(FIRST_VERSION, oldDep);
      entry.setValue(value, FIRST_VERSION, /* maxTransitiveSourceVersion= */ null);
      entry.markDirty(DirtyType.CHANGE);
      entry.checkIfDoneForDirtyReverseDep(PARENT);
      entry.markRebuilding();
    }
    SkyKey attemptDep = new Key("before-reset", false);
    entry.addSingletonTemporaryDirectDep(attemptDep);
    entry.signalDep(incremental ? NEXT_VERSION : FIRST_VERSION, attemptDep);

    entry.resetEvaluationFromScratch();

    assertThat(entry.getLifecycleState()).isEqualTo(LifecycleState.REBUILDING);
    assertThat(entry.getResetDirectDeps()).containsExactly(attemptDep);
    assertThat(entry.directDeps).isNull();
    IntVersion completionVersion = incremental ? NEXT_VERSION : FIRST_VERSION;
    assertThat(entry.setValue(value, completionVersion, /* maxTransitiveSourceVersion= */ null))
        .containsExactly(PARENT);
    assertEmptyDone(entry, value, FIRST_VERSION, completionVersion);
    assertThat(entry.getReverseDepsForDoneEntry()).containsExactly(PARENT);
  }

  private IncrementalInMemoryNodeEntry newEntry() {
    return new IncrementalInMemoryNodeEntry(new Key("node", skipsBatchPrefetch));
  }

  private static void assertEmptyDone(
      IncrementalInMemoryNodeEntry entry,
      SkyValue value,
      Version lastChanged,
      Version lastEvaluated) {
    assertThat(entry.isDone()).isTrue();
    assertThat(entry.getLifecycleState()).isEqualTo(LifecycleState.DONE);
    assertThat(entry.getCompressedDirectDepsForDoneEntry())
        .isSameInstanceAs(GroupedDeps.EMPTY_COMPRESSED);
    assertThat(entry.getDirectDeps()).isEmpty();
    assertThat(entry.hasAtLeastOneDep()).isFalse();
    assertThat(entry.getValue()).isSameInstanceAs(value);
    assertThat(entry.getVersion()).isSameInstanceAs(lastChanged);
    assertThat(entry.lastEvaluatedVersion()).isSameInstanceAs(lastEvaluated);
    assertThrows(IllegalStateException.class, entry::getTemporaryDirectDeps);
  }
}
