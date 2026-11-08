"""Offline acceptance protocol checks; fake binaries are never executed."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_integration_test as acceptance


class ProtocolTests(unittest.TestCase):

  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory(
        prefix=".acceptance-test-", dir=Path(__file__).resolve().parent)
    self.addCleanup(self.temporary.cleanup)
    self.directory = Path(self.temporary.name).resolve()
    self.binary = self.directory / "fake-bazel"
    self.binary.write_bytes(b"Never execute this file")

  def harness(self, **kwargs):
    return acceptance.Harness(self.binary, self.directory / "run", **kwargs)

  def test_cap_and_resource_contract(self):
    h = self.harness(expected_engine_cap=10)
    self.assertEqual(len(h.workspaces), 10)
    self.assertEqual(len([p for p in h.root.iterdir() if (p / "MODULE.bazel").exists()]), 11)
    self.assertIn("--host_jvm_args=-Xmx7680m", h.startup)
    self.assertIn("--host_jvm_args=-XX:ActiveProcessorCount=2", h.startup)
    self.assertIn("--ignore_all_rc_files", h.startup)
    self.assertEqual(h.env["TMPDIR"], str(h.root / "tmp"))
    self.assertEqual(h.repository_cache, h.root / "repository-cache")
    self.assertEqual(h.common.count("--repo_env=TMP=" + str(h.tmp_root)), 1)

  def test_explicit_resources_and_default_cap(self):
    h = self.harness(heap_mib=2048, processors=3)
    self.assertEqual(h.expected_engine_cap, 2)
    self.assertEqual(h.workspaces, ["a", "b"])
    self.assertIn("--host_jvm_args=-Xmx2048m", h.startup)
    self.assertIn("--host_jvm_args=-XX:ActiveProcessorCount=3", h.startup)

  def test_writable_paths_outside_root_rejected_before_creation(self):
    for key in ("tmp_root", "repository_cache"):
      with self.subTest(key=key), self.assertRaises(ValueError):
        self.harness(**{key: self.directory / "outside"})
    self.assertFalse((self.directory / "run").exists())

  def test_existing_root_preserved(self):
    root = self.directory / "run"
    root.mkdir()
    (root / "keep").write_text("unchanged")
    with self.assertRaises(ValueError):
      self.harness()
    self.assertEqual((root / "keep").read_text(), "unchanged")

  def test_capwide_case_has_each_workspace_and_unique_barrier(self):
    h = self.harness(expected_engine_cap=4)
    d = h.case("wide", h.workspaces, workspaces=h.workspaces)
    for name in h.workspaces:
      self.assertEqual((h.root / name / "input.txt").read_text(), name + "\n")
      text = (h.root / name / "BUILD.bazel").read_text()
      self.assertIn("ready-", text)
      command = json.loads(text.split("cmd=", 1)[1].strip()[:-1])
      self.assertIn(str(d / "release"), command)
    with self.assertRaises(AssertionError):
      h.case("bad", ["a"], workspaces=h.workspaces)

  def test_completed_cleanup_does_not_launch_replacement_daemon(self):
    h = self.harness()
    h.daemon_identity = (101, 400)
    h.daemon_history = [(101, 400)]
    h.checks = sorted(acceptance.REQUIRED_CHECKS)
    h.run_completed = True
    with patch.object(acceptance, "process_identity", return_value=None), \
         patch.object(acceptance.subprocess, "Popen", side_effect=AssertionError("must not launch")):
      h.close()
    self.assertTrue(json.loads((h.root / "results.json").read_text())["success"])

  def test_reused_pid_is_not_owned_and_never_shutdown(self):
    h = self.harness()
    h.daemon_identity = (101, 400)
    h.daemon_history = [(101, 400)]
    with patch.object(acceptance, "process_identity", return_value=(101, 900)), \
         patch.object(h, "start", side_effect=AssertionError("must not launch")):
      h.close()
    self.assertFalse(json.loads((h.root / "results.json").read_text())["success"])

  def test_cleanup_covers_all_open_engines_and_excludes_closed_slots(self):
    h = self.harness(expected_engine_cap=4)
    h.daemon_identity = (101, 400)
    h.daemon_history = [(101, 400)]
    h.started_engines = set(h.workspaces)
    h.closed_engines = {"a"}
    h.open_engines = set(h.workspaces) - {"a"}
    remaining = set(h.open_engines)
    shutdowns = []
    def shutdown(name, directory, label):
      shutdowns.append(name)
      remaining.remove(name)
    with patch.object(acceptance, "process_identity", side_effect=lambda _: (101, 400) if remaining else None), \
         patch.object(h, "shutdown_engine", side_effect=shutdown):
      h.close()
    self.assertEqual(set(shutdowns), set(h.workspaces) - {"a"})
    self.assertEqual(h.cleanup_errors, [])

  def test_named_check_set_and_completion_both_required(self):
    h = self.harness()
    h.checks = sorted(acceptance.REQUIRED_CHECKS)
    h.close()
    result = json.loads((h.root / "results.json").read_text())
    self.assertFalse(result["success"])
    self.assertFalse(result["run_completed"])
    with self.assertRaises(AssertionError):
      h.check("invented check")
    with self.assertRaises(AssertionError):
      h.check(h.checks[0])

  def test_identical_materialization_rejects_missing_and_stale_outputs(self):
    h = self.harness()
    for name in ("a", "b"):
      (h.root / name / "bazel-bin").mkdir()
    a = h.root / "a/bazel-bin/out.txt"
    b = h.root / "b/bazel-bin/out.txt"
    a.write_text("same\n")
    with self.assertRaises(FileNotFoundError):
      h.check_identical_materialization()
    self.assertEqual(h.checks, [])
    for stale, correct in ((a, b), (b, a)):
      stale.write_text("stale\n")
      correct.write_text("same\n")
      with self.assertRaises(AssertionError):
        h.check_identical_materialization()
      self.assertEqual(h.checks, [])
    a.write_text("same\n")
    b.write_text("same\n")
    h.check_identical_materialization()
    self.assertEqual(h.checks, ["identical action digest executes once and materializes twice"])

  def test_closed_reopen_accepts_only_known_fenced_or_closed_rejection(self):
    identities = {"workspace": (7, 101), "output_base": (7, 202)}
    for reason in (
        "Workspace directories changed; engine is fenced",
        "Workspace engine has shut down; restart the daemon to reopen it",
    ):
      with self.subTest(reason=reason):
        acceptance.validate_closed_reopen(
            "WARNING: startup diagnostic\nCannot admit workspace engine: " + reason + "\n",
            identities, identities)
    for message in (
        "Cannot admit workspace engine: unrelated failure\n",
        "Workspace directories changed; engine is fenced\n",
        "Cannot admit workspace engine: Workspace directories changed; engine is fenced unexpectedly\n",
        "Cannot admit workspace engine: Workspace directories changed; engine is fenced\n"
        "Cannot admit workspace engine: unrelated failure\n",
    ):
      with self.subTest(message=message), self.assertRaises(AssertionError):
        acceptance.validate_closed_reopen(message, identities, identities)

  def test_closed_reopen_rejects_changed_workspace_or_output_base_identity(self):
    identities = {"workspace": (7, 101), "output_base": (7, 202)}
    message = "Cannot admit workspace engine: Workspace directories changed; engine is fenced\n"
    for name in identities:
      for field in (0, 1):
        changed = dict(identities)
        pair = list(changed[name])
        pair[field] += 1
        changed[name] = tuple(pair)
        with self.subTest(name=name, field=field), self.assertRaises(AssertionError):
          acceptance.validate_closed_reopen(message, identities, changed)
    h = self.harness()
    actual = (h.root / "a").stat()
    self.assertEqual(acceptance.directory_identity(h.root / "a"),
                     (actual.st_dev, actual.st_ino))


if __name__ == "__main__":
  unittest.main()
