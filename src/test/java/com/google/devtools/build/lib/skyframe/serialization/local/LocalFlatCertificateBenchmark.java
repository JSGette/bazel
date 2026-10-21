// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.devtools.build.lib.skyframe.serialization.FingerprintValueService.NONPROD_FINGERPRINTER;
import static java.nio.file.LinkOption.NOFOLLOW_LINKS;

import com.google.devtools.build.lib.compress.CompressionServiceImpl;
import com.google.devtools.build.lib.skyframe.serialization.PackedFingerprint;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.lang.management.ManagementFactory;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.FileTime;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HexFormat;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Experimental fixed-batch certificate, not a production cache format or full-build benchmark.
 *
 * <p>Usage: workspace output_base cache_root root_hex_file environment_binary artifact_prefix. Run
 * only against a stable fixture, with no concurrent builds or collector. Every cache session
 * finishes unsuccessfully and performs no puts. Complete pack/index/manifest hashes bind the proof
 * before and after the experiment, outside measured validation. The preparation session pins its
 * used publication closure until all rounds finish. A changed fixture invalidates the whole run.
 */
public final class LocalFlatCertificateBenchmark {
  private static final int MAGIC = 0x534b4533;
  private static final int MAX_CERTIFICATE_BYTES = 256 * 1024 * 1024;
  private static final int MAX_ROOTS = 250_000;
  private static final int MAX_WITNESSES = 1_000_000;
  private static final HexFormat HEX = HexFormat.of();

  private record Certificate(
      String namespace,
      String binding,
      List<String> roots,
      List<LocalInputSnapshot.Witness> witnesses) {}

  private record BoundFile(String path, long bytes, String sha256) {}

  private record Binding(String sha256, long bytes, long nanos, List<BoundFile> files) {}

  private record FlatResult(boolean valid, long admitted, long fresh) {}

