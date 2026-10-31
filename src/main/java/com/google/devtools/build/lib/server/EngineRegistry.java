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

import static com.google.common.base.Preconditions.checkArgument;
import static com.google.common.base.Preconditions.checkNotNull;
import static com.google.common.base.Preconditions.checkState;

import com.google.devtools.build.lib.runtime.CommandDispatcher;
import com.google.devtools.build.lib.vfs.PathFragment;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;
import javax.annotation.Nullable;
import javax.annotation.concurrent.GuardedBy;

/**
 * Process-local ownership of private workspace engines.
 *
 * <p>This is not a filesystem lock or a startup compatibility check. Callers must canonicalize
 * paths and hold the appropriate external locks before reserving an output base. An owner reserves
 * before initialization, activates only after initialization succeeds, then drains requests before
 * cleaning up the engine. It closes the registration only after cleanup succeeds. Failed cleanup
 * deliberately leaves the output base reserved; it must not be handed to a new engine with old
 * resources alive.
 */
final class EngineRegistry {
  record Key(PathFragment workspace, PathFragment outputBase) {
    Key {
      checkNotNull(workspace);
      checkArgument(workspace.isEmpty() || workspace.isAbsolute(), "workspace must be absolute");
      checkArgument(outputBase.isAbsolute(), "output base must be absolute");
    }
  }

  enum State {
    CREATING,
    READY,
    DRAINING,
    CLOSED
  }

  @GuardedBy("this")
  private final Map<PathFragment, Registration> registrations = new HashMap<>();

  synchronized Registration reserve(Key key) {
    checkState(!registrations.containsKey(key.outputBase()), "output base is already reserved");
    Registration registration = new Registration(key);
    registrations.put(key.outputBase(), registration);
    return registration;
  }

  /** Returns null for an absent, not-ready or stale registration. Empty IDs are legacy requests. */
  @Nullable
  synchronized Lease acquire(Key key, String instanceId) {
    Registration registration = registrations.get(key.outputBase());
    return registration == null || !registration.key.equals(key)
        ? null
        : registration.acquire(instanceId);
  }

  final class Registration implements AutoCloseable {
    private final Key key;
    private final String instanceId = UUID.randomUUID().toString();

    @GuardedBy("EngineRegistry.this")
    private State state = State.CREATING;

    @GuardedBy("EngineRegistry.this")
    private int leases;

    @GuardedBy("EngineRegistry.this")
    @Nullable
    private CommandDispatcher dispatcher;

    private Registration(Key key) {
      this.key = key;
    }

    @Nullable
    private Lease acquire(String requestedId) {
      synchronized (EngineRegistry.this) {
        if (state != State.READY || (!requestedId.isEmpty() && !instanceId.equals(requestedId))) {
          return null;
        }
        leases++;
        return new Lease(this, checkNotNull(dispatcher));
      }
    }

    private void releaseLease() {
      synchronized (EngineRegistry.this) {
        leases--;
        EngineRegistry.this.notifyAll();
      }
    }

    String instanceId() {
      return instanceId;
    }

    State state() {
      synchronized (EngineRegistry.this) {
        return state;
      }
    }

    void activate(CommandDispatcher dispatcher) {
      synchronized (EngineRegistry.this) {
        checkState(state == State.CREATING, "registration is not creating");
        this.dispatcher = checkNotNull(dispatcher);
        state = State.READY;
      }
    }

    void beginDrain() {
      synchronized (EngineRegistry.this) {
        checkState(state == State.READY || state == State.DRAINING, "registration is not ready");
        state = State.DRAINING;
      }
    }

    /**
     * Waits without releasing output-base ownership. Call outside any lease held by this thread.
     */
    void awaitDrained() throws InterruptedException {
      synchronized (EngineRegistry.this) {
        checkState(state == State.DRAINING, "registration is not draining");
        while (leases != 0) {
          EngineRegistry.this.wait();
        }
      }
    }

    /** Aborts creation, or releases a drained registration after the owner has cleaned it up. */
    @Override
    public void close() {
      synchronized (EngineRegistry.this) {
        if (state == State.CLOSED) {
          return;
        }
        checkState(state == State.CREATING || state == State.DRAINING, "engine must drain first");
        checkState(leases == 0, "engine still has active leases");
        state = State.CLOSED;
        dispatcher = null;
        registrations.remove(key.outputBase(), this);
      }
    }
  }

  final class Lease implements AutoCloseable {
    private final Registration registration;

    @GuardedBy("EngineRegistry.this")
    @Nullable
    private CommandDispatcher dispatcher;

    @GuardedBy("EngineRegistry.this")
    private boolean closed;

    private Lease(Registration registration, CommandDispatcher dispatcher) {
      this.registration = registration;
      this.dispatcher = dispatcher;
    }

    CommandDispatcher dispatcher() {
      synchronized (EngineRegistry.this) {
        checkState(!closed, "lease is closed");
        return checkNotNull(dispatcher);
      }
    }

    @Override
    public void close() {
      synchronized (EngineRegistry.this) {
        if (!closed) {
          closed = true;
          dispatcher = null;
          registration.releaseLease();
        }
      }
    }
  }
}
