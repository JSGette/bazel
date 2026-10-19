// Copyright 2023 The Bazel Authors. All rights reserved.
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
import com.google.devtools.build.skyframe.GraphTester.StringValue;
import com.google.devtools.build.skyframe.QueryableGraph.Reason;
import java.io.UncheckedIOException;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

/**
 * The regular graph contract plus tests that force a page-out between every completed operation.
 */
public final class DiskBackedGraphTest extends GraphTest {
  @Rule public final TemporaryFolder directory = new TemporaryFolder();
  private DiskBackedGraph diskGraph;
  private DiskBackedGraphTestSupport.FileStore store;

  @Override
  protected void makeGraph() {
    store = new DiskBackedGraphTestSupport.FileStore(directory.getRoot().toPath());
    diskGraph = new DiskBackedGraph(store, DiskBackedGraphTestSupport.STRINGS, 0);
    graph = diskGraph;
  }

  @Override
  protected ProcessableGraph getGraph(Version version) {
    return graph;
  }

  @Override
  protected Version getStartingVersion() {
    return IntVersion.of(0);
  }

  @Override
  protected Version getNextVersion(Version version) {
    return ((IntVersion) version).next();
  }

  private InMemoryNodeEntry newEntry(String name) throws Exception {
    SkyKey key = key(name);
    return (InMemoryNodeEntry)
        graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(key)).get(key);
  }

  private InMemoryNodeEntry finish(String name) throws Exception {
    InMemoryNodeEntry entry = newEntry(name);
    entry.addReverseDepAndCheckIfDone(null);
    entry.markRebuilding();
    entry.setValue(new StringValue(name), IntVersion.of(1), null);
    return entry;
  }

  @Test
  public void metadataScansAndCleanupDoNotRestoreColdNodes() throws Exception {
    for (int i = 0; i < 40; i++) {
      finish("node-" + i);
    }
    assertThat(diskGraph.statistics().cold()).isEqualTo(40);
    assertThat(diskGraph.statistics().resident()).isEqualTo(0);
    long reads = diskGraph.statistics().pageIns();
    for (InMemoryNodeEntry node : diskGraph.getAllNodeEntries()) {
      assertThat(node.isDone()).isTrue();
      assertThat(node.isDirty()).isFalse();
      assertThat(node.getVersion()).isEqualTo(IntVersion.of(1));
      assertThat(node.getLifecycleState()).isEqualTo(NodeEntry.LifecycleState.DONE);
    }
    diskGraph.cleanupInterningPools();
    assertThat(diskGraph.statistics().pageIns()).isEqualTo(reads);
    assertThat(diskGraph.valuesSize()).isEqualTo(0);
  }

  @Test
  public void groupedEdgesAndReverseEdgesSurviveEviction() throws Exception {
    InMemoryNodeEntry entry = newEntry("parent");
    SkyKey a = key("a");
    SkyKey b = key("b");
    SkyKey c = key("c");
    SkyKey r1 = key("r1");
    SkyKey r2 = key("r2");
    entry.addReverseDepAndCheckIfDone(r1);
    entry.markRebuilding();
    entry.addTemporaryDirectDepGroup(ImmutableList.of(a, b));
    entry.addSingletonTemporaryDirectDep(c);
    for (SkyKey dep : ImmutableList.of(a, b, c)) {
      entry.signalDep(IntVersion.of(1), dep);
    }
    entry.setValue(new StringValue("value"), IntVersion.of(1), null);
    assertThat(diskGraph.statistics().cold()).isEqualTo(1);
    entry.addReverseDepAndCheckIfDone(r2);
    entry.removeReverseDep(r1);
    assertThat(entry.getReverseDepsForDoneEntry()).containsExactly(r2);
    assertThat(
            ImmutableList.copyOf(
                GroupedDeps.decompress(entry.getCompressedDirectDepsForDoneEntry())))
        .containsExactly(ImmutableList.of(a, b), ImmutableList.of(c))
        .inOrder();
    assertThat(entry.getValue()).isEqualTo(new StringValue("value"));
    assertThat(diskGraph.statistics().resident()).isEqualTo(0);
  }

  @Test
  public void unsupportedAndEvaluatingNodesStayPinned() throws Exception {
    InMemoryNodeEntry active = newEntry("active");
    active.addReverseDepAndCheckIfDone(null);
    active.markRebuilding();
    GroupedDeps deps = active.getTemporaryDirectDeps();
    finish("evictable");
    assertThat(active.getTemporaryDirectDeps()).isSameInstanceAs(deps);
    InMemoryNodeEntry unsupported = newEntry("unsupported");
    unsupported.addReverseDepAndCheckIfDone(null);
    unsupported.markRebuilding();
    SkyValue value = new SkyValue() {};
    unsupported.setValue(value, IntVersion.of(1), null);
    assertThat(unsupported.getValue()).isSameInstanceAs(value);
    assertThat(diskGraph.statistics().pinned()).isEqualTo(2);
    assertThat(diskGraph.statistics().cold()).isEqualTo(1);
  }

  @Test
  public void writeFailureRetainsAuthoritativeLiveNode() throws Exception {
    store.failWrites = true;
    assertThrows(UncheckedIOException.class, () -> finish("failure"));
    assertThat(diskGraph.statistics().resident()).isEqualTo(1);
    store.failWrites = false;
    InMemoryNodeEntry entry = diskGraph.getIfPresent(key("failure"));
    assertThat(entry.getValue()).isEqualTo(new StringValue("failure"));
    assertThat(diskGraph.statistics().cold()).isEqualTo(1);
  }

  @Test
  public void corruptReadFailsClosed() throws Exception {
    InMemoryNodeEntry entry = finish("corrupt");
    store.corrupt(((DiskBackedNodeEntry) entry).id);
    assertThrows(UncheckedIOException.class, entry::getValue);
    assertThat(entry.isDone()).isTrue();
    assertThat(diskGraph.statistics().cold()).isEqualTo(1);
  }

  @Test
  public void sameLengthPayloadCorruptionFailsClosed() throws Exception {
    InMemoryNodeEntry entry = finish("corrupt");
    store.flipByte(((DiskBackedNodeEntry) entry).id, -9);
    assertThrows(UncheckedIOException.class, entry::getValue);
  }

  @Test
  public void concurrentReverseDependencyRegistrationDoesNotLoseEdges() throws Exception {
    InMemoryNodeEntry entry = finish("shared");
    try (var executor = Executors.newFixedThreadPool(8)) {
      List<java.util.concurrent.Future<?>> futures = new ArrayList<>();
      for (int i = 0; i < 64; i++) {
        SkyKey reverse = key("reverse-" + i);
        futures.add(executor.submit(() -> entry.addReverseDepAndCheckIfDone(reverse)));
      }
      for (var future : futures) {
        future.get();
      }
    }
    assertThat(entry.getReverseDepsForDoneEntry()).hasSize(64);
    assertThat(diskGraph.statistics().resident()).isEqualTo(0);
  }

  @Test
  public void residentWorkingSetIsBoundedAcrossDisjointReads() throws Exception {
    diskGraph = new DiskBackedGraph(store, DiskBackedGraphTestSupport.STRINGS, 3);
    graph = diskGraph;
    for (int i = 0; i < 30; i++) {
      finish("node-" + i);
    }
    assertThat(diskGraph.statistics().resident()).isEqualTo(3);
    for (int i = 0; i < 30; i++) {
      assertThat(diskGraph.getIfPresent(key("node-" + i)).getValue())
          .isEqualTo(new StringValue("node-" + i));
      assertThat(diskGraph.statistics().resident()).isAtMost(3);
    }
    assertThat(diskGraph.statistics().pageIns()).isAtLeast(27);
  }
}
