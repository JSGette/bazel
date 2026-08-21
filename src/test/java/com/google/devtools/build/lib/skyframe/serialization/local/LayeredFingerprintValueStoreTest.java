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

import static com.google.common.truth.Truth.assertThat;
import static java.nio.charset.StandardCharsets.UTF_8;
import static org.junit.Assert.assertThrows;

import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore.MissingFingerprintValueException;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatuses.WriteStatus;
import java.util.concurrent.ExecutionException;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LayeredFingerprintValueStoreTest {
  private static final long MEMORY_BYTES = 1 << 20;

  private final CountingStore delegate = new CountingStore();
  private final LayeredFingerprintValueStore store =
      LayeredFingerprintValueStore.create(delegate, MEMORY_BYTES);

  @Test
  public void put_writesThroughToDelegate() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    byte[] value = "value".getBytes(UTF_8);

    store.put(key, value).get();

    assertThat(delegate.inner.get(key).get()).isEqualTo(value);
  }

  @Test
  public void get_afterPut_servedFromMemoryWithoutReadingDelegate() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    byte[] value = "value".getBytes(UTF_8);
    store.put(key, value).get();

    assertThat(store.get(key).get()).isEqualTo(value);

    assertThat(delegate.gets).isEqualTo(0);
    assertThat(store.getMemoryStats().hits()).isEqualTo(1);
    assertThat(store.getMemoryStats().misses()).isEqualTo(0);
  }

  @Test
  public void get_promotesDelegateValueIntoMemory() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    byte[] value = "value".getBytes(UTF_8);
    delegate.inner.put(key, value);

    assertThat(store.get(key).get()).isEqualTo(value);
    assertThat(store.get(key).get()).isEqualTo(value);

    assertThat(delegate.gets).isEqualTo(1);
    assertThat(store.getMemoryStats().hits()).isEqualTo(1);
    assertThat(store.getMemoryStats().misses()).isEqualTo(1);
  }

  @Test
  public void get_missingEverywhere_failsWithMissingFingerprintValueException() {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("absent");

    ExecutionException thrown = assertThrows(ExecutionException.class, () -> store.get(key).get());

    assertThat(thrown).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
    assertThat(store.getMemoryStats().hits()).isEqualTo(0);
  }

  /** Delegate that counts reads, so tests can tell which tier answered. */
  private static final class CountingStore implements FingerprintValueStore {
    private final InMemoryFingerprintValueStore inner = FingerprintValueStore.inMemoryStore();
    private int gets = 0;

    @Override
    public WriteStatus put(KeyBytesProvider fingerprint, byte[] serializedBytes) {
      return inner.put(fingerprint, serializedBytes);
    }

    @Override
    public ListenableFuture<byte[]> get(KeyBytesProvider fingerprint) {
      gets++;
      return inner.get(fingerprint);
    }
  }
}
