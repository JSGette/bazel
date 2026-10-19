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

import com.google.common.hash.Hashing;
import com.google.common.util.concurrent.Futures;
import com.google.common.util.concurrent.ListenableFuture;
import com.google.devtools.build.lib.skyframe.serialization.FingerprintValueStore;
import com.google.devtools.build.lib.skyframe.serialization.KeyBytesProvider;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatus;
import com.google.devtools.build.lib.skyframe.serialization.WriteStatuses;
import java.io.IOException;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicLong;

/** Fingerprint-addressed shared chunks in the same exclusively owned Rust disk store. */
final class SkystoreFingerprintStore implements FingerprintValueStore {
  private final SkystoreProcess process;
  private final AtomicLong reads = new AtomicLong();
  private final AtomicLong writes = new AtomicLong();

  SkystoreFingerprintStore(SkystoreProcess process) {
    this.process = process;
  }

  private static byte[] key(KeyBytesProvider fingerprint) {
    byte[] key = Arrays.copyOf(Hashing.sha256().hashBytes(fingerprint.toBytes()).asBytes(), 16);
    // Reserve a namespace separate from node keys (which begin with 0x53).
    // The remaining 120 bits are a content-addressed key; Rust also checks payload CRC32C.
    key[0] = (byte) 0xfe;
    return key;
  }

  @Override
  public WriteStatus put(KeyBytesProvider fingerprint, byte[] bytes) {
    try {
      process.put(key(fingerprint), bytes);
      writes.incrementAndGet();
      return WriteStatuses.immediateWriteStatus();
    } catch (IOException e) {
      return WriteStatuses.immediateFailedWriteStatus(e);
    }
  }

  @Override
  public ListenableFuture<byte[]> get(KeyBytesProvider fingerprint) {
    try {
      byte[] bytes = process.get(key(fingerprint));
      reads.incrementAndGet();
      return Futures.immediateFuture(bytes);
    } catch (IOException e) {
      // NestedSetStore publishes its shared future before fetching. Complete it exceptionally
      // instead of throwing synchronously and leaving subsequent readers blocked forever.
      return Futures.immediateFailedFuture(e);
    }
  }

  long reads() {
    return reads.get();
  }

  long writes() {
    return writes.get();
  }
}