  public static void main(String[] args) throws Exception {
    if (args.length != 6) {
      throw new IllegalArgumentException(
          "workspace output_base cache_root root_hex_file environment_binary artifact_prefix");
    }
    Path workspace = Path.of(args[0]);
    Path output = Path.of(args[1]);
    Path cache = Path.of(args[2]);
    Path rootFile = Path.of(args[3]);
    var environment = readEnvironment(Path.of(args[4]));
    Path prefix = Path.of(args[5]).toAbsolutePath();
    Files.createDirectories(prefix.getParent());
    Path certificatePath = Path.of(prefix + ".certificate");
    Path resultPath = Path.of(prefix + ".json");
    Path progressPath = Path.of(prefix + ".jsonl");
    if (Files.exists(certificatePath) || Files.exists(resultPath) || Files.exists(progressPath)) {
      throw new IllegalArgumentException("experiment artifacts already exist");
    }
    byte[] rootFileBytes = Files.readAllBytes(rootFile);
    String rootFileSha256 = sha256(rootFileBytes);
    var roots =
        new String(rootFileBytes, java.nio.charset.StandardCharsets.UTF_8)
            .lines()
            .filter(s -> !s.isBlank())
            .sorted()
            .distinct()
            .toList();
    if (roots.isEmpty() || roots.size() > MAX_ROOTS) {
      throw new IllegalArgumentException("invalid root count");
    }
    for (String root : roots) {
      if (!root.matches("[0-9a-f]{32}")) {
        throw new IllegalArgumentException("invalid dependency root: " + root);
      }
    }
    var keys = roots.stream().map(s -> PackedFingerprint.fromBytes(HEX.parseHex(s))).toList();
    var allocation = (com.sun.management.ThreadMXBean) ManagementFactory.getThreadMXBean();
    if (allocation.isThreadAllocatedMemorySupported()) {
      allocation.setThreadAllocatedMemoryEnabled(true);
    }
    long started = System.nanoTime();
    long sourceStart = System.nanoTime();
    var inputs = new LocalInputSnapshot(workspace, output, environment);
    long preparationSourceNanos = System.nanoTime() - sourceStart;
    Binding before = bindFixture(cache, inputs.cacheNamespace());
    long sessionStart = System.nanoTime();
    var preparationStore = new LocalCacheSession(cache, inputs);
    long preparationSessionNanos = System.nanoTime() - sessionStart;
    var rows = new ArrayList<String>();
    String probes;
    Binding after;
    long preparationAdmissionNanos;
    long preparationValidationNanos;
    long preparationWriteNanos;
    long preparationFinalCheckNanos;
    String preparationStats;
    Certificate certificate;
    byte[] encoded;
    try {
      long stageStart = System.nanoTime();
      primeAdmission(preparationStore, keys.getFirst());
      preparationAdmissionNanos = System.nanoTime() - stageStart;
      var validator = validator(preparationStore, inputs);
      stageStart = System.nanoTime();
      validateRoots(validator, keys);
      preparationValidationNanos = System.nanoTime() - stageStart;
      preparationStats = validator.statisticsJson();
      certificate =
          new Certificate(inputs.cacheNamespace(), before.sha256(), roots, inputs.witnesses());
      stageStart = System.nanoTime();
      if (!inputs.isUnchanged()) {
        throw new IllegalStateException("preparation inputs changed");
      }
      preparationFinalCheckNanos = System.nanoTime() - stageStart;
      stageStart = System.nanoTime();
      encoded = encode(certificate);
      Files.write(certificatePath, encoded, StandardOpenOption.CREATE_NEW);
      preparationWriteNanos = System.nanoTime() - stageStart;
      String expectedCertificateDigest = sha256(encoded);
      Files.writeString(
          progressPath,
          "{\"stage\":\"prepared\",\"root_count\":"
              + roots.size()
              + ",\"witness_count\":"
              + certificate.witnesses().size()
              + ",\"certificate_bytes\":"
              + encoded.length
              + ",\"certificate_sha256\":"
              + quote(expectedCertificateDigest)
              + ",\"fixture_binding_sha256\":"
              + quote(before.sha256())
              + ",\"source_ms\":"
              + millis(preparationSourceNanos)
              + ",\"admission_ms\":"
              + millis(preparationAdmissionNanos)
              + ",\"validation_ms\":"
              + millis(preparationValidationNanos)
              + ",\"final_input_check_ms\":"
              + millis(preparationFinalCheckNanos)
              + ",\"encode_write_ms\":"
              + millis(preparationWriteNanos)
              + "}\n",
          StandardOpenOption.CREATE_NEW);
      // Alternate ordering to reduce systematic first/second bias. Both paths use fresh sessions,
      // the same admission lookup and their own fresh LocalInputSnapshot on every round.
      for (int round = -2; round < 3; round++) {
        boolean flatFirst = (round & 1) != 0;
        for (int ordinal = 0; ordinal < 2; ordinal++) {
          boolean flat = ordinal == 0 ? flatFirst : !flatFirst;
          String row =
              measure(
                  workspace,
                  output,
                  cache,
                  environment,
                  certificatePath,
                  certificate.namespace(),
                  certificate.binding(),
                  expectedCertificateDigest,
                  roots,
                  keys,
                  allocation,
                  round,
                  ordinal,
                  flat);
          rows.add(row);
          Files.writeString(progressPath, row + "\n", StandardOpenOption.APPEND);
        }
      }
      probes = mutationProbes(prefix.getParent());
      after = bindFixture(cache, inputs.cacheNamespace());
      if (!before.sha256().equals(after.sha256())) {
        throw new IllegalStateException("pack/index/manifest fixture changed during experiment");
      }
      if (!inputs.isUnchanged()) {
        throw new IllegalStateException("preparation inputs changed before final fixture check");
      }
      if (!rootFileSha256.equals(sha256(Files.readAllBytes(rootFile)))) {
        throw new IllegalStateException("input root list changed during experiment");
      }
    } finally {
      preparationStore.finish(false);
    }
    String result =
        "{\"experiment\":\"E3-flat-fixed-batch-certificate\","
            + "\"production_safe_format\":false,\"full_build_measurement\":false,"
            + "\"valid\":true,\"fixture_unchanged\":true,\"cache_puts\":0,\"fresh_matching_semantics\":\"same"
            + " admission memo and validateAndInclude as baseline\","
            + "\"source_and_admission_outside_validation_window\":true,"
            + "\"pack_binding_outside_validation_window\":true,"
            + "\"warmups_per_path\":2,\"measured_rounds_per_path\":3,\"namespace\":"
            + quote(certificate.namespace())
            + ","
            + "\"root_file_sha256\":"
            + quote(rootFileSha256)
            + ","
            + "\"root_count\":"
            + roots.size()
            + ","
            + "\"witness_count\":"
            + certificate.witnesses().size()
            + ","
            + "\"certificate_bytes\":"
            + encoded.length
            + ","
            + "\"certificate_sha256\":"
            + quote(sha256(encoded))
            + ","
            + "\"fixture_binding_sha256\":"
            + quote(before.sha256())
            + ","
            + "\"fixture_bytes\":"
            + before.bytes()
            + ","
            + "\"fixture_hash_before_ms\":"
            + millis(before.nanos())
            + ","
            + "\"fixture_hash_after_ms\":"
            + millis(after.nanos())
            + ","
            + "\"preparation\":{\"source_ms\":"
            + millis(preparationSourceNanos)
            + ",\"session_ms\":"
            + millis(preparationSessionNanos)
            + ",\"admission_ms\":"
            + millis(preparationAdmissionNanos)
            + ",\"validation_ms\":"
            + millis(preparationValidationNanos)
            + ",\"final_input_check_ms\":"
            + millis(preparationFinalCheckNanos)
            + ",\"encode_write_ms\":"
            + millis(preparationWriteNanos)
            + ",\"validation_stats\":"
            + preparationStats
            + "},"
            + "\"bound_files\":["
            + String.join(
                ",",
                before.files().stream()
                    .map(
                        f ->
                            "{\"path\":"
                                + quote(f.path())
                                + ",\"bytes\":"
                                + f.bytes()
                                + ",\"sha256\":"
                                + quote(f.sha256())
                                + "}")
                    .toList())
            + "],"
            + "\"rounds\":["
            + String.join(",", rows)
            + "],"
            + "\"negative_probes\":"
            + probes
            + ","
            + "\"elapsed_ms\":"
            + millis(System.nanoTime() - started)
            + "}\n";
    Files.writeString(resultPath, result, StandardOpenOption.CREATE_NEW);
    Files.writeString(
        progressPath,
        "{\"stage\":\"completed\",\"valid\":true,\"fixture_unchanged\":true}\n",
        StandardOpenOption.APPEND);
    System.out.print(result);
  }

