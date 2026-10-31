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

import com.google.common.collect.ImmutableList;
import com.google.devtools.build.lib.vfs.Dirent;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.Executors;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/** Tests for {@link CompactSortedDirents}. */
@RunWith(JUnit4.class)
public final class CompactSortedDirentsTest {

  @Test
  public void emptySingleton() {
    assertThat(CompactSortedDirents.create(ImmutableList.of()))
        .isSameInstanceAs(CompactSortedDirents.EMPTY);
    assertThat(CompactSortedDirents.EMPTY).isEmpty();
    assertThat(CompactSortedDirents.EMPTY.maybeGetDirent("foo")).isNull();
  }

  @Test
  public void instanceReuse() {
    CompactSortedDirents csd =
        CompactSortedDirents.create(
            ImmutableList.of(
                new Dirent("b", Dirent.Type.FILE), new Dirent("a", Dirent.Type.DIRECTORY)));
    assertThat(CompactSortedDirents.create(csd)).isSameInstanceAs(csd);
  }

  @Test
  public void sortingAndTypePreservation() {
    CompactSortedDirents csd =
        CompactSortedDirents.create(
            ImmutableList.of(
                new Dirent("c", Dirent.Type.SYMLINK),
                new Dirent("a", Dirent.Type.DIRECTORY),
                new Dirent("d", Dirent.Type.UNKNOWN),
                new Dirent("b", Dirent.Type.FILE)));

    assertThat(csd).hasSize(4);
    assertThat(csd)
        .containsExactly(
            new Dirent("a", Dirent.Type.DIRECTORY),
            new Dirent("b", Dirent.Type.FILE),
            new Dirent("c", Dirent.Type.SYMLINK),
            new Dirent("d", Dirent.Type.UNKNOWN))
        .inOrder();

    assertThat(csd.maybeGetDirent("a")).isEqualTo(new Dirent("a", Dirent.Type.DIRECTORY));
    assertThat(csd.maybeGetDirent("b")).isEqualTo(new Dirent("b", Dirent.Type.FILE));
    assertThat(csd.maybeGetDirent("c")).isEqualTo(new Dirent("c", Dirent.Type.SYMLINK));
    assertThat(csd.maybeGetDirent("d")).isEqualTo(new Dirent("d", Dirent.Type.UNKNOWN));
    assertThat(csd.maybeGetDirent("missing")).isNull();

    assertThat(csd.maybeGetDirentType("a")).isEqualTo(Dirent.Type.DIRECTORY);
    assertThat(csd.maybeGetDirentType("b")).isEqualTo(Dirent.Type.FILE);
    assertThat(csd.maybeGetDirentType("c")).isEqualTo(Dirent.Type.SYMLINK);
    assertThat(csd.maybeGetDirentType("d")).isEqualTo(Dirent.Type.UNKNOWN);
    assertThat(csd.maybeGetDirentType("missing")).isNull();
  }

  @Test
  public void equalsAndHashCode() {
    CompactSortedDirents csd1 =
        CompactSortedDirents.create(
            ImmutableList.of(
                new Dirent("b", Dirent.Type.FILE), new Dirent("a", Dirent.Type.DIRECTORY)));
    CompactSortedDirents csd2 =
        CompactSortedDirents.create(
            ImmutableList.of(
                new Dirent("a", Dirent.Type.DIRECTORY), new Dirent("b", Dirent.Type.FILE)));

    assertThat(csd1).isEqualTo(csd2);
    assertThat(csd1.hashCode()).isEqualTo(csd2.hashCode());
  }

  @Test
  public void equalListingsSharePayloadAcrossIndependentReaders() throws Exception {
    CompactSortedDirents expected =
        CompactSortedDirents.create(
            ImmutableList.of(
                new Dirent("file", Dirent.Type.FILE), new Dirent("subdir", Dirent.Type.DIRECTORY)));
    try (var executor = Executors.newFixedThreadPool(4)) {
      var tasks = new ArrayList<java.util.concurrent.Callable<CompactSortedDirents>>();
      for (int i = 0; i < 32; i++) {
        tasks.add(
            () ->
                CompactSortedDirents.create(
                    ImmutableList.of(
                        new Dirent(new String("subdir"), Dirent.Type.DIRECTORY),
                        new Dirent(new String("file"), Dirent.Type.FILE))));
      }
      for (var result : executor.invokeAll(tasks)) {
        assertThat(result.get()).isSameInstanceAs(expected);
      }
    }
  }

  @Test
  public void listingChangesDoNotMutateAnEarlierSnapshot() {
    List<Dirent> input = new ArrayList<>();
    input.add(new Dirent("entry", Dirent.Type.FILE));
    CompactSortedDirents before = CompactSortedDirents.create(input);
    input.set(0, new Dirent("entry", Dirent.Type.SYMLINK));
    CompactSortedDirents changedType = CompactSortedDirents.create(input);
    input.add(new Dirent("new", Dirent.Type.DIRECTORY));
    CompactSortedDirents changedNames = CompactSortedDirents.create(input);
    assertThat(changedType).isNotSameInstanceAs(before);
    assertThat(changedNames).isNotSameInstanceAs(changedType);
    assertThat(before).containsExactly(new Dirent("entry", Dirent.Type.FILE));
    assertThat(changedType).containsExactly(new Dirent("entry", Dirent.Type.SYMLINK));
    assertThat(CompactSortedDirents.create(ImmutableList.of(new Dirent("entry", Dirent.Type.FILE))))
        .isSameInstanceAs(before);
  }
}
