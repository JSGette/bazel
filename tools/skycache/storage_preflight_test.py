# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Regression checks for the guest-free/host-full failure that interrupted a probe."""

from pathlib import Path
import json
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from storage_preflight import GIB, _wsl_storage, check_storage


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

    def test_transient_interop_failures_retry_current_host_observation(self):
        observation = json.dumps({
            "system_drive": "C:\\", "system_free_bytes": 80 * GIB,
            "distros": [{"name": "fixture", "path": "E:\\WSL\\fixture",
                         "free_bytes": 70 * GIB}],
        })
        failures = [OSError("temporary interop failure"),
                    subprocess.CalledProcessError(1, "powershell.exe", stderr="transient"),
                    observation]
        with patch.dict("os.environ", {"WSL_DISTRO_NAME": "fixture"}), \
             patch("storage_preflight.subprocess.check_output", side_effect=failures) as run, \
             patch("storage_preflight.time.sleep") as sleep:
            result = _wsl_storage()
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(result["backing_free_bytes"], 70 * GIB)
        self.assertEqual(result["system_free_bytes"], 80 * GIB)

    def test_persistent_interop_failure_still_rejects_probe_with_diagnostic(self):
        failure = subprocess.CalledProcessError(1, "powershell.exe", stderr="interop unavailable")
        with patch.dict("os.environ", {"WSL_DISTRO_NAME": "fixture"}), \
             patch("storage_preflight.subprocess.check_output", side_effect=failure) as run, \
             patch("storage_preflight.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts: interop unavailable"):
                _wsl_storage()
        self.assertEqual(run.call_count, 3)


if __name__ == "__main__":
    unittest.main()