  private static String measure(
      Path workspace,
      Path output,
      Path cache,
      Map<String, String> environment,
      Path certificatePath,
      String namespace,
      String binding,
      String expectedCertificateDigest,
      List<String> roots,
      List<PackedFingerprint> keys,
      com.sun.management.ThreadMXBean allocation,
      int round,
      int ordinal,
      boolean flat)
      throws Exception {
    long stageStart = System.nanoTime();
    var inputs = new LocalInputSnapshot(workspace, output, environment);
    long sourceNanos = System.nanoTime() - stageStart;
    if (!inputs.cacheNamespace().equals(namespace)) {
      throw new IllegalStateException("round namespace changed");
    }
    stageStart = System.nanoTime();
    var store = new LocalCacheSession(cache, inputs);
    long sessionNanos = System.nanoTime() - stageStart;
    try {
      stageStart = System.nanoTime();
      primeAdmission(store, keys.getFirst());
      long admissionNanos = System.nanoTime() - stageStart;
      var validator = validator(store, inputs);
      long beforeBytes = allocated(allocation);
      stageStart = System.nanoTime();
      FlatResult flatResult = null;
      if (flat) {
        byte[] bytes = readCertificate(certificatePath);
        if (!sha256(bytes).equals(expectedCertificateDigest)) {
          throw new IOException("certificate changed after verified preparation");
        }
        Certificate loaded = decode(bytes, namespace, binding, roots);
        flatResult = validateFlat(inputs, loaded.witnesses());
        if (!flatResult.valid()) {
          throw new IllegalStateException("flat certificate rejected fixture");
        }
      } else {
        validateRoots(validator, keys);
      }
      long validationNanos = System.nanoTime() - stageStart;
      long afterBytes = allocated(allocation);
      long allocatedBytes = beforeBytes < 0 || afterBytes < 0 ? -1 : afterBytes - beforeBytes;
      stageStart = System.nanoTime();
      if (!inputs.isUnchanged()) {
        throw new IllegalStateException("round inputs changed");
      }
      long finalCheckNanos = System.nanoTime() - stageStart;
      return "{\"round\":"
          + round
          + ",\"measured\":"
          + (round >= 0)
          + ",\"ordinal\":"
          + ordinal
          + ",\"path\":"
          + quote(flat ? "flat" : "normal")
          + ",\"source_ms\":"
          + millis(sourceNanos)
          + ",\"session_ms\":"
          + millis(sessionNanos)
          + ",\"admission_ms\":"
          + millis(admissionNanos)
          + ",\"validation_ms\":"
          + millis(validationNanos)
          + ",\"source_session_admission_validation_ms\":"
          + millis(sourceNanos + sessionNanos + admissionNanos + validationNanos)
          + ",\"final_input_check_ms\":"
          + millis(finalCheckNanos)
          + ",\"validation_allocated_bytes\":"
          + allocatedBytes
          + ",\"flat_admission_memo_hits\":"
          + (flatResult == null ? -1 : flatResult.admitted())
          + ",\"flat_fresh_witness_checks\":"
          + (flatResult == null ? -1 : flatResult.fresh())
          + ",\"normal_validation_stats\":"
          + validator.statisticsJson()
          + ",\"store_stats\":"
          + store.statisticsJson(false)
          + "}";
    } finally {
      store.finish(false);
    }
  }

