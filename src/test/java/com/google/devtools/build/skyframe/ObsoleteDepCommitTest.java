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

import com.github.benmanes.caffeine.cache.Caffeine;
import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.google.common.collect.ImmutableSet;
import com.google.devtools.build.lib.events.ExtendedEventHandler;
import com.google.devtools.build.skyframe.NodeEntry.DependencyState;
import com.google.devtools.build.skyframe.NodeEntry.DirtyType;
import com.google.devtools.build.skyframe.QueryableGraph.Reason;
import com.google.testing.junit.testparameterinjector.TestParameter;
import com.google.testing.junit.testparameterinjector.TestParameterInjector;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import org.junit.Test;
import org.junit.runner.RunWith;

@RunWith(TestParameterInjector.class)
public final class ObsoleteDepCommitTest {
  @TestParameter private boolean withHashSet;

  @Test
  public void commitPreservesBothEdgeDirections(
      @TestParameter({
            "SINGLE_FIRST",
            "SINGLE_LAST",
            "SINGLE_ABSENT",
            "SINGLE_EMPTY",
            "ZERO_OLD",
            "TWO_OLD",
            "RESET_OLD_ONLY",
            "RESET_NEW_ONLY",
            "RESET_OVERLAP",
            "RESET_OVERLAP_ABSENT",
            "RESET_MIXED"
          })
          String shape)
      throws Exception {
    List<SkyKey> current =
        currentKeys(shape.equals("SINGLE_EMPTY") || shape.equals("RESET_OVERLAP_ABSENT") ? 0 : 16);
    SkyKey first = new Key(0, false);
    SkyKey absent = new Key(10000, false);
    ImmutableSet<SkyKey> old =
        switch (shape) {
          case "ZERO_OLD", "RESET_NEW_ONLY" -> ImmutableSet.of();
          case "SINGLE_FIRST",
              "RESET_OLD_ONLY",
              "RESET_OVERLAP",
              "RESET_OVERLAP_ABSENT",
              "RESET_MIXED" ->
              ImmutableSet.of(first);
          case "SINGLE_LAST" -> ImmutableSet.of(current.get(current.size() - 1));
          case "TWO_OLD" -> ImmutableSet.of(first, absent);
          default -> ImmutableSet.of(absent);
        };
    ImmutableSet<SkyKey> reset =
        switch (shape) {
          case "RESET_OLD_ONLY" -> ImmutableSet.of(first);
          case "RESET_NEW_ONLY" -> ImmutableSet.of(absent);
          case "RESET_OVERLAP", "RESET_OVERLAP_ABSENT" -> ImmutableSet.of(first, absent);
          case "RESET_MIXED" -> ImmutableSet.of(new Key(1, false), absent);
          default -> ImmutableSet.of();
        };
    Fixture fixture = new Fixture(withHashSet, current, old, reset);
    // Repetition proves cleanup does not leave pending reverse operations or stale parent nodes.
    for (int i = 0; i < 2; i++) {
      fixture.prepare();
      if (!fixture.commit().isEmpty()) {
        throw new AssertionError("Unexpected parent signal");
      }
      fixture.verifyAndClear();
    }
  }

  private static final Version OLD_VERSION = IntVersion.of(0);
  private static final Version NEW_VERSION = IntVersion.of(1);
  private static final SkyValue OLD_VALUE = new SkyValue() {};
  private static final SkyValue NEW_VALUE = new SkyValue() {};

  private record Key(int id, boolean skipPrefetch) implements SkyKey {
    @Override
    public SkyFunctionName functionName() {
      return SkyFunctionName.FOR_TESTING;
    }

    @Override
    public boolean skipsBatchPrefetch() {
      return skipPrefetch;
    }
  }

  private static final class Fixture {
    final SkyKey root;
    final ImmutableList<SkyKey> current;
    final ImmutableSet<SkyKey> old;
    final ImmutableSet<SkyKey> reset;
    final ImmutableSet<SkyKey> allLeaves;
    final InMemoryGraphImpl graph = new InMemoryGraphImpl(false);
    final ParallelEvaluatorContext context;
    InMemoryNodeEntry entry;
    SkyFunctionEnvironment environment;

    Fixture(boolean withHashSet, List<SkyKey> current, Set<SkyKey> old, Set<SkyKey> reset)
        throws InterruptedException {
      root = new Key(-1, withHashSet);
      this.current = ImmutableList.copyOf(current);
      this.old = ImmutableSet.copyOf(old);
      this.reset = ImmutableSet.copyOf(reset);
      allLeaves = ImmutableSet.<SkyKey>builder().addAll(current).addAll(old).addAll(reset).build();
      NodeBatch leaves = graph.createIfAbsentBatch(root, Reason.OTHER, allLeaves);
      for (SkyKey key : allLeaves) {
        NodeEntry leaf = leaves.get(key);
        if (leaf.addReverseDepAndCheckIfDone(null) != DependencyState.NEEDS_SCHEDULING) {
          throw new AssertionError("Expected a fresh leaf");
        }
        leaf.markRebuilding();
        leaf.setValue(OLD_VALUE, OLD_VERSION, null);
      }
      context =
          new ParallelEvaluatorContext(
              graph,
              NEW_VERSION,
              OLD_VERSION,
              ImmutableMap.of(),
              ExtendedEventHandler.NOOP,
              new EmittedEventState(),
              new InflightOnlyTrackingProgressReceiver(EvaluationProgressReceiver.NULL),
              EventFilter.NO_STORAGE,
              ErrorInfoManager.UseChildErrorInfoIfNecessary.INSTANCE,
              GraphInconsistencyReceiver.THROWING,
              null,
              () -> {
                throw new AssertionError("Commit fixture must not schedule");
              },
              Caffeine.newBuilder().build(),
              unused -> true);
    }

