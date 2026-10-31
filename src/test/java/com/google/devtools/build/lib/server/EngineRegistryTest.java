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

package com.google.devtools.build.lib.server;

import static com.google.common.truth.Truth.assertThat;
import static com.google.devtools.build.lib.testutil.TestUtils.WAIT_TIMEOUT_MILLISECONDS;
import static org.junit.Assert.assertThrows;

import com.google.devtools.build.lib.runtime.BlazeCommandResult;
import com.google.devtools.build.lib.runtime.CommandDispatcher;
import com.google.devtools.build.lib.server.EngineRegistry.Key;
import com.google.devtools.build.lib.server.EngineRegistry.Lease;
import com.google.devtools.build.lib.server.EngineRegistry.Registration;
import com.google.devtools.build.lib.server.EngineRegistry.State;
import com.google.devtools.build.lib.testutil.TestThread;
import com.google.devtools.build.lib.vfs.PathFragment;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.junit.runners.JUnit4;

@RunWith(JUnit4.class)
public final class EngineRegistryTest {
  private final EngineRegistry registry = new EngineRegistry();
  private static final Key A = key("/worktree-a", "/output-a");
  private static final Key B = key("/worktree-b", "/output-b");

  private static Key key(String workspace, String output) {
    return new Key(PathFragment.create(workspace), PathFragment.create(output));
  }

  private static CommandDispatcher dispatcher() {
    AtomicInteger calls = new AtomicInteger();
    return (policy,
        args,
        outErr,
        timeout,
        verbosity,
        client,
        start,
        options,
        idle,
        extensions,
        reporter) -> {
      calls.incrementAndGet();
      return BlazeCommandResult.success();
    };
  }

  @Test
  public void creationCannotDispatchAndFailureReleasesReservation() {
    Registration first = registry.reserve(A);
    assertThat(first.state()).isEqualTo(State.CREATING);
    assertThat(registry.acquire(A, first.instanceId())).isNull();
    assertThrows(IllegalStateException.class, () -> registry.reserve(A));
    first.close();
    Registration replacement = registry.reserve(A);
    assertThat(replacement.instanceId()).isNotEqualTo(first.instanceId());
    replacement.close();
  }

  @Test
  public void outputBaseCannotBeOwnedByTwoWorkspaces() {
    Registration first = registry.reserve(A);
    Key collision = key("/worktree-b", "/output-a");
    assertThrows(IllegalStateException.class, () -> registry.reserve(collision));
    first.activate(dispatcher());
    assertThrows(IllegalStateException.class, () -> registry.reserve(collision));
    assertThat(registry.acquire(collision, first.instanceId())).isNull();
    first.beginDrain();
    // Ownership persists during cleanup, even with no active requests.
    assertThrows(IllegalStateException.class, () -> registry.reserve(collision));
    first.close();
    registry.reserve(collision).close();
  }

  @Test
  public void concurrentCreationHasExactlyOneOwner() throws Exception {
    CyclicBarrier barrier = new CyclicBarrier(2);
    AtomicReference<Registration> winner = new AtomicReference<>();
    AtomicInteger collisions = new AtomicInteger();
    TestThread[] threads = new TestThread[2];
    for (int i = 0; i < threads.length; i++) {
      Key key = key("/worktree-" + i, "/shared-output");
      threads[i] =
          new TestThread(
              () -> {
                barrier.await();
                try {
                  winner.set(registry.reserve(key));
                } catch (IllegalStateException expected) {
                  collisions.incrementAndGet();
                }
              });
      threads[i].start();
    }
    for (TestThread thread : threads) {
      thread.joinAndAssertState(WAIT_TIMEOUT_MILLISECONDS);
    }
    assertThat(collisions.get()).isEqualTo(1);
    assertThat(winner.get()).isNotNull();
    winner.get().close();
  }

