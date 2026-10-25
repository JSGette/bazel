// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.remote.worker;

import static com.google.devtools.build.lib.remote.util.Futures.getFromFuture;

import build.bazel.remote.execution.v2.Digest;
import com.google.devtools.build.lib.remote.common.CacheNotFoundException;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheCatalogGrpc.SkyCacheCatalogImplBase;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverResponse;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationBundle;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProtocol;
import com.google.protobuf.ExtensionRegistry;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.stub.StreamObserver;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.util.HashSet;

/** Catalog RPCs over the worker's existing CAS; dependency validation remains client-owned. */
final class SkyCacheServer extends SkyCacheCatalogImplBase {
  interface BlobAccess {
    void download(Digest digest, OutputStream output) throws IOException, InterruptedException;

    boolean refresh(Digest digest) throws IOException;
  }

  private final SkyCacheCatalogStore store;
  private final BlobAccess blobs;
  private final String instance;

  SkyCacheServer(SkyCacheCatalogStore store, OnDiskBlobStoreCache cache, String instance) {
    this(
        store,
        new BlobAccess() {
          @Override
          public void download(Digest digest, OutputStream output)
              throws IOException, InterruptedException {
            // The CombinedCache byte[] API can grow past the digest's declared size. Its stream
            // API is package-private; use the disk client's public stream API for a hard bound.
            getFromFuture(cache.getDiskCacheClient().downloadBlob(digest, output));
          }

          @Override
          public boolean refresh(Digest digest) throws IOException {
            return cache.refresh(digest);
          }
        },
        instance);
  }

  SkyCacheServer(SkyCacheCatalogStore store, BlobAccess blobs, String instance) {
    this.store = store;
    this.blobs = blobs;
    this.instance = instance;
  }

  @Override
  public void discoverPublications(
      DiscoverRequest request, StreamObserver<DiscoverResponse> observer) {
    try {
      validateScope(request.getScope());
      observer.onNext(store.discover(request));
      observer.onCompleted();
    } catch (StatusRuntimeException e) {
      observer.onError(e);
    } catch (IllegalArgumentException e) {
      observer.onError(
          Status.INVALID_ARGUMENT.withDescription(e.getMessage()).asRuntimeException());
    } catch (IOException e) {
      observer.onError(Status.UNAVAILABLE.withDescription(e.getMessage()).asRuntimeException());
    }
  }

  @Override
  public void commitPublication(CommitRequest request, StreamObserver<PublicationRef> observer) {
    try {
      validateScope(request.getScope());
      try {
        SkyCacheProtocol.validateDigest(request.getManifest(), SkyCacheProtocol.MAX_BUNDLE_BYTES);
      } catch (IOException e) {
        throw invalidArgument(e);
      }
      // A durable receipt wins over later CAS eviction. Check idempotency before any blob I/O.
      PublicationRef publication = store.lookup(request);
      if (publication == null) {
        PublicationBundle bundle = readBundle(request);
        var checked = new HashSet<Digest>();
        for (var member : bundle.getPublicationsList()) {
          for (var file : member.getFilesList()) {
            for (Digest chunk : file.getChunksList()) {
              if (checked.add(chunk) && !blobs.refresh(chunk)) {
                throw new CacheNotFoundException(chunk);
              }
            }
          }
        }
        publication = store.commit(request);
      }
      observer.onNext(publication);
      observer.onCompleted();
    } catch (StatusRuntimeException e) {
      observer.onError(e);
    } catch (CacheNotFoundException e) {
      observer.onError(
          Status.FAILED_PRECONDITION.withDescription(e.getMessage()).asRuntimeException());
    } catch (CorruptBlobException e) {
      observer.onError(Status.DATA_LOSS.withDescription(e.getMessage()).asRuntimeException());
    } catch (IllegalArgumentException e) {
      observer.onError(
          Status.INVALID_ARGUMENT.withDescription(e.getMessage()).asRuntimeException());
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
      observer.onError(
          Status.CANCELLED.withDescription("Catalog commit interrupted").asRuntimeException());
    } catch (IOException e) {
      observer.onError(Status.UNAVAILABLE.withDescription(e.getMessage()).asRuntimeException());
    }
  }

  private void validateScope(Scope scope) throws IOException {
    try {
      SkyCacheProtocol.validateScope(scope);
    } catch (IOException e) {
      throw invalidArgument(e);
    }
    if (!instance.equals(scope.getInstanceName())) {
      throw Status.INVALID_ARGUMENT.withDescription("Unknown CAS instance").asRuntimeException();
    }
  }

  private PublicationBundle readBundle(CommitRequest request)
      throws IOException, InterruptedException {
    Digest manifest = request.getManifest();
    var output = new BoundedBlobOutput((int) manifest.getSizeBytes());
    blobs.download(manifest, output);
    byte[] bytes = output.bytes();
    if (!SkyCacheProtocol.digest(bytes).equals(manifest)) {
      throw new CorruptBlobException("Publication bundle differs from its CAS digest");
    }
    try {
      PublicationBundle bundle =
          PublicationBundle.parseFrom(bytes, ExtensionRegistry.getEmptyRegistry());
      SkyCacheProtocol.validateBundle(bundle, request.getScope().getNamespaceId());
      return bundle;
    } catch (IOException e) {
      throw invalidArgument(e);
    }
  }

  private static StatusRuntimeException invalidArgument(IOException failure) {
    return Status.INVALID_ARGUMENT.withDescription(failure.getMessage()).asRuntimeException();
  }

  private static final class CorruptBlobException extends IOException {
    CorruptBlobException(String message) {
      super(message);
    }
  }

  /** Applies the validated digest's size before allocating or accepting any CAS bytes. */
  private static final class BoundedBlobOutput extends OutputStream {
    private final int limit;
    private final ByteArrayOutputStream output;

    BoundedBlobOutput(int limit) {
      this.limit = limit;
      output = new ByteArrayOutputStream(limit);
    }

    @Override
    public void write(int value) throws IOException {
      requireCapacity(1);
      output.write(value);
    }

    @Override
    public void write(byte[] bytes, int offset, int length) throws IOException {
      requireCapacity(length);
      output.write(bytes, offset, length);
    }

    private void requireCapacity(int length) throws IOException {
      if (length < 0 || length > limit - output.size()) {
        throw new CorruptBlobException("Publication bundle exceeds its declared CAS size");
      }
    }

    byte[] bytes() {
      return output.toByteArray();
    }
  }
}
