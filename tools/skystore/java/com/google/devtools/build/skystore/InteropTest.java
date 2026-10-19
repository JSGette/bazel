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

package com.google.devtools.build.skystore;

import java.io.DataOutputStream;
import java.io.IOException;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.channels.Channels;
import java.nio.channels.SocketChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.TimeUnit;

/** Launches the real Rust binary and verifies Java/Rust interoperability and process lifecycle. */
public final class InteropTest {
  public static void main(String[] args) throws Exception {
    if (args.length != 1) {
      throw new IllegalArgumentException("usage: InteropTest ABSOLUTE_SKYSTORE_BINARY");
    }
    Path directory = Files.createTempDirectory(Path.of("/tmp"), "skystore-interop-");
    Path socket = directory.resolve("sock");
    Process server = null;
    byte[] key = new byte[16];
    byte[] emptyKey = new byte[16];
    emptyKey[0] = 1;
    byte[] missing = new byte[16];
    missing[0] = 2;
    byte[] value = new byte[100_000];
    for (int i = 0; i < value.length; i++) {
      value[i] = (byte) i;
    }
    try {
      server = start(args[0], socket);
      try (SkystoreClient client = connect(server, socket)) {
        client.put(
            List.of(
                new SkystoreClient.Entry(key, value),
                new SkystoreClient.Entry(emptyKey, new byte[0])));
        var values = client.get(List.of(emptyKey, missing, key, key));
        check(values.get(0).orElseThrow().length == 0, "empty value");
        check(values.get(1).isEmpty(), "cache miss");
        check(Arrays.equals(value, values.get(2).orElseThrow()), "large value");
        check(Arrays.equals(value, values.get(3).orElseThrow()), "duplicate read");
        client.put(
            List.of(
                new SkystoreClient.Entry(key, new byte[] {4}),
                new SkystoreClient.Entry(key, new byte[] {5})));
        check(client.get(List.of(key)).get(0).orElseThrow()[0] == 5, "last write wins");
        client.put(List.of());
        check(client.get(List.of()).isEmpty(), "empty batch");
      }
      // A second sidecar must not unlink or replace the active socket.
      Process duplicate = start(args[0], socket);
      try {
        check(duplicate.waitFor(5, TimeUnit.SECONDS), "duplicate process exit");
        check(duplicate.exitValue() != 0, "reject occupied socket");
      } finally {
        stop(duplicate);
      }
      // Full frame but truncated PUT body: reject without modifying the first record.
      try (SocketChannel raw = SocketChannel.open(StandardProtocolFamily.UNIX)) {
        raw.connect(UnixDomainSocketAddress.of(socket));
        DataOutputStream output = new DataOutputStream(Channels.newOutputStream(raw));
        output.writeInt(27);
        output.write(new byte[] {1, 1, 0, 0, 0, 2});
        output.write(key);
        output.writeInt(1);
        output.writeByte(99);
        output.flush();
        raw.shutdownOutput();
        check(Channels.newInputStream(raw).read() == -1, "malformed request disconnect");
      }
      try (SkystoreClient client = connect(server, socket)) {
        check(client.get(List.of(key)).get(0).orElseThrow()[0] == 5, "atomic rejected batch");
      }
      // Restart is intentionally cache loss in Phase 0.
      stop(server);
      Files.delete(socket);
      server = start(args[0], socket);
      try (SkystoreClient client = connect(server, socket)) {
        check(client.get(List.of(key)).get(0).isEmpty(), "restart loses ephemeral values");
        stop(server);
        boolean disconnected = false;
        try {
          client.ping();
        } catch (IOException expected) {
          disconnected = true;
        }
        check(disconnected, "server death surfaces as IOException");
      }
      System.out.println(
          "PASS: Java/Rust batches, missing/empty values, reconnect, malformed requests,"
              + " socket ownership, restart and server death");
    } finally {
      if (server != null) {
        stop(server);
      }
      Files.deleteIfExists(socket);
      Files.delete(directory);
    }
  }

  private static Process start(String binary, Path socket) throws IOException {
    return new ProcessBuilder(binary, socket.toString())
        .redirectError(ProcessBuilder.Redirect.INHERIT)
        .start();
  }

  private static SkystoreClient connect(Process process, Path socket) throws Exception {
    long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
    while (System.nanoTime() < deadline) {
      check(process.isAlive(), "sidecar exited before ready");
      try {
        SkystoreClient client = new SkystoreClient(socket, Duration.ofSeconds(2));
        try {
          client.ping();
          return client;
        } catch (IOException e) {
          client.close();
          throw e;
        }
      } catch (IOException e) {
        Thread.sleep(10);
      }
    }
    throw new AssertionError("sidecar not ready");
  }

  private static void stop(Process process) throws InterruptedException {
    process.destroyForcibly();
    check(process.waitFor(5, TimeUnit.SECONDS), "sidecar termination");
  }

  private static void check(boolean condition, String message) {
    if (!condition) {
      throw new AssertionError(message);
    }
  }

  private InteropTest() {}
}
