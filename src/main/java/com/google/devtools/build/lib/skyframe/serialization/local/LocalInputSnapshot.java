// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.charset.StandardCharsets.UTF_8;
import static java.nio.file.LinkOption.NOFOLLOW_LINKS;

import com.google.devtools.build.lib.versioning.IndependentFileDependencyProvider;
import java.io.IOException;
import java.io.InterruptedIOException;
import java.nio.file.Files;
import java.nio.file.NoSuchFileException;
import java.nio.file.Path;
import java.nio.file.attribute.BasicFileAttributes;
import java.nio.file.attribute.FileTime;
import java.nio.file.attribute.PosixFileAttributes;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorCompletionService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import javax.annotation.Nullable;

/**
 * Content evidence for a local analysis-cache invocation.
 *
 * <p>The project digest includes untracked and ignored source files, directory membership, modes
 * and symlinks. Only Git administration and Bazel's own output convenience links are excluded.
 * Observed file operations additionally cover Git metadata and files outside the project. This
 * deliberately favors conservative misses over treating a Git revision as a source snapshot.
 */
final class LocalInputSnapshot implements IndependentFileDependencyProvider {
  private static final int MAX_VALIDATION_WORKERS = 4;
  private static final int PARALLEL_VALIDATION_MINIMUM_WITNESSES = 16_384;

  record Witness(String path, boolean listing, String digest) {}

  // A cheap retry hint, never validity evidence. Any retry still verifies the content witness.
  record InputStamp(Path path, @Nullable Object fileKey, @Nullable FileTime modified, long size) {}

  private final Path workspace;
  private final Path outputBase;
  private final Path external;
  private final Map<String, String> environment;
  private final ConcurrentHashMap<String, Witness> witnesses = new ConcurrentHashMap<>();
  // Successful admission checks are hints for this invocation's entry validation only. They
  // do not become publication evidence until an entry actually uses them.
  private final HashSet<Witness> admittedWitnesses = new HashSet<>();
  private final int admissionWitnessLimit;
  private final String sourceDigest;
  private final String cacheNamespace;
  private volatile boolean changed;

  LocalInputSnapshot(Path workspace, Path outputBase, Map<String, String> environment)
      throws IOException {
    this(workspace, outputBase, environment, 250_000);
  }

  LocalInputSnapshot(
      Path workspace, Path outputBase, Map<String, String> environment, int admissionWitnessLimit)
      throws IOException {
    if (admissionWitnessLimit < 1) {
      throw new IllegalArgumentException("invalid admission witness capacity");
    }
    this.admissionWitnessLimit = admissionWitnessLimit;
    this.workspace = workspace.toAbsolutePath().normalize();
    this.outputBase = outputBase.toAbsolutePath().normalize();
    this.external = this.outputBase.resolve("external");
    this.environment = Map.copyOf(environment);
    this.cacheNamespace = captureNamespace();
    this.sourceDigest = captureSource();
  }

  String sourceDigest() {
    return sourceDigest;
  }

  String cacheNamespace() {
    return cacheNamespace;
  }

  /** Source dependencies are validated per entry; external bindings remain conservative guards. */
  List<Witness> globalWitnesses() {
    return witnesses().stream().filter(w -> !w.path().startsWith("workspace/")).toList();
  }

  /** Exact representation comparison after final validation; never authorizes a cache hit. */
  boolean hasSameGlobalWitnesses(List<Witness> expected) {
    if (changed
        || witnesses.values().stream().filter(w -> !w.path().startsWith("workspace/")).count()
            != expected.size()) {
      return false;
    }
    Witness previous = null;
    var order = Comparator.comparing(Witness::path).thenComparing(Witness::listing);
    for (Witness witness : expected) {
      // Only canonical, unique global tables can stand in for a freshly generated table.
      if (witness.path().startsWith("workspace/")
          || (previous != null && order.compare(previous, witness) >= 0)
          || !witness.equals(witnesses.get((witness.listing() ? "d:" : "f:") + witness.path()))) {
        return false;
      }
      previous = witness;
    }
    return true;
  }

