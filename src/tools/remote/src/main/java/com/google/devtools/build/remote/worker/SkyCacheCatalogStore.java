// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.remote.worker;

import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CatalogReceipt;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.CommitRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverRequest;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.DiscoverResponse;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.PublicationRef;
import com.google.devtools.build.lib.skyframe.serialization.remote.SkyCacheProto.Scope;
import com.google.protobuf.ByteString;
import io.grpc.Status;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.channels.OverlappingFileLockException;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.PosixFilePermission;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.GeneralSecurityException;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;
import java.util.NavigableMap;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;

/**
 * A durable, single-writer MVP catalog. Metadata is never evicted: each unique idempotency key
 * consumes one of 4096 receipts, including aliases for an existing manifest. A POSIX filesystem
 * supporting atomic moves and directory fsync is required. The caller validates CAS closure before
 * committing; discovery is advisory and does not establish that CAS objects still exist.
 */
final class SkyCacheCatalogStore implements AutoCloseable {
  // POSIX record locks can be dropped when ANY descriptor for the locked file is closed.
  // Reject duplicate JVM-local writers before opening a second descriptor.
  private static final Set<Path> OPEN_DIRECTORIES = ConcurrentHashMap.newKeySet();
  private static final int MAX_RECEIPTS = 4096;
  private static final int MAX_RECEIPT_BYTES = 64 * 1024;
  private static final int DEFAULT_PAGE_SIZE = 16;
  private static final int MAX_PAGE_SIZE = 64;
  private static final int SIGNATURE_BYTES = 32;
  private static final Set<PosixFilePermission> SECRET_PERMISSIONS =
      PosixFilePermissions.fromString("rw-------");

  private record ScopeKey(String instance, String namespace) {
    static ScopeKey of(Scope scope) {
      return new ScopeKey(scope.getInstanceName(), scope.getNamespaceId());
    }
  }

  private record RequestKey(ScopeKey scope, String idempotencyKey) {}

  private record ManifestKey(ScopeKey scope, String hash, long size) {
    static ManifestKey of(CommitRequest request) {
      return new ManifestKey(
          ScopeKey.of(request.getScope()),
          request.getManifest().getHash(),
          request.getManifest().getSizeBytes());
    }
  }

  private final Path directory;
  private final FileChannel lockChannel;
  private final FileLock lock;
  private final byte[] signingKey;
  private final Map<RequestKey, CatalogReceipt> requests = new HashMap<>();
  private final Map<ManifestKey, PublicationRef> manifests = new HashMap<>();
  private final Map<ScopeKey, NavigableMap<Long, PublicationRef>> publications = new HashMap<>();
  private int receiptCount;
  private boolean closed;
  private boolean storageFailed;

  SkyCacheCatalogStore(Path directory) throws IOException {
    Path normalized = directory.toAbsolutePath().normalize();
    Files.createDirectories(normalized);
    if (!Files.isDirectory(normalized, LinkOption.NOFOLLOW_LINKS)) {
      throw new IOException("Catalog directory must not be a symbolic link");
    }
    this.directory = normalized.toRealPath();
    if (!OPEN_DIRECTORIES.add(this.directory)) {
      throw new IOException("SkyCache catalog already has a writer");
    }
    FileChannel opened = null;
    FileLock acquired = null;
    try {
      opened =
          FileChannel.open(
              this.directory.resolve("catalog.lock"),
              StandardOpenOption.CREATE,
              StandardOpenOption.WRITE,
              LinkOption.NOFOLLOW_LINKS);
      try {
        acquired = opened.tryLock();
      } catch (OverlappingFileLockException e) {
        throw new IOException("SkyCache catalog already has a writer", e);
      }
      if (acquired == null) {
        throw new IOException("SkyCache catalog already has a writer");
      }
      lock = acquired;
      lockChannel = opened;
      signingKey = loadSigningKey();
      replay();
    } catch (IOException | RuntimeException e) {
      try {
        if (acquired != null) {
          acquired.release();
        }
      } catch (IOException cleanup) {
        e.addSuppressed(cleanup);
      }
      try {
        if (opened != null) {
          opened.close();
        }
      } catch (IOException cleanup) {
        e.addSuppressed(cleanup);
      } finally {
        OPEN_DIRECTORIES.remove(this.directory);
      }
      throw e;
    }
  }

