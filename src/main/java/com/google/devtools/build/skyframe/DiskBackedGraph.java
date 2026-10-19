// Copyright 2023 The Bazel Authors. All rights reserved.
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
package com.google.devtools.build.skyframe;

import static com.google.common.base.Preconditions.checkArgument;

import com.google.common.collect.ImmutableList;
import com.google.devtools.build.skyframe.IncrementalInMemoryNodeEntry.DoneSnapshot;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.ByteBuffer;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.locks.ReentrantLock;
import java.util.zip.CRC32C;

/**
 * Experimental incremental graph with stable handles and demand-loaded done-node records.
 *
 * <p>The resident limit applies to completed nodes supported by the value codec. In-flight nodes
 * and unsupported values stay pinned. Keys, handles and the edge-key dictionary remain in memory;
 * this is not a bound on total heap. Callers may also retain values returned by graph APIs.
 *
 * <p>There is no bulk restore. Metadata scans do not load records. The existing incremental node
 * implementation owns evaluation semantics, and cannot be evicted while evaluating. Storage errors
 * fail the operation: losing an authoritative record must never look like a graph cache miss.
 *
 * <p>The supplied codec must preserve local evaluation semantics, including wrappers and object
 * identity where required. Remote analysis-cache codecs are not interchangeable with this contract.
 * The store and key dictionary belong to one graph lifetime; records cannot be reopened by a new
 * graph. Pooled interning is disabled so that loading package values cannot perform I/O under the
 * global label-interner lock.
 */
public final class DiskBackedGraph extends InMemoryGraphImpl {
  private static final int MAGIC = 0x534b4701;
  private static final int MAX_RECORD_BYTES = 12 * 1024 * 1024;

  /**
   * Atomic replacement and exact retrieval of opaque records. Implementations must be thread-safe.
   */
  public interface Store {
    void put(long id, byte[] record) throws IOException;

    byte[] get(long id) throws IOException;
  }

  /** An explicit allowlist of lossless, local-value codecs; unsupported values are pinned. */
  public interface ValueCodec {
    boolean supports(SkyValue value);

    byte[] encode(SkyValue value) throws IOException;

    SkyValue decode(byte[] bytes) throws IOException;
  }

  /** Counts include pinned nodes separately from the bounded eligible working set. */
  public record Statistics(
      int nodes,
      int resident,
      int pinned,
      int cold,
      long pageIns,
      long evictions,
      long bytesRead,
      long bytesWritten) {}

  private final Store store;
  private final ValueCodec codec;
  private final int residentLimit;
  private final AtomicLong nextId = new AtomicLong();
  private final AtomicLong pageIns = new AtomicLong();
  private final AtomicLong evictions = new AtomicLong();
  private final AtomicLong bytesRead = new AtomicLong();
  private final AtomicLong bytesWritten = new AtomicLong();
  // Never acquire an entry lock while holding this map's monitor.
  private final LinkedHashMap<DiskBackedNodeEntry, Boolean> residents =
      new LinkedHashMap<>(16, 0.75f, true);
  private final ReentrantLock evictionLock = new ReentrantLock();
  // Edge keys may not yet have node entries. IDs are never recycled during a graph lifetime.
  private final Map<SkyKey, Integer> keyIds = new HashMap<>();
  private final ArrayList<SkyKey> keys = new ArrayList<>();

  public DiskBackedGraph(Store store, ValueCodec codec, int residentLimit) {
    super(/* usePooledInterning= */ false);
    checkArgument(residentLimit >= 0);
    this.store = java.util.Objects.requireNonNull(store);
    this.codec = java.util.Objects.requireNonNull(codec);
    this.residentLimit = residentLimit;
  }

  @Override
  protected InMemoryNodeEntry newNodeEntry(SkyKey key) {
    return new DiskBackedNodeEntry(this, key, nextId.getAndIncrement());
  }

  boolean eligible(IncrementalInMemoryNodeEntry entry) {
    return entry.isDone()
        && supportedVersion(entry.getVersion())
        && supportedVersion(entry.lastEvaluatedVersion())
        && codec.supports(entry.getValueMaybeWithMetadata());
  }

  private static boolean supportedVersion(Version version) {
    return version instanceof IntVersion
        || version == Version.minimal()
        || version == Version.constant();
  }

  void accessed(DiskBackedNodeEntry entry) {
    synchronized (residents) {
      if (entry.eligible && !entry.removed) {
        residents.put(entry, Boolean.TRUE);
      } else {
        residents.remove(entry);
      }
    }
  }

