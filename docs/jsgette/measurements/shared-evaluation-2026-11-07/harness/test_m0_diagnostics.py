"""Offline protocol tests; never launch Bazel or read the Linux runtime mount."""

import copy
import json
from pathlib import Path
import tempfile
import threading
import sys
import unittest
from unittest.mock import Mock, patch

import m0_diagnostics as diagnostic


ARCHIVE = Path(__file__).parent.parent / "evidence/m0-v7b-01/i1-off-r-run.tar.gz"


class DiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template, cls.reference = diagnostic.load_template(ARCHIVE)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="m0-diagnostics-tests-", dir=Path(__file__).resolve().parents[6])
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_archived_template_and_report_hashes_are_pinned(self):
        self.assertEqual(self.reference["binary_sha256"], diagnostic.BINARY_SHA256)
        self.assertEqual(self.template["argv"][9], diagnostic.fixture.TARGET)
        with self.assertRaises(ValueError):
            diagnostic.pinned_json(b"{}", diagnostic.COMMAND_SHA256)

    def test_only_declared_operational_and_profiler_arguments_change(self):
        root = Path("/mnt/shared-evaluation-20261107/m0-diagnostics-v1/on")
        argv = diagnostic.command_argv(self.template, self.template["argv"][0], root, "on", "restart", True)
        changed = [(old, new) for old, new in zip(self.template["argv"], argv) if old != new]
        self.assertEqual(len(changed), 7)
        self.assertIn("--experimental_command_profile=cpu", argv)
        self.assertIn("--experimental_analysis_cache_idle_retention=3600s", argv)
        self.assertIn("--repository_cache=/mnt/shared-evaluation-20261107/repository-cache-v7", argv)
        self.assertIn("--host_jvm_args=-Xmx768m", argv)
        self.assertIn("--host_jvm_args=-XX:ActiveProcessorCount=2", argv)
        self.assertIn("--repo_env=TMP=" + str(root / "tmp"), argv)
        self.assertIn("--repo_env=TMPDIR=" + str(root / "tmp"), argv)
        prep = diagnostic.command_argv(self.template, Path(self.template["argv"][0]), root, "off", "preparation", False)
        self.assertFalse(any("command_profile" in x for x in prep))
        self.assertIn("--experimental_local_skycache=", prep)
        self.assertFalse(any("idle_retention" in x for x in prep))

    def test_phase_outputs_are_distinct_and_startup_is_identical(self):
        commands = [diagnostic.command_argv(self.template, Path("binary"), self.root, "off", phase, True)
                    for phase in ("preparation", "restart", "warm")]
        self.assertEqual(diagnostic.startup(commands[0]), diagnostic.startup(commands[1]))
        self.assertEqual(len({next(x for x in c if x.startswith("--build_event_json_file=")) for c in commands}), 3)
        self.assertEqual(len({next(x for x in c if x.startswith("--profile=")) for c in commands}), 3)

    def test_run_root_refuses_existing_wrong_arm_and_outside_paths(self):
        with patch.object(diagnostic, "RUNTIME", self.root):
            path = self.root / "m0-diagnostics-v1/off"
            diagnostic.require_new_root(path, "off")
            with self.assertRaises(ValueError):
                diagnostic.require_new_root(path, "on")
            with self.assertRaises(ValueError):
                diagnostic.require_new_root(self.root / "outside", "off")
            path.mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                diagnostic.require_new_root(path, "off")

    def oracle(self):
        oracle = copy.deepcopy(self.reference)
        oracle["mode"] = "vanilla"
        return oracle

    def test_oracle_requires_successful_matching_vanilla_inputs(self):
        oracle = self.oracle()
        expected = oracle["measured"]["results"][0]["output"]["sha256"]
        self.assertEqual(diagnostic.check_oracle(oracle, self.reference["fixture"]), expected)
        for mutate in (
                lambda x: x.update(passed=False),
                lambda x: x.update(mode="skycache"),
                lambda x: x["fixture"].update(private_tree="changed"),
                lambda x: x["measured"]["results"][0]["behavior"].update(passed=False),
                lambda x: x["fixture"]["worktrees"][0].update(compilation_mode="opt")):
            bad = copy.deepcopy(oracle)
            mutate(bad)
            with self.assertRaises(ValueError):
                diagnostic.check_oracle(bad, self.reference["fixture"])

    def test_stale_or_missing_profile_is_a_gap_never_reused(self):
        source, destination = self.root / "cpu.jfr", self.root / "saved.jfr"
        self.assertFalse(diagnostic.copy_jfr(source, destination, None)["available"])
        source.write_bytes(b"old")
        token = diagnostic.file_token(source)
        self.assertFalse(diagnostic.copy_jfr(source, destination, token)["available"])
        self.assertFalse(destination.exists())
        source.write_bytes(b"fresh profile")
        result = diagnostic.copy_jfr(source, destination, token)
        self.assertTrue(result["available"])
        self.assertEqual(destination.read_bytes(), b"fresh profile")
        with self.assertRaises(FileExistsError):
            diagnostic.copy_jfr(source, destination, None)

    @staticmethod
    def sample(pid=30, start=1100, ticks=50, scope=1000, eligible=True):
        return {"eligible": eligible, "processes": ([{"pid": pid, "start_ticks": start}] if eligible else []),
                "cpu_ticks": ticks if eligible else None, "pss_bytes": 1000 if eligible else None,
                "observer_thread_cpu_ns": 17,
                "cgroup": {"eligible": True, "cpu.stat": {"usage_usec": scope}}}

    def finish_window(self, rows, boundary=1000):
        window = diagnostic.Window.__new__(diagnostic.Window)
        window.stop, window.thread, window.sample = threading.Event(), Mock(), Mock()
        window.rows, window.errors = rows, []
        window.hz, window.birth_boundary, window.interval = 100, boundary, .1
        return window.finish()

    def test_new_daemon_cpu_requires_birth_after_window_and_final_observation(self):
        rows = [self.sample(eligible=False), self.sample(scope=2000)]
        result = self.finish_window(rows)
        self.assertTrue(result["accounting_passed"])
        self.assertEqual(result["engine_cpu_seconds"], .5)
        self.assertEqual(result["observer_thread_cpu_seconds"], 34 / 1e9)
        self.assertFalse(self.finish_window(rows, boundary=1200)["accounting_passed"])
        self.assertFalse(self.finish_window(rows + [self.sample(eligible=False)])["accounting_passed"])

    def test_warm_cpu_uses_identity_matched_delta_not_process_lifetime(self):
        result = self.finish_window([self.sample(ticks=40), self.sample(ticks=50, scope=2000)])
        self.assertTrue(result["accounting_passed"])
        self.assertEqual(result["engine_cpu_seconds"], .1)
        changed = self.finish_window([self.sample(start=1100), self.sample(start=1200, scope=2000)])
        self.assertFalse(changed["accounting_passed"])
        changed = self.finish_window([self.sample(pid=30), self.sample(pid=31, scope=2000)])
        self.assertFalse(changed["accounting_passed"])

    def test_observed_but_ineligible_process_is_not_treated_as_pending_startup(self):
        invalid = self.sample(eligible=False)
        invalid["processes"] = [{"pid": 30, "start_ticks": 1100, "errors": {"membership": "outside"}}]
        self.assertFalse(self.finish_window([invalid, self.sample(scope=2000)])["accounting_passed"])

    def test_restart_and_warm_lifecycle_is_enforced(self):
        def phase(name, pid):
            return {"phase": name, "resources": {"engine_identity": {"pid": pid, "start_ticks": pid}}}
        prep, restart = phase("preparation", 1), phase("restart", 2)
        diagnostic.require_lifecycle([prep], restart)
        diagnostic.require_lifecycle([prep, restart], phase("warm", 2))
        with self.assertRaises(RuntimeError):
            diagnostic.require_lifecycle([prep], phase("restart", 1))
        with self.assertRaises(RuntimeError):
            diagnostic.require_lifecycle([prep, restart], phase("warm", 3))

    def test_output_digest_failure_blocks_behavior_success(self):
        executable = self.root / "normalize_cmd"
        executable.write_bytes(b"wrong output")
        bep = {"finished": True, "exit_code": {"name": "SUCCESS"},
               "files": [{"uri": executable.as_uri()}]}
        # URL handling is Linux-specific; force the fake artifact path explicitly on Windows.
        with patch.object(diagnostic.baseline, "bep_metrics", return_value=bep), \
                patch.object(diagnostic, "unquote", return_value=str(executable)), \
                patch.object(diagnostic.fixture, "verify_program") as behavior:
            with self.assertRaisesRegex(ValueError, "digest"):
                diagnostic.verify_output(self.root, "expected-digest")
            behavior.assert_not_called()

    def test_build_failure_still_shutdown_and_persists_failed_report(self):
        runtime = self.root / "runtime"
        runtime.mkdir()
        template = copy.deepcopy(self.template)
        for name, flag in (("install", "--output_user_root="), ("repository", "--repository_cache="),
                           ("actions", "--disk_cache=")):
            path = runtime / name
            path.mkdir()
            template["argv"] = [flag + str(path) if item.startswith(flag) else item for item in template["argv"]]
        manifest_path, oracle_path, binary = self.root / "manifest.json", self.root / "oracle.json", self.root / "binary"
        manifest_path.write_text(json.dumps(self.reference["fixture"]))
        oracle_path.write_text(json.dumps(self.oracle()))
        binary.write_bytes(b"fake")
        original_hash = diagnostic.fixture.sha256
        def digest(path):
            path = Path(path)
            if path == binary:
                return diagnostic.BINARY_SHA256
            if path == oracle_path:
                return self.reference["oracle_report"]["sha256"]
            return diagnostic.HELPER_HASHES.get(path.name) or original_hash(path)
        run_root = runtime / "m0-diagnostics-v1/off"
        with patch.object(diagnostic, "RUNTIME", runtime), \
                patch.object(diagnostic.sys, "platform", "linux"), \
                patch.object(diagnostic, "load_template", return_value=(template, self.reference)), \
                patch.object(diagnostic.fixture, "sha256", side_effect=digest), \
                patch.object(diagnostic, "check_sources"), \
                patch.object(diagnostic, "scope_contract", return_value=(Path("/sys/fs/cgroup/test.scope"), {})), \
                patch.object(diagnostic, "build_phase", side_effect=ValueError("behavior failure")), \
                patch.object(diagnostic, "run_command", return_value={"exit_code": 0, "timed_out": False}) as command, \
                patch.dict(diagnostic.os.environ):
            code = diagnostic.main(["--run-root", str(run_root), "--arm", "off", "--binary", str(binary),
                                    "--template-archive", str(ARCHIVE), "--fixture-manifest", str(manifest_path),
                                    "--oracle-report", str(oracle_path)])
        self.assertEqual(code, 1)
        self.assertEqual(command.call_args.args[0][-1], "shutdown")
        report = json.loads((run_root / "report.json").read_text())
        self.assertFalse(report["passed"])
        self.assertIn("behavior failure", report["error"])

    @unittest.skipUnless(sys.platform == "linux", "Child CPU rusage requires Linux")
    def test_command_logs_preserve_stdout_stderr_and_nonzero_status(self):
        result = diagnostic.run_command(
            [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"],
            self.root, dict(diagnostic.os.environ), self.root / "child", 5)
        self.assertEqual(result["exit_code"], 7)
        self.assertFalse(result["timed_out"])
        self.assertEqual((self.root / "child/stdout.log").read_text().strip(), "out")
        self.assertEqual((self.root / "child/stderr.log").read_text().strip(), "err")
        self.assertGreaterEqual(result["waited_children_user_cpu_seconds"], 0)


if __name__ == "__main__":
    unittest.main()
