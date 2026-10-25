// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.CREATE_NEW;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

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

/**
 * Bounded complete-publication transport. Admission remains the local validator's responsibility.
 */
final class RemoteSkyCache implements AutoCloseable {
  private final Path managed;
  private final Path objects;
  private final Path publications;
  private final Scope scope;
  private final long metadataBudget;
  private final SkyCacheTransport transport;

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
            options.getRemoteTimeout()));
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

  /** Imports the newest structurally complete candidate in the first bounded discovery page. */
  int hydrate() throws IOException {
    transport.beginTransfer();
    var response = transport.discover(scope, ByteString.EMPTY);
    IOException lastFailure = null;
    for (var candidate : response.getCandidatesList()) {
      Path stage = null;
      try {
        byte[] encoded = transport.download(candidate.getManifest());
        var bundle = PublicationBundle.parseFrom(encoded);
        SkyCacheProtocol.validateBundle(bundle, scope.getNamespaceId());
        stage =
            Files.createTempDirectory(
                managed,
                ".remote-",
                PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
        Path stageObjects = Files.createDirectory(stage.resolve("objects"));
        Path stageManifests = Files.createDirectory(stage.resolve("manifests"));
        for (var publication : bundle.getPublicationsList()) {
          Path directory = Files.createDirectory(stageObjects.resolve(publication.getId()));
          for (var file : publication.getFilesList()) {
            Path target =
                file.getName().equals("manifest")
                    ? stageManifests.resolve(publication.getId() + ".manifest")
                    : directory.resolve(file.getName());
            try (var channel = FileChannel.open(target, CREATE_NEW, WRITE, NOFOLLOW_LINKS)) {
              for (var chunk : file.getChunksList()) {
                var bytes = ByteBuffer.wrap(transport.download(chunk));
                while (bytes.hasRemaining()) {
                  channel.write(bytes);
                }
              }
              channel.force(true);
            }
          }
          sync(directory);
        }
        validateLocalBundle(bundle, stageObjects, stageManifests);
        install(bundle, stageObjects, stageManifests);
        return bundle.getPublicationsCount();
      } catch (IOException e) {
        // A corrupt or evicted candidate is a miss. The invocation-wide transfer budget/deadline
        // also applies to these attempts, so a broken catalog cannot induce unbounded downloads.
        lastFailure = e;
      } finally {
        if (stage != null) {
          removeStage(stage);
        }
      }
    }
    if (lastFailure != null) {
      throw lastFailure;
    }
    return 0;
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
      var bundle =
          PublicationBundle.newBuilder()
              .setFormatVersion(1)
              .setNamespaceId(scope.getNamespaceId())
              .setRootId(id);
      for (var header : ordered.values()) {
        var publication =
            LocalPublication.newBuilder().setId(header.id()).addAllParentIds(header.parents());
        for (String name : filesFor(header, objects)) {
          Path file =
              name.equals("manifest")
                  ? publications.resolve(header.id() + ".manifest")
                  : objects.resolve(header.id()).resolve(name);
          var entry = PublicationFile.newBuilder().setName(name);
          try (var channel = FileChannel.open(file, READ, NOFOLLOW_LINKS)) {
            long length = channel.size();
            if (length > SkyCacheProtocol.MAX_TRANSFER_BYTES) {
              throw new IOException("publication file exceeds transfer budget");
            }
            entry.setSizeBytes(length);
            long remaining = length;
            while (remaining > 0) {
              var bytes =
                  ByteBuffer.allocate((int) Math.min(remaining, SkyCacheProtocol.CHUNK_BYTES));
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
        bundle.addPublications(publication);
      }
      var completed = bundle.build();
      SkyCacheProtocol.validateBundle(completed, scope.getNamespaceId());
      var digest = transport.upload(completed.toByteArray());
      transport.commit(
          CommitRequest.newBuilder()
              .setScope(scope)
              .setManifest(digest)
              // Deterministic key permits retry after an uncertain response or process restart.
              .setIdempotencyKey(digest.getHash())
              .build());
    }
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
    try (var leases = LocalCacheLeaseManager.open(managed)) {
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
    transport.close();
  }
}