  private String captureNamespace() throws IOException {
    MessageDigest digest = sha256();
    // Upstream test-configuration keys changed wire format. Keep old publications isolated.
    update(digest, "local-project-v4-content-sha256");
    update(digest, java.nio.ByteOrder.nativeOrder().toString());
    Path git = workspace.resolve(".git");
    if (!Files.exists(git, NOFOLLOW_LINKS)) {
      // Without Git metadata there is no safe way to infer that two directories are one project.
      update(digest, "workspace/" + workspace.toRealPath());
    } else {
      Path gitDirectory;
      if (Files.isDirectory(git)) {
        gitDirectory = git.toRealPath();
      } else {
        String pointer = readGitPointer(git);
        if (!pointer.startsWith("gitdir: ") || pointer.length() == 8) {
          throw new IOException("invalid worktree Git directory pointer");
        }
        gitDirectory = workspace.resolve(pointer.substring(8)).toRealPath();
      }
      Path commonPointer = gitDirectory.resolve("commondir");
      Path common =
          Files.exists(commonPointer, NOFOLLOW_LINKS)
              ? gitDirectory.resolve(readGitPointer(commonPointer)).toRealPath()
              : gitDirectory;
      if (!Files.isDirectory(common.resolve("objects"))
          || !Files.isRegularFile(common.resolve("HEAD"))) {
        throw new IOException("incomplete shared Git directory");
      }
      update(digest, "git-common/" + common);
    }
    update(digest, Integer.toString(environment.size()));
    for (var entry : new TreeMap<>(environment).entrySet()) {
      update(digest, entry.getKey());
      update(digest, entry.getValue());
    }
    return HexFormat.of().formatHex(digest.digest());
  }

  private static String readGitPointer(Path path) throws IOException {
    try (var stream = Files.newInputStream(path)) {
      byte[] bytes = stream.readNBytes(16385);
      String text = new String(bytes, UTF_8).stripTrailing();
      if (bytes.length > 16384
          || text.isEmpty()
          || text.indexOf('\n') >= 0
          || text.indexOf('\r') >= 0) {
        throw new IOException("invalid or oversized Git directory pointer");
      }
      return text;
    }
  }

  List<Witness> witnesses() {
    return witnesses.values().stream()
        .sorted(Comparator.comparing(Witness::path).thenComparing(Witness::listing))
        .toList();
  }

  void recordRepositoryBindings() throws IOException {
    if (!Files.isDirectory(external)) {
      return;
    }
    // FileValue can point directly into Bazel's shared repository contents cache. The bytes there
    // are insufficient evidence unless this reader binds the same repository name to that tree.
    for (Path repository : children(external)) {
      if (Files.isDirectory(repository) || Files.isSymbolicLink(repository)) {
        observe(repository, false);
      }
    }
  }

  /**
   * Registers used parents for the fresh final check. This does not validate or authorize reads:
   * callers must run {@link #isUnchanged} before publishing. Conflicting evidence is still
   * rejected, and identical witnesses shared by several parents are checked once in that final
   * pass.
   */
  void registerInheritedForFinalValidation(List<Witness> inherited) {
    inherited.forEach(this::remember);
  }

  /**
   * Validates one candidate witness and registers the exact evidence for final revalidation. A
   * stale candidate is a cache miss, not evidence that this invocation's inputs have changed.
   */
  boolean validateAndInclude(Witness witness) throws IOException {
    if (!matches(witness)) {
      return false;
    }
    remember(witness);
    return true;
  }

  boolean isUnchanged() throws IOException {
    return isUnchanged(
        witnesses.size() < PARALLEL_VALIDATION_MINIMUM_WITNESSES
            ? 1
            : Math.min(MAX_VALIDATION_WORKERS, Runtime.getRuntime().availableProcessors()));
  }

  /** Explicit worker count for exercising both validation paths on small test fixtures. */
  boolean isUnchanged(int parallelism) throws IOException {
    if (parallelism < 1 || parallelism > MAX_VALIDATION_WORKERS) {
      throw new IllegalArgumentException("invalid final validation worker count");
    }
    checkValidationInterrupted();
    var contents = new LocalContentHasher();
    if (changed
        || !cacheNamespace.equals(captureNamespace())
        || !sourceDigest.equals(captureSource(contents))) {
      return false;
    }
    if (parallelism == 1) {
      return matchesAll(witnesses.values(), contents) && !changed;
    }
    var partitions = new ArrayList<ArrayList<Witness>>(parallelism);
    for (int i = 0; i < parallelism; i++) {
      partitions.add(new ArrayList<>());
    }
    int next = 0;
    for (Witness witness : witnesses.values()) {
      checkValidationInterrupted();
      partitions.get(next++ % parallelism).add(witness);
    }
    // Only a handful of tasks, not a future per witness. Joining the executor on every exit keeps
    // validation inside the caller's publication/lease lifetime, including failure and
    // cancellation.
    try (var executor =
        Executors.newFixedThreadPool(
            parallelism, Thread.ofPlatform().name("skycache-final-validation-", 0).factory())) {
      var completion = new ExecutorCompletionService<Boolean>(executor);
      var pending = new ArrayList<Future<Boolean>>(parallelism);
      try {
        for (var partition : partitions) {
          var workerContents = new LocalContentHasher(contents);
          pending.add(completion.submit(() -> matchesAll(partition, workerContents)));
        }
        for (int i = 0; i < pending.size(); i++) {
          if (!completion.take().get()) {
            return false;
          }
        }
        return !changed;
      } catch (InterruptedException e) {
        Thread.currentThread().interrupt();
        throw new InterruptedIOException("interrupted local input validation");
      } catch (ExecutionException e) {
        if (e.getCause() instanceof IOException cause) {
          throw cause;
        }
        if (e.getCause() instanceof RuntimeException cause) {
          throw cause;
        }
        if (e.getCause() instanceof Error cause) {
          throw cause;
        }
        throw new IOException("local input validation failed", e.getCause());
      } finally {
        pending.forEach(task -> task.cancel(true));
      }
    }
  }

