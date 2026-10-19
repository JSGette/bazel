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

import com.google.devtools.build.skyframe.InMemoryGraph;
import com.google.devtools.build.skyframe.InMemoryNodeEntry;
import com.google.devtools.build.skyframe.SkyValue;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.List;

/**
 * Quiescent value tier. No evaluator or graph reader may run between spill and restore.
 *
 * <p>The graph retains keys, versions and dependency topology. Only exact allowlisted value classes
 * without metadata are detached. Storage I/O never occurs under an entry monitor.
 */
final class IdleValueTier {
  interface Storage {
    void put(byte[] key, byte[] value) throws IOException;

    byte[] get(byte[] key) throws IOException;

    void clear() throws IOException;
  }

  private enum DetachedValue implements SkyValue {
    INSTANCE
  }

  private record Detached(InMemoryNodeEntry entry, byte[] key, byte[] digest) {}

  private final Storage storage;
  private final List<Detached> detached = new ArrayList<>();
  private final MessageDigest digest;
  private long nextId;
  private long serializedBytes;

  IdleValueTier(Storage storage) {
    this.storage = storage;
    try {
      digest = MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }

  int detachedCount() {
    return detached.size();
  }

  long serializedBytes() {
    return serializedBytes;
  }

  void spill(InMemoryGraph graph, long byteBudget) throws IOException {
    if (!detached.isEmpty()) {
      throw new IllegalStateException("restore before spilling again");
    }
    serializedBytes = 0;
    for (InMemoryNodeEntry entry : graph.getAllNodeEntries()) {
      if (!entry.isDone() || !entry.keepsEdges()) {
        continue;
      }
      SkyValue value = entry.getValueMaybeWithMetadata();
      if (!GlobValueCodec.supports(value)) {
        continue;
      }
      byte[] bytes;
      try {
        bytes = GlobValueCodec.encode(value);
      } catch (IOException e) {
        // Oversized or otherwise unencodable values remain pinned.
        continue;
      }
      if (bytes.length < 1024 || serializedBytes + bytes.length + 16 > byteBudget) {
        continue;
      }
      byte[] key = ByteBuffer.allocate(16).putLong(0).putLong(++nextId).array();
      byte[] checksum = digest.digest(bytes);
      storage.put(key, bytes); // Acknowledgement precedes detaching the only graph reference.
      if (entry.replaceDoneValueForMaintenance(value, DetachedValue.INSTANCE)) {
        detached.add(new Detached(entry, key, checksum));
        serializedBytes += bytes.length + 16;
      }
    }
  }

  /**
   * On failure the owner MUST discard the entire evaluator before any command proceeds. Some
   * entries may already be restored; missing/corrupt bytes must never be interpreted as a value.
   */
  void restore() throws IOException {
    for (Detached value : detached) {
      byte[] bytes = storage.get(value.key());
      if (bytes == null || !MessageDigest.isEqual(value.digest(), digest.digest(bytes))) {
        throw new IOException("missing or corrupt Skyframe spill value");
      }
      SkyValue decoded = GlobValueCodec.decode(bytes);
      if (!value.entry().replaceDoneValueForMaintenance(DetachedValue.INSTANCE, decoded)) {
        throw new IOException("Skyframe entry changed while quiescent");
      }
    }
    detached.clear();
    serializedBytes = 0;
    storage.clear();
  }
}
