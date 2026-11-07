"""No Bazel builds: verify the exclusive output-state lease and fail-closed reuse."""

import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import baseline
import fixture
import output_bases


@unittest.skipUnless(sys.platform == "linux", "Pool uses Linux flock")
class OutputBasePoolTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="output-base-pool-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pool = self.root / "pool"
        self.binding = {"binary_sha256": "binary", "mode": "vanilla",
                        "fixture": {"clients": 1, "private_tree": "tree", "workspaces": ["workspace"]},
                        "resource_flags": {"jobs": 2}, "cache_paths": {"disk": "disk"}}

    def acquire(self, name, binding=None):
        return output_bases.Pool(self.pool, binding or self.binding, self.root / (name + ".json"))

    def save_report(self, name, **changes):
        report = {"passed": True, "output_base_root": str(self.pool),
                  "final_shutdown": [{"exit_code": 0, "timed_out": False}], **changes}
        output_bases.write_new(self.root / (name + ".json"), report)

    def test_competing_lock_fails_without_adding_marker(self):
        with self.acquire("one"):
            with self.assertRaisesRegex(RuntimeError, "already leased"):
                with self.acquire("two"):
                    self.fail("Concurrent lease acquired")
        self.assertEqual(len(list((self.pool / "runs").iterdir())), 1)

    def test_successful_previous_run_can_be_reused_without_overwriting_evidence(self):
        with self.acquire("one"):
            (self.pool / "o00").mkdir()
            (self.pool / "o00/state").write_bytes(b"existing state")
            self.save_report("one")
        before = (self.root / "one.json").read_bytes()
        with self.acquire("two"):
            self.assertEqual((self.pool / "o00/state").read_bytes(), b"existing state")
            self.save_report("two")
        self.assertEqual((self.root / "one.json").read_bytes(), before)
        markers = sorted((self.pool / "runs").iterdir())
        self.assertEqual(len(markers), 2)
        self.assertEqual(json.loads(markers[-1].read_text())["report_path"], str(self.root / "two.json"))

    def test_mismatched_binding_is_rejected(self):
        with self.acquire("one"):
            self.save_report("one")
        for field, value in (("binary_sha256", "changed"), ("mode", "skycache"),
                             ("fixture", {"clients": 1, "private_tree": "other"}),
                             ("resource_flags", {"jobs": 3}), ("cache_paths", {"disk": "other"})):
            changed = copy.deepcopy(self.binding)
            changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "binding differs"):
                with self.acquire("two", changed):
                    self.fail("Mismatched pool reused")

    def test_interrupted_previous_run_is_rejected(self):
        with self.acquire("one"):
            pass
        with self.assertRaisesRegex(ValueError, "no completed report"):
            with self.acquire("two"):
                self.fail("Interrupted pool reused")

    def test_failed_previous_run_or_shutdown_is_rejected(self):
        for name, changes in (("failed", {"passed": False}),
                              ("shutdown", {"final_shutdown": [{"exit_code": 1}]}),
                              ("missing", {"final_shutdown": []})):
            self.pool = self.root / name
            with self.subTest(failure=name):
                with self.acquire(name):
                    self.save_report(name, **changes)
                with self.assertRaisesRegex(ValueError, "did not finish successfully"):
                    with self.acquire("next-" + name):
                        self.fail("Failed prior run reused")

    def test_live_daemon_is_rejected_even_if_report_claims_shutdown(self):
        with self.acquire("one"):
            directory = self.pool / "o00/server"
            directory.mkdir(parents=True)
            (directory / "server.pid.txt").write_text(str(os.getpid()))
            self.save_report("one")
        with self.assertRaisesRegex(ValueError, "live or invalid daemon"):
            with self.acquire("two"):
                self.fail("Live daemon reused")