  /**
   * Enforces the completed-node bound after operations. A concurrent operation can temporarily
   * exceed the limit; its own completion trims again. Busy entries are skipped, never waited for
   * while an eviction is holding another entry's lock.
   */
  void trim() {
    evictionLock.lock();
    try {
      int attempts;
      synchronized (residents) {
        attempts = residents.size();
      }
      while (attempts-- > 0) {
        DiskBackedNodeEntry victim;
        synchronized (residents) {
          if (residents.size() <= residentLimit) {
            return;
          }
          victim = residents.keySet().iterator().next();
          residents.remove(victim);
        }
        if (!victim.lock.tryLock()) {
          accessed(victim);
          continue;
        }
        try {
          synchronized (residents) {
            // An operation may have reinserted this handle before we acquired its lock.
            residents.remove(victim);
          }
          if (victim.delegate == null || !victim.eligible || victim.removed) {
            continue;
          }
          if (!victim.persisted) {
            byte[] record = encode(victim.id, victim.delegate.snapshotDone());
            store.put(victim.id, record);
            bytesWritten.addAndGet(record.length);
            victim.persisted = true;
          }
          victim.rememberValue();
          victim.delegate = null;
          evictions.incrementAndGet();
        } catch (IOException e) {
          accessed(victim); // The live node is still authoritative if a write fails.
          throw new UncheckedIOException("Cannot spill Skyframe node " + victim.getKey(), e);
        } finally {
          victim.lock.unlock();
        }
      }
    } finally {
      evictionLock.unlock();
    }
  }

  IncrementalInMemoryNodeEntry load(DiskBackedNodeEntry entry) {
    try {
      byte[] bytes = store.get(entry.id);
      DoneSnapshot snapshot = decode(entry.id, bytes);
      SkyValue retained = entry.rememberedValue();
      if (retained != null) {
        // Preserve identity when an evaluator or caller still holds the original value.
        snapshot =
            new DoneSnapshot(
                retained, snapshot.version(), snapshot.directDeps(), snapshot.reverseDeps());
      }
      if (!snapshot.version().lastChanged().equals(entry.getVersion())
          || !snapshot.version().lastEvaluated().equals(entry.lastEvaluatedVersion())) {
        throw new IOException("record version does not match node handle");
      }
      pageIns.incrementAndGet();
      bytesRead.addAndGet(bytes.length);
      return IncrementalInMemoryNodeEntry.fromSnapshot(entry.getKey(), snapshot);
    } catch (IOException e) {
      throw new UncheckedIOException("Cannot load Skyframe node " + entry.getKey(), e);
    }
  }

  public Statistics statistics() {
    int resident = 0;
    int pinned = 0;
    for (InMemoryNodeEntry node : nodeMap.values()) {
      DiskBackedNodeEntry entry = (DiskBackedNodeEntry) node;
      if (entry.delegate != null) {
        resident++;
        if (!entry.eligible) {
          pinned++;
        }
      }
    }
    int nodes = nodeMap.size();
    return new Statistics(
        nodes,
        resident,
        pinned,
        nodes - resident,
        pageIns.get(),
        evictions.get(),
        bytesRead.get(),
        bytesWritten.get());
  }

  @Override
  public void remove(SkyKey key) {
    DiskBackedNodeEntry entry = (DiskBackedNodeEntry) nodeMap.remove(key);
    if (entry != null) {
      forget(entry);
    }
  }

  @Override
  public void removeIfDone(SkyKey key) {
    nodeMap.computeIfPresent(
        key,
        (unused, node) -> {
          DiskBackedNodeEntry entry = (DiskBackedNodeEntry) node;
          entry.lock.lock();
          try {
            if (!entry.isDone()) {
              return entry;
            }
            forget(entry);
            return null;
          } finally {
            entry.lock.unlock();
          }
        });
  }

  private void forget(DiskBackedNodeEntry entry) {
    entry.removed = true;
    synchronized (residents) {
      residents.remove(entry);
    }
    // Keep a removed handle usable for readers already holding it. Its record ID is never reused.
    // Reclaiming obsolete records requires store compaction, separately from node eviction.
  }

  /** Discards the graph without touching cold records. Only safe after all graph readers stop. */
  @Override
  public void cleanupInterningPools() {
    synchronized (residents) {
      residents.clear();
    }
    nodeMap.clear();
    synchronized (keyIds) {
      keyIds.clear();
      keys.clear();
    }
  }

