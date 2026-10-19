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

import com.google.devtools.build.skystore.SkystoreClient;
import java.io.Closeable;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.PosixFilePermissions;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.TimeUnit;

/** One optional, exclusively owned Rust process and ephemeral spill file per Bazel server. */
final class SkystoreProcess implements IdleValueTier.Storage, Closeable {
  private final Path directory;
  private final Path socket;
  private final Path data;
  private final Process process;
  private SkystoreClient client;

  SkystoreProcess(Path binary, Path outputBase) throws IOException {
    if (!binary.isAbsolute() || !Files.isExecutable(binary)) {
      throw new IOException("skystore binary must be an absolute executable path");
    }
    // A short private pathname avoids the Unix socket pathname limit on long output bases.
    directory =
        Files.createTempDirectory(
            Path.of("/tmp"),
            "bazel-skystore-",
            PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------")));
    socket = directory.resolve("socket");
    data = directory.resolve("values");
    try {
      process =
          new ProcessBuilder(
                  binary.toString(), socket.toString(), data.toString(), "--watch-parent-stdin")
              .redirectErrorStream(true)
              .redirectOutput(
                  ProcessBuilder.Redirect.appendTo(outputBase.resolve("skystore.log").toFile()))
              .start();
    } catch (IOException e) {
      Files.deleteIfExists(directory);
      throw e;
    }
    long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10);
    try {
      while (!Files.exists(socket)) {
        if (!process.isAlive() || System.nanoTime() >= deadline) {
          throw new IOException("skystore failed to start");
        }
        try {
          Thread.sleep(10);
        } catch (InterruptedException e) {
          Thread.currentThread().interrupt();
          throw new IOException("interrupted starting skystore", e);
        }
      }
      connect().ping();
    } catch (IOException | RuntimeException e) {
      close();
      throw e;
    }
  }

  long pid() {
    return process.pid();
  }

  private SkystoreClient connect() throws IOException {
    if (!process.isAlive()) {
      throw new IOException("skystore process exited");
    }
    if (client == null) {
      client = new SkystoreClient(socket, Duration.ofSeconds(10));
    }
    return client;
  }

  @Override
  public void put(byte[] key, byte[] value) throws IOException {
    connect().put(List.of(new SkystoreClient.Entry(key, value)));
  }

  @Override
  public byte[] get(byte[] key) throws IOException {
    return connect()
        .get(List.of(key))
        .getFirst()
        .orElseThrow(() -> new IOException("skystore value missing"));
  }

  @Override
  public void clear() throws IOException {
    connect().clear();
  }

  void disconnect() {
    if (client != null) {
      try {
        client.close();
      } catch (IOException ignored) {
        // Reconnect on the next command; no live graph data is owned by the connection.
      }
      client = null;
    }
  }

  @Override
  public void close() {
    disconnect();
    process.destroyForcibly();
    try {
      if (!process.waitFor(5, TimeUnit.SECONDS)) {
        return; // Never remove files while their owner may still be writing.
      }
      Files.deleteIfExists(socket);
      Files.deleteIfExists(data);
      Files.deleteIfExists(directory);
    } catch (InterruptedException e) {
      Thread.currentThread().interrupt();
    } catch (IOException ignored) {
      // A stale private directory is harmless; it is never reopened or reused.
    }
  }
}
