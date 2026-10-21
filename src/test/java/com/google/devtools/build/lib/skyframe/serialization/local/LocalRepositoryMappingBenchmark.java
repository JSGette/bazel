// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.common.collect.ImmutableMap;
import com.google.devtools.build.lib.cmdline.RepositoryMapping;
import com.google.devtools.build.lib.cmdline.RepositoryName;
import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.concurrent.safeexecutor.SafeExecutorOwner;
import com.google.devtools.build.lib.skyframe.serialization.AutoRegistry;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueCache;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService;
import com.google.devtools.build.lib.skyframe.serialization.InMemoryFingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodecs;
import com.google.devtools.build.lib.skyframe.serialization.ValueSharingAdapter;
import com.google.protobuf.ByteString;
import java.lang.ref.Reference;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.IdentityHashMap;
import java.util.Locale;
import java.util.concurrent.Executors;

/** Isolates mapping duplication across roots; not a whole-build performance benchmark. */
public final class LocalRepositoryMappingBenchmark {
  public static void main(String[] args) throws Exception {
    boolean shared = Boolean.parseBoolean(args[0]);
    run(shared, 64, false);
    run(shared, 1024, true);
  }

  private static void run(boolean shared, int count, boolean report) throws Exception {
    var registry = AutoRegistry.get().getBuilder();
    if (shared) {
      registry.add(new ValueSharingAdapter<>(new LocalRepositoryMappingCodec()));
    }
    var codecs = new ObjectCodecs(registry.build());
    var compression = new CompressionServiceImpl();
    var store = new InMemoryFingerprintValueStore();
    var pool = Executors.newSingleThreadExecutor();
    var executor = new SafeExecutorOwner(pool);
    try {
      var writer =
          new FingerprintValueService(
              executor,
              store,
              new FingerprintValueCache(FingerprintValueCache.SyncMode.NOT_LINKED),
              FingerprintValueService.NONPROD_FINGERPRINTER);
      var reader =
          new FingerprintValueService(
              executor,
              store,
              new FingerprintValueCache(FingerprintValueCache.SyncMode.NOT_LINKED),
              FingerprintValueService.NONPROD_FINGERPRINTER);
      var entries = ImmutableMap.<String, RepositoryName>builder();
      for (int i = 0; i < 1502; i++) {
        entries.put("apparent_" + i, RepositoryName.createUnvalidated("canonical_" + i));
      }
      var original = RepositoryMapping.create(entries.buildOrThrow(), RepositoryName.MAIN);
      var encoded = new ArrayList<ByteString>();
      long started = System.nanoTime();
      long rootBytes = 0;
      for (int i = 0; i < count; i++) {
        var result = codecs.serializeMemoizedAndBlocking(compression, writer, original);
        if (result.getFutureToBlockWritesOn() != null) {
          result.getFutureToBlockWritesOn().get();
        }
        encoded.add(result.getObject());
        rootBytes += result.getObject().size();
      }
      double encodeMs = (System.nanoTime() - started) / 1e6;
      long before = liveHeap();
      var decoded = new ArrayList<RepositoryMapping>();
      started = System.nanoTime();
      for (var bytes : encoded) {
        decoded.add(
            (RepositoryMapping) codecs.deserializeMemoizedAndBlocking(compression, reader, bytes));
      }
      double decodeMs = (System.nanoTime() - started) / 1e6;
      long after = liveHeap();
      var identities = Collections.newSetFromMap(new IdentityHashMap<RepositoryMapping, Boolean>());
      identities.addAll(decoded);
      long distinctEntries = identities.stream().mapToLong(m -> m.entries().size()).sum();
      long storeBytes =
          store.fingerprintToContents.values().stream().mapToLong(ByteString::size).sum();
      if (report) {
        System.out.printf(
            Locale.ROOT,
            "{\"shared\":%s,\"roots\":%d,\"entries_per_mapping\":1502,\"encode_ms\":%.3f,"
                + "\"decode_ms\":%.3f,\"root_bytes\":%d,\"shared_store_bytes\":%d,"
                + "\"distinct_mappings\":%d,\"distinct_mapping_entries\":%d,"
                + "\"gc_live_heap_delta_bytes\":%d}%n",
            shared,
            count,
            encodeMs,
            decodeMs,
            rootBytes,
            storeBytes,
            identities.size(),
            distinctEntries,
            after - before);
      }
      Reference.reachabilityFence(decoded);
      Reference.reachabilityFence(encoded);
      Reference.reachabilityFence(store);
      Reference.reachabilityFence(writer);
      Reference.reachabilityFence(reader);
    } finally {
      pool.shutdown();
      if (!executor.awaitTermination(Duration.ofSeconds(5))) {
        throw new IllegalStateException("benchmark executor did not terminate");
      }
    }
  }

  private static long liveHeap() {
    System.gc();
    return Runtime.getRuntime().totalMemory() - Runtime.getRuntime().freeMemory();
  }
}
