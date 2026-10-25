// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static java.nio.file.LinkOption.NOFOLLOW_LINKS;
import static java.nio.file.StandardOpenOption.CREATE_NEW;
import static java.nio.file.StandardOpenOption.READ;
import static java.nio.file.StandardOpenOption.WRITE;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.channels.FileChannel;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Set;
import java.util.zip.DataFormatException;
import java.util.zip.Deflater;
import java.util.zip.Inflater;

/** Immutable, bounded witness blocks; only their authenticated descriptors enter the manifest. */
final class LocalWitnessTable {
  private static final int MAX_BLOCKS = 16384;
  private static final int MAX_COUNT = 4096;
  private static final int TARGET_BYTES = 1024 * 1024;
  private static final int MAX_BYTES = TARGET_BYTES + 128 * 1024;
  private static final int MAX_PATH_BYTES = 65535;

  record Block(int count, int encodedBytes, int decodedBytes, long expandedBytes, String digest) {}

  static List<Block> write(Path directory, List<LocalInputSnapshot.Witness> witnesses)
      throws IOException {
    var blocks = new ArrayList<Block>();
    int offset = 0;
    while (offset < witnesses.size()) {
      if (blocks.size() >= MAX_BLOCKS) {
        throw new IOException("witness block count exceeds size limit");
      }
      var bytes = new ByteArrayOutputStream();
      int count = 0;
      long expandedBytes = 0;
      byte[] previous = new byte[0];
      try (var out = new DataOutputStream(bytes)) {
        while (offset < witnesses.size() && count < MAX_COUNT && bytes.size() < TARGET_BYTES) {
          var witness = witnesses.get(offset++);
          byte[] path = witness.path().getBytes(StandardCharsets.UTF_8);
          if (path.length > MAX_PATH_BYTES) {
            throw new IOException("witness path exceeds size limit");
          }
          int prefix = 0;
          while (prefix < Math.min(previous.length, path.length)
              && previous[prefix] == path[prefix]) {
            prefix++;
          }
          out.writeShort(prefix);
          out.writeShort(path.length - prefix);
          out.write(path, prefix, path.length - prefix);
          out.writeBoolean(witness.listing());
          String digest = witness.digest();
          if (digest.equals("missing")) {
            out.writeByte(1);
          } else if (digest.equals("not-directory")) {
            out.writeByte(2);
          } else {
            if (!digest.matches("[0-9a-f]{64}")) {
              throw new IOException("invalid witness digest");
            }
            out.writeByte(0);
            out.write(HexFormat.of().parseHex(digest));
          }
          expandedBytes += path.length + 38L;
          previous = path;
          count++;
        }
      }
      byte[] plain = bytes.toByteArray();
      var deflater = new Deflater(Deflater.BEST_SPEED, true);
      byte[] compressed;
      try {
        deflater.setInput(plain);
        deflater.finish();
        var encoded = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        while (!deflater.finished()) {
          int n = deflater.deflate(chunk);
          if (n == 0 || encoded.size() + n > MAX_BYTES) {
            throw new IOException("witness compression exceeds size limit");
          }
          encoded.write(chunk, 0, n);
        }
        compressed = encoded.toByteArray();
      } finally {
        deflater.end();
      }
      Path file = directory.resolve(filename(blocks.size()));
      try (var channel =
          FileChannel.open(
              file,
              Set.of(CREATE_NEW, WRITE, NOFOLLOW_LINKS),
              PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rw-------")))) {
        ByteBuffer buffer = ByteBuffer.wrap(compressed);
        while (buffer.hasRemaining()) {
          channel.write(buffer);
        }
        channel.force(true);
      }
      blocks.add(
          new Block(count, compressed.length, plain.length, expandedBytes, hash(compressed)));
    }
    try (var channel = FileChannel.open(directory, READ)) {
      channel.force(true);
    }
    return List.copyOf(blocks);
  }

  static void writeDescriptors(DataOutputStream out, List<Block> blocks) throws IOException {
    out.writeInt(blocks.size());
    for (Block block : blocks) {
      out.writeInt(block.count());
      out.writeInt(block.encodedBytes());
      out.writeInt(block.decodedBytes());
      out.writeLong(block.expandedBytes());
      out.write(HexFormat.of().parseHex(block.digest()));
    }
  }

  static List<Block> readDescriptors(DataInputStream in) throws IOException {
    int count = in.readInt();
    if (count < 0 || count > MAX_BLOCKS) {
      throw new IOException("invalid witness block count");
    }
    var blocks = new ArrayList<Block>();
    for (int i = 0; i < count; i++) {
      int entries = in.readInt();
      int encoded = in.readInt();
      int decoded = in.readInt();
      long expanded = in.readLong();
      byte[] digest = new byte[32];
      in.readFully(digest);
      if (entries < 1
          || entries > MAX_COUNT
          || encoded < 1
          || encoded > MAX_BYTES
          || decoded < 1
          || decoded > MAX_BYTES
          || expanded < 38L * entries
          || expanded > (MAX_PATH_BYTES + 38L) * entries) {
        throw new IOException("invalid witness block bounds");
      }
      blocks.add(new Block(entries, encoded, decoded, expanded, HexFormat.of().formatHex(digest)));
    }
    return List.copyOf(blocks);
  }

  /** Charge fully expanded paths too: prefix compression must not bypass the admission budget. */
  static long metadataBytes(List<Block> blocks) {
    return blocks.stream()
        .mapToLong(b -> Math.max(Math.max(b.encodedBytes(), b.decodedBytes()), b.expandedBytes()))
        .sum();
  }

  static List<LocalInputSnapshot.Witness> read(Path directory, List<Block> blocks, long budget)
      throws IOException {
    if (metadataBytes(blocks) > budget) {
      throw new IOException("witness blocks exceed metadata budget");
    }
    var witnesses = new ArrayList<LocalInputSnapshot.Witness>();
    for (int i = 0; i < blocks.size(); i++) {
      Block block = blocks.get(i);
      byte[] encoded = new byte[block.encodedBytes()];
      try (var channel = FileChannel.open(directory.resolve(filename(i)), READ, NOFOLLOW_LINKS)) {
        if (channel.size() != encoded.length) {
          throw new IOException("witness block length mismatch");
        }
        ByteBuffer buffer = ByteBuffer.wrap(encoded);
        while (buffer.hasRemaining()) {
          if (channel.read(buffer) < 0) {
            throw new IOException("truncated witness block");
          }
        }
      }
      if (!hash(encoded).equals(block.digest())) {
        throw new IOException("witness block checksum mismatch");
      }
      byte[] plain = new byte[block.decodedBytes()];
      var inflater = new Inflater(true);
      try {
        inflater.setInput(encoded);
        int position = 0;
        while (!inflater.finished() && position < plain.length) {
          int n = inflater.inflate(plain, position, plain.length - position);
          if (n == 0) {
            throw new IOException("incomplete witness compression stream");
          }
          position += n;
        }
        if (position != plain.length || !inflater.finished() || inflater.getRemaining() != 0) {
          throw new IOException("witness decompression bounds mismatch");
        }
      } catch (DataFormatException e) {
        throw new IOException("invalid witness compression stream", e);
      } finally {
        inflater.end();
      }
      try (var in = new DataInputStream(new ByteArrayInputStream(plain))) {
        byte[] previous = new byte[0];
        long expanded = 0;
        for (int n = 0; n < block.count(); n++) {
          int prefix = in.readUnsignedShort();
          int suffix = in.readUnsignedShort();
          if (prefix > previous.length || prefix + suffix > MAX_PATH_BYTES) {
            throw new IOException("invalid witness path prefix");
          }
          expanded += prefix + suffix + 38L;
          if (expanded > block.expandedBytes()) {
            throw new IOException("expanded witness paths exceed metadata budget");
          }
          byte[] path = java.util.Arrays.copyOf(previous, prefix + suffix);
          in.readFully(path, prefix, suffix);
          boolean listing = in.readBoolean();
          int tag = in.readUnsignedByte();
          String digest;
          if (tag == 0) {
            byte[] value = new byte[32];
            in.readFully(value);
            digest = HexFormat.of().formatHex(value);
          } else if (tag == 1) {
            digest = "missing";
          } else if (tag == 2) {
            digest = "not-directory";
          } else {
            throw new IOException("invalid witness digest tag");
          }
          String name;
          try {
            name =
                StandardCharsets.UTF_8
                    .newDecoder()
                    .onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT)
                    .decode(ByteBuffer.wrap(path))
                    .toString();
          } catch (CharacterCodingException e) {
            throw new IOException("invalid witness path encoding", e);
          }
          witnesses.add(new LocalInputSnapshot.Witness(name, listing, digest));
          previous = path;
        }
        if (expanded != block.expandedBytes() || in.read() != -1) {
          throw new IOException("trailing witness block bytes");
        }
      }
    }
    return List.copyOf(witnesses);
  }

  static String filename(int index) {
    return String.format(java.util.Locale.ROOT, "witnesses-%05d.block", index);
  }

  private static String hash(byte[] bytes) {
    try {
      return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    } catch (NoSuchAlgorithmException e) {
      throw new AssertionError(e);
    }
  }

  private LocalWitnessTable() {}
}