  private boolean matchesAll(Iterable<Witness> batch, LocalContentHasher contents)
      throws IOException {
    for (Witness witness : batch) {
      checkValidationInterrupted();
      if (!matches(witness, contents)) {
        return false;
      }
    }
    return true;
  }

  private static void checkValidationInterrupted() throws InterruptedIOException {
    if (Thread.currentThread().isInterrupted()) {
      throw new InterruptedIOException("interrupted local input validation");
    }
  }

  /** Always checks current content; only a success may later help entry validation. */
  boolean matchesForAdmission(Witness witness) throws IOException {
    return matchesForAdmission(witness, new LocalContentHasher());
  }

  boolean matchesForAdmission(Witness witness, LocalContentHasher contents) throws IOException {
    boolean matches = false;
    try {
      matches = matches(witness, contents);
      return matches;
    } finally {
      // Never hold this monitor across filesystem work or callbacks into a cache session.
      synchronized (admittedWitnesses) {
        if (!matches) {
          admittedWitnesses.remove(witness);
        } else if (admittedWitnesses.size() < admissionWitnessLimit) {
          admittedWitnesses.add(witness);
        }
      }
    }
  }

  /** Uses exact positive evidence from this command, registering it for fresh final checking. */
  boolean includeAdmittedWitness(Witness witness) {
    synchronized (admittedWitnesses) {
      if (!admittedWitnesses.contains(witness)) {
        return false;
      }
    }
    remember(witness);
    return true;
  }

  void clearAdmissionWitnesses() {
    synchronized (admittedWitnesses) {
      admittedWitnesses.clear();
    }
  }

  boolean matches(Witness witness) throws IOException {
    return matches(witness, new LocalContentHasher());
  }

  private boolean matches(Witness witness, LocalContentHasher contents) throws IOException {
    return witness
        .digest()
        .equals(fingerprint(resolve(witness.path()), witness.listing(), contents));
  }

  InputStamp inputStamp(Witness witness) throws IOException {
    return inputStamp(resolve(witness.path()));
  }

  static InputStamp inputStamp(Path path) throws IOException {
    try {
      // Follow links so a newly created referent or a changed repository binding triggers a retry.
      var attributes = Files.readAttributes(path, BasicFileAttributes.class);
      return new InputStamp(
          path, attributes.fileKey(), attributes.lastModifiedTime(), attributes.size());
    } catch (NoSuchFileException e) {
      return new InputStamp(path, null, null, -1);
    }
  }

  void observe(Path path, boolean listing) throws IOException {
    captureWitnesses(path, listing).forEach(this::remember);
  }

  @Override
  public byte[] captureFileDependency(
      com.google.devtools.build.lib.vfs.Path path, boolean listing, List<String> parentKeys)
      throws IOException {
    return captureDependency(Path.of(path.getPathString()), listing, parentKeys);
  }

  byte[] captureDependency(Path path, boolean listing, List<String> parentKeys) throws IOException {
    var observed = captureWitnesses(path, listing);
    observed.forEach(this::remember);
    return new LocalDependencyEvidence(parentKeys, observed).encode();
  }

