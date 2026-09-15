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

import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore.MissingFingerprintValueException;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import com.google.devtools.build.lib.skyframe.serialization.StringKey;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.ExecutionException;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class PackedFingerprintValueStoreTest {

  private Path root;
  private PackedFingerprintValueStore store;

  @Before
  public void setUp() throws IOException {
    root = Files.createTempDirectory("packed-fingerprint-value-store");
    store = PackedFingerprintValueStore.create(root);
  }

  @Test
  public void putThenGet_roundTripsFingerprintKey() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    byte[] value = "hello".getBytes(UTF_8);

    store.put(key, value).get();

    assertThat(store.get(key).get()).isEqualTo(value);
  }

  @Test
  public void putThenGet_roundTripsStringKey() throws Exception {
    var key = new StringKey("some/path:with/characters");
    byte[] value = "hello".getBytes(UTF_8);

    store.put(key, value).get();

    assertThat(store.get(key).get()).isEqualTo(value);
  }

  @Test
  public void get_beforeFlush_readsBufferedEntry() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    byte[] value = "buffered".getBytes(UTF_8);
    store.put(key, value).get();

    // The pack file is still empty: the entry only exists in the write buffer.
    assertThat(Files.size(root.resolve("0.pack"))).isEqualTo(0);

    assertThat(store.get(key).get()).isEqualTo(value);
  }

  @Test
  public void get_missingKey_failsWithMissingFingerprintValueException() {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("absent");

    ExecutionException thrown = assertThrows(ExecutionException.class, () -> store.get(key).get());

    assertThat(thrown).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
  }

  @Test
  public void put_sameKeyTwice_lastValueWins() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");

    store.put(key, "first".getBytes(UTF_8)).get();
    store.put(key, "second".getBytes(UTF_8)).get();

    assertThat(store.get(key).get()).isEqualTo("second".getBytes(UTF_8));
  }

  @Test
  public void get_manyEntries_findsEachOne() throws Exception {
    for (int i = 0; i < 1000; i++) {
      store.put(PackedFingerprint.getFingerprintForTesting("key" + i), valueOfLength(i)).get();
    }

    for (int i = 0; i < 1000; i++) {
      assertThat(store.get(PackedFingerprint.getFingerprintForTesting("key" + i)).get())
          .isEqualTo(valueOfLength(i));
    }
  }

  @Test
  public void create_afterFlush_readsEntriesWrittenByPreviousStore() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    byte[] value = "durable".getBytes(UTF_8);
    store.put(key, value).get();
    store.flush();

    PackedFingerprintValueStore reopened = PackedFingerprintValueStore.create(root);

    assertThat(reopened.get(key).get()).isEqualTo(value);
  }

  @Test
  public void create_writesToFreshPack_leavingPreviousPackIntact() throws Exception {
    store.put(PackedFingerprint.getFingerprintForTesting("first"), new byte[10]).get();
    store.flush();

    PackedFingerprintValueStore second = PackedFingerprintValueStore.create(root);
    second.put(PackedFingerprint.getFingerprintForTesting("second"), new byte[10]).get();
    second.flush();

    assertThat(Files.exists(root.resolve("0.pack"))).isTrue();
    assertThat(Files.exists(root.resolve("1.pack"))).isTrue();
    assertThat(second.get(PackedFingerprint.getFingerprintForTesting("first")).get())
        .hasLength(10);
  }

  @Test
  public void create_indexRecordPastEndOfPack_dropsEntry() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    store.put(key, "value".getBytes(UTF_8)).get();
    store.flush();
    // Simulates a server that died after its index reached disk but before its pack did.
    Files.write(root.resolve("0.pack"), new byte[0]);

    PackedFingerprintValueStore reopened = PackedFingerprintValueStore.create(root);

    assertThrows(ExecutionException.class, () -> reopened.get(key).get());
  }

  @Test
  public void getStats_countsHitsAndMisses() throws Exception {
    PackedFingerprint present = PackedFingerprint.getFingerprintForTesting("present");
    PackedFingerprint absent = PackedFingerprint.getFingerprintForTesting("absent");
    store.put(present, new byte[10]).get();

    var unusedValue = store.get(present).get();
    assertThrows(ExecutionException.class, () -> store.get(absent).get());

    var stats = store.getStats();
    assertThat(stats.entriesWritten()).isEqualTo(1);
    assertThat(stats.entriesFound()).isEqualTo(1);
    assertThat(stats.entriesNotFound()).isEqualTo(1);
    assertThat(stats.valueBytesSent()).isEqualTo(10);
    assertThat(stats.valueBytesReceived()).isEqualTo(10);
  }

  @Test
  public void sweep_underLimit_deletesNothing() throws Exception {
    store.put(PackedFingerprint.getFingerprintForTesting("a"), new byte[100]).get();
    store.flush();

    var result = store.sweep(/* maxBytes= */ 1_000_000);

    assertThat(result.packsDeleted()).isEqualTo(0);
  }

  @Test
  public void sweep_overLimit_deletesOldPacksButNotTheOneBeingWritten() throws Exception {
    PackedFingerprint old = PackedFingerprint.getFingerprintForTesting("old");
    store.put(old, new byte[1000]).get();
    store.flush();

    PackedFingerprintValueStore current = PackedFingerprintValueStore.create(root);
    PackedFingerprint fresh = PackedFingerprint.getFingerprintForTesting("fresh");
    current.put(fresh, new byte[1000]).get();
    current.flush();

    var result = current.sweep(/* maxBytes= */ 1500);

    assertThat(result.packsDeleted()).isEqualTo(1);
    assertThat(Files.exists(root.resolve("0.pack"))).isFalse();
    assertThat(current.get(fresh).get()).hasLength(1000);
    assertThrows(ExecutionException.class, () -> current.get(old).get());
  }

  private static byte[] valueOfLength(int length) {
    byte[] value = new byte[length];
    for (int i = 0; i < length; i++) {
      value[i] = (byte) i;
    }
    return value;
  }
}