  private static LocalDependencyValidator validator(
      LocalCacheSession store, LocalInputSnapshot inputs) {
    return new LocalDependencyValidator(
        store, inputs, NONPROD_FINGERPRINTER, new CompressionServiceImpl());
  }

  private static void primeAdmission(LocalCacheSession store, PackedFingerprint root)
      throws Exception {
    // Both paths read the same one record after the same candidate-discovery/admission work.
    if (store.get(root).get() == null) {
      throw new IllegalStateException("admission root not found");
    }
  }

  private static void validateRoots(
      LocalDependencyValidator validator, List<PackedFingerprint> roots) throws Exception {
    for (var root : roots) {
      if (!validator.validate(root)) {
        throw new IllegalStateException("root rejected: " + root.toHex());
      }
    }
  }

  private static FlatResult validateFlat(
      LocalInputSnapshot inputs, List<LocalInputSnapshot.Witness> witnesses) throws IOException {
    long admitted = 0;
    long fresh = 0;
    for (var witness : witnesses) {
      if (inputs.includeAdmittedWitness(witness)) {
        admitted++;
      } else {
        fresh++;
        // Exactly the baseline's non-admitted check; do not give this path an extra content memo.
        if (!inputs.validateAndInclude(witness)) {
          return new FlatResult(false, admitted, fresh);
        }
      }
    }
    return new FlatResult(true, admitted, fresh);
  }

  private static byte[] encode(Certificate certificate) throws IOException {
    if (certificate.roots().size() > MAX_ROOTS || certificate.witnesses().size() > MAX_WITNESSES) {
      throw new IOException("certificate count exceeds bound");
    }
    var buffer = new ByteArrayOutputStream();
    try (var out = new DataOutputStream(buffer)) {
      out.writeInt(MAGIC);
      out.writeUTF(certificate.namespace());
      out.writeUTF(certificate.binding());
      out.writeInt(certificate.roots().size());
      for (String root : certificate.roots()) {
        out.writeUTF(root);
      }
      out.writeInt(certificate.witnesses().size());
      for (var witness : certificate.witnesses()) {
        out.writeUTF(witness.path());
        out.writeBoolean(witness.listing());
        out.writeUTF(witness.digest());
      }
    }
    byte[] payload = buffer.toByteArray();
    if (payload.length > MAX_CERTIFICATE_BYTES - 32) {
      throw new IOException("certificate exceeds byte bound");
    }
    buffer.writeBytes(digest(payload));
    return buffer.toByteArray();
  }