  private List<Witness> captureWitnesses(Path path, boolean listing) throws IOException {
    var observed = new java.util.ArrayList<Witness>();
    var contents = new LocalContentHasher();
    Path normalized = path.toAbsolutePath().normalize();
    observed.add(
        new Witness(logicalPath(normalized), listing, fingerprint(normalized, listing, contents)));
    // Follow each component, retaining every link in the chain. In particular, do not normalize
    // "link/.." lexically: the parent is determined after resolving "link".
    var remaining = new ArrayDeque<Path>();
    normalized.forEach(remaining::addLast);
    Path resolved = normalized.getRoot();
    int links = 0;
    while (!remaining.isEmpty()) {
      String component = remaining.removeFirst().toString();
      if (component.equals(".")) {
        continue;
      }
      if (component.equals("..")) {
        resolved = resolved.getParent() == null ? resolved : resolved.getParent();
        continue;
      }
      Path next = resolved.resolve(component);
      if (!Files.isSymbolicLink(next)) {
        resolved = next;
        continue;
      }
      if (++links > 256) {
        throw new IOException("input symlink cycle or excessive depth: " + normalized);
      }
      observed.add(new Witness(logicalPath(next), false, fingerprint(next, false, contents)));
      Path target = Files.readSymbolicLink(next);
      if (target.isAbsolute()) {
        resolved = target.getRoot();
      }
      for (int i = target.getNameCount() - 1; i >= 0; --i) {
        remaining.addFirst(target.getName(i));
      }
    }
    if (!resolved.equals(normalized)) {
      observed.add(
          new Witness(logicalPath(resolved), listing, fingerprint(resolved, listing, contents)));
    }
    return observed;
  }

  private void remember(Witness witness) {
    Witness previous =
        witnesses.putIfAbsent((witness.listing() ? "d:" : "f:") + witness.path(), witness);
    if (previous != null && !previous.digest().equals(witness.digest())) {
      changed = true;
    }
  }

  private String logicalPath(Path path) {
    if (path.startsWith(workspace)) {
      return "workspace/" + workspace.relativize(path);
    }
    if (path.startsWith(external)) {
      return "external/" + external.relativize(path);
    }
    return "absolute/" + path;
  }

  private Path resolve(String logical) throws IOException {
    Path result;
    Path root;
    if (logical.startsWith("workspace/")) {
      root = workspace;
      result = root.resolve(logical.substring("workspace/".length())).normalize();
    } else if (logical.startsWith("external/")) {
      root = external;
      result = root.resolve(logical.substring("external/".length())).normalize();
    } else if (logical.startsWith("absolute/")) {
      result = Path.of(logical.substring("absolute/".length())).normalize();
      if (!result.isAbsolute()) {
        throw new IOException("relative absolute witness");
      }
      return result;
    } else {
      throw new IOException("unknown witness root");
    }
    if (!result.startsWith(root)) {
      throw new IOException("witness escapes its root");
    }
    return result;
  }

  private String captureSource() throws IOException {
    return captureSource(new LocalContentHasher());
  }

  private String captureSource(LocalContentHasher contents) throws IOException {
    MessageDigest digest = sha256();
    update(digest, "local-source-v11-content-sha256");
    update(digest, Integer.toString(environment.size()));
    for (var entry : new TreeMap<>(environment).entrySet()) {
      update(digest, entry.getKey());
      update(digest, entry.getValue());
    }
    update(digest, "source-files");
    captureDirectory(workspace, digest, new HashSet<>(), contents);
    return HexFormat.of().formatHex(digest.digest());
  }

  private void captureDirectory(
      Path directory, MessageDigest digest, Set<Path> ancestors, LocalContentHasher contents)
      throws IOException {
    Path realDirectory = directory.toRealPath();
    if (ancestors.size() >= 256 || !ancestors.add(realDirectory)) {
      throw new IOException("source directory symlink cycle or excessive depth: " + directory);
    }
    try {
      for (Path path : children(directory)) {
        String relative = workspace.relativize(path).toString();
        if (directory.equals(workspace)) {
          if (path.getFileName().toString().equals(".git")) {
            continue;
          }
          if (path.getFileName().toString().startsWith("bazel-") && Files.isSymbolicLink(path)) {
            Path target = path.getParent().resolve(Files.readSymbolicLink(path)).normalize();
            if (target.startsWith(outputBase)) {
              continue;
            }
          }
        }
        update(digest, relative);
        PosixFileAttributes attributes;
        try {
          attributes = Files.readAttributes(path, PosixFileAttributes.class, NOFOLLOW_LINKS);
        } catch (NoSuchFileException e) {
          update(digest, "missing");
          continue;
        }
        update(digest, fingerprint(path, contents, attributes));
        // Reuse this pass's type observation for ordinary entries. Symlink traversal still
        // requires a fresh followed lookup, including dangling links and directory aliases.
        if (attributes.isDirectory() || (attributes.isSymbolicLink() && Files.isDirectory(path))) {
          captureDirectory(path, digest, ancestors, contents);
        }
      }
    } finally {
      ancestors.remove(realDirectory);
    }
  }

