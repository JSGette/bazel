// Copyright 2026 The Bazel Authors. All rights reserved.
// Licensed under the Apache License, Version 2.0. See the repository LICENSE.
package com.google.devtools.build.lib.skyframe.serialization.local;

import com.google.devtools.build.lib.skyframe.serialization.DeserializationContext;
import com.google.devtools.build.lib.skyframe.serialization.ObjectCodec;
import com.google.devtools.build.lib.skyframe.serialization.SerializationContext;
import com.google.devtools.build.lib.skyframe.serialization.SerializationException;
import com.google.devtools.build.lib.vfs.Path;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.protobuf.CodedInputStream;
import com.google.protobuf.CodedOutputStream;
import java.io.IOException;

/** Relocates filesystem Path objects without rewriting arbitrary user-provided strings. */
final class LocalPathCodec implements ObjectCodec<Path> {
  private final Path workspace;
  private final Path outputBase;

  LocalPathCodec(Path workspace, Path outputBase) {
    this.workspace = workspace;
    this.outputBase = outputBase;
  }

  @Override
  public boolean autoRegister() {
    return false;
  }

  @Override
  public Class<Path> getEncodedClass() {
    return Path.class;
  }

  @Override
  public void serialize(SerializationContext context, Path value, CodedOutputStream out)
      throws IOException {
    if (value.startsWith(workspace)) {
      out.writeInt32NoTag(1);
      out.writeStringNoTag(value.relativeTo(workspace).getPathString());
    } else if (value.startsWith(outputBase)) {
      out.writeInt32NoTag(2);
      out.writeStringNoTag(value.relativeTo(outputBase).getPathString());
    } else {
      out.writeInt32NoTag(0);
      out.writeStringNoTag(value.getPathString());
    }
  }

  @Override
  public Path deserialize(DeserializationContext context, CodedInputStream in)
      throws IOException, SerializationException {
    int kind = in.readInt32();
    PathFragment fragment = PathFragment.create(in.readStringRequireUtf8());
    if (kind == 0 && fragment.isAbsolute()) {
      return workspace.getFileSystem().getPath(fragment);
    }
    if ((kind != 1 && kind != 2) || fragment.isAbsolute() || fragment.containsUplevelReferences()) {
      throw new SerializationException("invalid local path");
    }
    return (kind == 1 ? workspace : outputBase).getRelative(fragment);
  }
}
