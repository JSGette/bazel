// Copyright 2023 The Bazel Authors. All rights reserved.
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
package com.google.devtools.build.skyframe;

import com.google.devtools.build.skyframe.GraphTester.StringValue;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;

/** Real disk records and an exact, deliberately narrow value codec for eviction tests. */
final class DiskBackedGraphTestSupport {
  static final DiskBackedGraph.ValueCodec STRINGS =
      new DiskBackedGraph.ValueCodec() {
        @Override
        public boolean supports(SkyValue value) {
          return value != null && value.getClass() == StringValue.class;
        }

        @Override
        public byte[] encode(SkyValue value) throws IOException {
          ByteArrayOutputStream bytes = new ByteArrayOutputStream();
          try (DataOutputStream out = new DataOutputStream(bytes)) {
            out.writeUTF(((StringValue) value).getValue());
          }
          return bytes.toByteArray();
        }

        @Override
        public SkyValue decode(byte[] bytes) throws IOException {
          try (DataInputStream in = new DataInputStream(new ByteArrayInputStream(bytes))) {
            StringValue value = new StringValue(in.readUTF());
            if (in.available() != 0) {
              throw new IOException("trailing value bytes");
            }
            return value;
          }
        }
      };

  static final class FileStore implements DiskBackedGraph.Store {
    private final Path directory;
    boolean failWrites;

    FileStore(Path directory) {
      this.directory = directory;
    }

    @Override
    public void put(long id, byte[] bytes) throws IOException {
      if (failWrites) {
        throw new IOException("injected write failure");
      }
      Path temporary = Files.createTempFile(directory, "record-", ".tmp");
      try {
        Files.write(temporary, bytes);
        Files.move(
            temporary,
            directory.resolve(Long.toString(id)),
            StandardCopyOption.ATOMIC_MOVE,
            StandardCopyOption.REPLACE_EXISTING);
      } finally {
        Files.deleteIfExists(temporary);
      }
    }

    @Override
    public byte[] get(long id) throws IOException {
      return Files.readAllBytes(directory.resolve(Long.toString(id)));
    }

    void flipByte(long id, int offset) throws IOException {
      Path path = directory.resolve(Long.toString(id));
      byte[] bytes = Files.readAllBytes(path);
      bytes[offset < 0 ? bytes.length + offset : offset] ^= 1;
      Files.write(path, bytes);
    }

    void corrupt(long id) throws IOException {
      Files.write(directory.resolve(Long.toString(id)), new byte[] {0});
    }
  }

  private DiskBackedGraphTestSupport() {}
}
