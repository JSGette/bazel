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

import com.google.common.collect.ImmutableSet;
import com.google.devtools.build.lib.skyframe.GlobValueWithImmutableSet;
import com.google.devtools.build.lib.skyframe.GlobsValue;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.build.skyframe.InMemoryGraph;
import com.google.devtools.build.skyframe.InMemoryNodeEntry;
import com.google.devtools.build.skyframe.IntVersion;
import com.google.devtools.build.skyframe.NodeEntry;
import com.google.devtools.build.skyframe.SkyFunctionName;
import com.google.devtools.build.skyframe.SkyKey;
import com.google.devtools.build.skyframe.SkyValue;
import com.google.devtools.build.skyframe.ValueWithMetadata;
import com.google.devtools.common.options.OptionsParser;
import java.io.IOException;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class IdleValueTierTest {
  private record Key(String name) implements SkyKey {
    @Override
    public SkyFunctionName functionName() {
      return SkyFunctionName.createHermetic("TIER_TEST");
    }
  }

  private static final class MemoryStorage implements IdleValueTier.Storage {
    final Map<String, byte[]> values = new HashMap<>();
    boolean failWrites;
    boolean corruptReads;
    boolean failReads;
    InMemoryNodeEntry monitoredEntry;

    @Override
    public void put(byte[] key, byte[] value) throws IOException {
      assertThat(Thread.holdsLock(monitoredEntry)).isFalse();
      if (failWrites) {
        throw new IOException("write failed");
      }
      values.put(Arrays.toString(key), value.clone());
    }

    @Override
    public byte[] get(byte[] key) throws IOException {
      assertThat(Thread.holdsLock(monitoredEntry)).isFalse();
      if (failReads) {
        throw new IOException("read failed");
      }
      return corruptReads ? new byte[] {0} : values.get(Arrays.toString(key));
    }

    @Override
    public void clear() {
      values.clear();
    }
  }

  private static GlobsValue value() {
    ImmutableSet.Builder<PathFragment> paths = ImmutableSet.builder();
    for (int i = 0; i < 100; i++) {
      paths.add(PathFragment.create("directory/with/several/segments/file-" + i + ".java"));
    }
    return new GlobsValue(paths.build());
  }

  private static InMemoryNodeEntry complete(InMemoryGraph graph, String name, SkyValue value)
      throws InterruptedException {
    InMemoryNodeEntry entry =
        (InMemoryNodeEntry)
            graph
                .createIfAbsentBatch(
                    null,
                    com.google.devtools.build.skyframe.QueryableGraph.Reason.OTHER,
                    ImmutableSet.of(new Key(name)))
                .get(new Key(name));
    entry.addReverseDepAndCheckIfDone(null);
    entry.markRebuilding();
    entry.setValue(value, IntVersion.of(0), null);
    return entry;
  }

  @Test
  public void engineOptionsAreRegisteredAndDefaultOff() throws Exception {
    OptionsParser parser =
        OptionsParser.builder().optionsClasses(SkyframeTieringModule.Options.class).build();
    assertThat(parser.getOptions(SkyframeTieringModule.Options.class).getEngine())
        .isEqualTo(SkyframeTieringModule.Engine.INMEMORY);
    parser.parse(
        "--experimental_skyframe_engine=tiered_idle",
        "--experimental_skystore_binary=/absolute/skystore");
    assertThat(parser.getOptions(SkyframeTieringModule.Options.class).getEngine())
        .isEqualTo(SkyframeTieringModule.Engine.TIERED_IDLE);
  }

  @Test
  public void metadataDirtyAndSmallValuesRemainResident() throws Exception {
    InMemoryGraph graph = InMemoryGraph.create();
    SkyValue wrapped = ValueWithMetadata.wrapWithMetadata(value());
    InMemoryNodeEntry metadata = complete(graph, "metadata", wrapped);
    SkyValue smallValue = new GlobsValue(ImmutableSet.of(PathFragment.create("small")));
    InMemoryNodeEntry small = complete(graph, "small", smallValue);
    InMemoryNodeEntry dirty = complete(graph, "dirty", value());
    dirty.markDirty(NodeEntry.DirtyType.CHANGE);
    MemoryStorage storage = new MemoryStorage();
    storage.monitoredEntry = metadata;
    IdleValueTier tier = new IdleValueTier(storage);
    tier.spill(graph, 1_000_000);
    assertThat(tier.detachedCount()).isEqualTo(0);
    assertThat(metadata.getValueMaybeWithMetadata()).isSameInstanceAs(wrapped);
    assertThat(small.getValue()).isSameInstanceAs(smallValue);
    assertThat(dirty.isDirty()).isTrue();
  }

  @Test
  public void codecPreservesClassContentsOrderAndUnusualNames() throws Exception {
    ImmutableSet<PathFragment> paths =
        ImmutableSet.of(
            PathFragment.create("a"), PathFragment.create("space and ü"),
            PathFragment.create("surrogate-\ud800"), PathFragment.create("emoji-\ud83d\ude00"));
    for (SkyValue original :
        new SkyValue[] {
          new GlobsValue(paths),
          new GlobValueWithImmutableSet(paths),
          new GlobsValue(ImmutableSet.of()),
          GlobValueWithImmutableSet.EMPTY
        }) {
      SkyValue restored = GlobValueCodec.decode(GlobValueCodec.encode(original));
      assertThat(restored.getClass()).isEqualTo(original.getClass());
      assertThat(restored).isEqualTo(original);
      assertThat(restored.hashCode()).isEqualTo(original.hashCode());
    }
    byte[] valid = GlobValueCodec.encode(value());
    for (int end = 0; end < valid.length; end++) {
      byte[] truncated = Arrays.copyOf(valid, end);
      assertThrows(IOException.class, () -> GlobValueCodec.decode(truncated));
    }
    assertThrows(
        IOException.class, () -> GlobValueCodec.decode(Arrays.copyOf(valid, valid.length + 1)));
    assertThat(GlobValueCodec.supports(new GlobsValue(paths) {})).isFalse();
  }

  @Test
  public void roundtripPreservesVersionEdgesAndSubsequentInvalidation() throws Exception {
    InMemoryGraph graph = InMemoryGraph.create();
    GlobsValue original = value();
    InMemoryNodeEntry entry = complete(graph, "glob", original);
    entry.addReverseDepAndCheckIfDone(new Key("parent"));
    MemoryStorage storage = new MemoryStorage();
    storage.monitoredEntry = entry;
    IdleValueTier tier = new IdleValueTier(storage);
    var version = entry.getVersion();
    var edges = entry.getCompressedDirectDepsForDoneEntry();
    tier.spill(graph, 1_000_000);
    assertThat(tier.detachedCount()).isEqualTo(1);
    assertThat(entry.isDone()).isTrue();
    assertThat(entry.getValue()).isNotSameInstanceAs(original);
    assertThat(entry.getVersion()).isSameInstanceAs(version);
    assertThat(entry.getCompressedDirectDepsForDoneEntry()).isSameInstanceAs(edges);
    tier.restore();
    assertThat(entry.getValue()).isEqualTo(original);
    assertThat(entry.getValue()).isNotSameInstanceAs(original);
    assertThat(entry.getReverseDepsForDoneEntry()).containsExactly(new Key("parent"));
    assertThat(storage.values).isEmpty();
    entry.markDirty(NodeEntry.DirtyType.CHANGE);
    assertThat(entry.isDirty()).isTrue();
    assertThat(entry.toValue()).isEqualTo(original);
  }

  @Test
  public void failedWriteNeverDetachesAndBudgetPinsValues() throws Exception {
    InMemoryGraph graph = InMemoryGraph.create();
    SkyValue original = value();
    InMemoryNodeEntry entry = complete(graph, "glob", original);
    MemoryStorage storage = new MemoryStorage();
    storage.monitoredEntry = entry;
    IdleValueTier tier = new IdleValueTier(storage);
    tier.spill(graph, 1);
    assertThat(entry.getValue()).isSameInstanceAs(original);
    storage.failWrites = true;
    assertThrows(IOException.class, () -> tier.spill(graph, 1_000_000));
    assertThat(tier.detachedCount()).isEqualTo(0);
    assertThat(entry.getValue()).isSameInstanceAs(original);
  }

  @Test
  public void corruptMissingAndUnavailableStorageCannotReturnAValue() throws Exception {
    for (int failure = 0; failure < 3; failure++) {
      InMemoryGraph graph = InMemoryGraph.create();
      InMemoryNodeEntry entry = complete(graph, "glob", value());
      MemoryStorage storage = new MemoryStorage();
      storage.monitoredEntry = entry;
      IdleValueTier tier = new IdleValueTier(storage);
      tier.spill(graph, 1_000_000);
      if (failure == 0) {
        storage.corruptReads = true;
      } else if (failure == 1) {
        storage.values.clear();
      } else {
        storage.failReads = true;
      }
      assertThrows(IOException.class, tier::restore);
      assertThat(tier.detachedCount()).isEqualTo(1);
    }
  }

  @Test
  public void identityGuardAndDirtyStateRejectMaintenanceReplacement() throws Exception {
    InMemoryGraph graph = InMemoryGraph.create();
    SkyValue original = value();
    InMemoryNodeEntry entry = complete(graph, "glob", original);
    assertThat(entry.replaceDoneValueForMaintenance(value(), value())).isFalse();
    entry.markDirty(NodeEntry.DirtyType.CHANGE);
    assertThat(entry.replaceDoneValueForMaintenance(original, value())).isFalse();
    assertThat(entry.toValue()).isSameInstanceAs(original);
  }
}
