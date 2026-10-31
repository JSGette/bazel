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

import java.util.HashSet;
import java.util.Set;

/**
 * Daemon-wide bounded admission for equal content-addressed actions.
 *
 * <p>Only immutable keys are retained. A lease spans cache lookup, local execution, and synchronous
 * CAS publication. After release, waiters retry their own cache lookup and materialize into their
 * own execroots. Failed/cancelled producers release the lease without sharing a partial result.
 * Cancelling a waiter cannot cancel its producer. No graph, Artifact, output path, or caller
 * callback crosses the engine boundary.
 */
final class SharedActionExecutionRegistry {
  static final SharedActionExecutionRegistry INSTANCE = new SharedActionExecutionRegistry(256);

  private final int limit;
  private final Set<String> running = new HashSet<>();

  SharedActionExecutionRegistry(int limit) {
    if (limit <= 0) {
      throw new IllegalArgumentException("flight limit must be positive");
    }
    this.limit = limit;
  }

  synchronized Lease acquire(String key) throws InterruptedException {
    while (running.contains(key) || running.size() >= limit) {
      wait();
    }
    if (Thread.interrupted()) {
      throw new InterruptedException();
    }
    running.add(key);
    return new Lease(key);
  }

  synchronized int size() {
    return running.size();
  }

  final class Lease implements AutoCloseable {
    private final String key;
    private boolean closed;

    private Lease(String key) {
      this.key = key;
    }

    @Override
    public void close() {
      synchronized (SharedActionExecutionRegistry.this) {
        if (!closed) {
          closed = true;
          running.remove(key);
          SharedActionExecutionRegistry.this.notifyAll();
        }
      }
    }
  }
}
