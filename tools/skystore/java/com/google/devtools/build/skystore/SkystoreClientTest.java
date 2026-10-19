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

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertThrows;
import static org.junit.Assert.assertTrue;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.channels.Channels;
import java.nio.channels.ServerSocketChannel;
import java.nio.channels.SocketChannel;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Arrays;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class SkystoreClientTest {
  @FunctionalInterface
  private interface Peer {
    void run(DataInputStream input, DataOutputStream output) throws Exception;
  }

  @FunctionalInterface
  private interface ClientAction {
    void run(SkystoreClient client) throws Exception;
  }

  private static void withPeer(Peer peer, ClientAction action) throws Exception {
    // Keep Unix socket paths below the platform limit even in deep Bazel sandboxes.
    Path directory = Files.createTempDirectory(Path.of("/tmp"), "skystore-test-");
    Path socket = directory.resolve("sock");
    try (ServerSocketChannel listener = ServerSocketChannel.open(StandardProtocolFamily.UNIX);
        var executor = Executors.newSingleThreadExecutor()) {
      listener.bind(UnixDomainSocketAddress.of(socket));
      var future =
          executor.submit(
              () -> {
                try (SocketChannel connection = listener.accept()) {
                  peer.run(
                      new DataInputStream(Channels.newInputStream(connection)),
                      new DataOutputStream(Channels.newOutputStream(connection)));
                }
                return null;
              });
      try (SkystoreClient client = new SkystoreClient(socket, Duration.ofSeconds(1))) {
        action.run(client);
      } finally {
        listener.close();
        future.get(5, TimeUnit.SECONDS);
      }
    } finally {
      Files.deleteIfExists(socket);
      Files.delete(directory);
    }
  }

  private static byte[] receive(DataInputStream input) throws IOException {
    int size = input.readInt();
    if (size < 2 || size > SkystoreClient.MAX_FRAME) {
      throw new IOException("invalid test request");
    }
    byte[] bytes = new byte[size];
    input.readFully(bytes);
    return bytes;
  }

  private static void respond(DataOutputStream output, byte[] response) throws IOException {
    output.writeInt(response.length);
    for (byte value : response) {
      output.writeByte(value);
      output.flush();
    }
  }

  @Test
  public void goldenWireFormatAndEmptyVersusMissing() throws Exception {
    withPeer(
        (input, output) -> {
          assertArrayEquals(new byte[] {1, 0}, receive(input));
          respond(output, new byte[] {1, 0});
          byte[] put = receive(input);
          assertEquals(29, put.length);
          assertArrayEquals(new byte[] {1, 1, 0, 0, 0, 1}, Arrays.copyOf(put, 6));
          assertArrayEquals(new byte[] {0, 0, 0, 3, 4, 5, 6}, Arrays.copyOfRange(put, 22, 29));
          respond(output, new byte[] {1, 1, 0, 0, 0, 1});
          assertEquals(38, receive(input).length);
          respond(output, new byte[] {1, 2, 0, 0, 0, 2, 1, 0, 0, 0, 0, 0});
        },
        client -> {
          client.ping();
          client.put(List.of(new SkystoreClient.Entry(new byte[16], new byte[] {4, 5, 6})));
          var values = client.get(List.of(new byte[16], new byte[16]));
          assertArrayEquals(new byte[0], values.get(0).orElseThrow());
          assertFalse(values.get(1).isPresent());
        });
  }

  @Test
  public void invalidArgumentsDoNotSendAnything() throws Exception {
    withPeer(
        (input, output) -> {
          assertArrayEquals(new byte[] {1, 0}, receive(input));
          respond(output, new byte[] {1, 0});
        },
        client -> {
          assertThrows(IllegalArgumentException.class, () -> client.get(List.of(new byte[15])));
          assertThrows(
              IllegalArgumentException.class,
              () ->
                  client.put(
                      List.of(
                          new SkystoreClient.Entry(
                              new byte[16], new byte[SkystoreClient.MAX_FRAME]))));
          assertThrows(
              IllegalArgumentException.class,
              () -> client.get(java.util.Collections.nCopies(4097, new byte[16])));
          client.ping();
        });
  }

  @Test
  public void rejectsInvalidResponseHeadersAndTrailingData() throws Exception {
    for (byte[] response : List.of(new byte[] {2, 0}, new byte[] {1, 2}, new byte[] {1, 0, 0})) {
      withPeer(
          (input, output) -> {
            receive(input);
            respond(output, response);
          },
          client -> assertThrows(IOException.class, client::ping));
    }
  }

  @Test
  public void rejectsOversizedAndTruncatedFrames() throws Exception {
    for (int size : new int[] {0, 1, -1, SkystoreClient.MAX_FRAME + 1, 10}) {
      withPeer(
          (input, output) -> {
            receive(input);
            output.writeInt(size);
            output.flush();
          },
          client -> assertThrows(IOException.class, client::ping));
    }
  }

  @Test
  public void rejectsInvalidGetResults() throws Exception {
    for (byte[] response :
        List.of(
            new byte[] {1, 2, 0, 0, 0, 2},
            new byte[] {1, 2, 0, 0, 0, 1},
            new byte[] {1, 2, 0, 0, 0, 1, 2},
            new byte[] {1, 2, 0, 0, 0, 1, 1, -1, -1, -1, -1},
            new byte[] {1, 2, 0, 0, 0, 1, 1, 0, 0, 0, 2, 42},
            new byte[] {1, 2, 0, 0, 0, 1, 0, 42})) {
      withPeer(
          (input, output) -> {
            receive(input);
            respond(output, response);
          },
          client -> assertThrows(IOException.class, () -> client.get(List.of(new byte[16]))));
    }
  }

  @Test
  public void rejectsIncorrectPutAcknowledgement() throws Exception {
    withPeer(
        (input, output) -> {
          receive(input);
          respond(output, new byte[] {1, 1, 0, 0, 0, 1});
        },
        client -> assertThrows(IOException.class, () -> client.put(List.of())));
  }

  @Test
  public void requestDeadlineClosesUnresponsivePeer() throws Exception {
    withPeer(
        (input, output) -> {
          receive(input);
          assertEquals(-1, input.read());
        },
        client -> {
          IOException failure = assertThrows(IOException.class, client::ping);
          assertTrue(failure.getMessage().contains("timed out"));
          assertThrows(IOException.class, client::ping);
        });
  }

  @Test
  public void interruptedRequestFailsAndPreservesInterrupt() throws Exception {
    withPeer(
        (input, output) -> assertEquals(-1, input.read()),
        client -> {
          Thread.currentThread().interrupt();
          try {
            assertThrows(IOException.class, client::ping);
            assertTrue(Thread.currentThread().isInterrupted());
          } finally {
            Thread.interrupted();
          }
        });
  }
}
