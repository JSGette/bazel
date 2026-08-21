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
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.util.concurrent.Futures.immediateFuture;
import static com.google.common.util.concurrent.MoreExecutors.directExecutor;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.google.common.util.concurrent.Futures;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.WriteStatus;
import java.io.IOException;
import java.util.concurrent.atomic.LongAdder;

/**
 * A {@link FingerprintValueStore} that serves reads from a bounded in-memory tier and falls back to
 * a slower delegate.
 *
 * <p>Writes go to both tiers, so an entry written by one command is served from memory for the rest
 * of the server's lifetime, or until evicted.
 *
 * <p>This only removes I/O. Deserializing the bytes and inserting the resulting nodes into Skyframe
 * still happens on every restore; the memoization of deserialized values is the separate concern of
 * {@link com.google.devtools.build.lib.skyframe.serialization.FingerprintValueCache}.
 */
public final class LayeredFingerprintValueStore implements FingerprintValueStore {
  private final FingerprintValueStore delegate;
  private final Cache<KeyBytesProvider, byte[]> memory;

  private final LongAdder memoryHits = new LongAdder();
  private final LongAdder memoryMisses = new LongAdder();

  /** Hit counts of the in-memory tier. */
  public record MemoryStats(long hits, long misses, long entries) {}

  private LayeredFingerprintValueStore(
      FingerprintValueStore delegate, Cache<KeyBytesProvider, byte[]> memory) {
    this.delegate = delegate;
    this.memory = memory;
  }

  public static LayeredFingerprintValueStore create(
      FingerprintValueStore delegate, long memoryBytes) {
    Cache<KeyBytesProvider, byte[]> memory =
        Caffeine.newBuilder()
            .maximumWeight(memoryBytes)
            .weigher((KeyBytesProvider key, byte[] value) -> value.length)
            .build();
    return new LayeredFingerprintValueStore(delegate, memory);
  }

  @Override
  public WriteStatus put(KeyBytesProvider fingerprint, byte[] serializedBytes) {
    memory.put(fingerprint, serializedBytes);
    return delegate.put(fingerprint, serializedBytes);
  }

  @Override
  public ListenableFuture<byte[]> get(KeyBytesProvider fingerprint) throws IOException {
    byte[] cached = memory.getIfPresent(fingerprint);
    if (cached != null) {
      memoryHits.increment();
      return immediateFuture(cached);
    }
    memoryMisses.increment();
    return Futures.transform(
        delegate.get(fingerprint),
        serializedBytes -> {
          memory.put(fingerprint, serializedBytes);
          return serializedBytes;
        },
        directExecutor());
  }

  @Override
  public Stats getStats() {
    return delegate.getStats();
  }

  public MemoryStats getMemoryStats() {
    return new MemoryStats(memoryHits.sum(), memoryMisses.sum(), memory.estimatedSize());
  }

  public FingerprintValueStore getDelegate() {
    return delegate;
  }
}
