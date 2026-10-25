// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.CREATE_NEW;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.LocalPublication;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationBundle;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationFile;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProtocol;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheTransport;
import com.google.protobuf.ByteString;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Invocation-owned remote candidates with authenticated, demand-driven pack reads. Only complete
 * publications enter the durable local catalog; admission remains the local validator's job.
 */
final class RemoteSkyCache implements AutoCloseable {
  private final Path managed;
  private final Path objects;
  private final Path publications;
  private final Scope scope;
  private final long metadataBudget;
  private final SkyCacheTransport transport;
  private final Map<String, Candidate> candidates = new LinkedHashMap<>();
  private final List<Path> stages = new ArrayList<>();
  private final Set<String> materialized = new HashSet<>();
  private LocalCacheLeaseManager importLeases;
  private Path chunks;
  private boolean prepared;
  private long downloadedBytes;
  private long downloadedChunks;
  private long attemptedMaterializationBytes;
  private int reusedPublications;

  record Candidate(
      LocalPublication publication,
      Path objectRoot,
      Path manifestRoot,
      SegmentedFingerprintValueStore.FileSource source) {
    String id() {
      return publication.getId();
    }
  }

  /** Resolves ranges using declared chunk lengths, which need not all be one MiB. */
  private final class FileSource implements SegmentedFingerprintValueStore.FileSource {
    private final Map<String, PublicationFile> files = new LinkedHashMap<>();

    FileSource(LocalPublication publication) {
      for (var file : publication.getFilesList()) {
        files.put(file.getName(), file);
      }
    }

    private PublicationFile file(String name) throws IOException {
      var file = files.get(name);
      if (file == null) {
        throw new IOException("missing remote publication file");
      }
      return file;
    }

    @Override
    public long size(String name) throws IOException {
      return file(name).getSizeBytes();
    }

    @Override
    public byte[] read(String name, long maxBytes) throws IOException {
      long size = size(name);
      if (size > maxBytes || size > Integer.MAX_VALUE) {
        throw new IOException("remote metadata exceeds read budget");
      }
      var buffer = ByteBuffer.allocate((int) size);
      read(name, buffer, 0);
      return buffer.array();
    }

    @Override
    public void read(String name, ByteBuffer target, long offset) throws IOException {
      read(name, target, offset, false);
    }

    @Override
    public boolean isCached(String name, long offset, long length) throws IOException {
      var file = file(name);
      if (offset < 0 || length < 0 || offset > file.getSizeBytes() - length) {
        throw new IOException("remote range outside file bounds");
      }
      long start = 0;
      for (var chunk : file.getChunksList()) {
        long end = start + chunk.getSizeBytes();
        if (start < offset + length && offset < end && !cached(chunk)) {
          return false;
        }
        start = end;
      }
      return true;
    }

    @Override
    public void readCached(String name, ByteBuffer target, long offset) throws IOException {
      read(name, target, offset, true);
    }

    private void read(String name, ByteBuffer target, long offset, boolean cacheOnly)
        throws IOException {
      var file = file(name);
      if (offset < 0 || offset > file.getSizeBytes() - target.remaining()) {
        throw new IOException("remote range outside file bounds");
      }
      long start = 0;
      for (var chunk : file.getChunksList()) {
        if (!target.hasRemaining()) {
          break;
        }
        long end = start + chunk.getSizeBytes();
        if (offset < end) {
          byte[] bytes = cacheOnly ? readCachedChunk(chunk) : download(chunk);
          int within = (int) (offset - start);
          int count = Math.min(target.remaining(), bytes.length - within);
          target.put(bytes, within, count);
          offset += count;
        }
        start = end;
      }
      if (target.hasRemaining()) {
        throw new IOException("incomplete remote range");
      }
    }
  }

  private synchronized boolean cached(Digest digest) throws IOException {
    if (chunks == null) {
      return false;
    }
    Path path = chunks.resolve(digest.getHash());
    return Files.isRegularFile(path, NOFOLLOW_LINKS) && Files.size(path) == digest.getSizeBytes();
  }

