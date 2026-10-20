# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Regression checks for the guest-free/host-full failure that interrupted a probe."""

from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from storage_preflight import GIB, check_storage


class StoragePreflightTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "not-created"
        self.host = {
            "distro": "fixture", "distro_path": "E:\\WSL\\fixture",
            "backing_free_bytes": 100 * GIB,
            "system_drive": "C:\\", "system_free_bytes": 100 * GIB,
        }

    def check(self, guest_free=100 * GIB):
        with patch("storage_preflight._wsl_storage", return_value=self.host), \
             patch("storage_preflight.shutil.disk_usage",
                   return_value=SimpleNamespace(free=guest_free)):
            return check_storage([self.path])

    def test_full_host_is_rejected_even_when_guest_has_space(self):
        self.host["backing_free_bytes"] = 0
        with self.assertRaisesRegex(RuntimeError, "WSL backing volume"):
            self.check()
        self.assertFalse(self.path.exists())

    def test_system_drive_reserve_is_independent_of_backing_volume(self):
        self.host["system_free_bytes"] = GIB
        with self.assertRaisesRegex(RuntimeError, "Windows system drive"):
            self.check()

    def test_guest_volume_must_also_have_space(self):
        with self.assertRaisesRegex(RuntimeError, "Insufficient storage"):
            self.check(guest_free=GIB)

    def test_non_wsl_linux_does_not_require_windows(self):
        self.host = None
        result = self.check()
        self.assertIsNone(result["wsl_host"])
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