  /**
   * Returns the durable response for this key, even when its CAS manifest has since been evicted.
   */
  synchronized PublicationRef lookup(CommitRequest request) throws IOException {
    checkOpen();
    validateRequest(request);
    CatalogReceipt previous =
        requests.get(new RequestKey(ScopeKey.of(request.getScope()), request.getIdempotencyKey()));
    if (previous == null) {
      return null;
    }
    if (!ManifestKey.of(previous.getRequest()).equals(ManifestKey.of(request))) {
      throw Status.ALREADY_EXISTS
          .withDescription("Idempotency key is already bound to another manifest")
          .asRuntimeException();
    }
    return previous.getPublication();
  }

  synchronized PublicationRef commit(CommitRequest request) throws IOException {
    PublicationRef previous = lookup(request);
    if (previous != null) {
      return previous;
    }
    if (receiptCount == MAX_RECEIPTS) {
      throw Status.RESOURCE_EXHAUSTED
          .withDescription("MVP catalog has reached its 4096 immutable receipt limit")
          .asRuntimeException();
    }
    int number = receiptCount + 1;
    PublicationRef publication = manifests.get(ManifestKey.of(request));
    if (publication == null) {
      publication =
          PublicationRef.newBuilder()
              .setManifest(request.getManifest())
              .setCatalogSequence(number)
              .build();
    }
    CatalogReceipt receipt =
        CatalogReceipt.newBuilder().setRequest(request).setPublication(publication).build();
    byte[] bytes = receipt.toByteArray();
    if (bytes.length > MAX_RECEIPT_BYTES) {
      throw Status.INVALID_ARGUMENT
          .withDescription("Catalog receipt is too large")
          .asRuntimeException();
    }
    try {
      // Authenticate the protobuf receipt so valid-looking disk corruption also fails closed.
      byte[] authenticated = Arrays.copyOf(bytes, bytes.length + SIGNATURE_BYTES);
      System.arraycopy(sign(bytes), 0, authenticated, bytes.length, SIGNATURE_BYTES);
      writeAtomically(receiptPath(number), authenticated, false);
    } catch (IOException e) {
      // An atomic rename might already have succeeded. Reopen/replay before assigning another ID.
      storageFailed = true;
      throw e;
    }
    index(receipt);
    receiptCount = number;
    return publication;
  }

  synchronized DiscoverResponse discover(DiscoverRequest request) throws IOException {
    checkOpen();
    validateScope(request.getScope());
    int pageSize = request.getPageSize() == 0 ? DEFAULT_PAGE_SIZE : request.getPageSize();
    if (pageSize < 1 || pageSize > MAX_PAGE_SIZE) {
      throw invalidToken("Page size must be between 1 and 64");
    }
    long highwater = receiptCount;
    long cursor = highwater + 1;
    if (!request.getPageToken().isEmpty()) {
      long[] bounds = decodeToken(request, pageSize);
      highwater = bounds[0];
      cursor = bounds[1];
    }
    NavigableMap<Long, PublicationRef> entries = publications.get(ScopeKey.of(request.getScope()));
    DiscoverResponse.Builder response = DiscoverResponse.newBuilder();
    if (entries == null) {
      return response.build();
    }
    var iterator =
        entries.headMap(Math.min(highwater + 1, cursor), false).descendingMap().values().iterator();
    while (iterator.hasNext() && response.getCandidatesCount() < pageSize) {
      response.addCandidates(iterator.next());
    }
    if (iterator.hasNext()) {
      long nextCursor =
          response.getCandidates(response.getCandidatesCount() - 1).getCatalogSequence();
      response.setNextPageToken(encodeToken(request.getScope(), pageSize, highwater, nextCursor));
    }
    return response.build();
  }

