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

package com.google.devtools.build.lib.skyframe.tiering;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.common.collect.ImmutableList;
import com.google.common.collect.ImmutableSet;
import com.google.devtools.build.lib.skyframe.GlobsValue;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.build.runfiles.Runfiles;
import com.google.devtools.build.skyframe.DiskBackedGraph;
import com.google.devtools.build.skyframe.InMemoryNodeEntry;
import com.google.devtools.build.skyframe.IntVersion;
import com.google.devtools.build.skyframe.QueryableGraph;
import com.google.devtools.build.skyframe.SkyFunctionName;
import com.google.devtools.build.skyframe.SkyKey;
import com.google.devtools.build.skyframe.SkyValue;
import java.io.UncheckedIOException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.concurrent.Executors;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Full node paging through the actual rules_rs-built Rust process and its disk store. */
@RunWith(JUnit4.class)
public final class SkystoreGraphTest {
  @Rule public final TemporaryFolder directory = new TemporaryFolder();

  private record Key(String name) implements SkyKey {
    @Override
    public SkyFunctionName functionName() {
      return SkyFunctionName.createHermetic("DISK_GRAPH_TEST");
    }
  }

  private static final DiskBackedGraph.ValueCodec GLOBS =
      new DiskBackedGraph.ValueCodec() {
        @Override
        public boolean supports(SkyValue value) {
          return GlobValueCodec.supports(value);
        }

        @Override
        public byte[] encode(SkyValue value) throws java.io.IOException {
          return GlobValueCodec.encode(value);
        }

        @Override
        public SkyValue decode(byte[] bytes) throws java.io.IOException {
          return GlobValueCodec.decode(bytes);
        }
      };

  private SkystoreProcess start() throws Exception {
    Path binary = Path.of(Runfiles.create().rlocation("_main/tools/skystore/skystore"));
    return new SkystoreProcess(binary.toAbsolutePath(), directory.getRoot().toPath());
  }

  private static InMemoryNodeEntry finish(DiskBackedGraph graph, String name) throws Exception {
    SkyKey key = new Key(name);
    InMemoryNodeEntry node =
        (InMemoryNodeEntry)
            graph
                .createIfAbsentBatch(null, QueryableGraph.Reason.OTHER, ImmutableList.of(key))
                .get(key);
    node.addReverseDepAndCheckIfDone(null);
    node.markRebuilding();
    SkyKey dep = new Key(name + "-dependency");
    node.addSingletonTemporaryDirectDep(dep);
    node.signalDep(IntVersion.of(1), dep);
    node.setValue(
        new GlobsValue(ImmutableSet.of(PathFragment.create(name + ".java"))),
        IntVersion.of(1),
        null);
    return node;
  }

  @Test
  public void interruptedRequestDoesNotPoisonLaterConnections() throws Exception {
    try (SkystoreProcess store = start()) {
      store.put(17, new byte[] {1, 2, 3});
      try {
        Thread.currentThread().interrupt();
        assertThrows(java.io.IOException.class, () -> store.get(17));
      } finally {
        Thread.interrupted();
      }
      assertThat(store.isAlive()).isTrue();
      assertThat(store.get(17)).isEqualTo(new byte[] {1, 2, 3});
    }
  }

  @Test
  public void lazyRustReadsPreserveNodesAndBoundWorkingSet() throws Exception {
    try (SkystoreProcess store = start()) {
      DiskBackedGraph graph = new DiskBackedGraph(store, GLOBS, 2);
      for (int i = 0; i < 32; i++) {
        finish(graph, "node-" + i);
      }
      assertThat(graph.statistics().cold()).isEqualTo(30);
      assertThat(graph.statistics().resident()).isEqualTo(2);
      long pageIns = graph.statistics().pageIns();
      graph
          .getAllNodeEntries()
          .forEach(
              e -> {
                assertThat(e.isDone()).isTrue();
                assertThat(e.getVersion()).isEqualTo(IntVersion.of(1));
              });
      assertThat(graph.statistics().pageIns()).isEqualTo(pageIns);
      try (var executor = Executors.newFixedThreadPool(8)) {
        var results = new ArrayList<java.util.concurrent.Future<?>>();
        for (int i = 0; i < 32; i++) {
          String name = "node-" + i;
          results.add(
              executor.submit(
                  () -> {
                    InMemoryNodeEntry entry = graph.getIfPresent(new Key(name));
                    assertThat(entry.getValue())
                        .isEqualTo(
                            new GlobsValue(ImmutableSet.of(PathFragment.create(name + ".java"))));
                    assertThat(entry.getDirectDeps())
                        .containsExactly(new Key(name + "-dependency"));
                    entry.addReverseDepAndCheckIfDone(new Key(name + "-parent"));
                    assertThat(entry.getReverseDepsForDoneEntry())
                        .containsExactly(new Key(name + "-parent"));
                  }));
        }
        for (var result : results) {
          result.get();
        }
      }
      assertThat(graph.statistics().resident()).isAtMost(2);
      assertThat(graph.statistics().pageIns()).isAtLeast(30);
      assertThat(graph.statistics().bytesRead()).isGreaterThan(0);
      pageIns = graph.statistics().pageIns();
      graph.cleanupInterningPools();
      assertThat(graph.statistics().pageIns()).isEqualTo(pageIns);
    }
  }

  @Test
  public void missingAuthoritativeRustRecordIsAnError() throws Exception {
    try (SkystoreProcess store = start()) {
      DiskBackedGraph graph = new DiskBackedGraph(store, GLOBS, 0);
      InMemoryNodeEntry node = finish(graph, "missing");
      store.clear();
      assertThrows(UncheckedIOException.class, node::getValue);
    }
  }

  @Test
  public void deadRustProcessFailsClosed() throws Exception {
    try (SkystoreProcess store = start()) {
      DiskBackedGraph graph = new DiskBackedGraph(store, GLOBS, 0);
      InMemoryNodeEntry node = finish(graph, "dead");
      ProcessHandle.of(store.pid()).orElseThrow().destroyForcibly();
      ProcessHandle.of(store.pid()).ifPresent(handle -> handle.onExit().join());
      assertThrows(UncheckedIOException.class, node::getValue);
    }
  }
}