  private static List<Path> children(Path directory) throws IOException {
    try (var stream = Files.list(directory)) {
      return stream.sorted(Comparator.comparing(p -> p.getFileName().toString())).toList();
    }
  }

  private String fingerprint(Path path, boolean listing, LocalContentHasher contents)
      throws IOException {
    if (listing) {
      return fingerprintListing(path);
    }
    PosixFileAttributes attributes;
    try {
      attributes = Files.readAttributes(path, PosixFileAttributes.class, NOFOLLOW_LINKS);
    } catch (NoSuchFileException e) {
      return "missing";
    }
    return fingerprint(path, contents, attributes);
  }

  private String fingerprintListing(Path path) throws IOException {
    BasicFileAttributes attributes;
    try {
      attributes = Files.readAttributes(path, BasicFileAttributes.class, NOFOLLOW_LINKS);
    } catch (NoSuchFileException e) {
      return "missing";
    }
    if (!attributes.isDirectory() && (!attributes.isSymbolicLink() || !Files.isDirectory(path))) {
      return "not-directory";
    }
    MessageDigest digest = sha256();
    update(digest, "listing");
    for (Path child : children(path)) {
      update(digest, child.getFileName().toString());
      BasicFileAttributes childAttributes =
          Files.readAttributes(child, BasicFileAttributes.class, NOFOLLOW_LINKS);
      update(digest, kind(childAttributes));
    }
    return HexFormat.of().formatHex(digest.digest());
  }

  private String fingerprint(Path path, LocalContentHasher contents, PosixFileAttributes attributes)
      throws IOException {
    // Type, size and permissions come from one fresh metadata read. This does not reuse metadata
    // across validation passes or replace the content digest with a timestamp/size check.
    MessageDigest digest = sha256();
    update(digest, kind(attributes));
    update(digest, PosixFilePermissions.toString(attributes.permissions()));
    if (attributes.isRegularFile()) {
      contents.hashContents(path, digest, attributes.size());
    } else if (attributes.isSymbolicLink()) {
      Path target = Files.readSymbolicLink(path);
      update(
          digest,
          target.isAbsolute() && target.startsWith(workspace)
              ? "workspace/" + workspace.relativize(target)
              : target.isAbsolute() && target.startsWith(outputBase)
                  ? "output/" + outputBase.relativize(target)
                  : target.toString());
      // Includes the referent bytes for file symlinks. Directory membership is observed
      // separately through the listing operation.
      try {
        PosixFileAttributes referent = Files.readAttributes(path, PosixFileAttributes.class);
        update(digest, kind(referent));
        update(digest, PosixFilePermissions.toString(referent.permissions()));
        if (referent.isRegularFile()) {
          contents.hashContents(path, digest, referent.size());
        }
      } catch (NoSuchFileException e) {
        update(digest, "missing");
      }
    } else if (!attributes.isDirectory()) {
      throw new IOException("unsupported input file type: " + path);
    }
    return HexFormat.of().formatHex(digest.digest());
  }

  private static String kind(BasicFileAttributes attributes) {
    return attributes.isRegularFile()
        ? "file"
        : attributes.isDirectory()
            ? "directory"
            : attributes.isSymbolicLink() ? "symlink" : "other";
  }

  private static void update(MessageDigest digest, String value) {
    byte[] bytes = value.getBytes(UTF_8);
    digest.update((byte) (bytes.length >>> 24));
    digest.update((byte) (bytes.length >>> 16));
    digest.update((byte) (bytes.length >>> 8));
    digest.update((byte) bytes.length);
    digest.update(bytes);
  }

  private static MessageDigest sha256() {
    try {
      return MessageDigest.getInstance("SHA-256");
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }

  @Override
  public long getFilePathOrSymlinkVersion(com.google.devtools.build.lib.vfs.Path path)
      throws IOException {
    observe(Path.of(path.getPathString()), false);
    return MINIMAL;
  }

  @Override
  public long getDirectoryListingVersion(com.google.devtools.build.lib.vfs.Path path)
      throws IOException {
    observe(Path.of(path.getPathString()), true);
    return MINIMAL;
  }

  @Override
  public long getNonexistentPathVersion(com.google.devtools.build.lib.vfs.Path path)
      throws IOException {
    observe(Path.of(path.getPathString()), false);
    return MINIMAL;
  }

  @Override
  public boolean recordExternalSymlinkDependencies(com.google.devtools.build.lib.vfs.Path path)
      throws IOException {
    observe(Path.of(path.getPathString()), false);
    return true;
  }

  @Override
  public boolean usesIndependentInvalidationData() {
    return true;
  }
}
