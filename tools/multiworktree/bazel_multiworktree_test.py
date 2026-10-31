# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import time
import unittest

import bazel_multiworktree as mw


def engine(workspace, quarantined=False):
  return mw.Engine(workspace, {}, "registration", None, quarantined=quarantined)


def eventually(predicate):
  deadline = time.monotonic() + 3
  while not predicate():
    if time.monotonic() > deadline:
      raise AssertionError("Timed out")
    time.sleep(0.01)


class SchedulerTest(unittest.TestCase):

  def setUp(self):
    self.scheduler = mw.Scheduler(2)
    self.cancelled = threading.Event()
    self.threads = []

  def tearDown(self):
    self.cancelled.set()
    for thread in self.threads:
      thread.join(3)
      self.assertFalse(thread.is_alive())

  def acquire(self, workspace, **kwargs):
    return self.scheduler.acquire(workspace, self.cancelled, **kwargs)

  def background(self, workspace):
    result = []

    def run():
      try:
        result.append(self.acquire(workspace))
      except mw.Rejected as error:
        result.append(error.code)

    thread = threading.Thread(target=run)
    thread.start()
    self.threads.append(thread)
    return result

  def test_independent_engines_overlap_but_third_waits(self):
    first = self.acquire("a")
    second = self.acquire("b")
    self.assertIsNot(first, second)
    third = self.background("c")
    eventually(lambda: len(self.scheduler.queue) == 1)
    self.assertEqual([], third)
    self.scheduler.release(first)
    eventually(lambda: bool(third))
    self.assertIs(first, third[0])

  def test_same_engine_serializes_even_when_other_slot_free(self):
    first = self.acquire("a")
    first.engine = engine("a")
    second = self.background("a")
    eventually(lambda: len(self.scheduler.queue) == 1)
    self.assertEqual([], second)
    self.scheduler.release(first)
    eventually(lambda: bool(second))
    self.assertIs(first, second[0])

  def test_busy_engine_does_not_block_independent_request(self):
    self.acquire("a")
    waiting = self.background("a")
    eventually(lambda: len(self.scheduler.queue) == 1)
    self.acquire("b", no_wait=True)
    self.assertEqual([], waiting)

  def test_same_engine_fifo(self):
    first = self.acquire("a")
    earlier = self.background("a")
    eventually(lambda: len(self.scheduler.queue) == 1)
    later = self.background("a")
    eventually(lambda: len(self.scheduler.queue) == 2)
    self.scheduler.release(first)
    eventually(lambda: bool(earlier))
    self.assertEqual([], later)
    self.scheduler.release(earlier[0])
    eventually(lambda: bool(later))

  def test_queued_cancellation_removes_ticket(self):
    self.acquire("a")
    result = self.background("a")
    eventually(lambda: len(self.scheduler.queue) == 1)
    self.cancelled.set()
    eventually(lambda: bool(result))
    self.assertEqual([8], result)
    self.assertEqual([], self.scheduler.queue)

  def test_nonblocking_rejects_without_queue_leak(self):
    self.acquire("a")
    with self.assertRaises(mw.Rejected) as failure:
      self.acquire("a", no_wait=True)
    self.assertEqual(9, failure.exception.code)
    self.assertEqual([], self.scheduler.queue)

  def test_later_workspace_cannot_steal_capacity_from_eligible_waiter(self):
    self.scheduler.queue.append((object(), "earlier"))
    with self.assertRaises(mw.Rejected) as failure:
      self.acquire("later", no_wait=True)
    self.assertEqual(9, failure.exception.code)
    self.assertEqual(1, len(self.scheduler.queue))

  def test_idle_lru_eviction(self):
    a, b = self.scheduler.slots
    a.engine, b.engine = engine("a"), engine("b")
    a.touched, b.touched = 2, 1
    self.assertIs(b, self.acquire("c"))

  def test_empty_slot_preferred_over_warm_engine(self):
    a, b = self.scheduler.slots
    a.engine = engine("a")
    self.assertIs(b, self.acquire("b"))

  def test_quarantined_slot_never_evicted(self):
    a, b = self.scheduler.slots
    a.engine = engine("a", quarantined=True)
    self.assertIs(b, self.acquire("b"))
    with self.assertRaises(mw.Rejected):
      self.acquire("c", no_wait=True)
    with self.assertRaises(mw.Rejected):
      self.acquire("a")
    self.assertIs(a, self.acquire("a", shutdown=True))

  def test_stop_rejects_new_admission(self):
    self.scheduler.stopping = True
    with self.assertRaises(mw.Rejected) as failure:
      self.acquire("a")
    self.assertEqual(8, failure.exception.code)

  def test_bounded_queue(self):
    self.scheduler.queue = [(object(), "a") for _ in range(mw.MAX_QUEUE)]
    with self.assertRaises(mw.Rejected) as failure:
      self.acquire("a")
    self.assertEqual(9, failure.exception.code)