  private void replay() throws IOException {
    NavigableMap<Integer, Path> receipts = new TreeMap<>();
    try (var paths = Files.newDirectoryStream(directory, "receipt-*.pb")) {
      for (Path path : paths) {
        String name = path.getFileName().toString();
        if (!name.matches("receipt-[0-9]{8}\\.pb")
            || !Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS)) {
          throw new IOException("Malformed catalog receipt: " + name);
        }
        int number = Integer.parseInt(name.substring(8, 16));
        if (number < 1 || number > MAX_RECEIPTS) {
          throw new IOException("Catalog receipt number is out of range");
        }
        receipts.put(number, path);
      }
    }
    for (var entry : receipts.entrySet()) {
      int number = entry.getKey();
      if (number != receiptCount + 1
          || Files.size(entry.getValue()) > MAX_RECEIPT_BYTES + SIGNATURE_BYTES) {
        throw new IOException("Catalog receipts have a gap or exceed the size limit");
      }
      byte[] persisted = Files.readAllBytes(entry.getValue());
      if (persisted.length <= SIGNATURE_BYTES) {
        throw new IOException("Truncated catalog receipt " + number);
      }
      byte[] payload = Arrays.copyOf(persisted, persisted.length - SIGNATURE_BYTES);
      byte[] signature = Arrays.copyOfRange(persisted, payload.length, persisted.length);
      if (!MessageDigest.isEqual(sign(payload), signature)) {
        throw new IOException("Corrupt catalog receipt " + number);
      }
      CatalogReceipt receipt = CatalogReceipt.parseFrom(payload);
      try {
        validateRequest(receipt.getRequest());
      } catch (RuntimeException e) {
        throw new IOException("Invalid request in catalog receipt " + number, e);
      }
      if (!receipt.hasRequest()
          || !receipt.hasPublication()
          || !receipt.getPublication().hasManifest()
          || !receipt
              .getRequest()
              .getManifest()
              .getHash()
              .equals(receipt.getPublication().getManifest().getHash())
          || receipt.getRequest().getManifest().getSizeBytes()
              != receipt.getPublication().getManifest().getSizeBytes()) {
        throw new IOException("Invalid publication in catalog receipt " + number);
      }
      RequestKey requestKey =
          new RequestKey(
              ScopeKey.of(receipt.getRequest().getScope()),
              receipt.getRequest().getIdempotencyKey());
      PublicationRef prior = manifests.get(ManifestKey.of(receipt.getRequest()));
      long expectedSequence = prior == null ? number : prior.getCatalogSequence();
      if (requests.containsKey(requestKey)
          || receipt.getPublication().getCatalogSequence() != expectedSequence) {
        throw new IOException("Conflicting catalog receipt " + number);
      }
      index(receipt);
      receiptCount = number;
    }
  }

  private void index(CatalogReceipt receipt) {
    CommitRequest request = receipt.getRequest();
    ScopeKey scope = ScopeKey.of(request.getScope());
    PublicationRef publication = receipt.getPublication();
    requests.put(new RequestKey(scope, request.getIdempotencyKey()), receipt);
    manifests.putIfAbsent(ManifestKey.of(request), publication);
    publications
        .computeIfAbsent(scope, ignored -> new TreeMap<>())
        .putIfAbsent(publication.getCatalogSequence(), publication);
  }

  private byte[] loadSigningKey() throws IOException {
    Path secret = directory.resolve("token.key");
    if (!Files.exists(secret, LinkOption.NOFOLLOW_LINKS)) {
      // Losing a secret from a nonempty catalog must fail closed, not invalidate issued tokens.
      try (var receipts = Files.newDirectoryStream(directory, "receipt-*.pb")) {
        if (receipts.iterator().hasNext()) {
          throw new IOException("Catalog signing key is missing");
        }
      }
      byte[] generated = new byte[SIGNATURE_BYTES];
      new SecureRandom().nextBytes(generated);
      writeAtomically(secret, generated, true);
    }
    if (!Files.isRegularFile(secret, LinkOption.NOFOLLOW_LINKS)
        || Files.size(secret) != SIGNATURE_BYTES) {
      throw new IOException("Invalid catalog signing key");
    }
    try {
      if (!Files.getPosixFilePermissions(secret, LinkOption.NOFOLLOW_LINKS)
          .equals(SECRET_PERMISSIONS)) {
        throw new IOException("Catalog signing key must have owner-only read/write permissions");
      }
    } catch (UnsupportedOperationException e) {
      throw new IOException("Catalog requires POSIX file permissions", e);
    }
    return Files.readAllBytes(secret);
  }

  private void writeAtomically(Path destination, byte[] bytes, boolean secret) throws IOException {
    Path temporary = directory.resolve(destination.getFileName().toString() + ".tmp");
    Files.deleteIfExists(temporary);
    if (secret) {
      try {
        Files.createFile(temporary, PosixFilePermissions.asFileAttribute(SECRET_PERMISSIONS));
      } catch (UnsupportedOperationException e) {
        throw new IOException("Catalog requires POSIX file permissions", e);
      }
    } else {
      Files.createFile(temporary);
    }
    try (FileChannel output =
        FileChannel.open(temporary, StandardOpenOption.WRITE, LinkOption.NOFOLLOW_LINKS)) {
      ByteBuffer buffer = ByteBuffer.wrap(bytes);
      while (buffer.hasRemaining()) {
        output.write(buffer);
      }
      output.force(true);
    }
    Files.move(temporary, destination, StandardCopyOption.ATOMIC_MOVE);
    try (FileChannel parent = FileChannel.open(directory, StandardOpenOption.READ)) {
      parent.force(true);
    }
  }

  private Path receiptPath(int number) {
    return directory.resolve(String.format(java.util.Locale.ROOT, "receipt-%08d.pb", number));
  }

  private ByteString encodeToken(Scope scope, int pageSize, long highwater, long cursor)
      throws IOException {
    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    try (DataOutputStream output = new DataOutputStream(bytes)) {
      output.writeInt(1);
      output.writeUTF(scope.getInstanceName());
      output.writeUTF(scope.getNamespaceId());
      output.writeInt(pageSize);
      output.writeLong(highwater);
      output.writeLong(cursor);
    }
    byte[] payload = bytes.toByteArray();
    bytes.write(sign(payload));
    return ByteString.copyFrom(bytes.toByteArray());
  }

  private long[] decodeToken(DiscoverRequest request, int pageSize) {
    byte[] token = request.getPageToken().toByteArray();
    if (token.length <= SIGNATURE_BYTES || token.length > 16 * 1024) {
      throw invalidToken("Malformed page token");
    }
    byte[] payload = Arrays.copyOf(token, token.length - SIGNATURE_BYTES);
    byte[] signature = Arrays.copyOfRange(token, payload.length, token.length);
    if (!MessageDigest.isEqual(sign(payload), signature)) {
      throw invalidToken("Invalid page token signature");
    }
    try (DataInputStream input = new DataInputStream(new ByteArrayInputStream(payload))) {
      if (input.readInt() != 1
          || !input.readUTF().equals(request.getScope().getInstanceName())
          || !input.readUTF().equals(request.getScope().getNamespaceId())
          || input.readInt() != pageSize) {
        throw invalidToken("Page token does not match the discovery query");
      }
      long highwater = input.readLong();
      long cursor = input.readLong();
      if (input.available() != 0
          || highwater < 1
          || highwater > receiptCount
          || cursor < 1
          || cursor > highwater) {
        throw invalidToken("Invalid page token bounds");
      }
      return new long[] {highwater, cursor};
    } catch (IOException e) {
      throw invalidToken("Malformed page token");
    }
  }

  private byte[] sign(byte[] payload) {
    try {
      Mac mac = Mac.getInstance("HmacSHA256");
      mac.init(new SecretKeySpec(signingKey, "HmacSHA256"));
      return mac.doFinal(payload);
    } catch (GeneralSecurityException e) {
      throw new IllegalStateException("HmacSHA256 is unavailable", e);
    }
  }

  private static void validateRequest(CommitRequest request) {
    validateScope(request.getScope());
    if (!request.hasScope()
        || !request.hasManifest()
        || !request.getManifest().getHash().matches("[0-9a-f]{64}")
        || request.getManifest().getSizeBytes() < 0
        || request.getIdempotencyKey().isEmpty()
        || request.getIdempotencyKey().length() > 4096) {
      throw invalidToken("Invalid catalog commit request");
    }
  }

  private static void validateScope(Scope scope) {
    if (scope.getInstanceName().length() > 128
        || scope.getNamespaceId().isEmpty()
        || scope.getNamespaceId().length() > 64) {
      throw invalidToken("Invalid catalog scope");
    }
  }

  private static io.grpc.StatusRuntimeException invalidToken(String message) {
    return Status.INVALID_ARGUMENT.withDescription(message).asRuntimeException();
  }

  private void checkOpen() throws IOException {
    if (closed || storageFailed) {
      throw new IOException("Catalog is closed or requires recovery after a storage failure");
    }
  }

  @Override
  public synchronized void close() throws IOException {
    if (!closed) {
      closed = true;
      try {
        lock.release();
      } finally {
        try {
          lockChannel.close();
        } finally {
          OPEN_DIRECTORIES.remove(directory);
        }
      }
    }
  }
}