    void prepare() throws Exception {
      if (graph.getIfPresent(root) != null) {
        throw new AssertionError("Previous invocation retained the parent");
      }
      graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(root));
      entry = graph.getIfPresent(root);
      entry.addReverseDepAndCheckIfDone(null);
      entry.markRebuilding();
      if (!old.isEmpty()) {
        addGroup(old.asList(), ImmutableSet.of());
        entry.setValue(OLD_VALUE, OLD_VERSION, null);
        entry.markDirty(DirtyType.CHANGE);
        entry.addReverseDepAndCheckIfDone(null);
        entry.markRebuilding();
      }
      if (!reset.isEmpty()) {
        addGroup(reset.asList(), old);
        entry.resetEvaluationFromScratch();
      }
      ImmutableSet<SkyKey> remaining = entry.getAllRemainingDirtyDirectDeps();
      if (!remaining.equals(old) || !entry.getResetDirectDeps().equals(reset)) {
        throw new AssertionError("Incorrect old/reset lifecycle");
      }
      addGroup(current, ImmutableSet.<SkyKey>builder().addAll(old).addAll(reset).build());
      if ((entry.getTemporaryDirectDeps() instanceof GroupedDeps.WithHashSet)
          != root.skipsBatchPrefetch()) {
        throw new AssertionError("Incorrect temporary dependency representation");
      }
      environment =
          SkyFunctionEnvironment.create(
              root, entry.getTemporaryDirectDeps(), remaining, OLD_VERSION, context);
      environment.ensurePreviouslyRequestedDepsFetched();
      environment.doneBuilding();
      environment.setValue(NEW_VALUE);
    }

    private void addGroup(List<SkyKey> keys, Set<SkyKey> registered) throws InterruptedException {
      entry.addTemporaryDirectDepGroup(keys);
      for (SkyKey key : keys) {
        InMemoryNodeEntry leaf = graph.getIfPresent(key);
        DependencyState state =
            registered.contains(key)
                ? leaf.checkIfDoneForDirtyReverseDep(root)
                : leaf.addReverseDepAndCheckIfDone(root);
        if (state != DependencyState.DONE) {
          throw new AssertionError("Expected a done leaf");
        }
        entry.signalDep(OLD_VERSION, key);
      }
    }

    Set<SkyKey> commit() throws InterruptedException {
      return environment.commitAndGetParents(entry, true);
    }

    void verifyAndClear() {
      try {
        if (!entry.isDone()
            || entry.getValue() != NEW_VALUE
            || !ImmutableList.copyOf(entry.getDirectDeps()).equals(current)) {
          throw new AssertionError("Incorrect committed value or forward dependency order");
        }
        Set<SkyKey> expected = ImmutableSet.copyOf(current);
        for (SkyKey key : allLeaves) {
          Set<SkyKey> actual =
              ImmutableSet.copyOf(graph.getIfPresent(key).getReverseDepsForDoneEntry());
          if (!actual.equals(expected.contains(key) ? ImmutableSet.of(root) : ImmutableSet.of())) {
            throw new AssertionError("Incorrect reverse edge for " + key);
          }
        }
      } finally {
        for (SkyKey key : allLeaves) {
          InMemoryNodeEntry leaf = graph.getIfPresent(key);
          if (leaf.getReverseDepsForDoneEntry().contains(root)) {
            leaf.removeReverseDep(root);
          }
          if (!leaf.getReverseDepsForDoneEntry().isEmpty()) {
            throw new AssertionError("Reverse edges survived invocation cleanup");
          }
        }
        graph.remove(root);
        entry = null;
        environment = null;
      }
      if (graph.valuesSize() != allLeaves.size()) {
        throw new AssertionError("Graph grew across invocations");
      }
    }
  }

  private static List<SkyKey> currentKeys(int count) {
    List<SkyKey> result = new ArrayList<>(count);
    for (int i = 0; i < count; i++) {
      result.add(new Key(i, false));
    }
    return result;
  }

  private static SkyKey oldKey(List<SkyKey> current, String position) {
    if (current.isEmpty() || position.equals("ABSENT")) {
      return new Key(10000, false);
    }
    return current.get(position.equals("FIRST") ? 0 : current.size() - 1);
  }
}
