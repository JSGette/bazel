// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.READ;

import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.nio.channels.Channels;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.FileTime;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.Set;
import java.util.function.LongSupplier;

/** Collects inactive publications without breaking live readers or publication parent closure. */
final class LocalCacheGarbageCollector {
  private static final int MANIFEST_MAGIC = 0x534b5334;
  private static final long MAX_MANIFEST_BYTES = 64L * 1024 * 1024 + 32;
  private static final long MAX_SCAN_BYTES = 2L * 1024 * 1024 * 1024;
  private static final long MAX_RECONCILE_BYTES = 128L * 1024 * 1024;
  private static final int MAX_ENTRIES = 131072;
  private static final int MAX_PUBLICATIONS = 16384;
  private static final int MAX_EDGES = 1_000_000;
  private static final int MAX_CLOSURE_VISITS = 2_000_000;

  record Policy(long maxBytes, long maxAgeMillis) {
    Policy {
      if (maxBytes < 0 || maxAgeMillis < 0) {
        throw new IllegalArgumentException("negative local SkyCache collection policy");
      }
    }

    boolean enabled() {
      return maxBytes > 0 || maxAgeMillis > 0;
    }
  }

  record Stats(
      String status,
      int publications,
      long knownBytesBefore,
      long knownBytesRetained,
      long protectedBytes,
      int activeSlots,
      int foreignEntries,
      int deletedPublications,
      int deletedPacks,
      int deletedPendingFiles,
      long deletedBytes,
      long scanBytes,
      long elapsedNanos,
      long catalogNanos) {
    String json() {
      return String.format(
          java.util.Locale.ROOT,
          "{\"status\":\"%s\",\"publications\":%d,\"known_bytes_before\":%s,"
              + "\"known_bytes_retained\":%s,\"protected_bytes\":%d,\"active_slots\":%d,"
              + "\"foreign_entries\":%d,\"deleted_publications\":%d,\"deleted_packs\":%d,"
              + "\"deleted_pending_files\":%d,\"deleted_logical_bytes\":%d,\"manifest_scan_bytes\":%d,"
              + "\"elapsed_ms\":%.3f,\"catalog_ms\":%.3f}",
          status,
          publications,
          available(knownBytesBefore),
          available(knownBytesRetained),
          protectedBytes,
          activeSlots,
          foreignEntries,
          deletedPublications,
          deletedPacks,
          deletedPendingFiles,
          deletedBytes,
          scanBytes,
          elapsedNanos / 1e6,
          catalogNanos / 1e6);
    }

    private static String available(long value) {
      return value < 0 ? "null" : Long.toString(value);
    }
  }

  // A deterministic failure-injection seam for interruption/process-death tests. The callback is
  // after a manifest unlink has been synced and before any of its pack files may be removed.
  interface Observer {
    default void afterScan() throws IOException, InterruptedException {}

    default void afterUnpublish(Path manifest) throws IOException, InterruptedException {}
  }

  private record Stamp(long size, FileTime modified, Object key) {
    static Stamp regular(Path path) throws IOException {
      var attributes = Files.readAttributes(path, BasicFileAttributes.class, NOFOLLOW_LINKS);
      if (!attributes.isRegularFile()) {
        throw new IOException("non-regular managed cache file: " + path);
      }
      return new Stamp(attributes.size(), attributes.lastModifiedTime(), attributes.fileKey());
    }
  }

  private record Publication(
      String id, String namespace, Path path, Stamp stamp, List<String> parents) {}

  private record Pack(String id, Path path, Map<Path, Stamp> files, long bytes, boolean safe) {}

  private record Inventory(Map<String, Path> manifests, Map<Path, Stamp> pending, int foreign) {}

  private static final class Counters {
    final long start = System.nanoTime();
    int entries;
    int publications;
    int foreign;
    int edges;
    int closureVisits;
    int activeSlots;
    int deletedPublications;
    int deletedPacks;
    int deletedPending;
    long scanBytes;
    long before = -1;
    long retained = -1;
    long protectedBytes;
    long deletedBytes;
    long catalogNanos;

    void entry() throws IOException, InterruptedException {
      interrupted();
      if (++entries > MAX_ENTRIES) {
        throw new IOException("local SkyCache collection entry limit exceeded");
      }
    }

    Stats result(String status) {
      return new Stats(
          status,
          publications,
          before,
          retained,
          protectedBytes,
          activeSlots,
          foreign,
          deletedPublications,
          deletedPacks,
          deletedPending,
          deletedBytes,
          scanBytes,
          System.nanoTime() - start,
          catalogNanos);
    }
  }

