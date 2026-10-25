// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.remote;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationBundle;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;

/** Shared bounds and structural checks for the experimental complete-bundle protocol. */
public final class SkyCacheProtocol {
  public static final int CHUNK_BYTES = 1024 * 1024;
  public static final int MAX_BUNDLE_BYTES = 1024 * 1024;
  public static final long MAX_TRANSFER_BYTES = 256L * 1024 * 1024;
  public static final int MAX_PUBLICATIONS = 64;

  private SkyCacheProtocol() {}

  public static Digest digest(byte[] bytes) {
    try {
      return Digest.newBuilder()
          .setHash(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)))
          .setSizeBytes(bytes.length)
          .build();
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }

  public static void validateDigest(Digest digest, long maximum) throws IOException {
    if (!digest.getHash().matches("[0-9a-f]{64}")
        || digest.getSizeBytes() < 0
        || digest.getSizeBytes() > maximum) {
      throw new IOException("invalid or over-budget SHA-256 digest");
    }
  }

  public static void validateScope(Scope scope) throws IOException {
    if (!scope.getNamespaceId().matches("[0-9a-f]{64}")
        || scope.getInstanceName().length() > 128
        || (!scope.getInstanceName().isEmpty()
            && !scope.getInstanceName().matches("[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*"))) {
      throw new IOException("invalid SkyCache scope");
    }
  }

  public static void validateBundle(PublicationBundle bundle, String namespace) throws IOException {
    if (bundle.getSerializedSize() > MAX_BUNDLE_BYTES
        || bundle.getFormatVersion() != 1
        || !bundle.getNamespaceId().equals(namespace)
        || bundle.getPublicationsCount() == 0
        || bundle.getPublicationsCount() > MAX_PUBLICATIONS) {
      throw new IOException("unsupported or over-budget publication bundle");
    }
    var parents = new HashMap<String, java.util.List<String>>();
    long total = 0;
    int files = 0;
    int chunks = 0;
    for (var publication : bundle.getPublicationsList()) {
      String id = publication.getId();
      if (!id.matches("[0-9a-f]{64}") || parents.containsKey(id)) {
        throw new IOException("invalid or duplicate publication identity");
      }
      var uniqueParents = new HashSet<String>();
      for (String parent : publication.getParentIdsList()) {
        if (!parents.containsKey(parent) || !uniqueParents.add(parent)) {
          throw new IOException("parents must be unique and precede children");
        }
      }
      parents.put(id, publication.getParentIdsList());
      var names = new HashSet<String>();
      for (var file : publication.getFilesList()) {
        if (++files > 16384
            || !file.getName()
                .matches(
                    "manifest|index|segment-[0-9]{5}\\.(index|pack)|witnesses-[0-9]{5}\\.block")
            || !names.add(file.getName())
            || file.getSizeBytes() < 0
            || file.getSizeBytes() > MAX_TRANSFER_BYTES - total) {
          throw new IOException("invalid or over-budget publication file");
        }
        total += file.getSizeBytes();
        long size = 0;
        for (var chunk : file.getChunksList()) {
          validateDigest(chunk, CHUNK_BYTES);
          if (++chunks > 32768
              || chunk.getSizeBytes() == 0
              || chunk.getSizeBytes() > file.getSizeBytes() - size) {
            throw new IOException("invalid chunk sequence");
          }
          size += chunk.getSizeBytes();
        }
        if (size != file.getSizeBytes()) {
          throw new IOException("publication file length differs from its chunks");
        }
      }
      if (!names.contains("manifest") || !names.contains("index")) {
        throw new IOException("publication omits manifest or index");
      }
    }
    var reachable = new HashSet<String>();
    var pending = new java.util.ArrayDeque<String>();
    pending.add(bundle.getRootId());
    while (!pending.isEmpty()) {
      String id = pending.remove();
      if (!parents.containsKey(id)) {
        throw new IOException("missing root publication");
      }
      if (reachable.add(id)) {
        pending.addAll(parents.get(id));
      }
    }
    if (reachable.size() != parents.size()) {
      throw new IOException("bundle contains unrelated publications");
    }
  }
}