  private static byte[] readCertificate(Path path) throws IOException {
    if (Files.size(path) > MAX_CERTIFICATE_BYTES) {
      throw new IOException("certificate file too large");
    }
    try (var input = Files.newInputStream(path)) {
      byte[] bytes = input.readNBytes(MAX_CERTIFICATE_BYTES + 1);
      if (bytes.length > MAX_CERTIFICATE_BYTES) {
        throw new IOException("certificate grew beyond byte bound");
      }
      return bytes;
    }
  }

  private static Certificate decode(
      byte[] bytes, String namespace, String binding, List<String> expectedRoots)
      throws IOException {
    if (bytes.length < 36 || bytes.length > MAX_CERTIFICATE_BYTES) {
      throw new IOException("invalid certificate length");
    }
    int payloadSize = bytes.length - 32;
    byte[] payload = Arrays.copyOf(bytes, payloadSize);
    if (!MessageDigest.isEqual(
        digest(payload), Arrays.copyOfRange(bytes, payloadSize, bytes.length))) {
      throw new IOException("certificate checksum mismatch");
    }
    try (var input = new DataInputStream(new ByteArrayInputStream(payload))) {
      if (input.readInt() != MAGIC
          || !input.readUTF().equals(namespace)
          || !input.readUTF().equals(binding)) {
        throw new IOException("certificate identity mismatch");
      }
      int roots = count(input, MAX_ROOTS);
      var observedRoots = new ArrayList<String>(roots);
      for (int i = 0; i < roots; i++) {
        observedRoots.add(input.readUTF());
      }
      if (!observedRoots.equals(expectedRoots)) {
        throw new IOException("certificate root set mismatch");
      }
      int witnesses = count(input, MAX_WITNESSES);
      var observed = new ArrayList<LocalInputSnapshot.Witness>(witnesses);
      for (int i = 0; i < witnesses; i++) {
        observed.add(
            new LocalInputSnapshot.Witness(input.readUTF(), input.readBoolean(), input.readUTF()));
      }
      if (input.read() != -1) {
        throw new IOException("trailing certificate bytes");
      }
      return new Certificate(namespace, binding, List.copyOf(observedRoots), List.copyOf(observed));
    }
  }

  private static Binding bindFixture(Path cache, String namespace) throws Exception {
    long start = System.nanoTime();
    Path managed = cache.resolve("leased-v1");
    Path publications = managed.resolve("publications-v3").resolve(namespace);
    List<Path> manifests;
    try (var stream = Files.list(publications)) {
      manifests =
          stream.filter(p -> p.getFileName().toString().endsWith(".manifest")).sorted().toList();
    }
    if (manifests.isEmpty()) {
      throw new IOException("no published fixture in namespace " + namespace);
    }
    var paths = new ArrayList<Path>();
    for (Path manifest : manifests) {
      String name = manifest.getFileName().toString();
      if (!name.matches("[0-9a-f]{64}\\.manifest")) {
        throw new IOException("invalid fixture publication name");
      }
      paths.add(manifest);
      Path pack = managed.resolve("objects/packed-v1").resolve(name.substring(0, 64));
      paths.add(pack.resolve("index"));
      paths.add(pack.resolve("data.pack"));
    }
    paths.sort(Comparator.comparing(p -> managed.relativize(p).toString()));
    var files = new ArrayList<BoundFile>();
    long total = 0;
    var buffer = new byte[1024 * 1024];
    var encoded = new ByteArrayOutputStream();
    try (var out = new DataOutputStream(encoded)) {
      out.writeUTF(namespace);
      for (Path path : paths) {
        if (!Files.isRegularFile(path, NOFOLLOW_LINKS)) {
          throw new IOException("fixture file is absent or is a symlink: " + path);
        }
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        long bytes = 0;
        try (var input = Files.newInputStream(path)) {
          int n;
          while ((n = input.read(buffer)) != -1) {
            digest.update(buffer, 0, n);
            bytes += n;
          }
        }
        String relative = managed.relativize(path).toString();
        String hash = HEX.formatHex(digest.digest());
        files.add(new BoundFile(relative, bytes, hash));
        out.writeUTF(relative);
        out.writeLong(bytes);
        out.writeUTF(hash);
        total += bytes;
      }
    }
    return new Binding(
        sha256(encoded.toByteArray()), total, System.nanoTime() - start, List.copyOf(files));
  }

