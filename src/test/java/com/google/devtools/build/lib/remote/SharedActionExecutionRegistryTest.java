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

package com.google.devtools.build.lib.remote;

import static org.junit.Assert.*;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class SharedActionExecutionRegistryTest {
  @Test
  public void equalKeysWaitForPublicationAndDifferentKeysProgress() throws Exception {
    var registry = new SharedActionExecutionRegistry(2);
    try (var producer = registry.acquire("same");
        var executor = Executors.newVirtualThreadPerTaskExecutor()) {
      var entered = new CountDownLatch(1);
      var waiter =
          executor.submit(
              () -> {
                entered.countDown();
                return registry.acquire("same");
              });
      assertTrue(entered.await(5, TimeUnit.SECONDS));
      assertThrows(TimeoutException.class, () -> waiter.get(100, TimeUnit.MILLISECONDS));
      try (var divergent = registry.acquire("different")) {
        assertEquals(2, registry.size());
      }
      producer.close();
      try (var consumer = waiter.get(5, TimeUnit.SECONDS)) {
        assertEquals(1, registry.size());
        producer.close();
        assertEquals(1, registry.size());
      }
    }
    assertEquals(0, registry.size());
  }

  @Test
  public void cancellingWaiterDoesNotReleaseProducer() throws Exception {
    var registry = new SharedActionExecutionRegistry(1);
    try (var producer = registry.acquire("key");
        var executor = Executors.newVirtualThreadPerTaskExecutor()) {
      var entered = new CountDownLatch(1);
      var waiter =
          executor.submit(
              () -> {
                entered.countDown();
                try (var ignored = registry.acquire("key")) {
                  return true;
                }
              });
      assertTrue(entered.await(5, TimeUnit.SECONDS));
      waiter.cancel(true);
      assertEquals(1, registry.size());
    }
    assertEquals(0, registry.size());
  }

  @Test
  public void capacityBlocksDistinctKeyAndReleaseAllowsRetry() throws Exception {
    var registry = new SharedActionExecutionRegistry(1);
    try (var producer = registry.acquire("a");
        var executor = Executors.newVirtualThreadPerTaskExecutor()) {
      var waiter = executor.submit(() -> registry.acquire("b"));
      assertThrows(TimeoutException.class, () -> waiter.get(100, TimeUnit.MILLISECONDS));
      producer.close();
      try (var retry = waiter.get(5, TimeUnit.SECONDS)) {
        assertEquals(1, registry.size());
      }
    }
    assertEquals(0, registry.size());
  }

  @Test
  public void interruptedAcquisitionDoesNotLeakSlot() throws Exception {
    var registry = new SharedActionExecutionRegistry(1);
    Thread.currentThread().interrupt();
    try {
      assertThrows(InterruptedException.class, () -> registry.acquire("a"));
    } finally {
      Thread.interrupted();
    }
    assertEquals(0, registry.size());
    try (var lease = registry.acquire("a")) {
      assertEquals(1, registry.size());
    }
  }
}
