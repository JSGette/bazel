"""Run with python3 -B -m unittest discover -s <harness> -p test_harness.py.

Uses tiny private Git repositories and mocked accounting observations. These
tests validate the measurement harness; they run no Bazel build and provide no
performance or real-Datadog compatibility evidence.
"""

from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import baseline
import fixture


GO_SOURCES = (
    "bundle.go", "clone.go", "doc.go", "listmap.go", "metadata.go", "metrics.go",
    "profile_definition.go", "splitoids.go", "validation.go", "yaml_utils.go",
)


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="shared-evaluation-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


@unittest.skipUnless(shutil.which("git"), "Git is required for fixture preparation")
class ScenarioTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        self.source = self.root / "source"
        package = self.source / fixture.PACKAGE
        command = self.source / fixture.COMMAND
        command.mkdir(parents=True)
        (package / "BUILD.bazel").write_text(
            'go_library(\nname = "profiledefinition",\nsrcs = '
            + json.dumps(GO_SOURCES) + ',\n)\n', encoding="utf-8")
        for name in GO_SOURCES:
            (package / name).write_text("package profiledefinition\n", encoding="utf-8")
        (command / "root.go").write_text(
            'package cmd\n// filename = name + ".json"\n', encoding="utf-8")
        (command / "BUILD.bazel").write_text(
            'go_library(name="cmd", srcs = ["root.go"],)\n', encoding="utf-8")
        fixture.git(self.source, "init", "--quiet")
        fixture.git(self.source, "add", "--all")
        self.commit("source")
        revision = fixture.git(self.source, "rev-parse", "HEAD").decode().strip()
        self.addCleanup(patch.stopall)
        patch.object(fixture, "REVISION", revision).start()
        patch.object(fixture, "PROJECTION", (".",)).start()

    def commit(self, message):
        fixture.git(self.source, "-c", "user.name=Harness test", "-c", "user.email=test@invalid",
                    "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", message)

    def prepare(self, name, clients, scenario):
        return fixture.prepare(self.source, self.root / name, clients, scenario)

    def test_disjoint_edits_use_ten_distinct_declared_sources(self):
        before = fixture.source_state(self.source)
        manifest = self.prepare("disjoint", 10, "disjoint")
        rows = manifest["worktrees"]
        self.assertEqual(len({row["changed_file"] for row in rows}), 10)
        self.assertEqual(len({fixture.oracle_key(row) for row in rows}), 10)
        for index, row in enumerate(rows):
            self.assertEqual(row["changed_file"], fixture.PACKAGE + "/" + GO_SOURCES[index])
            self.assertEqual(row["behavior_variant"], "base")
            self.assertEqual(row["changed_sha256"],
                             fixture.sha256(Path(row["workspace"]) / row["changed_file"]))
            self.assertEqual(fixture.source_state(row["workspace"]), row["source_state"])
        self.assertEqual(fixture.source_state(self.source), before)

    def test_configurations_have_separate_oracle_keys(self):
        manifest = self.prepare("config", 10, "config")
        self.assertEqual([row["compilation_mode"] for row in manifest["worktrees"]],
                         ["fastbuild", "opt"] * 5)
        self.assertEqual({fixture.oracle_key(row) for row in manifest["worktrees"]},
                         {"base|fastbuild", "base|opt"})
        one = self.prepare("config-one", 1, "config")
        self.assertEqual(one["worktrees"][0]["compilation_mode"], "opt")

    def test_glob_records_expected_listing_and_guards_untracked_bytes(self):
        manifest = self.prepare("glob", 1, "glob")
        row = manifest["worktrees"][0]
        self.assertEqual(row["expected_glob_sources"],
                         ["shared_eval_glob/added-00.txt", "shared_eval_glob/base.txt"])
        self.assertEqual(row["extra_targets"], [fixture.GLOB_TARGET])
        workspace = Path(row["workspace"])
        self.assertEqual(fixture.source_state(workspace), row["source_state"])
        (workspace / row["changed_file"]).write_text("changed untracked bytes\n", encoding="utf-8")
        self.assertNotEqual(fixture.source_state(workspace), row["source_state"])
        actual_tree = fixture.git(Path(manifest["private_repository"]), "rev-parse",
                                  "HEAD^{tree}").decode().strip()
        self.assertEqual(manifest["private_tree"], actual_tree)

    def test_auxiliary_rule_changes_private_tree_identity(self):
        plain = self.prepare("plain", 1, "identical")
        glob = self.prepare("glob", 1, "glob")
        self.assertEqual(plain["projection_archive_sha256"], glob["projection_archive_sha256"])
        self.assertNotEqual(plain["private_tree"], glob["private_tree"])

    def test_committed_change_outside_command_files_is_rejected(self):
        manifest = self.prepare("committed-mutation", 1, "identical")
        row = manifest["worktrees"][0]
        workspace = Path(row["workspace"])
        baseline.require_fixture_tree(workspace, manifest["private_tree"])
        changed = workspace / fixture.PACKAGE / "clone.go"
        changed.write_text(changed.read_text() + "// committed mutation\n", encoding="utf-8")
        fixture.git(workspace, "add", str(changed))
        fixture.git(workspace, "-c", "user.name=Harness test", "-c", "user.email=test@invalid",
                    "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "mutation")
        # A clean diff and unchanged command files alone miss this mutation.
        self.assertEqual(fixture.source_state(workspace), row["source_state"])
        self.assertEqual(fixture.sha256(workspace / fixture.COMMAND / "root.go"), row["source_sha256"])
        self.assertEqual(fixture.sha256(workspace / fixture.COMMAND / "BUILD.bazel"), row["build_sha256"])
        with self.assertRaisesRegex(RuntimeError, "committed tree changed"):
            baseline.require_fixture_tree(workspace, manifest["private_tree"])

    def test_ignored_untracked_input_is_guarded(self):
        (self.source / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
        fixture.git(self.source, "add", ".gitignore")
        self.commit("ignore")
        before = fixture.source_state(self.source)
        (self.source / "ignored.txt").write_text("first\n", encoding="utf-8")
        added = fixture.source_state(self.source)
        self.assertEqual(before["git_status"], added["git_status"])
        self.assertNotEqual(before, added)
        (self.source / "ignored.txt").write_text("second\n", encoding="utf-8")
        self.assertNotEqual(added, fixture.source_state(self.source))


class CacheInventoryTests(TemporaryFiles):
    def test_empty_and_populated_publications_are_distinct(self):
        directory = self.root / "leased-v2/publications-v4/namespace"
        directory.mkdir(parents=True)
        self.assertEqual(baseline.publication_inventory(self.root)["state"], "empty")
        (directory / "entry.manifest").write_bytes(b"abcd")
        measured = baseline.publication_inventory(self.root)
        self.assertEqual((measured["publication_count"], measured["publication_bytes"]), (1, 4))
        self.assertEqual(measured["state"], "populated")
        self.assertFalse(measured["truncated"])

    def test_truncated_zero_count_does_not_claim_empty_store(self):
        directory = self.root / "leased-v2/publications-v4/namespace"
        directory.mkdir(parents=True)
        (directory / "entry.manifest").write_bytes(b"abcd")
        self.assertEqual(baseline.publication_inventory(self.root, budget=1)["state"], "unknown")
        self.assertTrue(baseline.inventory(self.root, budget=1)["truncated"])


class AccountingTests(TemporaryFiles):
    def test_missing_required_cgroup_fails_closed(self):
        with patch.object(baseline, "cgroup_path", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "dedicated cgroup"):
                baseline.Sampler([], 0.01, require_cgroup=True)

    def test_successful_commands_do_not_override_failed_accounting(self):
        args = SimpleNamespace(require_cgroup=True, clients=1, sample_interval=0.01)
        sampler = SimpleNamespace(start=lambda: None, finish=lambda: {
            "engine_membership_verified": False, "samples": []})
        with patch.object(baseline, "Sampler", return_value=sampler), patch.object(
                baseline, "build", return_value={"exit_code": 0, "bep": {"finished": True}}):
            result = baseline.cohort(args, self.root, [{"index": 0}], "measured", {}, {})
        self.assertFalse(result["passed"])
        self.assertFalse(result["accounting_passed"])

    @unittest.skipUnless(sys.platform == "linux", "Linux /proc and cgroup path semantics")
    def test_all_daemons_must_belong_to_exact_scope_or_descendant(self):
        group = Path("/sys/fs/cgroup/harness-test.scope")
        bases = [self.root / "o0", self.root / "o1"]
        for base, pid in zip(bases, (101, 202)):
            (base / "server").mkdir(parents=True)
            (base / "server/server.pid.txt").write_text(str(pid), encoding="utf-8")
        original_read = Path.read_text
        for second_group, live in (("0::/harness-test.scope", True),
                                   ("0::/harness-test.scope/worker", True),
                                   ("0::/harness-test.scope-extra", True),
                                   ("0::/other.scope", True),
                                   ("0::/harness-test.scope", False)):
            with self.subTest(second_group=second_group, second_alive=live):
                def read(path, *args, **kwargs):
                    if str(path) == "/proc/101/cgroup":
                        return "0::/harness-test.scope"
                    if str(path) == "/proc/202/cgroup":
                        return second_group
                    return original_read(path, *args, **kwargs)

                def stat(pid):
                    return {"pid": pid, "start": pid, "ticks": 100} if pid == 101 or live else None

                with ExitStack() as stack:
                    stack.enter_context(patch.object(baseline, "cgroup_path", return_value=group))
                    stack.enter_context(patch.object(baseline, "cgroup_cpu", return_value=1000))
                    stack.enter_context(patch.object(baseline, "cgroup_memory_stat", return_value={}))
                    stack.enter_context(patch.object(baseline, "read_number", return_value=100))
                    stack.enter_context(patch.object(baseline, "proc_stat", side_effect=stat))
                    stack.enter_context(patch.object(baseline, "pss", return_value=128))
                    stack.enter_context(patch.object(Path, "is_file", return_value=True))
                    stack.enter_context(patch.object(Path, "read_text", new=read))
                    sampler = baseline.Sampler(bases, 0.01, require_cgroup=True)
                    sampler.start()
                    result = sampler.finish()
                expected = live and second_group in (
                    "0::/harness-test.scope", "0::/harness-test.scope/worker")
                self.assertEqual(result["engine_membership_verified"], expected)


class SerialPreparationTests(TemporaryFiles):
    def test_serial_preparation_stops_each_daemon_and_measured_cohort_keeps_ten(self):
        args = SimpleNamespace(require_cgroup=True, clients=10, sample_interval=0.01)
        rows = [{"index": index} for index in range(10)]
        events, sampled_counts = [], []

        def sampler(bases, interval, required):
            sampled_counts.append(len(bases))
            return SimpleNamespace(start=lambda: None, finish=lambda: {
                "engine_membership_verified": True, "engine_expected_count": len(bases),
                "engine_sampled_peak_pss_bytes": 100, "samples": []})

        def build(args, run, row, phase, env, oracles):
            events.append((phase, row["index"]))
            return {"index": row["index"], "exit_code": 0, "bep": {"finished": True}}

        def shutdown(args, run, rows, env, phase):
            events.extend((phase, row["index"]) for row in rows)
            return [{"exit_code": 0} for row in rows]

        with patch.object(baseline, "Sampler", side_effect=sampler), patch.object(
                baseline, "build", side_effect=build), patch.object(
                baseline, "shutdown", side_effect=shutdown), patch.object(
                baseline, "cgroup_path", return_value=Path("/fake.scope")), patch.object(
                baseline, "cgroup_cpu", side_effect=[1000000, 4000000]), patch.object(
                baseline, "read_number", return_value=500):
            prepared = baseline.serial_restart_preparation(args, self.root, rows, {}, {})
            measured = baseline.cohort(args, self.root, rows, "measured", {}, {})
        self.assertEqual(events[:20], [(phase, index) for index in range(10)
                                      for phase in ("preparation", "restart-shutdown")])
        self.assertEqual(sampled_counts, [1] * 10 + [10])
        self.assertTrue(prepared["passed"])
        self.assertTrue(measured["passed"])
        self.assertEqual(prepared["resources"]["scope_cpu_seconds"], 3)
        self.assertEqual(prepared["resources"]["scope_lifetime_peak_bytes"], 500)
        self.assertEqual(len(prepared["results"]), 10)
        self.assertEqual(len(prepared["shutdown"]), 10)
        self.assertEqual(len(list(self.root.glob("preparation-c*-resources.json"))), 10)
        self.assertTrue((self.root / "measured-resources.json").is_file())
        self.assertTrue((self.root / "serial-preparation.json").is_file())

    def test_failure_stops_preparation_and_still_shuts_down_current_daemon(self):
        args = SimpleNamespace(require_cgroup=True, clients=10, sample_interval=0.01)
        for name, build_result, shutdown_result in (
                ("build", {"passed": False, "accounting_passed": True, "results": []}, [{"exit_code": 0}]),
                ("shutdown", {"passed": True, "accounting_passed": True, "results": []}, [{"exit_code": 1}]),
                ("exception", RuntimeError("build raised"), [{"exit_code": 0}])):
            with self.subTest(failure=name):
                run = self.root / name
                run.mkdir()
                with patch.object(baseline, "cohort", side_effect=[build_result]) as build, patch.object(
                        baseline, "shutdown", return_value=shutdown_result) as shutdown, patch.object(
                        baseline, "cgroup_path", return_value=None):
                    result = baseline.serial_restart_preparation(
                        args, run, [{"index": 0}, {"index": 1}], {}, {})
                self.assertFalse(result["passed"])
                self.assertEqual(build.call_count, 1)
                self.assertEqual(shutdown.call_count, 1)
                self.assertEqual(shutdown.call_args.args[2], [{"index": 0}])

    def test_serial_preparation_rejected_for_other_cache_states(self):
        for state in ("cold", "warm"):
            argv = ["baseline.py", "--root", str(self.root), "--mode", "vanilla",
                    "--binary", str(self.root / "binary"), "--fresh-caches", "--run-id", "invalid",
                    "--cache-state", state, "--serial-restart-preparation"]
            with self.subTest(state=state), patch.object(sys, "argv", argv), patch.object(
                    sys, "platform", "linux"), patch("sys.stderr"), patch.object(
                    baseline, "cohort") as build:
                with self.assertRaises(SystemExit) as raised:
                    baseline.main()
                self.assertEqual(raised.exception.code, 2)
                build.assert_not_called()


class OracleRejectionTests(TemporaryFiles):
    def test_missing_configuration_oracle_rejected_before_any_build(self):
        workspace = self.root / "workspace"
        command = workspace / fixture.COMMAND
        command.mkdir(parents=True)
        (command / "root.go").write_bytes(b"source")
        (command / "BUILD.bazel").write_bytes(b"build")
        row = {"index": 0, "workspace": str(workspace), "variant": "base",
               "compilation_mode": "opt", "source_state": {},
               "source_sha256": fixture.sha256(command / "root.go"),
               "build_sha256": fixture.sha256(command / "BUILD.bazel")}
        manifest = {"schema_version": 2, "clients": 1, "scenario": "config", "worktrees": [row],
                    "source_revision": fixture.REVISION, "target": fixture.TARGET,
                    "projection_archive_sha256": "archive", "private_tree": "tree"}
        (self.root / "fixture").mkdir()
        fixture.save_new(self.root / "fixture/manifest.json", manifest)
        oracle_row = {**row, "compilation_mode": "fastbuild"}
        oracle = {"passed": True, "mode": "vanilla", "analysis_only": False,
                  "fixture": {**manifest, "worktrees": [oracle_row]},
                  "measured": {"results": [{**oracle_row, "exit_code": 0,
                      "behavior": {"passed": True}, "output": {"sha256": "output"}}]}}
        reference = self.root / "oracle.json"
        fixture.save_new(reference, oracle)
        binary = self.root / "binary"
        binary.write_bytes(b"never execute")
        argv = ["baseline.py", "--root", str(self.root), "--scenario", "config",
                "--mode", "skycache", "--binary", str(binary), "--repository-cache", str(self.root / "cache"),
                "--run-id", "missing-opt-oracle", "--oracle-report", str(reference)]
        with patch.object(sys, "argv", argv), patch.object(sys, "platform", "linux"), patch.object(
                fixture, "source_state", return_value={}), patch.object(
                baseline, "require_fixture_tree"), patch.object(baseline, "cohort") as build:
            with self.assertRaisesRegex(ValueError, "missing required variants"):
                baseline.main()
            build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