  private final Path managed;
  private final Path publications;
  private final Path objects;
  private final Policy policy;
  private final LongSupplier clock;
  private final Observer observer;

  LocalCacheGarbageCollector(Path root, Policy policy) {
    this(root, policy, System::currentTimeMillis, new Observer() {});
  }

  LocalCacheGarbageCollector(Path root, Policy policy, LongSupplier clock, Observer observer) {
    this.managed = root.toAbsolutePath().normalize().resolve(LocalCacheSession.MANAGED_DIRECTORY);
    this.publications = managed.resolve("publications-v4");
    this.objects = managed.resolve("objects/segmented-v1");
    this.policy = policy;
    this.clock = clock;
    this.observer = observer;
  }

  Stats run() throws IOException, InterruptedException {
    var counters = new Counters();
    if (!policy.enabled()) {
      return counters.result("disabled");
    }
    if (!Files.exists(managed, NOFOLLOW_LINKS)) {
      return counters.result("absent");
    }
    try (var leases = LocalCacheLeaseManager.open(managed);
        var coordinator = leases.tryCollector()) {
      if (coordinator == null) {
        return counters.result("collector_busy");
      }
      directory(publications);
      directory(objects.getParent());
      directory(objects);
      // Only collectors remove immutable manifests. The coordinator excludes other collectors;
      // ordinary publishers/readers keep running while this potentially large checksum scan runs.
      Inventory first = inventory(counters);
      Map<String, Publication> graph = readPublications(first, counters);
      observer.afterScan();
      String status;
      long catalogStart = System.nanoTime();
      try (var catalog = leases.tryExclusiveCatalog()) {
        if (catalog == null) {
          return counters.result("catalog_busy");
        }
        try {
          status = reconcileAndCollect(leases, graph, counters);
        } finally {
          counters.catalogNanos += System.nanoTime() - catalogStart;
        }
      }
      return counters.result(status);
    }
  }

  private String reconcileAndCollect(
      LocalCacheLeaseManager leases, Map<String, Publication> graph, Counters counters)
      throws IOException, InterruptedException {
    Inventory current = inventory(counters);
    long addedBytes = 0;
    for (var entry : current.manifests().entrySet()) {
      Publication prior = graph.get(entry.getKey());
      Stamp stamp = Stamp.regular(entry.getValue());
      if (prior != null) {
        if (!prior.path().equals(entry.getValue()) || !prior.stamp().equals(stamp)) {
          return "catalog_changed";
        }
      } else {
        if (stamp.size() > MAX_RECONCILE_BYTES - addedBytes) {
          return "catalog_changed";
        }
        addedBytes += stamp.size();
        graph.put(entry.getKey(), readPublication(entry.getValue(), counters));
      }
    }
    if (!current.manifests().keySet().equals(graph.keySet())) {
      return "catalog_changed";
    }
    counters.publications = graph.size();
    counters.foreign = current.foreign();
    List<String> order = childFirstOrder(graph);
    return collect(leases, graph, order, current, counters);
  }

  private Inventory inventory(Counters counters) throws IOException, InterruptedException {
    Map<String, Path> manifests = new HashMap<>();
    Map<Path, Stamp> pending = new HashMap<>();
    int foreign = 0;
    try (var namespaces = Files.newDirectoryStream(publications)) {
      for (Path namespace : namespaces) {
        counters.entry();
        if (!id(namespace.getFileName().toString())
            || !Files.isDirectory(namespace, NOFOLLOW_LINKS)) {
          foreign++;
          continue;
        }
        try (var entries = Files.newDirectoryStream(namespace)) {
          for (Path path : entries) {
            counters.entry();
            String filename = path.getFileName().toString();
            if (filename.length() == 73
                && filename.endsWith(".manifest")
                && id(filename.substring(0, 64))) {
              if (manifests.put(filename.substring(0, 64), path) != null) {
                throw new IOException("duplicate publication identity across namespaces");
              }
              if (manifests.size() > MAX_PUBLICATIONS) {
                throw new IOException("local SkyCache publication scan limit exceeded");
              }
            } else if (pending(filename, ".tmp")) {
              // A live publisher may remove this temporary before final catalog reconciliation.
              try {
                pending.put(path, Stamp.regular(path));
              } catch (NoSuchFileException e) {
                // The final inventory is authoritative for pending-file deletion.
              }
            } else {
              foreign++;
            }
          }
        }
      }
    }
    return new Inventory(manifests, pending, foreign);
  }