  private static String mutationProbes(Path parent) throws Exception {
    Path root = Files.createTempDirectory(parent, "e3-negative-probes-");
    try {
      for (String probe :
          List.of("same_size_restored_mtime", "directory_membership", "symlink_retarget")) {
        Path workspace = Files.createDirectory(root.resolve(probe));
        Path output = Files.createDirectory(root.resolve(probe + "-output"));
        Path first = Files.writeString(workspace.resolve("first"), "one");
        Path second = Files.writeString(workspace.resolve("second"), "one");
        Path link = Files.createSymbolicLink(workspace.resolve("link"), Path.of("first"));
        var inputs = new LocalInputSnapshot(workspace, output, Map.of());
        Path observed =
            probe.equals("directory_membership")
                ? workspace
                : probe.equals("symlink_retarget") ? link : first;
        inputs.observe(observed, probe.equals("directory_membership"));
        var certificate =
            new Certificate(
                inputs.cacheNamespace(), "isolated-probe", List.of(), inputs.witnesses());
        Certificate loaded =
            decode(
                encode(certificate),
                certificate.namespace(),
                certificate.binding(),
                certificate.roots());
        if (!validateFlat(inputs, loaded.witnesses()).valid()) {
          throw new IllegalStateException("probe initially invalid: " + probe);
        }
        switch (probe) {
          case "same_size_restored_mtime" -> {
            FileTime time = Files.getLastModifiedTime(first);
            Files.writeString(first, "two");
            Files.setLastModifiedTime(first, time);
          }
          case "directory_membership" -> Files.writeString(workspace.resolve("added"), "new");
          case "symlink_retarget" -> {
            Files.delete(link);
            Files.createSymbolicLink(link, second.getFileName());
          }
          default -> throw new AssertionError(probe);
        }
        if (validateFlat(inputs, loaded.witnesses()).valid()) {
          throw new IllegalStateException("negative probe accepted mutation: " + probe);
        }
      }
      return "{\"same_size_restored_mtime_rejected\":true,\"directory_membership_rejected\":true,\"symlink_retarget_rejected\":true}";
    } finally {
      try (var paths = Files.walk(root)) {
        for (Path path : paths.sorted(Comparator.reverseOrder()).toList()) {
          Files.delete(path);
        }
      }
    }
  }

  private static Map<String, String> readEnvironment(Path path) throws IOException {
    var result = new HashMap<String, String>();
    try (var input = new DataInputStream(Files.newInputStream(path))) {
      for (int i = count(input, 10_000); i > 0; i--) {
        if (result.put(input.readUTF(), input.readUTF()) != null) {
          throw new IOException("duplicate environment key");
        }
      }
      if (input.read() != -1) {
        throw new IOException("trailing environment bytes");
      }
    }
    return Map.copyOf(result);
  }

  private static int count(DataInputStream input, int maximum) throws IOException {
    int value = input.readInt();
    if (value < 0 || value > maximum) {
      throw new IOException("count outside bounds");
    }
    return value;
  }

  private static byte[] digest(byte[] bytes) {
    try {
      return MessageDigest.getInstance("SHA-256").digest(bytes);
    } catch (java.security.NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }

  private static String sha256(byte[] bytes) {
    return HEX.formatHex(digest(bytes));
  }

  private static long allocated(com.sun.management.ThreadMXBean allocation) {
    return allocation.isThreadAllocatedMemoryEnabled()
        ? allocation.getThreadAllocatedBytes(Thread.currentThread().threadId())
        : -1;
  }

  private static String millis(long nanos) {
    return String.format(Locale.ROOT, "%.3f", nanos / 1e6);
  }

  private static String quote(String value) {
    var out = new StringBuilder("\"");
    for (int i = 0; i < value.length(); i++) {
      char ch = value.charAt(i);
      switch (ch) {
        case '\\' -> out.append("\\\\");
        case '\"' -> out.append("\\\"");
        case '\n' -> out.append("\\n");
        case '\r' -> out.append("\\r");
        case '\t' -> out.append("\\t");
        default -> {
          if (ch < 0x20) {
            out.append(String.format(Locale.ROOT, "\\u%04x", (int) ch));
          } else {
            out.append(ch);
          }
        }
      }
    }
    return out.append('\"').toString();
  }
}
