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

import java.nio.ByteBuffer;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Optional;

/**
 * Reports end-to-end client latency, including copies and allocation; no performance assertions.
 */
public final class IpcBenchmark {
  public static void main(String[] args) throws Exception {
    if (args.length != 4) {
      throw new IllegalArgumentException("usage: IpcBenchmark SOCKET BATCH VALUE_BYTES ITERATIONS");
    }
    int batch = Integer.parseInt(args[1]);
    int valueBytes = Integer.parseInt(args[2]);
    int iterations = Integer.parseInt(args[3]);
    if (batch < 1
        || batch > SkystoreClient.MAX_BATCH
        || valueBytes < 0
        || iterations < 1
        || iterations > 1_000_000
        || 6L + batch * (21L + valueBytes) > SkystoreClient.MAX_FRAME) {
      throw new IllegalArgumentException("invalid benchmark dimensions");
    }
    List<SkystoreClient.Entry> entries = new ArrayList<>();
    List<byte[]> keys = new ArrayList<>();
    for (int i = 0; i < batch; i++) {
      byte[] key = ByteBuffer.allocate(16).putLong(i).putLong(valueBytes).array();
      byte[] value = new byte[valueBytes];
      for (int j = 0; j < valueBytes; j++) {
        value[j] = (byte) (i + j);
      }
      keys.add(key);
      entries.add(new SkystoreClient.Entry(key, value));
    }
    try (SkystoreClient client = new SkystoreClient(Path.of(args[0]), Duration.ofSeconds(10))) {
      client.ping();
      client.put(entries);
      verify(client.get(keys), entries);
      for (int i = 0; i < 1000; i++) {
        client.put(entries);
        verify(client.get(keys), entries);
      }
      long[] puts = new long[iterations];
      long[] gets = new long[iterations];
      for (int i = 0; i < iterations; i++) {
        long start = System.nanoTime();
        client.put(entries);
        puts[i] = System.nanoTime() - start;
        start = System.nanoTime();
        List<Optional<byte[]>> values = client.get(keys);
        gets[i] = System.nanoTime() - start;
        verify(values, entries);
      }
      System.out.println(
          "operation,batch,value_bytes,iterations,p50_us,p99_us,mean_us,values_per_second");
      report("put", batch, valueBytes, puts);
      report("get", batch, valueBytes, gets);
    }
  }

  private static void verify(List<Optional<byte[]>> values, List<SkystoreClient.Entry> expected) {
    for (int i = 0; i < expected.size(); i++) {
      if (values.get(i).isEmpty() || !Arrays.equals(values.get(i).get(), expected.get(i).value())) {
        throw new AssertionError("value mismatch at " + i);
      }
    }
  }

  private static void report(String operation, int batch, int bytes, long[] samples) {
    double mean = Arrays.stream(samples).average().orElseThrow();
    Arrays.sort(samples);
    long p50 = samples[(int) Math.ceil(samples.length * .50) - 1];
    long p99 = samples[(int) Math.ceil(samples.length * .99) - 1];
    System.out.printf(
        Locale.ROOT,
        "%s,%d,%d,%d,%.3f,%.3f,%.3f,%.0f%n",
        operation,
        batch,
        bytes,
        samples.length,
        p50 / 1000.0,
        p99 / 1000.0,
        mean / 1000.0,
        batch * 1_000_000_000.0 / mean);
  }

  private IpcBenchmark() {}
}