  private Map<String, Publication> readPublications(Inventory inventory, Counters counters)
      throws IOException, InterruptedException {
    Map<String, Publication> graph = new HashMap<>();
    for (var entry : inventory.manifests().entrySet()) {
      graph.put(entry.getKey(), readPublication(entry.getValue(), counters));
    }
    return graph;
  }

  private Publication readPublication(Path path, Counters counters)
      throws IOException, InterruptedException {
    interrupted();
    Stamp stamp = Stamp.regular(path);
    if (stamp.size() < 32
        || stamp.size() > MAX_MANIFEST_BYTES
        || counters.scanBytes > MAX_SCAN_BYTES - stamp.size()) {
      throw new IOException("local SkyCache manifest scan byte limit exceeded");
    }
    counters.scanBytes += stamp.size();
    MessageDigest digest;
    try {
      digest = MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
    try (var channel = FileChannel.open(path, READ, NOFOLLOW_LINKS)) {
      InputStream raw = Channels.newInputStream(channel);
      var payload = new LimitedInput(raw, stamp.size() - 32);
      var input =
          new DataInputStream(
              new BufferedInputStream(new DigestInputStream(payload, digest), 65536));
      if (input.readInt() != MANIFEST_MAGIC) {
        throw new IOException("unsupported local SkyCache publication format");
      }
      String publication = input.readUTF();
      String namespace = input.readUTF();
      if (!id(publication)
          || !id(namespace)
          || !id(input.readUTF())
          || !path.getFileName().toString().equals(publication + ".manifest")
          || !path.getParent().getFileName().toString().equals(namespace)) {
        throw new IOException("invalid local SkyCache publication identity");
      }
      int count = count(input);
      if (counters.edges > MAX_EDGES - count) {
        throw new IOException("local SkyCache parent edge limit exceeded");
      }
      counters.edges += count;
      List<String> parents = new ArrayList<>(count);
      Set<String> unique = new HashSet<>();
      for (int i = 0; i < count; i++) {
        String parent = input.readUTF();
        if (!id(parent) || parent.equals(publication) || !unique.add(parent)) {
          throw new IOException("invalid or duplicate parent publication");
        }
        parents.add(parent);
      }
      LocalWitnessTable.readDescriptors(input);
      byte[] scratch = new byte[32];
      if (input.read() != -1
          || raw.readNBytes(scratch, 0, 32) != 32
          || !MessageDigest.isEqual(digest.digest(), java.util.Arrays.copyOf(scratch, 32))
          || channel.size() != stamp.size()
          || !stamp.equals(Stamp.regular(path))) {
        throw new IOException("invalid or changing publication checksum/length");
      }
      return new Publication(publication, namespace, path, stamp, List.copyOf(parents));
    }
  }

  private static void skipUtf(DataInputStream input, byte[] scratch) throws IOException {
    input.readFully(scratch, 0, input.readUnsignedShort());
  }

  private static int count(DataInputStream input) throws IOException {
    int count = input.readInt();
    if (count < 0 || count > 1_000_000) {
      throw new IOException("publication count outside bounds");
    }
    return count;
  }

  private static final class LimitedInput extends InputStream {
    private final InputStream input;
    private long remaining;

    LimitedInput(InputStream input, long remaining) {
      this.input = input;
      this.remaining = remaining;
    }

    @Override
    public int read() throws IOException {
      if (remaining == 0) {
        return -1;
      }
      int value = input.read();
      if (value < 0) {
        throw new EOFException("truncated publication payload");
      }
      remaining--;
      return value;
    }

    @Override
    public int read(byte[] bytes, int offset, int length) throws IOException {
      if (length == 0) {
        return 0;
      }
      if (remaining == 0) {
        return -1;
      }
      int read = input.read(bytes, offset, (int) Math.min(length, remaining));
      if (read < 0) {
        throw new EOFException("truncated publication payload");
      }
      remaining -= read;
      return read;
    }
  }

  private static List<String> childFirstOrder(Map<String, Publication> graph) throws IOException {
    Map<String, Integer> children = new HashMap<>();
    graph.keySet().forEach(id -> children.put(id, 0));
    for (Publication publication : graph.values()) {
      for (String parent : publication.parents()) {
        Publication dependency = graph.get(parent);
        if (dependency == null || !dependency.namespace().equals(publication.namespace())) {
          throw new IOException("incomplete or cross-namespace publication parent graph");
        }
        children.merge(parent, 1, Integer::sum);
      }
    }
    var queue = new PriorityQueue<String>();
    children.forEach(
        (id, count) -> {
          if (count == 0) {
            queue.add(id);
          }
        });
    List<String> order = new ArrayList<>(graph.size());
    while (!queue.isEmpty()) {
      String id = queue.remove();
      order.add(id);
      for (String parent : graph.get(id).parents()) {
        if (children.merge(parent, -1, Integer::sum) == 0) {
          queue.add(parent);
        }
      }
    }
    if (order.size() != graph.size()) {
      throw new IOException("publication parent cycle");
    }
    return order;
  }

  private String collect(
      LocalCacheLeaseManager leases,
      Map<String, Publication> graph,
      List<String> order,
      Inventory inventory,
      Counters counters)
      throws IOException, InterruptedException {
    Map<String, Path> directories = new HashMap<>();
    try (var entries = Files.newDirectoryStream(objects)) {
      for (Path path : entries) {
        counters.entry();
        if (id(path.getFileName().toString()) && Files.isDirectory(path, NOFOLLOW_LINKS)) {
          directories.put(path.getFileName().toString(), path);
        } else {
          counters.foreign++;
        }
      }
    }
    if (!directories.keySet().containsAll(graph.keySet())) {
      throw new IOException("published pack directory is missing or unsafe");
    }
    Set<Integer> visitedSlots = new HashSet<>();
    Set<Integer> activeSlots = new HashSet<>();
    List<LocalCacheLeaseManager.Lease> locks = new ArrayList<>();
    try {
      for (String id : directories.keySet()) {
        interrupted();
        int slot = LocalCacheLeaseManager.publicationSlot(id);
        if (!visitedSlots.add(slot)) {
          continue;
        }
        var lock = leases.tryCollectPublication(id);
        if (lock == null) {
          activeSlots.add(slot);
        } else {
          locks.add(lock);
        }
      }
      counters.activeSlots = activeSlots.size();
      Map<String, Pack> packs = new HashMap<>();
      Set<String> protectedIds = new HashSet<>();
      for (var entry : directories.entrySet()) {
        boolean active =
            activeSlots.contains(LocalCacheLeaseManager.publicationSlot(entry.getKey()));
        Pack pack = pack(entry.getKey(), entry.getValue(), active, counters);
        packs.put(pack.id(), pack);
        if (active || !pack.safe()) {
          protectedIds.add(pack.id());
        }
      }
      Set<String> keep = closure(protectedIds, graph, counters);
      long protectedBytes = bytes(keep, graph, packs);
      counters.protectedBytes = protectedBytes;
      long retained = protectedBytes;
      long cutoff;
      try {
        cutoff = Math.subtractExact(clock.getAsLong(), policy.maxAgeMillis());
      } catch (ArithmeticException e) {
        cutoff = Long.MIN_VALUE;
      }
      List<Publication> newest =
          graph.values().stream()
              .sorted(
                  Comparator.comparing((Publication p) -> p.stamp().modified())
                      .reversed()
                      .thenComparing(Publication::id))
              .toList();
      for (Publication publication : newest) {
        interrupted();
        if (keep.contains(publication.id())
            || (policy.maxAgeMillis() > 0 && publication.stamp().modified().toMillis() < cutoff)) {
          continue;
        }
        Set<String> additional = closure(Set.of(publication.id()), graph, counters);
        additional.removeAll(keep);
        long cost = bytes(additional, graph, packs);
        if (policy.maxBytes() > 0
            && (retained > policy.maxBytes() || cost > policy.maxBytes() - retained)) {
          continue;
        }
        keep.addAll(additional);
        retained = Math.addExact(retained, cost);
      }
      long pendingBytes = inventory.pending().values().stream().mapToLong(Stamp::size).sum();
      counters.before = Math.addExact(bytes(packs.keySet(), graph, packs), pendingBytes);
      counters.retained = retained;
      // Sync every child unlink before deleting a parent manifest. A crash must never leave a
      // discoverable child whose parent deletion was durable before its own deletion.
      for (String id : order) {
        if (!keep.contains(id)) {
          interrupted();
          Publication publication = graph.get(id);
          delete(publication.path(), publication.stamp());
          sync(publication.path().getParent());
          counters.deletedPublications++;
          counters.deletedBytes += publication.stamp().size();
          observer.afterUnpublish(publication.path());
        }
      }
      for (Pack pack : packs.values()) {
        if (keep.contains(pack.id())) {
          continue;
        }
        interrupted();
        // Only exact, previously inspected files are removed. No recursive deletion or symlink
        // traversal is used, including for unpublished packs left by a dead writer.
        for (var entry : pack.files().entrySet()) {
          delete(entry.getKey(), entry.getValue());
          counters.deletedBytes += entry.getValue().size();
          if (pending(entry.getKey().getFileName().toString(), ".index")) {
            counters.deletedPending++;
          }
        }
        sync(pack.path());
        Files.delete(pack.path());
        sync(objects);
        counters.deletedPacks++;
      }
      for (var entry : inventory.pending().entrySet()) {
        interrupted();
        delete(entry.getKey(), entry.getValue());
        sync(entry.getKey().getParent());
        counters.deletedBytes += entry.getValue().size();
        counters.deletedPending++;
      }
      return "complete";
    } finally {
      // Release slot locks before returning to the caller, which releases catalog coordination.
      IOException failure = null;
      for (var lock : locks) {
        try {
          lock.close();
        } catch (IOException e) {
          if (failure == null) {
            failure = e;
          } else {
            failure.addSuppressed(e);
          }
        }
      }
      if (failure != null) {
        throw failure;
      }
    }
  }

  private Pack pack(String id, Path directory, boolean active, Counters counters)
      throws IOException, InterruptedException {
    Map<Path, Stamp> files = new HashMap<>();
    long bytes = 0;
    boolean safe = true;
    try (var entries = Files.newDirectoryStream(directory)) {
      for (Path file : entries) {
        counters.entry();
        String name = file.getFileName().toString();
        if (!name.equals("data.pack")
            && !name.equals("index")
            && !pending(name, ".index")
            && !name.matches("segment-[0-9]{5}\\.(pack|index)")
            && !name.matches("witnesses-[0-9]{5}\\.block")) {
          safe = false;
          counters.foreign++;
        }
        try {
          Stamp stamp = Stamp.regular(file);
          files.put(file, stamp);
          bytes = Math.addExact(bytes, stamp.size());
        } catch (NoSuchFileException e) {
          if (!active) {
            throw e;
          }
        } catch (IOException e) {
          safe = false;
          counters.foreign++;
        }
      }
    }
    return new Pack(id, directory, Map.copyOf(files), bytes, safe);
  }

  private static Set<String> closure(
      Set<String> roots, Map<String, Publication> graph, Counters counters)
      throws IOException, InterruptedException {
    Set<String> result = new HashSet<>();
    var queue = new ArrayDeque<>(roots);
    while (!queue.isEmpty()) {
      if ((++counters.closureVisits & 255) == 0) {
        interrupted();
      }
      if (counters.closureVisits > MAX_CLOSURE_VISITS) {
        throw new IOException("local SkyCache closure traversal limit exceeded");
      }
      String id = queue.remove();
      if (result.add(id) && graph.containsKey(id)) {
        queue.addAll(graph.get(id).parents());
      }
    }
    return result;
  }

  private static long bytes(
      Set<String> ids, Map<String, Publication> graph, Map<String, Pack> packs) {
    long sum = 0;
    for (String id : ids) {
      sum = Math.addExact(sum, packs.get(id).bytes());
      if (graph.containsKey(id)) {
        sum = Math.addExact(sum, graph.get(id).stamp().size());
      }
    }
    return sum;
  }

  private static void delete(Path file, Stamp stamp) throws IOException {
    if (!stamp.equals(Stamp.regular(file))) {
      throw new IOException("managed cache file changed before deletion: " + file);
    }
    Files.delete(file);
  }

  private static void directory(Path path) throws IOException {
    if (!Files.isDirectory(path, NOFOLLOW_LINKS)) {
      throw new IOException("unsafe or missing cache format directory: " + path);
    }
  }

  private static void sync(Path path) throws IOException {
    try (var channel = FileChannel.open(path, READ, NOFOLLOW_LINKS)) {
      channel.force(true);
    }
  }

  private static boolean id(String name) {
    if (name.length() != 64) {
      return false;
    }
    for (int i = 0; i < name.length(); i++) {
      char c = name.charAt(i);
      if (!(c >= '0' && c <= '9') && !(c >= 'a' && c <= 'f')) {
        return false;
      }
    }
    return true;
  }

  private static boolean pending(String name, String suffix) {
    if (!name.startsWith(".pending-")
        || !name.endsWith(suffix)
        || name.length() <= 9 + suffix.length()) {
      return false;
    }
    for (int i = 9; i < name.length() - suffix.length(); i++) {
      if (name.charAt(i) < '0' || name.charAt(i) > '9') {
        return false;
      }
    }
    return true;
  }

  private static void interrupted() throws InterruptedException {
    if (Thread.currentThread().isInterrupted()) {
      throw new InterruptedException("SkyCache collection interrupted");
    }
  }
}
