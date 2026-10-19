// Copyright 2026 The Bazel Authors. All rights reserved.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//    http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

package com.google.devtools.build.lib.skyframe.tiering;

import com.google.common.collect.ImmutableSet;
import com.google.devtools.build.lib.skyframe.GlobValueWithImmutableSet;
import com.google.devtools.build.lib.skyframe.GlobsValue;
import com.google.devtools.build.lib.vfs.PathFragment;
import com.google.devtools.build.skyframe.SkyValue;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;

/** Closed, versioned allowlist. Metadata, subclasses and nested-set globs stay resident. */
final class GlobValueCodec {
  private static final int VERSION = 1;
  private static final int MAX_PATHS = 1_000_000;
  static final int MAX_BYTES = 8 * 1024 * 1024;

  static boolean supports(SkyValue value) {
    return value != null
        && (value.getClass() == GlobsValue.class
            || value.getClass() == GlobValueWithImmutableSet.class);
  }

  static byte[] encode(SkyValue value) throws IOException {
    if (!supports(value)) {
      throw new IOException("unaudited SkyValue type");
    }
    boolean globs = value.getClass() == GlobsValue.class;
    ImmutableSet<PathFragment> paths =
        globs
            ? ((GlobsValue) value).getMatches()
            : ((GlobValueWithImmutableSet) value).getMatches();
    if (paths.size() > MAX_PATHS) {
      throw new IOException("too many glob paths");
    }
    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
    try (DataOutputStream out = new DataOutputStream(bytes)) {
      out.writeByte(VERSION);
      out.writeByte(globs ? 1 : 2);
      out.writeInt(paths.size());
      for (PathFragment path : paths) {
        // Modified UTF preserves every Java string code unit, including unusual file names.
        out.writeUTF(path.getPathString());
        if (bytes.size() > MAX_BYTES) {
          throw new IOException("glob value exceeds spill limit");
        }
      }
    }
    return bytes.toByteArray();
  }

  static SkyValue decode(byte[] bytes) throws IOException {
    if (bytes.length > MAX_BYTES) {
      throw new IOException("glob value exceeds spill limit");
    }
    try (DataInputStream in = new DataInputStream(new ByteArrayInputStream(bytes))) {
      if (in.readUnsignedByte() != VERSION) {
        throw new IOException("unsupported glob codec version");
      }
      int type = in.readUnsignedByte();
      int count = in.readInt();
      if ((type != 1 && type != 2)
          || count < 0
          || count > MAX_PATHS
          || count > in.available() / 2) {
        throw new IOException("invalid glob payload");
      }
      ImmutableSet.Builder<PathFragment> paths = ImmutableSet.builderWithExpectedSize(count);
      for (int i = 0; i < count; i++) {
        String path = in.readUTF();
        PathFragment fragment = PathFragment.create(path);
        if (!fragment.getPathString().equals(path)) {
          throw new IOException("noncanonical glob path");
        }
        paths.add(fragment);
      }
      ImmutableSet<PathFragment> result = paths.build();
      if (in.available() != 0 || result.size() != count) {
        throw new IOException("trailing bytes or duplicate glob paths");
      }
      return type == 1 ? new GlobsValue(result) : new GlobValueWithImmutableSet(result);
    }
  }

  private GlobValueCodec() {}
}
