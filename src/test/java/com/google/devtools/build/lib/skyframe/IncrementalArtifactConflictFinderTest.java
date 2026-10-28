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
package com.google.devtools.build.lib.skyframe;

import static com.google.common.truth.Truth.assertThat;
import static java.util.concurrent.TimeUnit.MILLISECONDS;
import static java.util.concurrent.TimeUnit.SECONDS;
import static org.junit.Assert.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import com.google.common.collect.ImmutableList;
import com.google.devtools.build.lib.actions.ActionAnalysisMetadata;
import com.google.devtools.build.lib.actions.ActionConflictException;
import com.google.devtools.build.lib.actions.ActionLookupKey;
import com.google.devtools.build.lib.actions.ActionLookupValue;
import com.google.devtools.build.lib.actions.Artifact;
import com.google.devtools.build.lib.actions.MutableActionGraph;
import com.google.devtools.build.skyframe.SkyKey;
import com.google.devtools.build.skyframe.WalkableGraph;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.After;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests traversal pruning independently of the action equality and output-prefix algorithms. */
@RunWith(JUnit4.class)
public final class IncrementalArtifactConflictFinderTest {
  private final WalkableGraph walkableGraph = mock(WalkableGraph.class);
  private final CountingActionGraph actionGraph = new CountingActionGraph();
  private final IncrementalArtifactConflictFinder finder =
      new IncrementalArtifactConflictFinder(actionGraph, walkableGraph);
  private final ExecutorService callers = Executors.newFixedThreadPool(2);

  @After
  public void shutdown() throws Exception {
    callers.shutdownNow();
    finder.shutdown();
    assertThat(callers.awaitTermination(10, SECONDS)).isTrue();
  }

  @Test
  public void repeatedRootRegistersActionsOnce() throws Exception {
    Node root = node("root");

    assertThat(finder.findArtifactConflicts(root.key()).conflicts()).isEmpty();
    var repeated = finder.findArtifactConflicts(root.key());

    assertThat(repeated.conflicts()).isEmpty();
    assertThat(actionGraph.registrations(root.action())).isEqualTo(1);
    assertThat(repeated.outputArtifactCount()).isEqualTo(1);
  }

  @Test
  public void rootAlreadyVisitedAsDependencyRegistersActionsOnce() throws Exception {
    Node dependency = node("dependency");
    Node root = node("root", dependency);

    assertThat(finder.findArtifactConflicts(root.key()).conflicts()).isEmpty();
    assertThat(finder.findArtifactConflicts(dependency.key()).conflicts()).isEmpty();

    assertThat(actionGraph.registrations(root.action())).isEqualTo(1);
    assertThat(actionGraph.registrations(dependency.action())).isEqualTo(1);
  }

  @Test
  public void dependencyAlreadyVisitedAsRootRegistersActionsOnce() throws Exception {
    Node dependency = node("dependency");
    Node root = node("root", dependency);

    assertThat(finder.findArtifactConflicts(dependency.key()).conflicts()).isEmpty();
    assertThat(finder.findArtifactConflicts(root.key()).conflicts()).isEmpty();

    assertThat(actionGraph.registrations(root.action())).isEqualTo(1);
    assertThat(actionGraph.registrations(dependency.action())).isEqualTo(1);
  }

  @Test
  public void overlappingRootsRegisterSharedDependencyOnce() throws Exception {
    Node dependency = node("dependency");
    Node first = node("first", dependency);
    Node second = node("second", dependency);

    assertThat(finder.findArtifactConflicts(first.key()).conflicts()).isEmpty();
    assertThat(finder.findArtifactConflicts(second.key()).conflicts()).isEmpty();

    assertThat(actionGraph.registrations(first.action())).isEqualTo(1);
    assertThat(actionGraph.registrations(second.action())).isEqualTo(1);
    assertThat(actionGraph.registrations(dependency.action())).isEqualTo(1);
  }

  @Test(timeout = 30000)
  public void prunedRootWaitsForOriginalActionCheck() throws Exception {
    Node root = node("root");
    var firstRegistration = new CountDownLatch(1);
    var releaseRegistration = new CountDownLatch(1);
    actionGraph.onRegistration =
        action -> {
          if (actionGraph.registrations(action) == 1) {
            firstRegistration.countDown();
            await(releaseRegistration);
          }
        };

    var first = callers.submit(() -> finder.findArtifactConflicts(root.key()));
    try {
      await(firstRegistration);
      var secondStarted = new CountDownLatch(1);
      var second =
          callers.submit(
              () -> {
                secondStarted.countDown();
                return finder.findArtifactConflicts(root.key());
              });
      await(secondStarted);
      // Collection may be pruned, but the predecessor still owns an unfinished action check.
      assertThrows(TimeoutException.class, () -> second.get(100, MILLISECONDS));
      releaseRegistration.countDown();

      assertThat(first.get(10, SECONDS).conflicts()).isEmpty();
      assertThat(second.get(10, SECONDS).conflicts()).isEmpty();
      assertThat(actionGraph.registrations(root.action())).isEqualTo(1);
    } finally {
      releaseRegistration.countDown();
    }
  }

