# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Focused checks for live measurement storage cancellation."""

from pathlib import Path
import signal
import tempfile
import threading
import unittest
from unittest.mock import Mock, call, patch

from datadog_worktree_probe import relocate_copied_symlinks, watch_storage


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


class CopiedOutputSymlinkTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old_workspace = self.root / "old-workspace"
        self.new_workspace = self.root / "new-workspace"
        self.old_output = self.root / "old-output"
        self.new_output = self.root / "new-output"
        for path in (self.old_workspace, self.new_workspace, self.old_output, self.new_output):
            path.mkdir()
        self.mappings = [(self.old_workspace, self.new_workspace), (self.old_output, self.new_output)]

    def test_repository_and_sdk_aliases_relocate_without_touching_other_data(self):
        external = self.new_output / "external"
        external.mkdir()
        (external / "_main").symlink_to(self.old_workspace)
        (external / "sdk").symlink_to(self.old_output / "external/go_sdk/bin")
        (external / "relative").symlink_to("../relative-target")
        outside = self.root / "shared-cache"
        outside.mkdir()
        (outside / "untouched").symlink_to(self.old_workspace)
        (external / "shared").symlink_to(outside)
        (external / "prefix-lookalike").symlink_to(Path(str(self.old_workspace) + "-other"))
        text = str(self.old_workspace).encode()
        (external / "data").write_bytes(text)
        source_link = self.old_output / "source-link"
        source_link.symlink_to(self.old_workspace)
        changes = relocate_copied_symlinks(self.new_output, self.mappings)
        self.assertEqual([r["path"] for r in changes], ["external/_main", "external/sdk"])
        self.assertEqual((external / "_main").readlink(), self.new_workspace)
        self.assertEqual((external / "sdk").readlink(), self.new_output / "external/go_sdk/bin")
        self.assertEqual((external / "relative").readlink(), Path("../relative-target"))
        self.assertEqual((external / "shared").readlink(), outside)
        self.assertEqual((outside / "untouched").readlink(), self.old_workspace)
        self.assertEqual(source_link.readlink(), self.old_workspace)
        self.assertEqual((external / "data").read_bytes(), text)
        self.assertEqual(relocate_copied_symlinks(self.new_output, self.mappings), [])

    def test_replacement_failure_preserves_original_link_and_cleans_temporary(self):
        link = self.new_output / "link"
        link.symlink_to(self.old_workspace)
        with patch("datadog_worktree_probe.os.replace", side_effect=OSError("replacement failed")):
            with self.assertRaisesRegex(OSError, "replacement failed"):
                relocate_copied_symlinks(self.new_output, self.mappings)
        self.assertEqual(link.readlink(), self.old_workspace)
        self.assertEqual([p.name for p in self.new_output.iterdir()], ["link"])

    def test_temporary_name_collision_preserves_the_preexisting_file(self):
        link = self.new_output / "link"
        link.symlink_to(self.old_workspace)
        occupied = self.new_output / ".skycache-relocate-occupied"
        occupied.write_bytes(b"preserve-existing-file")
        with patch("datadog_worktree_probe.uuid.uuid4", return_value=Mock(hex="occupied")):
            with self.assertRaises(FileExistsError):
                relocate_copied_symlinks(self.new_output, self.mappings)
        self.assertEqual(occupied.read_bytes(), b"preserve-existing-file")
        self.assertEqual(link.readlink(), self.old_workspace)

    def test_rejects_live_server_source_destination_and_symlink_root(self):
        server = self.new_output / "server"
        server.mkdir()
        (server / "server.pid.txt").write_text("123")
        with self.assertRaisesRegex(ValueError, "live server"):
            relocate_copied_symlinks(self.new_output, self.mappings)
        with self.assertRaisesRegex(ValueError, "separate"):
            relocate_copied_symlinks(self.old_output, self.mappings)
        alias = self.root / "alias"
        alias.symlink_to(self.new_output)
        with self.assertRaisesRegex(ValueError, "real directory"):
            relocate_copied_symlinks(alias, self.mappings)


if __name__ == "__main__":
    unittest.main()