class BaselinePoolIntegrationTests(unittest.TestCase):
    def test_output_paths_default_to_run_and_switch_together_for_pool(self):
        args = SimpleNamespace(binary=Path("binary"), install_root=Path("install"), heap_mb=768, jobs=2)
        run = Path("run")
        self.assertEqual(baseline.output_base(args, run, 3), Path("run/o03"))
        args.output_base_root = Path("pool")
        self.assertEqual(baseline.output_base(args, run, 3), Path("pool/o03"))
        self.assertIn("--output_base=" + str(Path("pool/o03")), baseline.startup(args, run, 3))

    @unittest.skipUnless(sys.platform == "linux", "Pool uses Linux flock")
    def test_main_holds_lease_through_shutdown_and_reuses_only_state(self):
        with tempfile.TemporaryDirectory(prefix="output-base-main-test-") as directory:
            root = Path(directory)
            pool = root / "pool"
            command = root / "workspace" / fixture.COMMAND
            command.mkdir(parents=True)
            (command / "root.go").write_bytes(b"source")
            (command / "BUILD.bazel").write_bytes(b"build")
            row = {"index": 0, "workspace": str(root / "workspace"), "variant": "base",
                   "source_state": {}, "source_sha256": fixture.sha256(command / "root.go"),
                   "build_sha256": fixture.sha256(command / "BUILD.bazel")}
            manifest = {"clients": 1, "scenario": "identical", "private_tree": "tree", "worktrees": [row]}
            (root / "fixture").mkdir()
            output_bases.write_new(root / "fixture/manifest.json", manifest)
            binary = root / "binary"
            binary.write_bytes(b"not executable")
            observed = []

            def ensure_lease(args, run, rows, phase):
                observed.append((run.name, phase, str(baseline.output_base(args, run, 0))))
                with self.assertRaisesRegex(RuntimeError, "already leased"):
                    with output_bases.Pool(pool, {}, root / "competing-report.json"):
                        self.fail("Lease not held throughout run")

            def cohort(args, run, rows, phase, env, oracles, **kwargs):
                ensure_lease(args, run, rows, phase)
                self.assertEqual(env["TMP"], str(root / "temporary/tmp"))
                self.assertEqual(env["TMPDIR"], env["TMP"])
                self.assertTrue((root / "temporary/java").is_dir())
                return {"passed": True, "accounting_passed": True, "results": [], "resources": {}}

            def shutdown(args, run, rows, env, phase):
                ensure_lease(args, run, rows, phase)
                return [{"exit_code": 0, "timed_out": False}]

            with ExitStack() as stack:
                stack.enter_context(patch.object(fixture, "source_state", return_value={}))
                stack.enter_context(patch.object(fixture, "clean_env", return_value={}))
                stack.enter_context(patch.object(baseline, "require_fixture_tree"))
                stack.enter_context(patch.object(baseline, "cache_inventory", return_value={}))
                stack.enter_context(patch.object(baseline, "cgroup_path", return_value=None))
                stack.enter_context(patch.object(baseline, "cohort", side_effect=cohort))
                stack.enter_context(patch.object(baseline, "shutdown", side_effect=shutdown))
                stack.enter_context(patch("sys.stdout"))
                for name in ("one", "two"):
                    argv = ["baseline.py", "--root", str(root), "--mode", "vanilla",
                            "--binary", str(binary), "--repository-cache", str(root / "cache"),
                            "--run-id", name, "--cache-state", "restart", "--serial-restart-preparation",
                            "--output-base-root", str(pool), "--temporary-root", str(root / "temporary")]
                    with patch.object(sys, "argv", argv):
                        self.assertEqual(baseline.main(), 0)
            self.assertEqual(len(observed), 8)
            self.assertEqual({path for _, _, path in observed}, {str(pool / "o00")})
            for number, name in enumerate(("one", "two"), 1):
                report = json.loads((root / "runs" / name / "report.json").read_text())
                self.assertTrue(report["passed"])
                self.assertEqual(report["output_base_root"], str(pool))
                self.assertEqual(report["temporary_policy"], {
                    "root": str(root / "temporary"), "tmp": str(root / "temporary/tmp"),
                    "java": str(root / "temporary/java")})
                binding = json.loads((pool / "binding.json").read_text())
                self.assertEqual(binding["temporary_policy"], report["temporary_policy"])
                self.assertIn("output_bases.py", report["harness_sha256"])
                self.assertEqual(report["output_base_pool"]["run_number"], number)
                self.assertEqual(report["output_base_pool"]["previous_report_path"],
                                 str(root / "runs/one/report.json") if number == 2 else None)
            self.assertEqual(len(list((pool / "runs").iterdir())), 2)

    def test_cold_or_fresh_caches_cannot_reuse_output_state(self):
        for state, cache in (("cold", ["--repository-cache", "cache"]),
                             ("restart", ["--fresh-caches"]), ("warm", ["--fresh-caches"])):
            argv = ["baseline.py", "--root", "root", "--mode", "vanilla", "--binary", "binary",
                    "--run-id", "invalid", "--cache-state", state, "--output-base-root", "pool", *cache]
            with self.subTest(state=state), patch.object(sys, "argv", argv), patch.object(
                    sys, "platform", "linux"), patch("sys.stderr"):
                with self.assertRaises(SystemExit) as raised:
                    baseline.main()
                self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
