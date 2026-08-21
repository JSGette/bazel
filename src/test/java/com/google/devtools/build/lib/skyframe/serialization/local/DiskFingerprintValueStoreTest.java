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
import java.nio.file.attribute.FileTime;
import java.util.concurrent.ExecutionException;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class DiskFingerprintValueStoreTest {

  private Path root;
  private DiskFingerprintValueStore store;

  @Before
  public void setUp() throws IOException {
    root = Files.createTempDirectory("disk-fingerprint-value-store");
    store = DiskFingerprintValueStore.create(root);
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
  public void get_missingKey_failsWithMissingFingerprintValueException() {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("absent");

    ExecutionException thrown = assertThrows(ExecutionException.class, () -> store.get(key).get());

    assertThat(thrown).hasCauseThat().isInstanceOf(MissingFingerprintValueException.class);
  }

  @Test
  public void put_overwritesExistingValue() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");

    store.put(key, "first".getBytes(UTF_8)).get();
    store.put(key, "second".getBytes(UTF_8)).get();

    assertThat(store.get(key).get()).isEqualTo("second".getBytes(UTF_8));
  }

  @Test
  public void get_afterReopeningStoreAtSameRoot_stillFindsValue() throws Exception {
    PackedFingerprint key = PackedFingerprint.getFingerprintForTesting("key");
    byte[] value = "durable".getBytes(UTF_8);
    store.put(key, value).get();

    DiskFingerprintValueStore reopened = DiskFingerprintValueStore.create(root);

    assertThat(reopened.get(key).get()).isEqualTo(value);
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

    var result = store.sweep(/* maxBytes= */ 1000);

    assertThat(result.entriesDeleted()).isEqualTo(0);
    assertThat(result.bytesAfter()).isEqualTo(100);
  }

  @Test
  public void sweep_overLimit_deletesOldestFirst() throws Exception {
    PackedFingerprint oldest = PackedFingerprint.getFingerprintForTesting("oldest");
    PackedFingerprint middle = PackedFingerprint.getFingerprintForTesting("middle");
    PackedFingerprint newest = PackedFingerprint.getFingerprintForTesting("newest");
    store.put(oldest, new byte[100]).get();
    store.put(middle, new byte[100]).get();
    store.put(newest, new byte[100]).get();
    setModificationTime(oldest, 1000);
    setModificationTime(middle, 2000);
    setModificationTime(newest, 3000);

    var result = store.sweep(/* maxBytes= */ 150);

    assertThat(result.entriesScanned()).isEqualTo(3);
    assertThat(result.entriesDeleted()).isEqualTo(2);
    assertThat(result.bytesAfter()).isEqualTo(100);
    assertThat(store.get(newest).get()).hasLength(100);
    assertThrows(ExecutionException.class, () -> store.get(oldest).get());
    assertThrows(ExecutionException.class, () -> store.get(middle).get());
  }

  private void setModificationTime(PackedFingerprint key, long millis) throws IOException {
    Files.setLastModifiedTime(store.pathFor(key), FileTime.fromMillis(millis));
  }
}
