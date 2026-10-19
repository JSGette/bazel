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

import java.io.Closeable;
import java.io.EOFException;
import java.io.IOException;
import java.net.StandardProtocolFamily;
import java.net.UnixDomainSocketAddress;
import java.nio.ByteBuffer;
import java.nio.channels.ClosedChannelException;
import java.nio.channels.SelectionKey;
import java.nio.channels.Selector;
import java.nio.channels.SocketChannel;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/** Thread-confined, bounded IPC client for the Phase 0 prototype. */
public final class SkystoreClient implements Closeable {
  static final int MAX_FRAME = 16 * 1024 * 1024;
  static final int MAX_BATCH = 4096;
  private static final byte VERSION = 1;
  private final SocketChannel channel;
  private final Selector selector;
  private final SelectionKey selectionKey;
  private final long timeoutNanos;

  public record Entry(byte[] key, byte[] value) {}

  public SkystoreClient(Path socket, Duration timeout) throws IOException {
    if (timeout.isNegative() || timeout.isZero() || timeout.compareTo(Duration.ofMinutes(1)) > 0) {
      throw new IllegalArgumentException("timeout must be in (0, 1 minute]");
    }
    timeoutNanos = timeout.toNanos();
    channel = SocketChannel.open(StandardProtocolFamily.UNIX);
    Selector openedSelector = null;
    try {
      channel.configureBlocking(false);
      openedSelector = Selector.open();
      selectionKey = channel.register(openedSelector, 0);
      selector = openedSelector;
      if (!channel.connect(UnixDomainSocketAddress.of(socket))) {
        long deadline = System.nanoTime() + timeoutNanos;
        selectionKey.interestOps(SelectionKey.OP_CONNECT);
        while (!channel.finishConnect()) {
          awaitReady(deadline);
        }
      }
    } catch (IOException | RuntimeException e) {
      channel.close();
      if (openedSelector != null) {
        openedSelector.close();
      }
      throw e;
    }
  }

  public void ping() throws IOException {
    finish(exchange((byte) 0, request((byte) 0, 0)));
  }

  public void put(List<Entry> entries) throws IOException {
    checkCount(entries.size());
    long size = 4;
    for (Entry entry : entries) {
      checkKey(entry.key());
      size += 20L + entry.value().length;
    }
    ByteBuffer request = request((byte) 1, size);
    request.putInt(entries.size());
    for (Entry entry : entries) {
      request.put(entry.key()).putInt(entry.value().length).put(entry.value());
    }
    ByteBuffer response = exchange((byte) 1, request);
    require(response.remaining() >= 4 && response.getInt() == entries.size(), "bad put count");
    finish(response);
  }

  public List<Optional<byte[]>> get(List<byte[]> keys) throws IOException {
    checkCount(keys.size());
    for (byte[] key : keys) {
      checkKey(key);
    }
    ByteBuffer request = request((byte) 2, 4L + 16L * keys.size());
    request.putInt(keys.size());
    for (byte[] key : keys) {
      request.put(key);
    }
    ByteBuffer response = exchange((byte) 2, request);
    require(response.remaining() >= 4 && response.getInt() == keys.size(), "bad get count");
    List<Optional<byte[]>> values = new ArrayList<>(keys.size());
    for (int i = 0; i < keys.size(); i++) {
      require(response.hasRemaining(), "missing result");
      int found = response.get();
      require(found == 0 || found == 1, "bad presence tag");
      if (found == 0) {
        values.add(Optional.empty());
      } else {
        require(response.remaining() >= 4, "missing value length");
        int size = response.getInt();
        require(size >= 0 && size <= response.remaining(), "bad value length");
        byte[] value = new byte[size];
        response.get(value);
        values.add(Optional.of(value));
      }
    }
    finish(response);
    return List.copyOf(values);
  }

  private static void checkKey(byte[] key) {
    if (key.length != 16) {
      throw new IllegalArgumentException("fingerprints must contain 16 bytes");
    }
  }

  private static void checkCount(int count) {
    if (count > MAX_BATCH) {
      throw new IllegalArgumentException("batch exceeds " + MAX_BATCH);
    }
  }

  private static ByteBuffer request(byte operation, long size) {
    if (size > MAX_FRAME - 2) {
      throw new IllegalArgumentException("request exceeds frame limit");
    }
    return ByteBuffer.allocate((int) size + 6).putInt((int) size + 2).put(VERSION).put(operation);
  }

  private ByteBuffer exchange(byte operation, ByteBuffer request) throws IOException {
    long deadline = System.nanoTime() + timeoutNanos;
    try {
      request.flip();
      transfer(request, true, deadline);
      ByteBuffer header = ByteBuffer.allocate(4);
      transfer(header, false, deadline);
      int size = header.flip().getInt();
      require(size >= 2 && size <= MAX_FRAME, "response exceeds frame bounds");
      ByteBuffer response = ByteBuffer.allocate(size);
      transfer(response, false, deadline);
      response.flip();
      require(response.get() == VERSION, "unsupported response version");
      require(response.get() == operation, "unexpected response operation");
      return response;
    } catch (IOException e) {
      close();
      throw e;
    }
  }

  private void transfer(ByteBuffer bytes, boolean write, long deadline) throws IOException {
    if (!channel.isOpen()) {
      throw new ClosedChannelException();
    }
    selectionKey.interestOps(write ? SelectionKey.OP_WRITE : SelectionKey.OP_READ);
    while (bytes.hasRemaining()) {
      remainingTime(deadline);
      int count = write ? channel.write(bytes) : channel.read(bytes);
      if (count < 0) {
        throw new EOFException("skystore disconnected during response");
      }
      if (count == 0) {
        awaitReady(deadline);
      }
    }
  }

  private void awaitReady(long deadline) throws IOException {
    selector.select(Math.max(1, remainingTime(deadline) / 1_000_000));
    selector.selectedKeys().clear();
  }

  private static long remainingTime(long deadline) throws IOException {
    if (Thread.currentThread().isInterrupted()) {
      throw new IOException("interrupted skystore request");
    }
    long remaining = deadline - System.nanoTime();
    if (remaining <= 0) {
      throw new IOException("skystore request timed out");
    }
    return remaining;
  }

  private void finish(ByteBuffer response) throws IOException {
    require(!response.hasRemaining(), "trailing response bytes");
  }

  private void require(boolean condition, String message) throws IOException {
    if (!condition) {
      close();
      throw new IOException(message);
    }
  }

  @Override
  public void close() throws IOException {
    try {
      channel.close();
    } finally {
      selector.close();
    }
  }
}
