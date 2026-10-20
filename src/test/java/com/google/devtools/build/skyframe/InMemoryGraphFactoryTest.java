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
import com.google.devtools.build.skyframe.QueryableGraph.Reason;
import com.google.devtools.build.skyframe.SkyKey.SkyKeyInterner;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Behavioral checks for pooled node creation through a graph's overridden factory. */
@RunWith(JUnit4.class)
public final class InMemoryGraphFactoryTest {
  private record Key(int id, SkyKeyInterner<Key> interner) implements SkyKey {
    @Override
    public SkyFunctionName functionName() {
      return SkyFunctionName.FOR_TESTING;
    }

    @Override
    public SkyKeyInterner<Key> getSkyKeyInterner() {
      return interner;
    }
  }

  private static final class FactoryGraph extends InMemoryGraphImpl {
    private final boolean failFirstCreation;
    private final IllegalStateException failure =
        new IllegalStateException("Expected node factory failure");
    private int factoryCalls;

    FactoryGraph(boolean failFirstCreation) {
      super(/* usePooledInterning= */ true);
      this.failFirstCreation = failFirstCreation;
    }

    @Override
    protected InMemoryNodeEntry newNodeEntry(SkyKey key) {
      factoryCalls++;
      if (failFirstCreation && factoryCalls == 1) {
        throw failure;
      }
      // An observable override result distinct from the base factory's incremental node type.
      return new NonIncrementalInMemoryNodeEntry(key);
    }
  }

  @Test
  public void pooledFactoryFailure_preservesWeakKeyAndAllowsRetry() throws InterruptedException {
    SkyKeyInterner<Key> interner = SkyKey.newInterner();
    // Seed only this test's weak pool, independently of any previous test's global graph pool.
    Key key = interner.weakIntern(new Key(1, interner));
    FactoryGraph graph = new FactoryGraph(/* failFirstCreation= */ true);
    try {
      IllegalStateException thrown =
          assertThrows(
              IllegalStateException.class,
              () -> graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(key)));
      assertThat(thrown).isSameInstanceAs(graph.failure);
      assertThat(graph.factoryCalls).isEqualTo(1);
      assertThat(graph.getIfPresent(key)).isNull();
      assertThat(graph.valuesSize()).isEqualTo(0);
      assertThat(interner.size()).isEqualTo(1);
      assertThat(interner.intern(new Key(1, interner))).isSameInstanceAs(key);

      NodeBatch batch = graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(key));
      InMemoryNodeEntry entry = graph.getIfPresent(key);
      assertThat(entry).isInstanceOf(NonIncrementalInMemoryNodeEntry.class);
      assertThat(entry.getKey()).isSameInstanceAs(key);
      assertThat(batch.get(key)).isSameInstanceAs(entry);
      assertThat(graph.factoryCalls).isEqualTo(2);
      assertThat(interner.size()).isEqualTo(0);
      assertThat(interner.intern(new Key(1, interner))).isSameInstanceAs(key);
      assertThat(interner.size()).isEqualTo(0);

      // A successful retry becomes an ordinary hit; it must not invoke the factory again.
      graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(key));
      assertThat(graph.getIfPresent(key)).isSameInstanceAs(entry);
      assertThat(graph.factoryCalls).isEqualTo(2);
      graph.remove(key);
      assertThat(interner.size()).isEqualTo(1);
      assertThat(interner.intern(new Key(1, interner))).isSameInstanceAs(key);
    } finally {
      graph.cleanupInterningPools();
      interner.removeWeak(key);
    }
    assertThat(interner.size()).isEqualTo(0);
    assertThat(SkyKeyInterner.globalPool).isNull();
  }

  @Test
  public void pooledFactoryOverride_usedForInitialCreationAndBatchRecreation()
      throws InterruptedException {
    SkyKeyInterner<Key> interner = SkyKey.newInterner();
    Key key = interner.weakIntern(new Key(2, interner));
    FactoryGraph graph = new FactoryGraph(/* failFirstCreation= */ false);
    try {
      NodeBatch batch = graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(key));
      NodeEntry first = batch.get(key);
      assertThat(first).isInstanceOf(NonIncrementalInMemoryNodeEntry.class);
      assertThat(graph.getIfPresent(key)).isSameInstanceAs(first);
      assertThat(graph.factoryCalls).isEqualTo(1);
      assertThat(interner.size()).isEqualTo(0);

      graph.createIfAbsentBatch(null, Reason.OTHER, ImmutableList.of(key));
      assertThat(batch.get(key)).isSameInstanceAs(first);
      assertThat(graph.factoryCalls).isEqualTo(1);

      graph.remove(key);
      assertThat(graph.getIfPresent(key)).isNull();
      assertThat(interner.size()).isEqualTo(1);

      // The original batch promises to recreate a removed node through the same virtual factory.
      NodeEntry recreated = batch.get(key);
      assertThat(recreated).isInstanceOf(NonIncrementalInMemoryNodeEntry.class);
      assertThat(recreated).isNotSameInstanceAs(first);
      assertThat(graph.getIfPresent(key)).isSameInstanceAs(recreated);
      assertThat(graph.factoryCalls).isEqualTo(2);
      assertThat(interner.size()).isEqualTo(0);
      assertThat(interner.intern(new Key(2, interner))).isSameInstanceAs(key);
    } finally {
      graph.cleanupInterningPools();
      interner.removeWeak(key);
    }
    assertThat(interner.size()).isEqualTo(0);
    assertThat(SkyKeyInterner.globalPool).isNull();
  }
}
