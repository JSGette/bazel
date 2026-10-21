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

from storage_preflight import GIB, _loop_windows_backing, _wsl_storage, check_storage


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

    def test_verified_loop_image_checks_its_windows_drive_instead_of_distro_drive(self):
        self.host["backing_free_bytes"] = 0
        self.host["windows_drives"] = {"C:\\": 80 * GIB}
        with patch("storage_preflight._loop_windows_backing", return_value={"drive": "C:\\"}):
            result = self.check()
        self.assertEqual(result["filesystems"][0]["host_backing"]["kind"], "windows_loop_image")
        self.assertEqual(result["filesystems"][0]["host_backing"]["free_bytes"], 80 * GIB)

    def test_full_loop_backing_drive_is_rejected_even_when_guest_and_distro_have_space(self):
        self.host["windows_drives"] = {"C:\\": GIB}
        with patch("storage_preflight._loop_windows_backing", return_value={"drive": "C:\\"}):
            with self.assertRaisesRegex(RuntimeError, "Loop backing volume"):
                self.check()

    def test_unobserved_loop_drive_fails_closed(self):
        with patch("storage_preflight._loop_windows_backing", return_value={"drive": "C:\\"}):
            with self.assertRaisesRegex(RuntimeError, "No current Windows capacity"):
                self.check()

    def test_mixed_loop_and_distro_paths_still_require_distro_headroom(self):
        self.host["backing_free_bytes"] = 0
        self.host["windows_drives"] = {"C:\\": 80 * GIB}
        # A Linux-backed path must not inherit another path's verified Windows mapping.
        with patch("storage_preflight._loop_windows_backing", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "WSL backing volume"):
                self.check()

    def test_two_distinct_write_volumes_each_require_its_own_host_check(self):
        other = Path(self.temporary.name) / "second-volume"
        other.mkdir()
        original_stat = Path.stat
        def device_stat(path, *args, **kwargs):
            actual = original_stat(path, *args, **kwargs)
            return SimpleNamespace(st_dev=98765) if path == other else actual
        self.host["backing_free_bytes"] = 0
        self.host["windows_drives"] = {"C:\\": 80 * GIB}
        with patch("storage_preflight.Path.stat", new=device_stat), \
             patch("storage_preflight._wsl_storage", return_value=self.host), \
             patch("storage_preflight.shutil.disk_usage", return_value=SimpleNamespace(free=100 * GIB)), \
             patch("storage_preflight._loop_windows_backing", side_effect=[{"drive": "C:\\"}, None]) as resolve:
            with self.assertRaisesRegex(RuntimeError, "WSL backing volume"):
                check_storage([self.path, other])
        self.assertEqual(resolve.call_count, 2)

    def test_loop_mapping_comes_from_sysfs_and_findmnt(self):
        backing = Path(self.temporary.name) / "image.ext4"
        backing.write_bytes(b"new fixture")
        mount = {"filesystems": [{"source": "C:\\", "fstype": "9p", "target": "/mnt/c"}]}
        with patch("storage_preflight.os.major", return_value=7), \
             patch("storage_preflight.os.minor", return_value=0), \
             patch("storage_preflight.Path.read_text", return_value=str(backing)), \
             patch("storage_preflight.subprocess.check_output", return_value=json.dumps(mount)):
            result = _loop_windows_backing(backing)
        self.assertEqual(result["drive"], "C:\\")
        self.assertEqual(result["image"], str(backing))

    def test_unverifiable_loop_mapping_fails_closed(self):
        with patch("storage_preflight.os.major", return_value=7), \
             patch("storage_preflight.os.minor", return_value=0), \
             patch("storage_preflight.Path.read_text", side_effect=OSError("missing sysfs")):
            with self.assertRaisesRegex(RuntimeError, "Cannot verify loop"):
                _loop_windows_backing(self.path.parent)

    def snapshot_observation(self):
        return {"schema_version": 1, "observed_unix": 990,
                "system_drive": "C:\\", "system_free_bytes": 80 * GIB,
                "distros": [{"name": "fixture", "path": "E:\\WSL\\fixture",
                             "free_bytes": 70 * GIB}],
                "drives": [{"root": "C:\\", "free_bytes": 80 * GIB}]}

    def read_snapshot(self, data):
        path = Path(self.temporary.name) / "windows-storage.json"
        if data is not None:
            path.write_text(json.dumps(data))
        with patch.dict("os.environ", {"WSL_DISTRO_NAME": "fixture",
                        "SKYCACHE_WINDOWS_STORAGE_SNAPSHOT": str(path)}), \
             patch("storage_preflight.time.time", return_value=1000), \
             patch("storage_preflight.subprocess.check_output") as interop:
            try:
                return _wsl_storage()
            finally:
                interop.assert_not_called()

    def test_fresh_host_snapshot_avoids_interop_and_preserves_capacity(self):
        result = self.read_snapshot(self.snapshot_observation())
        self.assertEqual(result["backing_free_bytes"], 70 * GIB)
        self.assertEqual(result["windows_drives"]["C:\\"], 80 * GIB)
        self.assertEqual(result["observed_unix"], 990)

    def test_stale_future_or_invalid_snapshot_timestamp_fails_closed(self):
        for timestamp in (969, 1001, float("nan"), float("inf"), True, "990", None):
            with self.subTest(timestamp=timestamp):
                data = self.snapshot_observation()
                data["observed_unix"] = timestamp
                with self.assertRaisesRegex(RuntimeError, "fresh Windows storage snapshot"):
                    self.read_snapshot(data)

    def test_missing_snapshot_fails_closed_without_interop_fallback(self):
        with self.assertRaisesRegex(RuntimeError, "fresh Windows storage snapshot"):
            self.read_snapshot(None)

    def test_unknown_snapshot_schema_fails_closed(self):
        data = self.snapshot_observation()
        data["schema_version"] = 2
        with self.assertRaisesRegex(RuntimeError, "unsupported observation schema"):
            self.read_snapshot(data)

    def test_malformed_and_oversized_snapshot_fails_closed(self):
        path = Path(self.temporary.name) / "windows-storage.json"
        for content in ('{"schema_version":', 'x' * (64 * 1024 + 1)):
            with self.subTest(size=len(content)):
                path.write_text(content)
                with self.assertRaisesRegex(RuntimeError, "fresh Windows storage snapshot"):
                    self.read_snapshot(None)

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
