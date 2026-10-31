"""Focused failure-detection and ownership tests; not a Bazel simulation."""

import tempfile
from pathlib import Path
import unittest
from unittest import mock
import subprocess
import sys

from run import (ExpectedState, expected_state, owned_directory, validate_output,
                 set_fixture_state, fixture_input_snapshot, cohort_is_valid,
                 shutdown_failed, java_trust_options, reap_owned_daemon, HERE,
                 CASES, parse_arguments)
import shutil


class HarnessTests(unittest.TestCase):
    def test_nonzero_exit_is_failure_even_if_previous_output_is_correct(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            state = ExpectedState()
            output.write_bytes(state.output_bytes())
            self.assertFalse(validate_output(1, output, state)["valid"])

    def test_zero_exit_with_wrong_or_missing_output_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            self.assertFalse(validate_output(0, output, ExpectedState())["valid"])
            output.write_bytes(b"incorrect\n")
            result = validate_output(0, output, ExpectedState())
            self.assertFalse(result["valid"])
            self.assertNotEqual(result["actual_sha256"], result["expected_sha256"])

    def test_exact_output_and_success_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out"
            state = expected_state("source_divergence", 2)
            output.write_bytes(state.output_bytes())
            self.assertTrue(validate_output(0, output, state)["valid"])

    def test_existing_directory_is_never_reused_or_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sentinel = root / "user-file"
            sentinel.write_text("preserve")
            with self.assertRaises(ValueError):
                owned_directory(root, "state")
            self.assertEqual(sentinel.read_text(), "preserve")

    def test_cases_reset_missing_glob_and_symlink_state(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            shutil.copytree(HERE / "fixture", fixture, symlinks=True)
            set_fixture_state(fixture, expected_state("missing_file_added", 1))
            self.assertTrue((fixture / "optional.txt").exists())
            set_fixture_state(fixture, expected_state("glob_added", 1))
            self.assertFalse((fixture / "optional.txt").exists())
            self.assertTrue((fixture / "extras/extra.txt").exists())
            set_fixture_state(fixture, expected_state("symlink_retarget", 1))
            self.assertFalse((fixture / "extras/extra.txt").exists())
            self.assertEqual((fixture / "link.txt").read_text(), "payload-b\n")
            set_fixture_state(fixture, ExpectedState())
            self.assertEqual((fixture / "link.txt").read_text(), "payload-a\n")

    def test_each_mutation_restores_every_input_including_module_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            shutil.copytree(HERE / "fixture", fixture, symlinks=True)
            baseline = fixture_input_snapshot(fixture)
            for case in CASES:
                with self.subTest(case=case):
                    set_fixture_state(fixture, expected_state(case, 0))
                    set_fixture_state(fixture, ExpectedState())
                    self.assertEqual(baseline, fixture_input_snapshot(fixture))

    def test_context_cases_have_independent_expected_outputs(self):
        contexts = {
            "warm_identical": "module-a|toolchain-a|base-config|base-env",
            "module_override": "module-b|toolchain-a|base-config|base-env",
            "repository_mapping": "module-other|toolchain-a|base-config|base-env",
            "toolchain_divergence": "module-a|toolchain-b|base-config|base-env",
            "environment_divergence": "module-a|toolchain-a|base-config|env-worktree-0",
            "configuration_divergence": "module-a|toolchain-a|config-worktree-0|base-env",
        }
        for case, context in contexts.items():
            with self.subTest(case=case):
                self.assertEqual(expected_state(case, 0).output_bytes(),
                                 ("message=base-build|base-bzl\ncontext=" + context + "\n"
                                  "base-source\npayload-a\nbase-extra\n").encode())
                if case != "warm_identical":
                    self.assertNotEqual(expected_state(case, 0).output_bytes(),
                                        expected_state(case, 1).output_bytes())

    def test_module_inputs_are_in_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            shutil.copytree(HERE / "fixture", fixture, symlinks=True)
            baseline = fixture_input_snapshot(fixture)
            for case in ("module_override", "repository_mapping"):
                set_fixture_state(fixture, expected_state(case, 0))
                self.assertNotEqual(baseline["sha256"], fixture_input_snapshot(fixture)["sha256"])
            set_fixture_state(fixture, ExpectedState())
            (fixture / "modules/dep_a/value.bzl").write_text('MODULE_VALUE = "dirty"\n')
            self.assertNotEqual(baseline["sha256"], fixture_input_snapshot(fixture)["sha256"])

    def test_invocation_options_reset_after_context_changes(self):
        self.assertEqual(ExpectedState().build_options(), [
            "--extra_toolchains=//:toolchain_a", "--action_env=PHASE0_MESSAGE=base-env",
            "--define=phase0_message=base-config"])
        for case, option in (("toolchain_divergence", "--extra_toolchains=//:toolchain_b"),
                             ("environment_divergence", "--action_env=PHASE0_MESSAGE=env-worktree-0"),
                             ("configuration_divergence", "--define=phase0_message=config-worktree-0")):
            self.assertIn(option, expected_state(case, 0).build_options())
            self.assertNotIn(option, ExpectedState().build_options())

    def test_unknown_and_repeated_cases_are_rejected(self):
        with self.assertRaises(ValueError):
            expected_state("module_typo", 0)
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            parse_arguments(["--bazel", "/bazel", "--state-dir", "/state",
                             "--results-dir", "/results",
                             "--cases", "warm_identical,warm_identical"])

    def test_warm_cohort_rejects_missing_or_restarted_server(self):
        record = {"validation": {"valid": True}, "timed_out": False,
                  "server": {"pid": 10, "starttime_ticks": 20},
                  "server_before": {"pid": 10, "starttime_ticks": 20},
                  "evidence_nonempty": {"bep.jsonl": True, "profile.json.gz": True}}
        self.assertTrue(cohort_is_valid([record], 1, 1, require_warm_servers=True))
        record["server_before"]["starttime_ticks"] = 19
        self.assertFalse(cohort_is_valid([record], 1, 1, require_warm_servers=True))
        record["server_before"] = None
        self.assertFalse(cohort_is_valid([record], 1, 1, require_warm_servers=True))

    def test_evidence_and_actual_overlap_are_required(self):
        def record(pid):
            return {"validation": {"valid": True}, "timed_out": False,
                    "server": {"pid": pid},
                    "evidence_nonempty": {"bep.jsonl": True, "profile.json.gz": True}}
        records = [record(10), record(11)]
        self.assertTrue(cohort_is_valid(records, 2, 2))
        self.assertFalse(cohort_is_valid(records, 2, 1))
        records[0]["evidence_nonempty"]["profile.json.gz"] = False
        self.assertFalse(cohort_is_valid(records, 2, 2))

    def test_shutdown_failure_or_remaining_live_server_fails_cleanup(self):
        self.assertFalse(shutdown_failed({"exit_code": 0, "after": None}))
        self.assertTrue(shutdown_failed({"exit_code": 1, "after": None}))
        self.assertTrue(shutdown_failed({"exit_code": 0, "after": {"pid": 10}}))
        self.assertTrue(shutdown_failed({"error": "timeout", "after": None}))

    def test_input_provenance_tracks_dirty_bytes_missing_paths_and_link_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture"
            shutil.copytree(HERE / "fixture", fixture, symlinks=True)
            before = fixture_input_snapshot(fixture)
            by_path = {item["path"]: item for item in before["inputs"]}
            self.assertEqual(by_path["optional.txt"]["type"], "missing")
            self.assertEqual(by_path["link.txt"]["target"], "payload_a.txt")
            (fixture / "bazel-bin").symlink_to("/not-a-fixture-input")
            self.assertEqual(before, fixture_input_snapshot(fixture))
            set_fixture_state(fixture, expected_state("source_divergence", 2))
            self.assertNotEqual(before["sha256"], fixture_input_snapshot(fixture)["sha256"])

    def test_trust_store_is_one_startup_argument_without_disabling_tls(self):
        self.assertEqual(java_trust_options(None), [])
        self.assertEqual(java_trust_options(Path("/tmp/store with spaces")),
                         ["--host_jvm_args=-Djavax.net.ssl.trustStore=/tmp/store with spaces"])

    def test_reused_pid_is_never_reaped(self):
        identity = {"pid": 12345, "starttime_ticks": 10}
        with mock.patch("run.process_identity", return_value={"pid": 12345, "starttime_ticks": 11}), \
                mock.patch("run.os.waitpid") as waitpid:
            self.assertEqual(reap_owned_daemon(identity)["outcome"], "identity-changed-not-reaped")
            waitpid.assert_not_called()

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux PR_SET_CHILD_SUBREAPER")
    def test_subreaper_adopts_and_reaps_only_known_daemon(self):
        # Isolate the process-wide subreaper setting in a separate interpreter.
        # Its double-fork daemon reproduces Bazel's orphan/reparent lifecycle.
        program = r'''
import os
import subprocess
import sys
import threading
import time
from run import enable_child_subreaper, process_identity, reap_owned_daemon
assert enable_child_subreaper()["enabled"]
pid_reader, pid_writer = os.pipe()
release_reader, release_writer = os.pipe()
launcher = os.fork()
if launcher == 0:
    os.close(pid_reader)
    os.close(release_writer)
    daemon = os.fork()
    if daemon == 0:
        os.write(pid_writer, (str(os.getpid()) + "\n").encode())
        os.close(pid_writer)
        os.read(release_reader, 1)
        os._exit(0)
    os._exit(0)
os.close(pid_writer)
os.close(release_reader)
daemon = int(os.read(pid_reader, 64))
os.close(pid_reader)
os.waitpid(launcher, 0)
identity = process_identity(daemon)
assert identity["ppid"] == os.getpid(), identity
unrelated = subprocess.Popen([sys.executable, "-c", "raise SystemExit(42)"])
os.write(release_writer, b"x")
os.close(release_writer)
deadline = time.monotonic() + 5
while time.monotonic() < deadline:
    result = reap_owned_daemon(identity)
    if result["outcome"] == "reaped":
        break
    threading.Event().wait(0.01)
else:
    raise AssertionError("owned orphan was not reaped")
assert result["wait_status"] == 0, result
assert process_identity(daemon) is None
assert unrelated.wait(timeout=3) == 42, "unrelated client exit status was stolen"
print("adopted owned orphan; reaped exact PID; unrelated client exit preserved")
'''
        result = subprocess.run([sys.executable, "-c", program], cwd=HERE,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("reaped exact PID", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
