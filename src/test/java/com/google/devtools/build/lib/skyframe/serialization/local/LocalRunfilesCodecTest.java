// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;

import com.google.devtools.build.lib.analysis.Runfiles;
import com.google.devtools.build.lib.collect.nestedset.DeferredNestedSetCodec;
import com.google.devtools.build.lib.collect.nestedset.NestedSet;
import com.google.devtools.build.lib.collect.nestedset.NestedSetBuilder;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.AutoRegistry;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.InMemoryFingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecs;
import com.google.devtools.build.lib.skyframe.serialization.ValueSharingAdapter;
import com.google.protobuf.ByteString;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalRunfilesCodecTest {
  private final CompressionServiceImpl compression = new CompressionServiceImpl();
  private final InMemoryFingerprintValueStore store = new InMemoryFingerprintValueStore();
  private final FingerprintValueService writer = FingerprintValueService.createForTesting(store);
  private final FingerprintValueService reader = FingerprintValueService.createForTesting(store);
  private final ObjectCodecs codecs =
      new ObjectCodecs(
          AutoRegistry.get()
              .getBuilder()
              .add(new DeferredNestedSetCodec())
              .add(new ValueSharingAdapter<>(new LocalRunfilesCodec()))
              .addReferenceConstant(Runfiles.EMPTY)
              .build());

  @Test
  public void sharedRunfilesLeafRemainsUniqueAcrossNestedSetBranches() throws Exception {
    Runfiles leaf = new Runfiles.Builder("leaf").build();
    var left =
        NestedSetBuilder.<Runfiles>stableOrder()
            .add(leaf)
            .add(new Runfiles.Builder("left").build())
            .build();
    var right =
        NestedSetBuilder.<Runfiles>stableOrder()
            .add(leaf)
            .add(new Runfiles.Builder("right").build())
            .build();
    var top =
        NestedSetBuilder.<Runfiles>stableOrder().addTransitive(left).addTransitive(right).build();
    assertThat(top.toList()).hasSize(3);
    NestedSet<?> restored = (NestedSet<?>) decode(encode(top));
    assertThat(restored.toList()).hasSize(3);
  }

  @Test
  public void distinctRunfilesWithIdenticalFieldsAreNotMerged() throws Exception {
    Runfiles first = new Runfiles.Builder("same").build();
    Runfiles second = new Runfiles.Builder("same").build();
    assertThat(first).isNotSameInstanceAs(second);
    Runfiles restoredFirst = (Runfiles) decode(encode(first));
    Runfiles restoredSecond = (Runfiles) decode(encode(second));
    assertThat(restoredFirst).isNotSameInstanceAs(restoredSecond);
    assertThat(
            NestedSetBuilder.stableOrder().add(restoredFirst).add(restoredSecond).build().toList())
        .hasSize(2);
  }

  @Test
  public void oneOriginalObjectHasOneReaderIdentityAcrossSeparateRoots() throws Exception {
    Runfiles original = new Runfiles.Builder("shared").build();
    ByteString first = encode(original);
    ByteString second = encode(original);
    Object restoredFirst = decode(first);
    assertThat(decode(second)).isSameInstanceAs(restoredFirst);
    assertThat(restoredFirst).isNotSameInstanceAs(original);
  }

  @Test
  public void emptySingletonRemainsTheReaderSingleton() throws Exception {
    assertThat(decode(encode(Runfiles.EMPTY))).isSameInstanceAs(Runfiles.EMPTY);
  }

  private ByteString encode(Object value) throws Exception {
    var serialized = codecs.serializeMemoizedAndBlocking(compression, writer, value);
    if (serialized.getFutureToBlockWritesOn() != null) {
      serialized.getFutureToBlockWritesOn().get();
    }
    return serialized.getObject();
  }

  private Object decode(ByteString value) throws Exception {
    return codecs.deserializeMemoizedAndBlocking(compression, reader, value);
  }
}