  private synchronized byte[] readCachedChunk(Digest digest) throws IOException {
    if (!cached(digest)) {
      throw new SegmentedFingerprintValueStore.NotCachedException();
    }
    byte[] bytes = Files.readAllBytes(chunks.resolve(digest.getHash()));
    if (!SkyCacheProtocol.digest(bytes).equals(digest)) {
      throw new IOException("cached remote chunk checksum mismatch");
    }
    return bytes;
  }

  private synchronized byte[] download(Digest digest) throws IOException {
    if (chunks == null) {
      chunks = newStage();
    }
    Path path = chunks.resolve(digest.getHash());
    if (Files.exists(path, NOFOLLOW_LINKS)) {
      return readCachedChunk(digest);
    }
    byte[] bytes = transport.download(digest);
    Files.write(path, bytes, CREATE_NEW, WRITE, NOFOLLOW_LINKS);
    downloadedBytes += bytes.length;
    downloadedChunks++;
    return bytes;
  }

  private Path newStage() throws IOException {
    Path stage =
        Files.createTempDirectory(
            managed,
            ".remote-",
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
    stages.add(stage);
    return stage;
  }

  List<Candidate> candidates() {
    return List.copyOf(candidates.values());
  }

  String statisticsJson() {
    return "{\"downloaded_bytes\":"
        + downloadedBytes
        + ",\"downloaded_chunks\":"
        + downloadedChunks
        + ",\"discovered_publications\":"
        + candidates.size()
        + ",\"materialized_publications\":"
        + materialized.size()
        + ",\"reused_publications\":"
        + reusedPublications
        + "}";
  }

  RemoteSkyCache(Path root, String namespace, LocalSkycacheOptions options) throws IOException {
    this(
        root,
        namespace,
        options.getMaxMetadataMb() * 1024L * 1024,
        options.getRemoteInstanceName(),
        new SkyCacheTransport(
            options.getRemoteEndpoint(),
            options.getRemoteInstanceName(),
            options.getRemoteTokenFile(),
            options.getRemoteTimeout(),
            options.getRemoteMaxTransferSize()));
  }

  RemoteSkyCache(
      Path root,
      String namespace,
      long metadataBudget,
      String instance,
      SkyCacheTransport transport)
      throws IOException {
    try {
      this.scope = Scope.newBuilder().setInstanceName(instance).setNamespaceId(namespace).build();
      SkyCacheProtocol.validateScope(scope);
      if (metadataBudget < 0) {
        throw new IOException("negative local metadata budget");
      }
      this.managed = LocalCacheSession.durableDirectory(root.resolve("leased-v2"));
      this.objects = LocalCacheSession.durableDirectory(managed.resolve("objects/segmented-v1"));
      this.publications =
          LocalCacheSession.durableDirectory(managed.resolve("publications-v4").resolve(namespace));
      this.metadataBudget = metadataBudget;
      this.transport = transport;
    } catch (IOException | RuntimeException e) {
      transport.close();
      throw e;
    }
  }

  /** Fetches admission metadata for a bounded discovery page, leaving segment data in CAS. */
  int prepare() throws IOException {
    if (prepared) {
      return candidates.size();
    }
    prepared = true;
    transport.beginReadSession();
    var response = transport.discover(scope, ByteString.EMPTY);
    IOException lastFailure = null;
    long remaining = metadataBudget;
    for (var candidate : response.getCandidatesList()) {
      Path stage = null;
      boolean retained = false;
      try {
        byte[] encoded = download(candidate.getManifest());
        var bundle = PublicationBundle.parseFrom(encoded);
        SkyCacheProtocol.validateBundle(bundle, scope.getNamespaceId());
        var ids = new HashSet<>(candidates.keySet());
        for (var publication : bundle.getPublicationsList()) {
          ids.add(publication.getId());
          var previous = candidates.get(publication.getId());
          if (previous != null && !previous.publication().equals(publication)) {
            throw new IOException("conflicting remote publication identity");
          }
        }
        if (ids.size() > SkyCacheProtocol.MAX_PUBLICATIONS) {
          continue;
        }
        stage = newStage();
        Path stageObjects = Files.createDirectory(stage.resolve("objects"));
        Path stageManifests = Files.createDirectory(stage.resolve("manifests"));
        var opened = new LinkedHashMap<String, Candidate>();
        long budget = remaining;
        for (var publication : bundle.getPublicationsList()) {
          if (candidates.containsKey(publication.getId())) {
            continue;
          }
          Path directory = Files.createDirectory(stageObjects.resolve(publication.getId()));
          var source = new FileSource(publication);
          Path manifest = stageManifests.resolve(publication.getId() + ".manifest");
          Files.write(manifest, source.read("manifest", budget), CREATE_NEW, WRITE);
          var header =
              LocalCacheSession.readManifestHeader(manifest, scope.getNamespaceId(), budget);
          if (!header.parents().equals(publication.getParentIdsList())) {
            throw new IOException("remote parent list differs from local manifest");
          }
          budget -= header.encodedBytes();
          long witnessBytes = LocalWitnessTable.metadataBytes(header.blocks());
          if (witnessBytes > budget) {
            throw new IOException("remote witnesses exceed metadata budget");
          }
          var expected = new HashSet<String>();
          expected.add("manifest");
          for (int i = 0; i < header.blocks().size(); i++) {
            String name = LocalWitnessTable.filename(i);
            expected.add(name);
            Files.write(directory.resolve(name), source.read(name, budget), CREATE_NEW, WRITE);
          }
          LocalWitnessTable.read(directory, header.blocks(), budget);
          budget -= witnessBytes;
          try (var store = SegmentedFingerprintValueStore.open(source, budget)) {
            expected.addAll(store.sealedFiles());
            budget -= store.indexBytes();
          }
          if (!expected.equals(
              new HashSet<>(
                  publication.getFilesList().stream().map(PublicationFile::getName).toList()))) {
            throw new IOException("remote file inventory differs from local index");
          }
          opened.put(
              publication.getId(),
              new Candidate(publication, stageObjects, stageManifests, source));
        }
        candidates.putAll(opened);
        remaining = budget;
        retained = true;
      } catch (IOException e) {
        // Broken metadata is a miss. All attempts share the read session's I/O and byte budgets.
        lastFailure = e;
      } finally {
        if (stage != null && !retained) {
          removeStage(stage);
          stages.remove(stage);
        }
      }
    }
    if (candidates.isEmpty() && lastFailure != null) {
      throw lastFailure;
    }
    return candidates.size();
  }

  /** Explicit complete import for callers requiring durable local backing. */
  int hydrate() throws IOException {
    prepare();
    materialize(candidates.keySet());
    return candidates.size();
  }

  void materialize(Set<String> used) throws IOException {
    var required = new LinkedHashMap<String, Candidate>();
    for (String id : used) {
      collectRemote(id, required);
    }
    if (required.isEmpty()) {
      return;
    }
    long attemptedBytes = 0;
    for (var candidate : required.values()) {
      for (var file : candidate.publication().getFilesList()) {
        if (file.getSizeBytes()
            > SkyCacheProtocol.MAX_PUBLICATION_BYTES
                - attemptedMaterializationBytes
                - attemptedBytes) {
          throw new IOException("remote materialization exceeds its logical byte budget");
        }
        attemptedBytes += file.getSizeBytes();
      }
    }
    // Charge the entire attempt before creating files. Reused CAS chunks still occupy their
    // full logical length on disk, and failed attempts must not reset this instance's bound.
    attemptedMaterializationBytes += attemptedBytes;
    Path stage = newStage();
    Path stageObjects = Files.createDirectory(stage.resolve("objects"));
    Path stageManifests = Files.createDirectory(stage.resolve("manifests"));
    var bundle = PublicationBundle.newBuilder();
    for (var candidate : required.values()) {
      Path directory = Files.createDirectory(stageObjects.resolve(candidate.id()));
      for (var file : candidate.publication().getFilesList()) {
        Path target =
            file.getName().equals("manifest")
                ? stageManifests.resolve(candidate.id() + ".manifest")
                : directory.resolve(file.getName());
        try (var channel = FileChannel.open(target, CREATE_NEW, WRITE, NOFOLLOW_LINKS)) {
          for (var chunk : file.getChunksList()) {
            var bytes = ByteBuffer.wrap(download(chunk));
            while (bytes.hasRemaining()) {
              channel.write(bytes);
            }
          }
          channel.force(true);
        }
      }
      sync(directory);
      bundle.addPublications(candidate.publication());
    }
    var completed = bundle.build();
    validateLocalBundle(completed, stageObjects, stageManifests);
    install(completed, stageObjects, stageManifests);
    materialized.addAll(required.keySet());
  }

  private void collectRemote(String id, LinkedHashMap<String, Candidate> required) {
    if (required.containsKey(id) || materialized.contains(id)) {
      return;
    }
    var candidate = candidates.get(id);
    if (candidate == null) {
      return; // A locally pinned parent needs no remote import.
    }
    for (String parent : candidate.publication().getParentIdsList()) {
      collectRemote(parent, required);
    }
    required.put(id, candidate);
  }

  void publish(Path manifest) throws IOException {
    transport.beginTransfer();
    String id = manifest.getFileName().toString().replaceFirst("\\.manifest$", "");
    try (var leases = LocalCacheLeaseManager.open(managed)) {
      try (var catalog = leases.trySharedCatalog()) {
        if (catalog == null) {
          throw new IOException("local catalog busy");
        }
        // The existing collector follows parent edges from every pinned root.
        leases.pinPublication(id);
      }
      var ordered = new LinkedHashMap<String, LocalCacheSession.ManifestHeader>();
      collect(id, ordered, new HashSet<>());
      var members = new LinkedHashMap<String, LocalPublication>();
      for (var header : ordered.values()) {
        members.put(
            header.id(), uploadLocal(header, publications.resolve(header.id() + ".manifest")));
      }
      commit(id, members);
    }
  }

  /**
   * Publishes a sealed child without importing its remote parents. The caller must retain its
   * private writer pin and all local parent pins until this method returns. Scratch manifests are
   * never discoverable by local readers or the collector.
   */
  void publishPending(
      String id, byte[] manifest, Set<String> localParents, Set<String> remoteParents)
      throws IOException {
    transport.beginTransfer();
    Path stagedManifest = newStage().resolve(id + ".manifest");
    Files.write(stagedManifest, manifest, CREATE_NEW, WRITE, NOFOLLOW_LINKS);
    var root =
        LocalCacheSession.readManifestHeader(
            stagedManifest, scope.getNamespaceId(), metadataBudget);
    var parents = new HashSet<>(localParents);
    parents.addAll(remoteParents);
    if (!parents.equals(new HashSet<>(root.parents()))
        || localParents.stream().anyMatch(remoteParents::contains)) {
      throw new IOException("pending publication parent provenance mismatch");
    }
    var members = new LinkedHashMap<String, LocalPublication>();
    for (String parent : remoteParents.stream().sorted().toList()) {
      collectDescriptors(parent, members);
    }
    var local = new LinkedHashMap<String, LocalCacheSession.ManifestHeader>();
    for (String parent : localParents.stream().sorted().toList()) {
      collect(parent, local, new HashSet<>());
    }
    int reused = members.size();
    local.put(id, root);
    for (var header : local.values()) {
      var uploaded =
          uploadLocal(
              header,
              header.id().equals(id)
                  ? stagedManifest
                  : publications.resolve(header.id() + ".manifest"));
      var previous = members.putIfAbsent(header.id(), uploaded);
      if (previous != null) {
        if (!previous.equals(uploaded)) {
          throw new IOException("conflicting local and remote publication identity");
        }
        reused--;
      }
    }
    commit(id, members);
    reusedPublications += reused;
  }

  private void collectDescriptors(String id, Map<String, LocalPublication> members)
      throws IOException {
    if (members.containsKey(id)) {
      return;
    }
    var candidate = candidates.get(id);
    if (candidate == null || members.size() >= SkyCacheProtocol.MAX_PUBLICATIONS) {
      throw new IOException("unknown or over-budget remote parent");
    }
    // prepare validated the authenticated descriptor closure, including acyclicity.
    for (String parent : candidate.publication().getParentIdsList()) {
      collectDescriptors(parent, members);
    }
    members.put(id, candidate.publication());
  }

  private LocalPublication uploadLocal(LocalCacheSession.ManifestHeader header, Path manifest)
      throws IOException {
    var publication =
        LocalPublication.newBuilder().setId(header.id()).addAllParentIds(header.parents());
    for (String name : filesFor(header, objects)) {
      Path file = name.equals("manifest") ? manifest : objects.resolve(header.id()).resolve(name);
      var entry = PublicationFile.newBuilder().setName(name);
      try (var channel = FileChannel.open(file, READ, NOFOLLOW_LINKS)) {
        long length = channel.size();
        if (length > SkyCacheProtocol.MAX_PUBLICATION_BYTES) {
          throw new IOException("publication file exceeds logical size bound");
        }
        entry.setSizeBytes(length);
        long remaining = length;
        while (remaining > 0) {
          var bytes = ByteBuffer.allocate((int) Math.min(remaining, SkyCacheProtocol.CHUNK_BYTES));
          while (bytes.hasRemaining()) {
            if (channel.read(bytes) < 0) {
              throw new IOException("truncated local publication");
            }
          }
          entry.addChunks(transport.upload(bytes.array()));
          remaining -= bytes.capacity();
        }
        if (channel.size() != length) {
          throw new IOException("local publication changed during transfer");
        }
      }
      publication.addFiles(entry);
    }
    return publication.build();
  }

  private void commit(String id, Map<String, LocalPublication> members) throws IOException {
    var ordered = new LinkedHashMap<String, LocalPublication>();
    order(id, members, ordered, new HashSet<>());
    var bundle =
        PublicationBundle.newBuilder()
            .setFormatVersion(1)
            .setNamespaceId(scope.getNamespaceId())
            .setRootId(id)
            .addAllPublications(ordered.values())
            .build();
    SkyCacheProtocol.validateBundle(bundle, scope.getNamespaceId());
    var digest = transport.upload(bundle.toByteArray());
    transport.commit(
        CommitRequest.newBuilder()
            .setScope(scope)
            .setManifest(digest)
            // Deterministic key permits retry after an uncertain response or process restart.
            .setIdempotencyKey(digest.getHash())
            .build());
  }

  private static void order(
      String id,
      Map<String, LocalPublication> members,
      LinkedHashMap<String, LocalPublication> ordered,
      Set<String> visiting)
      throws IOException {
    if (ordered.containsKey(id)) {
      return;
    }
    var member = members.get(id);
    if (member == null
        || !visiting.add(id)
        || visiting.size() > SkyCacheProtocol.MAX_PUBLICATIONS) {
      throw new IOException("incomplete, cyclic or over-budget parent closure");
    }
    for (String parent : member.getParentIdsList()) {
      order(parent, members, ordered, visiting);
    }
    ordered.put(id, member);
  }

  private void collect(
      String id,
      LinkedHashMap<String, LocalCacheSession.ManifestHeader> ordered,
      HashSet<String> visiting)
      throws IOException {
    if (ordered.containsKey(id)) {
      return;
    }
    if (!visiting.add(id) || visiting.size() > SkyCacheProtocol.MAX_PUBLICATIONS) {
      throw new IOException("cyclic or over-budget parent closure");
    }
    var header =
        LocalCacheSession.readManifestHeader(
            publications.resolve(id + ".manifest"), scope.getNamespaceId(), metadataBudget);
    for (String parent : header.parents()) {
      collect(parent, ordered, visiting);
    }
    ordered.put(id, header);
  }

  private List<String> filesFor(LocalCacheSession.ManifestHeader header, Path objectRoot)
      throws IOException {
    var names = new ArrayList<String>();
    names.add("manifest");
    try (var store = SegmentedFingerprintValueStore.open(objectRoot, header.id(), metadataBudget)) {
      names.addAll(store.sealedFiles());
    }
    for (int i = 0; i < header.blocks().size(); i++) {
      names.add(LocalWitnessTable.filename(i));
    }
    return names;
  }

  private void validateLocalBundle(PublicationBundle bundle, Path objectRoot, Path manifestRoot)
      throws IOException {
    long remaining = metadataBudget;
    for (var publication : bundle.getPublicationsList()) {
      var header =
          LocalCacheSession.readManifestHeader(
              manifestRoot.resolve(publication.getId() + ".manifest"),
              scope.getNamespaceId(),
              remaining);
      if (!header.parents().equals(publication.getParentIdsList())
          || !new HashSet<>(filesFor(header, objectRoot))
              .equals(
                  new HashSet<>(
                      publication.getFilesList().stream()
                          .map(PublicationFile::getName)
                          .toList()))) {
        throw new IOException("bundle differs from local manifest or index");
      }
      remaining -= header.encodedBytes();
      LocalWitnessTable.read(objectRoot.resolve(header.id()), header.blocks(), remaining);
      remaining -= LocalWitnessTable.metadataBytes(header.blocks());
      try (var store = SegmentedFingerprintValueStore.open(objectRoot, header.id(), remaining)) {
        remaining -= store.indexBytes();
      }
    }
  }

  private void install(PublicationBundle bundle, Path objectRoot, Path manifestRoot)
      throws IOException {
    if (importLeases == null) {
      importLeases = LocalCacheLeaseManager.open(managed);
    }
    var leases = importLeases;
    {
      try (var catalog = leases.trySharedCatalog()) {
        if (catalog == null) {
          throw new IOException("local catalog busy");
        }
        for (var publication : bundle.getPublicationsList()) {
          leases.pinPublication(publication.getId());
        }
      }
      try (var catalog = leases.tryExclusiveCatalog()) {
        if (catalog == null) {
          throw new IOException("local catalog busy");
        }
        // Compare all collisions before publishing anything; immutable IDs may never be replaced.
        for (var publication : bundle.getPublicationsList()) {
          Path target = objects.resolve(publication.getId());
          if (Files.exists(target, NOFOLLOW_LINKS)) {
            if (!Files.isDirectory(target, NOFOLLOW_LINKS)) {
              throw new IOException("publication object path is not a directory");
            }
            for (var file : publication.getFilesList()) {
              if (!file.getName().equals("manifest")) {
                sameFile(
                    objectRoot.resolve(publication.getId()).resolve(file.getName()),
                    target.resolve(file.getName()));
              }
            }
          }
          Path existing = publications.resolve(publication.getId() + ".manifest");
          if (Files.exists(existing, NOFOLLOW_LINKS)) {
            sameFile(manifestRoot.resolve(publication.getId() + ".manifest"), existing);
          }
        }
        for (var publication : bundle.getPublicationsList()) {
          Path target = objects.resolve(publication.getId());
          if (!Files.exists(target, NOFOLLOW_LINKS)) {
            Files.move(objectRoot.resolve(publication.getId()), target);
          }
        }
        sync(objects);
        // Parents precede children. Every manifest appears atomically after all data is durable.
        for (var publication : bundle.getPublicationsList()) {
          Path target = publications.resolve(publication.getId() + ".manifest");
          if (!Files.exists(target, NOFOLLOW_LINKS)) {
            Files.createLink(target, manifestRoot.resolve(publication.getId() + ".manifest"));
          }
        }
        sync(publications);
      }
    }
  }

  private static void sameFile(Path expected, Path existing) throws IOException {
    if (!Files.isRegularFile(existing, NOFOLLOW_LINKS)
        || Files.size(existing) != Files.size(expected)
        || Files.mismatch(expected, existing) != -1) {
      throw new IOException("immutable publication identity conflict");
    }
  }

  private static void sync(Path directory) throws IOException {
    try (var channel = FileChannel.open(directory, READ)) {
      channel.force(true);
    }
  }

  private static void removeStage(Path stage) throws IOException {
    try (var paths = Files.walk(stage)) {
      for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
        Files.delete(path);
      }
    }
  }

  @Override
  public void close() {
    try {
      if (importLeases != null) {
        importLeases.close();
      }
    } catch (IOException ignored) {
      // Closing the manager releases all pins, including when one individual release fails.
    } finally {
      transport.close();
      candidates.clear();
      for (Path stage : stages) {
        try {
          removeStage(stage);
        } catch (IOException ignored) {
          // Private scratch data is never discoverable as a local publication.
        }
      }
      stages.clear();
    }
  }
}