  @Test(timeout = 30000)
  public void conflictInPrunedDependencyIsRediscoveredForLaterRoot() throws Exception {
    Node dependency = node("dependency");
    Node first = node("first", dependency);
    Node second = node("second", dependency);
    var conflict = conflict(dependency);
    var firstRegistration = new CountDownLatch(1);
    var releaseRegistration = new CountDownLatch(1);
    var secondRegistration = new CountDownLatch(1);
    actionGraph.onRegistration =
        action -> {
          if (action == dependency.action()) {
            firstRegistration.countDown();
            await(releaseRegistration);
            throw conflict;
          }
          if (action == second.action()) {
            secondRegistration.countDown();
          }
        };

    var firstResult = callers.submit(() -> finder.findArtifactConflicts(first.key()));
    try {
      await(firstRegistration);
      var secondResult = callers.submit(() -> finder.findArtifactConflicts(second.key()));
      await(secondRegistration);
      assertThrows(TimeoutException.class, () -> secondResult.get(100, MILLISECONDS));
      releaseRegistration.countDown();

      assertThat(firstResult.get(10, SECONDS).conflicts())
          .containsExactly(dependency.action(), conflict);
      assertThat(secondResult.get(10, SECONDS).conflicts())
          .containsExactly(dependency.action(), conflict);
      // The second root must rescan its entire closure with a fresh local visited set.
      assertThat(actionGraph.registrations(dependency.action())).isEqualTo(2);
      assertThat(actionGraph.registrations(second.action())).isEqualTo(2);
      assertThat(actionGraph.registrations(first.action())).isEqualTo(1);
    } finally {
      releaseRegistration.countDown();
    }
  }

  @Test
  public void rerunDoesNotPrunePreviouslyVisitedRoot() throws Exception {
    Node root = node("root");

    assertThat(finder.findArtifactConflicts(root.key()).conflicts()).isEmpty();
    assertThat(finder.findArtifactConflicts(root.key(), /* inRerun= */ true).conflicts()).isEmpty();

    assertThat(actionGraph.registrations(root.action())).isEqualTo(2);
  }

  @Test
  public void conflictingRootIsNotCertifiedForLaterCheck() throws Exception {
    Node root = node("root");
    var conflict = conflict(root);
    actionGraph.onRegistration =
        action -> {
          throw conflict;
        };

    assertThat(finder.findArtifactConflicts(root.key()).conflicts())
        .containsExactly(root.action(), conflict);
    assertThat(finder.findArtifactConflicts(root.key()).conflicts())
        .containsExactly(root.action(), conflict);

    assertThat(actionGraph.registrations(root.action())).isEqualTo(2);
  }

  private record Node(ActionLookupKey key, ActionAnalysisMetadata action) {}

  private Node node(String name, Node... dependencies) throws Exception {
    var key = mock(ActionLookupKey.class, name);
    var action = mock(ActionAnalysisMetadata.class, name);
    when(action.getOutputs()).thenReturn(ImmutableList.of());
    ActionLookupValue value = () -> ImmutableList.of(action);
    when(walkableGraph.getValue(key)).thenReturn(value);
    var keys = ImmutableList.<SkyKey>builder();
    for (Node dependency : dependencies) {
      keys.add(dependency.key());
    }
    when(walkableGraph.getDirectDeps(key)).thenReturn(keys.build());
    return new Node(key, action);
  }

  private static ActionConflictException conflict(Node node) {
    return ActionConflictException.create(
        mock(Artifact.DerivedArtifact.class),
        node.action(),
        "test conflict",
        /* isPrefixConflict= */ false);
  }

  private static void await(CountDownLatch latch) throws InterruptedException {
    assertThat(latch.await(10, SECONDS)).isTrue();
  }

  @FunctionalInterface
  private interface OnRegistration {
    void run(ActionAnalysisMetadata action) throws ActionConflictException, InterruptedException;
  }

  private static final class CountingActionGraph implements MutableActionGraph {
    private final ConcurrentHashMap<ActionAnalysisMetadata, AtomicInteger> registrations =
        new ConcurrentHashMap<>();
    private volatile OnRegistration onRegistration = action -> {};

    @Override
    public void registerAction(ActionAnalysisMetadata action)
        throws ActionConflictException, InterruptedException {
      registrations.computeIfAbsent(action, unused -> new AtomicInteger()).incrementAndGet();
      onRegistration.run(action);
    }

    int registrations(ActionAnalysisMetadata action) {
      var count = registrations.get(action);
      return count == null ? 0 : count.get();
    }

    @Override
    public ActionAnalysisMetadata getGeneratingAction(Artifact artifact) {
      throw new AssertionError("These traversal tests do not register output artifacts");
    }

    @Override
    public int getSize() {
      return registrations.size();
    }
  }
}