class ProtocolTest(unittest.TestCase):

  def test_round_trip_non_ascii_and_bytes(self):
    a, b = socket.socketpair()
    with a, b:
      original = {"args": ["café", "x\udc80"], "version": 1}
      a.sendall(mw.encode_frame(original))
      self.assertEqual(original, mw.read_frame(b))

  def test_rejects_oversized_before_allocating_body(self):
    a, b = socket.socketpair()
    with a, b:
      a.sendall(struct.pack("!I", mw.MAX_FRAME + 1))
      with self.assertRaises(mw.Rejected):
        mw.read_frame(b)

  def test_truncated_frame(self):
    a, b = socket.socketpair()
    with a, b:
      a.sendall(struct.pack("!I", 100) + b"{")
      a.shutdown(socket.SHUT_WR)
      with self.assertRaises(EOFError):
        mw.read_frame(b)

  def test_invalid_shapes_and_command(self):
    good = {
        "version": 1,
        "command": "build",
        "cwd": "/a",
        "args": [],
        "environment": {},
    }
    for patch in (
        {"command": "clean"},
        {"environment": {"A=B": "x"}},
        {"args": [1]},
        {"cwd": None},
        {"no_wait": "false"},
    ):
      with self.subTest(patch=patch), self.assertRaises(mw.Rejected):
        mw.validate_request(dict(good, **patch))
    mw.validate_request(good)

  def test_disconnect_cancels(self):
    a, b = socket.socketpair()
    connection = mw.Connection(b)
    a.close()
    with b:
      connection.monitor()
    self.assertTrue(connection.cancelled.is_set())


class OwnershipTest(unittest.TestCase):

  def test_directory_replacement_sticky_fence(self):
    with tempfile.TemporaryDirectory() as root:
      original = Path(root) / "workspace"
      original.mkdir()
      pin = mw.DirectoryPin(original)
      try:
        saved = original.with_name("saved")
        original.rename(saved)
        original.mkdir()
        with self.assertRaises(mw.Rejected):
          pin.check()
        original.rmdir()
        saved.rename(original)
        with self.assertRaises(mw.Rejected):
          pin.check()
      finally:
        pin.close()

  def test_canonical_alias_and_nested_discovery(self):
    with tempfile.TemporaryDirectory() as root:
      workspace = Path(root) / "workspace"
      (workspace / "nested").mkdir(parents=True)
      (workspace / "MODULE.bazel").touch()
      alias = Path(root) / "alias"
      alias.symlink_to(workspace)
      self.assertEqual(str(workspace), mw.workspace_for(alias / "nested"))
      (workspace / "nested/MODULE.bazel").touch()
      self.assertEqual(
          str(workspace / "nested"), mw.workspace_for(alias / "nested")
      )

  def test_supervisor_collision_unclean_and_clean_restart(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      budget = mw.Budget(2, 4, 2048, 512)
      bazel = Path("/usr/bin/true").resolve()
      supervisor = mw.Supervisor(root, bazel, budget)
      try:
        with self.assertRaises(OSError):
          mw.Supervisor(root, bazel, budget)
        journal = json.loads((root / "session.json").read_text())
        self.assertFalse(journal["closed"])
        self.assertNotIn("environment", json.dumps(journal))
      finally:
        supervisor.listener.close()
        supervisor.object_owner.close()
        supervisor.run_pin.close()
        supervisor.root_pin.close()
      with self.assertRaises(mw.Rejected):
        mw.Supervisor(root, bazel, budget)
      journal["closed"] = True
      (root / "session.json").write_text(json.dumps(journal))
      next_supervisor = mw.Supervisor(root, bazel, budget)
      try:
        self.assertNotEqual(supervisor.session, next_supervisor.session)
      finally:
        next_supervisor.listener.close()
        next_supervisor.object_owner.close()
        next_supervisor.run_pin.close()
        next_supervisor.root_pin.close()

  def test_budget_rejects_impossible_allocations(self):
    for arguments in ((0, 4, 4096, 768), (2, 1, 4096, 768), (2, 4, 1024, 768)):
      with self.subTest(arguments=arguments), self.assertRaises(mw.Rejected):
        mw.Budget(*arguments)
    budget = mw.Budget(2, 4, 4096, 768)
    self.assertEqual(1152, budget.action_memory)
    self.assertEqual(2, budget.jobs)
    self.assertIn("FINAL_VALUE_IGNORE_OVERRIDES", budget.policy())


if __name__ == "__main__":
  from absl.testing import absltest

  absltest.main()
