"""Behavior tests for evidence capture and fail-closed fixture validation; no Bazel runs."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name("paired_baseline.py")
spec = importlib.util.spec_from_file_location("paired_baseline", MODULE)
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


class PairedBaselineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output_base = self.root / "output-base"
        self.output_base.mkdir()
        self.output = self.output_base / "fixture.txt"
        self.output.write_bytes(b"skycache paired fixture\n")
        self.spec = harness.load_fixture(MODULE.parent / "fixture" / "manifest.json")

    def bep(self, changes=None):
        events = [
            {"id": {"namedSet": {"id": "child"}}, "namedSetOfFiles": {
                "files": [{"name": "fixture.txt", "uri": self.output.as_uri()}]}},
            {"id": {"namedSet": {"id": "root"}}, "namedSetOfFiles": {
                "fileSets": [{"id": "child"}]}},
            {"id": {"targetCompleted": {"label": "//:fixture", "configuration": {"id": "real-config"}}}, "completed": {
                "success": True, "outputGroup": [{"name": "default", "fileSets": [{"id": "root"}]}]}},
        ]
        if changes:
            changes(events)
        events.extend([
            {"id": {"buildFinished": {}}, "children": [{"buildMetrics": {}}],
             "finished": {"exitCode": {"name": "SUCCESS"}}},
            {"id": {"buildMetrics": {}}, "buildMetrics": {}, "lastMessage": True},
        ])
        path = self.root / "build.bep.json"
        path.write_text("\n".join(json.dumps(event) for event in events))
        return path

    def test_balanced_pairs_and_invalid_counts(self):
        self.assertEqual(harness.balanced_order(2), [
            (0, "cache-off"), (0, "current-cache"),
            (1, "current-cache"), (1, "cache-off")])
        for count in (-2, 0, 1, 3):
            with self.assertRaises(ValueError):
                harness.balanced_order(count)

    def test_recursive_default_outputs_match_exactly(self):
        actual = harness.validate_outputs(self.bep(), self.output_base, self.spec)
        self.assertTrue(actual["passed"])
        self.assertEqual(actual["output_sha256"], self.spec["outputs"])
        self.assertEqual(actual["terminal"]["build_exit_code"], 0)

    def test_successful_target_prefix_is_not_a_complete_bep(self):
        path = self.bep()
        events = [json.loads(line) for line in path.read_text().splitlines()]
        for truncated in (events[:-1], events[:-2], []):
            with self.assertRaisesRegex(ValueError, "lastMessage"):
                harness.validate_bep_terminal(truncated)

    def test_terminal_marker_must_be_unique_and_last(self):
        events = [json.loads(line) for line in self.bep().read_text().splitlines()]
        with self.assertRaisesRegex(ValueError, "lastMessage"):
            harness.validate_bep_terminal(events + [{"id": {"progress": {"opaqueCount": 99}}}])
        events[0]["lastMessage"] = True
        with self.assertRaisesRegex(ValueError, "lastMessage"):
            harness.validate_bep_terminal(events)

    def test_absent_duplicate_or_failed_build_finished_is_rejected(self):
        events = [json.loads(line) for line in self.bep().read_text().splitlines()]
        for changed in (events[:-2] + events[-1:], events[:-1] + events[-2:]):
            with self.assertRaisesRegex(ValueError, "exactly one buildFinished"):
                harness.validate_bep_terminal(changed)
        for payload in ({"exitCode": {"code": 1, "name": "BUILD_FAILURE"}}, {}):
            events[-2]["finished"] = payload
            with self.assertRaisesRegex(ValueError, "successful exit"):
                harness.validate_bep_terminal(events)

    def test_missing_announced_event_is_rejected_despite_terminal_marker(self):
        events = [json.loads(line) for line in self.bep().read_text().splitlines()]
        events[-2]["children"].append({"buildToolLogs": {}})
        with self.assertRaisesRegex(ValueError, "missing announced child"):
            harness.validate_bep_terminal(events)

    def test_empty_aborted_unconfigured_placeholder_does_not_duplicate_success(self):
        placeholder = {"id": {"targetCompleted": {
            "label": "//:fixture", "configuration": {"id": "none"}}}, "aborted": {}}
        for index in (0, 3):
            path = self.bep(lambda events: events.insert(index, placeholder))
            actual = harness.validate_outputs(path, self.output_base, self.spec)
            self.assertEqual(actual["configured_targets"]["//:fixture"]["configuration"]["id"],
                             "real-config")
            self.assertEqual(actual["ignored_unconfigured_placeholders"], [
                {"identity": placeholder["id"]["targetCompleted"], "aborted": {}}])

    def test_unconfigured_placeholder_alone_cannot_prove_success(self):
        def placeholder_only(events):
            events[-1] = {"id": {"targetCompleted": {
                "label": "//:fixture", "configuration": {"id": "none"}}}, "aborted": {}}
        with self.assertRaisesRegex(ValueError, "every requested target"):
            harness.validate_outputs(self.bep(placeholder_only), self.output_base, self.spec)

    def test_failed_configured_event_cannot_be_hidden_by_success_or_placeholder(self):
        for completion in ({"aborted": {}}, {"completed": {"success": False}}):
            def failure(events):
                events.insert(0, {"id": {"targetCompleted": {
                    "label": "//:fixture", "configuration": {"id": "failed-config"}}}, **completion})
                events.append({"id": {"targetCompleted": {
                    "label": "//:fixture", "configuration": {"id": "none"}}}, "aborted": {}})
            with self.assertRaisesRegex(ValueError, "configured target did not complete successfully"):
                harness.validate_outputs(self.bep(failure), self.output_base, self.spec)

    def test_duplicate_real_completion_rejects_same_or_different_configuration(self):
        for configuration in ("real-config", "another-config"):
            def duplicate(events):
                events.append({"id": {"targetCompleted": {
                    "label": "//:fixture", "configuration": {"id": configuration}}},
                    "completed": {"success": True}})
            with self.assertRaisesRegex(ValueError, "duplicate configured"):
                harness.validate_outputs(self.bep(duplicate), self.output_base, self.spec)

    def test_unconfigured_success_or_nonempty_abort_is_not_the_known_placeholder(self):
        for payload in ({"completed": {"success": True}}, {"aborted": {"reason": "ANALYSIS_FAILURE"}}):
            def unsupported(events):
                events.append({"id": {"targetCompleted": {
                    "label": "//:fixture", "configuration": {"id": "none"}}}, **payload})
            with self.assertRaisesRegex(ValueError, "unsupported unconfigured"):
                harness.validate_outputs(self.bep(unsupported), self.output_base, self.spec)

    def test_output_corruption_fails(self):
        self.output.write_bytes(b"wrong output\n")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            harness.validate_outputs(self.bep(), self.output_base, self.spec)

    def test_incomplete_target_manifest_fails(self):
        path = self.bep(lambda events: events.pop())
        with self.assertRaisesRegex(ValueError, "every requested target"):
            harness.validate_outputs(path, self.output_base, self.spec)

    def test_missing_named_set_fails(self):
        path = self.bep(lambda events: events.pop(0))
        with self.assertRaisesRegex(ValueError, "missing named file set"):
            harness.validate_outputs(path, self.output_base, self.spec)

    def test_unexpected_default_output_fails(self):
        def extra(events):
            events[0]["namedSetOfFiles"]["files"].append(
                {"name": "unexpected.txt", "uri": self.output.as_uri()})
        with self.assertRaisesRegex(ValueError, "output set differs"):
            harness.validate_outputs(self.bep(extra), self.output_base, self.spec)

    def test_output_escape_is_rejected_even_with_correct_digest(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(self.output.read_bytes())
        def escape(events):
            events[0]["namedSetOfFiles"]["files"][0]["uri"] = outside.as_uri()
        with self.assertRaisesRegex(ValueError, "escapes owned"):
            harness.validate_outputs(self.bep(escape), self.output_base, self.spec)

    def test_fixture_input_changes_are_visible(self):
        before = harness.inventory(self.output_base)
        self.output.write_bytes(b"changed")
        self.assertNotEqual(before, harness.inventory(self.output_base))

    def test_optional_ownership_diagnostics_are_copied_and_hashed(self):
        ownership = self.output_base / "local-skycache-ownership.json"
        ownership.write_text('{"before_finish": {}, "after_session_close": {}}')
        result = harness.copy_diagnostics(self.root)
        self.assertEqual(result[ownership.name], harness.digest(ownership))
        self.assertEqual((self.root / "diagnostics" / ownership.name).read_bytes(), ownership.read_bytes())

    def test_wrong_source_commit_fails_before_any_execution(self):
        with patch.object(harness.subprocess, "check_output", return_value=b"actual-commit\n"):
            with self.assertRaisesRegex(ValueError, "source commit differs"):
                harness.source_provenance(self.root, "expected-commit")

    def test_source_cleanliness_and_unverified_binding_are_explicit(self):
        for status in (b"", b" M file.java\n"):
            answers = {("rev-parse", "HEAD"): b"expected\n",
                       ("diff", "--binary", "HEAD"): b"patch" if status else b"",
                       ("ls-files", "--others", "--exclude-standard", "-z"): b"",
                       ("status", "--porcelain=v1"): status,
                       ("rev-parse", "HEAD^{tree}"): b"tree\n"}
            with patch.object(harness.subprocess, "check_output",
                              side_effect=lambda argv: answers[tuple(argv[3:])]):
                result, _ = harness.source_provenance(self.root, "expected")
            self.assertEqual(result["source_state"], "dirty" if status else "clean")
            self.assertIn("not verified", result["binary_source_binding"])

    def test_harness_mutation_before_launch_prevents_worker_execution(self):
        with patch.object(harness, "digest", return_value="changed"), \
                patch.object(harness.subprocess, "run") as launch:
            with self.assertRaisesRegex(ValueError, "harness changed"):
                harness.run_command(self.root / "mutated-before", ["unused"], self.root,
                                    {}, 1, expected_harness_sha="recorded")
        launch.assert_not_called()

    def test_harness_mutation_during_command_is_detected_after_completion(self):
        with patch.object(harness, "digest", side_effect=["recorded", "changed"]), \
                patch.object(harness.subprocess, "run") as launch:
            with self.assertRaisesRegex(ValueError, "harness changed"):
                harness.run_command(self.root / "mutated-after", ["unused"], self.root,
                                    {}, 1, expected_harness_sha="recorded")
        launch.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith("linux"), "experiment requires Linux/WSL")
    def test_existing_run_directory_is_never_reused_or_deleted(self):
        marker = self.root / "keep.txt"
        marker.write_text("keep")
        args = SimpleNamespace(pairs=2, bazel=self.output, source_repo=self.root,
                               manifest=MODULE.parent / "fixture" / "manifest.json",
                               source_commit="declared", run_dir=self.root)
        with patch.object(harness, "source_provenance", return_value=({}, b"")):
            with self.assertRaisesRegex(ValueError, "must not already exist"):
                harness.experiment(args)
        self.assertEqual(marker.read_text(), "keep")

    @unittest.skipUnless(os.name == "posix", "Linux/WSL worker process semantics")
    def test_failed_command_preserves_logs_and_exit_status(self):
        result = harness.run_command(
            self.root / "failed", [sys.executable, "-c",
                "import sys; print('stdout evidence'); print('stderr evidence', file=sys.stderr); sys.exit(7)"],
            self.root, {"PATH": os.environ.get("PATH", "")}, 5)
        self.assertEqual(result["exit_code"], 7)
        self.assertFalse(result["timed_out"])
        self.assertIn("stdout evidence", (self.root / "failed" / "stdout.log").read_text())
        self.assertIn("stderr evidence", (self.root / "failed" / "stderr.log").read_text())
        self.assertGreater(result["elapsed_seconds"], 0)
        self.assertIn("ru_maxrss_raw", result["resources"])

    @unittest.skipUnless(os.name == "posix", "Linux/WSL worker process semantics")
    def test_timeout_preserves_failure_metrics(self):
        result = harness.run_command(
            self.root / "timeout", [sys.executable, "-c", "import time; time.sleep(30)"],
            self.root, {"PATH": os.environ.get("PATH", "")}, 0.05)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)
        self.assertTrue((self.root / "timeout" / "metrics.json").is_file())

    def test_arms_use_same_binary_and_isolated_output_cache_paths(self):
        binary = Path("/test/bazel")
        off = harness.command(binary, self.root, self.root / "off", None, ["//:fixture"], 2048)
        cached = harness.command(binary, self.root, self.root / "cached", self.root / "cache",
                                 ["//:fixture"], 2048)
        self.assertEqual(off[0], cached[0])
        self.assertIn("--experimental_local_skycache=", off)
        self.assertIn(f"--experimental_local_skycache={self.root / 'cache'}", cached)
        self.assertIn(f"--output_base={self.root / 'off' / 'output-base'}", off)
        self.assertIn(f"--output_base={self.root / 'cached' / 'output-base'}", cached)
        self.assertIn("--ignore_all_rc_files", off)
        self.assertIn("--batch", off)


if __name__ == "__main__":
    unittest.main()
