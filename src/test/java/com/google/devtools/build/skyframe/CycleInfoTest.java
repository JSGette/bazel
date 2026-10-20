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

import com.google.common.collect.ImmutableList;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests for cycle normalization used when errors propagate to a parent. */
@RunWith(JUnit4.class)
public final class CycleInfoTest {
  @Test
  public void prepareCycles_rotatesEveryBoundaryAndPreservesInput() {
    for (int length : new int[] {1, 2, 16, 1024}) {
      ImmutableList.Builder<SkyKey> keys = ImmutableList.builderWithExpectedSize(length);
      for (int i = 0; i < length; i++) {
        keys.add(GraphTester.skyKey("cycle-" + length + "-" + i));
      }
      ImmutableList<SkyKey> original = keys.build();
      CycleInfo input = CycleInfo.createCycleInfo(original);
      for (int rotation : new int[] {0, Math.min(1, length - 1), length / 2, length - 1}) {
        List<SkyKey> expected = new ArrayList<>(original);
        Collections.rotate(expected, -rotation);

        ImmutableList<CycleInfo> result =
            ImmutableList.copyOf(
                CycleInfo.prepareCycles(original.get(rotation), ImmutableList.of(input)));

        assertThat(result).hasSize(1);
        assertThat(result.getFirst().getCycle()).containsExactlyElementsIn(expected).inOrder();
        assertThat(result.getFirst().getPathToCycle()).isEmpty();
        assertThat(result.getFirst().getTopKey()).isEqualTo(original.get(rotation));
        assertThat(input.getCycle()).containsExactlyElementsIn(original).inOrder();
      }
    }
  }

  @Test
  public void prepareCycles_filtersRoundaboutPathsAndDeduplicatesNormalizedCycles() {
    SkyKey a = GraphTester.skyKey("a");
    SkyKey b = GraphTester.skyKey("b");
    SkyKey c = GraphTester.skyKey("c");
    SkyKey d = GraphTester.skyKey("d");
    CycleInfo original = CycleInfo.createCycleInfo(ImmutableList.of(a, b, c));
    CycleInfo roundabout =
        CycleInfo.createCycleInfo(
            ImmutableList.of(GraphTester.skyKey("detour")), original.getCycle());
    CycleInfo rotated = CycleInfo.createCycleInfo(ImmutableList.of(b, c, a));
    CycleInfo distinct = CycleInfo.createCycleInfo(ImmutableList.of(b, d));

    assertThat(
            CycleInfo.prepareCycles(
                b, ImmutableList.of(roundabout, original, rotated, distinct, roundabout)))
        .containsExactly(CycleInfo.createCycleInfo(ImmutableList.of(b, c, a)), distinct)
        .inOrder();
    assertThat(CycleInfo.prepareCycles(b, ImmutableList.of(roundabout))).isEmpty();
  }

  @Test
  public void prepareCycles_outsideCyclePrependsPath() {
    SkyKey parent = GraphTester.skyKey("parent");
    SkyKey child = GraphTester.skyKey("child");
    ImmutableList<SkyKey> cycle = ImmutableList.of(GraphTester.skyKey("cycle"));
    CycleInfo input = CycleInfo.createCycleInfo(ImmutableList.of(child), cycle);

    assertThat(CycleInfo.prepareCycles(parent, ImmutableList.of(input)))
        .containsExactly(CycleInfo.createCycleInfo(ImmutableList.of(parent, child), cycle));
  }

  @Test
  public void prepareCycles_noDetailsWinsBeforeOrAfterDetailedCycle() {
    SkyKey key = GraphTester.skyKey("key");
    CycleInfo detailed = CycleInfo.createCycleInfo(ImmutableList.of(key));
    CycleInfo sentinel = CycleInfo.cycleInfoNoDetails();
    for (ImmutableList<CycleInfo> cycles :
        ImmutableList.of(
            ImmutableList.of(sentinel, detailed),
            ImmutableList.of(detailed, sentinel),
            ImmutableList.of(detailed, sentinel, detailed, sentinel))) {
      assertThat(CycleInfo.prepareCycles(key, cycles)).containsExactly(sentinel);
    }
    assertThat(CycleInfo.prepareCycles(key, ImmutableList.of())).isEmpty();
  }
}
