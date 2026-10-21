// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;

import com.google.common.collect.ImmutableMap;
import com.google.devtools.build.lib.cmdline.RepositoryMapping;
import com.google.devtools.build.lib.cmdline.RepositoryName;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.AutoRegistry;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.InMemoryFingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecs;
import com.google.devtools.build.lib.skyframe.serialization.ValueSharingAdapter;
import com.google.protobuf.ByteString;
import java.util.ArrayList;
import java.util.concurrent.Executors;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalRepositoryMappingCodecTest {
  private final CompressionServiceImpl compression = new CompressionServiceImpl();
  private final InMemoryFingerprintValueStore store = new InMemoryFingerprintValueStore();
  private final FingerprintValueService writer = FingerprintValueService.createForTesting(store);
  private final FingerprintValueService reader = FingerprintValueService.createForTesting(store);
  private final ObjectCodecs codecs =
      new ObjectCodecs(
          AutoRegistry.get()
              .getBuilder()
              .add(new ValueSharingAdapter<>(new LocalRepositoryMappingCodec()))
              .build());

  @Test
  public void separateRootsShareOneMappingIncludingLargeEntryMap() throws Exception {
    var entries = ImmutableMap.<String, RepositoryName>builder();
    for (int i = 0; i < 1502; i++) {
      entries.put("apparent_" + i, RepositoryName.createUnvalidated("canonical_" + i));
    }
    var mapping = RepositoryMapping.create(entries.buildOrThrow(), RepositoryName.MAIN);
    var first = decode(encode(writer, mapping));
    for (int i = 0; i < 32; i++) {
      var restored = decode(encode(writer, mapping));
      assertThat(restored).isSameInstanceAs(first);
      assertThat(restored.entries()).isSameInstanceAs(first.entries());
    }
    assertThat(first).isEqualTo(mapping);
    assertThat(first.hashCode()).isEqualTo(mapping.hashCode());
  }

  @Test
  public void independentWritersAndConcurrentReadersAgreeOnValue() throws Exception {
    var entries = ImmutableMap.of("alias", RepositoryName.createUnvalidated("canonical"));
    var first = encode(writer, RepositoryMapping.create(entries, RepositoryName.MAIN));
    var second =
        encode(
            FingerprintValueService.createForTesting(store),
            RepositoryMapping.create(entries, RepositoryName.MAIN));
    assertThat(second).isEqualTo(first);
    try (var pool = Executors.newFixedThreadPool(4)) {
      var futures = new ArrayList<java.util.concurrent.Future<RepositoryMapping>>();
      for (int i = 0; i < 16; i++) {
        futures.add(pool.submit(() -> decode(second)));
      }
      var restored = futures.get(0).get();
      for (var future : futures) {
        assertThat(future.get()).isSameInstanceAs(restored);
      }
    }
  }

  @Test
  public void contextAndChangedEntriesRemainDistinct() throws Exception {
    var entries = ImmutableMap.of("alias", RepositoryName.createUnvalidated("first"));
    var original = decode(encode(writer, RepositoryMapping.create(entries, RepositoryName.MAIN)));
    var otherContext = RepositoryMapping.create(entries, RepositoryName.createUnvalidated("other"));
    var restoredContext = decode(encode(writer, otherContext));
    assertThat(restoredContext).isEqualTo(otherContext);
    assertThat(restoredContext).isNotSameInstanceAs(original);
    var changed =
        decode(
            encode(
                writer,
                RepositoryMapping.create(
                    ImmutableMap.of("alias", RepositoryName.createUnvalidated("second")),
                    RepositoryName.MAIN)));
    assertThat(changed).isNotSameInstanceAs(original);
    assertThat(changed.get("alias")).isEqualTo(RepositoryName.createUnvalidated("second"));
  }

  @Test
  public void cachedInverseAndIterationPrecedenceSurviveRoundTrip() throws Exception {
    var repo = RepositoryName.createUnvalidated("canonical");
    var entries = ImmutableMap.of("z_first", repo, "a_second", repo);
    var mapping = RepositoryMapping.create(entries, RepositoryName.MAIN).withCachedInverseMap();
    var restored = decode(encode(writer, mapping));
    assertThat(restored.getClass()).isEqualTo(mapping.getClass());
    assertThat(restored.getInverse(repo)).hasValue("z_first");
    assertThat(restored.entries().keySet()).containsExactly("z_first", "a_second").inOrder();
    var reversed =
        decode(
            encode(
                writer,
                RepositoryMapping.create(
                        ImmutableMap.of("a_second", repo, "z_first", repo), RepositoryName.MAIN)
                    .withCachedInverseMap()));
    assertThat(reversed.getInverse(repo)).hasValue("a_second");
    assertThat(reversed).isNotSameInstanceAs(restored);
  }

  @Test
  public void emptyMappingSurvivesRoundTrip() throws Exception {
    var mapping = RepositoryMapping.create(ImmutableMap.of(), RepositoryName.MAIN);
    var restored = decode(encode(writer, mapping));
    assertThat(restored).isEqualTo(mapping);
    assertThat(restored.entries()).isEmpty();
  }

  private ByteString encode(FingerprintValueService service, RepositoryMapping mapping)
      throws Exception {
    var result = codecs.serializeMemoizedAndBlocking(compression, service, mapping);
    if (result.getFutureToBlockWritesOn() != null) {
      result.getFutureToBlockWritesOn().get();
    }
    return result.getObject();
  }

  private RepositoryMapping decode(ByteString value) throws Exception {
    return (RepositoryMapping) codecs.deserializeMemoizedAndBlocking(compression, reader, value);
  }
}
