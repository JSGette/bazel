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

import com.github.benmanes.caffeine.cache.Caffeine;
import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableMap;
import com.google.common.collect.ImmutableSet;
import com.google.devtools.build.lib.events.ExtendedEventHandler;
import com.google.devtools.build.skyframe.QueryableGraph.Reason;
import com.google.devtools.build.skyframe.SkyFunctionEnvironment.UndonePreviouslyRequestedDeps;
import com.google.devtools.build.skyframe.proto.GraphInconsistency.Inconsistency;
import com.google.testing.junit.testparameterinjector.TestParameter;
import com.google.testing.junit.testparameterinjector.TestParameterInjector;
import java.util.ArrayList;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;

/** Checks completion prefetch at the missing/undone dependency boundary. */
@RunWith(TestParameterInjector.class)
public final class SkyFunctionEnvironmentPrefetchTest {
  private static final Version VERSION = IntVersion.of(0);
  private static final SkyValue VALUE = new SkyValue() {};
  private static final SkyKey A = new Key(31, false);
  private static final SkyKey B = new Key(2, false);
  private static final SkyKey C = new Key(19, false);

  @TestParameter private boolean partialReevaluation;

  private record Key(int id, boolean partial) implements SkyKey {
    @Override
    public SkyFunctionName functionName() {
      return SkyFunctionName.FOR_TESTING;
    }

    @Override
    public boolean skipsBatchPrefetch() {
      return true;
    }

    @Override
    public boolean supportsPartialReevaluation() {
      return partial;
    }
  }

  private record ObservedInconsistency(ImmutableList<SkyKey> keys, Inconsistency kind) {}

  private final InMemoryGraphImpl graph = new InMemoryGraphImpl(/* usePooledInterning= */ false);
  private final List<ObservedInconsistency> inconsistencies = new ArrayList<>();

  @Test
  public void completionFetchesOnlyRemainingDepsAcrossGroups() throws Exception {
    addDone(A);
    addDone(B);
    addDone(C);
    SkyFunctionEnvironment env = newEnvironment();

    assertThat(env.getValue(A)).isSameInstanceAs(VALUE);
    // A must be served by the environment's existing map, not fetched again from the graph.
    graph.remove(A);
    env.ensurePreviouslyRequestedDepsFetched();
    graph.remove(B);
    graph.remove(C);
    assertThat(env.getValue(B)).isSameInstanceAs(VALUE);
    assertThat(env.getValue(C)).isSameInstanceAs(VALUE);
    // Nothing remains to fetch even though the graph now contains no dependencies.
    env.ensurePreviouslyRequestedDepsFetched();
    assertThat(inconsistencies).isEmpty();
    assertThat(env.getNewlyRequestedDeps()).isEmpty();
    assertThat(env.valuesMissing()).isFalse();
  }

  @Test
  public void missingDepsAreReportedTogetherAndCanBeRetried() throws Exception {
    addDone(B);
    SkyFunctionEnvironment env = newEnvironment();

    UndonePreviouslyRequestedDeps failure =
        assertThrows(
            UndonePreviouslyRequestedDeps.class, env::ensurePreviouslyRequestedDepsFetched);
    assertThat(failure.getDepKeys()).containsExactly(A, C);
    assertThat(inconsistencies).hasSize(1);
    assertThat(inconsistencies.getFirst().keys()).containsExactly(A, C);
    assertThat(inconsistencies.getFirst().kind())
        .isEqualTo(Inconsistency.ALREADY_DECLARED_CHILD_MISSING);

    // The completed dependency was cached despite the other entries being absent.
    graph.remove(B);
    addDone(A);
    addDone(C);
    env.ensurePreviouslyRequestedDepsFetched();
    assertThat(env.getValue(A)).isSameInstanceAs(VALUE);
    assertThat(env.getValue(B)).isSameInstanceAs(VALUE);
    assertThat(env.getValue(C)).isSameInstanceAs(VALUE);
    assertThat(inconsistencies).hasSize(1);
  }

  @Test
  public void undoneDepTakesPrecedenceOverAccumulatedMissingDeps() throws Exception {
    NodeEntry undone = addBuilding(B);
    SkyFunctionEnvironment env = newEnvironment();

    UndonePreviouslyRequestedDeps failure =
        assertThrows(
            UndonePreviouslyRequestedDeps.class, env::ensurePreviouslyRequestedDepsFetched);
    assertThat(failure.getDepKeys()).containsExactly(B);
    assertThat(inconsistencies).hasSize(1);
    assertThat(inconsistencies.getFirst().keys()).containsExactly(B);
    assertThat(inconsistencies.getFirst().kind())
        .isEqualTo(Inconsistency.BUILDING_PARENT_FOUND_UNDONE_CHILD);

    undone.setValue(VALUE, VERSION, /* maxTransitiveSourceVersion= */ null);
    addDone(A);
    addDone(C);
    env.ensurePreviouslyRequestedDepsFetched();
    assertThat(env.getValue(A)).isSameInstanceAs(VALUE);
    assertThat(env.getValue(B)).isSameInstanceAs(VALUE);
    assertThat(env.getValue(C)).isSameInstanceAs(VALUE);
    assertThat(inconsistencies).hasSize(1);
  }

  private NodeEntry addBuilding(SkyKey key) throws InterruptedException {
    NodeEntry entry = graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(key)).get(key);
    assertThat(entry.addReverseDepAndCheckIfDone(null))
        .isEqualTo(NodeEntry.DependencyState.NEEDS_SCHEDULING);
    entry.markRebuilding();
    return entry;
  }

  private void addDone(SkyKey key) throws InterruptedException {
    addBuilding(key).setValue(VALUE, VERSION, /* maxTransitiveSourceVersion= */ null);
  }

  private SkyFunctionEnvironment newEnvironment() throws Exception {
    GroupedDeps deps = new GroupedDeps.WithHashSet();
    deps.appendGroup(ImmutableList.of(A, B));
    deps.appendSingleton(C);
    ParallelEvaluatorContext context =
        new ParallelEvaluatorContext(
            graph,
            VERSION,
            VERSION,
            ImmutableMap.of(),
            ExtendedEventHandler.NOOP,
            new EmittedEventState(),
            new InflightOnlyTrackingProgressReceiver(EvaluationProgressReceiver.NULL),
            EventFilter.NO_STORAGE,
            ErrorInfoManager.UseChildErrorInfoIfNecessary.INSTANCE,
            (key, otherKeys, inconsistency) ->
                inconsistencies.add(
                    new ObservedInconsistency(ImmutableList.copyOf(otherKeys), inconsistency)),
            // These completion-prefetch tests do not schedule work.
            /* executor= */ null,
            () -> {
              throw new AssertionError("Completion prefetch must not schedule work");
            },
            Caffeine.newBuilder().build(),
            unused -> true);
    return SkyFunctionEnvironment.create(
        new Key(-1, partialReevaluation), deps, ImmutableSet.of(), VERSION, context);
  }
}