  @Test
  public void drainRetainsLeasedDispatcherAndDoesNotAffectOtherEngine() throws Exception {
    Registration a = registry.reserve(A);
    CommandDispatcher dispatcherA = dispatcher();
    a.activate(dispatcherA);
    Registration b = registry.reserve(B);
    CommandDispatcher dispatcherB = dispatcher();
    b.activate(dispatcherB);
    Lease lease = registry.acquire(A, a.instanceId());
    a.beginDrain();
    assertThat(registry.acquire(A, a.instanceId())).isNull();
    assertThrows(IllegalStateException.class, a::close);
    assertThat(lease.dispatcher()).isSameInstanceAs(dispatcherA);
    TestThread draining = new TestThread(a::awaitDrained);
    draining.start();
    try {
      waitUntilWaiting(draining);
      try (Lease other = registry.acquire(B, b.instanceId())) {
        assertThat(other.dispatcher()).isSameInstanceAs(dispatcherB);
      }
    } finally {
      lease.close();
      lease.close(); // Duplicate release must not underflow the active count.
      draining.joinAndAssertState(WAIT_TIMEOUT_MILLISECONDS);
    }
    assertThrows(IllegalStateException.class, lease::dispatcher);
    a.close();
    assertThat(b.state()).isEqualTo(State.READY);
    b.beginDrain();
    b.close();
  }

  @Test
  public void staleRegistrationCannotAcquireOrRemoveReplacement() throws Exception {
    Registration first = registry.reserve(A);
    first.activate(dispatcher());
    assertThrows(IllegalStateException.class, first::close);
    first.beginDrain();
    first.awaitDrained();
    first.close();
    Registration second = registry.reserve(A);
    second.activate(dispatcher());
    first.close();
    assertThat(registry.acquire(A, first.instanceId())).isNull();
    try (Lease lease = registry.acquire(A, second.instanceId())) {
      assertThat(lease).isNotNull();
    }
    second.beginDrain();
    second.close();
  }

  @Test
  public void interruptedDrainKeepsOutputBaseReserved() throws Exception {
    Registration registration = registry.reserve(A);
    registration.activate(dispatcher());
    Lease lease = registry.acquire(A, registration.instanceId());
    registration.beginDrain();
    TestThread waiting =
        new TestThread(() -> assertThrows(InterruptedException.class, registration::awaitDrained));
    waiting.start();
    try {
      waitUntilWaiting(waiting);
      waiting.interrupt();
      waiting.joinAndAssertState(WAIT_TIMEOUT_MILLISECONDS);
      assertThat(registration.state()).isEqualTo(State.DRAINING);
      assertThrows(IllegalStateException.class, () -> registry.reserve(A));
      assertThat(registry.acquire(A, registration.instanceId())).isNull();
    } finally {
      lease.close();
      waiting.interrupt();
    }
    registration.awaitDrained();
    registration.close();
  }

  @Test
  public void lifecycleCannotBeReactivated() {
    Registration registration = registry.reserve(A);
    registration.activate(dispatcher());
    assertThrows(IllegalStateException.class, () -> registration.activate(dispatcher()));
    registration.beginDrain();
    assertThrows(IllegalStateException.class, () -> registration.activate(dispatcher()));
    registration.close();
    assertThrows(IllegalStateException.class, () -> registration.activate(dispatcher()));
  }

  @Test
  public void pathsMustBeAbsoluteExceptOutsideWorkspace() {
    assertThrows(IllegalArgumentException.class, () -> key("relative", "/output"));
    assertThrows(IllegalArgumentException.class, () -> key("/workspace", "relative"));
    registry.reserve(key("", "/output")).close();
  }

  private static void waitUntilWaiting(Thread thread) throws InterruptedException {
    long deadline = System.nanoTime() + java.util.concurrent.TimeUnit.SECONDS.toNanos(10);
    while (thread.getState() != Thread.State.WAITING && System.nanoTime() < deadline) {
      Thread.sleep(1);
    }
    assertThat(thread.getState()).isEqualTo(Thread.State.WAITING);
  }
}
