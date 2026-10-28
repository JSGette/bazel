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
package com.google.devtools.build.lib.collect.nestedset;

import static com.google.common.truth.Truth.assertThat;
import static com.google.common.truth.Truth.assertWithMessage;

import com.google.common.collect.ImmutableMap;
import com.google.devtools.build.lib.compress.CompressionService;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.packages.StarlarkInfo;
import com.google.devtools.build.lib.packages.StructProvider;
import com.google.devtools.build.lib.skyframe.serialization.AutoRegistry;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.InMemoryFingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecs;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

/**
 * Manual conformance reproducer for provider identity across separately restored branches.
 *
 * <p>The two cross-branch assertions describe required behavior and are currently expected to fail.
 * They must not become assertions that duplicate providers are correct. See the accompanying P02
 * runner for a strict, explicitly requested known-failure check.
 */
@RunWith(JUnit4.class)
public final class ProviderIdentityReproTest {
  private static final CompressionService COMPRESSION_SERVICE = new CompressionServiceImpl();

  @Test
  public void originalBranchesDeduplicateSharedProvider() {
    Branches branches = sharedBranches();
    assertThat(branches.left().toList().get(0))
        .isSameInstanceAs(branches.right().toList().get(0));
    assertThat(merge(branches.left(), branches.right()).toList()).hasSize(3);
  }

  @Test
  public void distinctEqualContentOriginsRemainDistinctAfterRestore() throws Exception {
    StarlarkInfo first = providerWithDeps("shared", deps());
    StarlarkInfo second = providerWithDeps("shared", deps());
    assertThat(first).isNotEqualTo(second);
    var original = NestedSetBuilder.<StarlarkInfo>stableOrder().add(first).add(second).build();
    assertThat(original.toList()).hasSize(2);

    var restored = new RoundTripper().restore(original);
    assertThat(restored.toList()).hasSize(2);
    assertSameFields(restored.toList().get(0), restored.toList().get(1));
    // Content interning all wrappers would hide the shared-origin defect by merging these origins.
    assertThat(restored.toList().get(0)).isNotEqualTo(restored.toList().get(1));
  }

  @Test
  public void independentRestoresPreserveSharedProvider() throws Exception {
    verifySharedProvider(/* restoreLeft= */ true);
  }

  @Test
  public void originalAndRestoredBranchesPreserveSharedProvider() throws Exception {
    verifySharedProvider(/* restoreLeft= */ false);
  }

  private static void verifySharedProvider(boolean restoreLeft) throws Exception {
    Branches branches = sharedBranches();
    assertThat(merge(branches.left(), branches.right()).toList()).hasSize(3);
    var roundTripper = new RoundTripper();
    var left = restoreLeft ? roundTripper.restore(branches.left()) : branches.left();
    var right = roundTripper.restore(branches.right());
    StarlarkInfo leftLeaf = left.toList().get(0);
    StarlarkInfo rightLeaf = right.toList().get(0);
    assertSameFields(leftLeaf, rightLeaf);
    if (restoreLeft) {
      // Sharing the backing array already works; the failing invariant is provider deduplication.
      assertThat(((Depset) leftLeaf.getValue("deps")).getSet().getChildren())
          .isSameInstanceAs(((Depset) rightLeaf.getValue("deps")).getSet().getChildren());
    }
    var result = merge(left, right).toList();
    assertWithMessage("SKYCACHE_PROVIDER_IDENTITY expected=3 actual=%s", result.size())
        .that(result)
        .hasSize(3);
  }

  private static void assertSameFields(StarlarkInfo left, StarlarkInfo right) throws Exception {
    assertThat(left.getProvider()).isSameInstanceAs(right.getProvider());
    assertThat(left.getValue("label")).isEqualTo(right.getValue("label"));
    assertThat(left.getValue("file")).isEqualTo(right.getValue("file"));
    assertThat(((Depset) left.getValue("deps")).toList())
        .containsExactlyElementsIn(((Depset) right.getValue("deps")).toList())
        .inOrder();
  }

  private record Branches(NestedSet<StarlarkInfo> left, NestedSet<StarlarkInfo> right) {}

  private static Branches sharedBranches() {
    Depset deps = deps();
    StarlarkInfo shared = providerWithDeps("shared", deps);
    return new Branches(
        NestedSetBuilder.<StarlarkInfo>stableOrder()
            .add(shared)
            .add(providerWithDeps("left", deps))
            .build(),
        NestedSetBuilder.<StarlarkInfo>stableOrder()
            .add(shared)
            .add(providerWithDeps("right", deps))
            .build());
  }

  private static Depset deps() {
    return Depset.of(
        String.class,
        NestedSetBuilder.<String>stableOrder().add("libfirst.a").add("libsecond.a").build());
  }

  private static StarlarkInfo providerWithDeps(String label, Depset deps) {
    return StarlarkInfo.create(
        StructProvider.STRUCT, ImmutableMap.of("label", label, "file", label + ".a", "deps", deps));
  }

  private static NestedSet<StarlarkInfo> merge(
      NestedSet<StarlarkInfo> left, NestedSet<StarlarkInfo> right) {
    return NestedSetBuilder.<StarlarkInfo>stableOrder()
        .addTransitive(left)
        .addTransitive(right)
        .build();
  }

  private static final class RoundTripper {
    private final InMemoryFingerprintValueStore store = new InMemoryFingerprintValueStore();
    private final FingerprintValueService writer = FingerprintValueService.createForTesting(store);
    private final FingerprintValueService reader = FingerprintValueService.createForTesting(store);
    private final ObjectCodecs codecs =
        new ObjectCodecs(
            AutoRegistry.get()
                .getBuilder()
                .add(new DeferredNestedSetCodec())
                .addReferenceConstant(StructProvider.STRUCT)
                .build());

    @SuppressWarnings("unchecked")
    private NestedSet<StarlarkInfo> restore(NestedSet<StarlarkInfo> value) throws Exception {
      var serialized = codecs.serializeMemoizedAndBlocking(COMPRESSION_SERVICE, writer, value);
      if (serialized.getFutureToBlockWritesOn() != null) {
        var unused = serialized.getFutureToBlockWritesOn().get();
      }
      return (NestedSet<StarlarkInfo>)
          codecs.deserializeMemoizedAndBlocking(
              COMPRESSION_SERVICE, reader, serialized.getObject());
    }
  }
}
