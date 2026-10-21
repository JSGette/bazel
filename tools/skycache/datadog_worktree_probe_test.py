# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Focused checks for live measurement storage cancellation."""

from pathlib import Path
import signal
import threading
import unittest
from unittest.mock import Mock, call, patch

from datadog_worktree_probe import watch_storage


class StorageGuardTest(unittest.TestCase):
    def test_capacity_failure_cancels_only_registered_live_clients(self):
        finished = Mock()
        finished.wait.return_value = False
        errors = []
        with patch("datadog_worktree_probe.check_storage", side_effect=RuntimeError("host floor")), \
             patch("datadog_worktree_probe.os.kill") as kill:
            watch_storage([Path("fixture")], finished, {"a": 123, "b": 456}, threading.Lock(), errors)
        self.assertEqual(kill.call_args_list, [call(123, signal.SIGINT), call(456, signal.SIGINT)])
        self.assertEqual(errors[0]["error"], "host floor")

    def test_completed_phase_neither_checks_nor_cancels(self):
        finished = Mock()
        finished.wait.return_value = True
        with patch("datadog_worktree_probe.check_storage") as check, \
             patch("datadog_worktree_probe.os.kill") as kill:
            watch_storage([], finished, {"a": 123}, threading.Lock(), [])
        check.assert_not_called()
        kill.assert_not_called()

    def test_capacity_is_rechecked_and_a_process_exiting_during_cancel_is_harmless(self):
        finished = Mock()
        finished.wait.return_value = False
        errors = []
        with patch("datadog_worktree_probe.check_storage", side_effect=[{}, RuntimeError("capacity lost")]) as check, \
             patch("datadog_worktree_probe.os.kill", side_effect=[ProcessLookupError(), None]) as kill:
            watch_storage([], finished, {"a": 123, "b": 456}, threading.Lock(), errors)
        self.assertEqual(check.call_count, 2)
        self.assertEqual(kill.call_count, 2)
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