  private int keyId(SkyKey key) {
    synchronized (keyIds) {
      return keyIds.computeIfAbsent(
          key,
          k -> {
            int id = keys.size();
            keys.add(k);
            return id;
          });
    }
  }

  private SkyKey readKey(DataInputStream in) throws IOException {
    int id = in.readInt();
    synchronized (keyIds) {
      if (id < 0 || id >= keys.size()) {
        throw new IOException("unknown edge key ID");
      }
      return keys.get(id);
    }
  }

  private byte[] encode(long id, DoneSnapshot snapshot) throws IOException {
    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    try (DataOutputStream out = new DataOutputStream(bytes)) {
      out.writeInt(MAGIC);
      out.writeLong(id);
      writeVersion(out, snapshot.version().lastChanged());
      writeVersion(out, snapshot.version().lastEvaluated());
      GroupedDeps deps = GroupedDeps.decompress(snapshot.directDeps());
      out.writeInt(deps.numGroups());
      for (java.util.List<SkyKey> group : deps) {
        out.writeInt(group.size());
        for (SkyKey key : group) {
          out.writeInt(keyId(key));
        }
      }
      out.writeInt(snapshot.reverseDeps().size());
      for (SkyKey key : snapshot.reverseDeps()) {
        out.writeInt(keyId(key));
      }
      byte[] value = codec.encode(snapshot.value());
      out.writeInt(value.length);
      out.write(value);
    }
    CRC32C checksum = new CRC32C();
    checksum.update(bytes.toByteArray());
    new DataOutputStream(bytes).writeLong(checksum.getValue());
    if (bytes.size() > MAX_RECORD_BYTES) {
      throw new IOException("node record exceeds spill limit");
    }
    return bytes.toByteArray();
  }

  private DoneSnapshot decode(long id, byte[] bytes) throws IOException {
    if (bytes.length < Long.BYTES || bytes.length > MAX_RECORD_BYTES) {
      throw new IOException("invalid node record size");
    }
    int payloadSize = bytes.length - Long.BYTES;
    CRC32C checksum = new CRC32C();
    checksum.update(bytes, 0, payloadSize);
    if (checksum.getValue() != ByteBuffer.wrap(bytes, payloadSize, Long.BYTES).getLong()) {
      throw new IOException("node record checksum mismatch");
    }
    try (DataInputStream in =
        new DataInputStream(new ByteArrayInputStream(bytes, 0, payloadSize))) {
      if (in.readInt() != MAGIC || in.readLong() != id) {
        throw new IOException("invalid node record header");
      }
      NodeVersion version = NodeVersionGetter.get(readVersion(in), readVersion(in));
      GroupedDeps deps = new GroupedDeps();
      int groups = count(in, Integer.BYTES);
      for (int i = 0; i < groups; i++) {
        int size = count(in, Integer.BYTES);
        if (size == 0) {
          throw new IOException("empty dependency group");
        }
        ImmutableList.Builder<SkyKey> group = ImmutableList.builderWithExpectedSize(size);
        for (int j = 0; j < size; j++) {
          group.add(readKey(in));
        }
        deps.appendGroup(group.build());
      }
      int reverseCount = count(in, Integer.BYTES);
      ImmutableList.Builder<SkyKey> reverseDeps =
          ImmutableList.builderWithExpectedSize(reverseCount);
      for (int i = 0; i < reverseCount; i++) {
        reverseDeps.add(readKey(in));
      }
      int size = count(in, 1);
      SkyValue value = codec.decode(in.readNBytes(size));
      if (value == null || !codec.supports(value) || in.available() != 0) {
        throw new IOException("invalid node value or trailing record bytes");
      }
      return new DoneSnapshot(value, version, deps.compress(), reverseDeps.build());
    }
  }

  private static int count(DataInputStream in, int minimumBytes) throws IOException {
    int count = in.readInt();
    if (count < 0 || count > in.available() / minimumBytes) {
      throw new IOException("invalid node record length");
    }
    return count;
  }

  private static void writeVersion(DataOutputStream out, Version version) throws IOException {
    if (version instanceof IntVersion integer) {
      out.writeByte(2);
      out.writeLong(integer.getVal());
    } else {
      out.writeByte(version == Version.minimal() ? 0 : 1);
    }
  }

  private static Version readVersion(DataInputStream in) throws IOException {
    return switch (in.readUnsignedByte()) {
      case 0 -> Version.minimal();
      case 1 -> Version.constant();
      case 2 -> IntVersion.of(in.readLong());
      default -> throw new IOException("invalid node version");
    };
  }
}
