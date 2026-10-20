// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import static com.google.common.truth.Truth.assertThat;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.devtools.build.lib.vfs.DigestHashFunction;
import com.google.devtools.build.lib.vfs.inmemoryfs.InMemoryFileSystem;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.ByteArrayOutputStream;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class LocalPathCodecTest {
  private final InMemoryFileSystem fs = new InMemoryFileSystem(DigestHashFunction.SHA256);
  private final LocalPathCodec writer =
      new LocalPathCodec(fs.getPath("/work/a"), fs.getPath("/output/a"));
  private final LocalPathCodec reader =
      new LocalPathCodec(fs.getPath("/work/b"), fs.getPath("/output/b"));

  @Test
  public void relocatesWorkspaceAndOutputPathsButPreservesOtherAbsolutePaths() throws Exception {
    check("/work/a/lib/source", "/work/b/lib/source");
    check(
        "/output/a/execroot/_main/bazel-out/bin/result",
        "/output/b/execroot/_main/bazel-out/bin/result");
    check("/output/a/external/repo/file", "/output/b/external/repo/file");
    check("/work/another/file", "/work/another/file");
    check("/usr/bin/tool", "/usr/bin/tool");
    check("/work/a", "/work/b");
  }

  private void check(String before, String after) throws Exception {
    var buffer = new ByteArrayOutputStream();
    var out = CodedOutputStream.newInstance(buffer);
    writer.serialize(null, fs.getPath(before), out);
    out.flush();
    assertThat(reader.deserialize(null, CodedInputStream.newInstance(buffer.toByteArray())))
        .isEqualTo(fs.getPath(after));
  }

  @Test
  public void rejectsUnknownKindsAndEscapingPaths() throws Exception {
    reject(3, "path");
    reject(1, "../escape");
    reject(2, "/absolute");
    reject(0, "relative");
  }

  private void reject(int kind, String path) throws Exception {
    var buffer = new ByteArrayOutputStream();
    var out = CodedOutputStream.newInstance(buffer);
    out.writeInt32NoTag(kind);
    out.writeStringNoTag(path);
    out.flush();
    assertThrows(
        SerializationException.class,
        () -> reader.deserialize(null, CodedInputStream.newInstance(buffer.toByteArray())));
  }
}
