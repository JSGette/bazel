"""Offline command/policy/lifecycle tests. No Bazel, Linux scans or real subprocesses.

python -B -m unittest discover -s <harness> -p test_native_comparison.py
"""

import copy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import native_comparison as n
from m1_resources import EngineMapping, ProcessIdentity


def cgroup(cpu=100):
    return {"eligible": True, "directory_identity": {"device": 1, "inode": 2},
            "cpu.stat": {"usage_usec": cpu}, "memory.events": {"oom": 0, "oom_kill": 0}}


def canonical(expected):
    options = []
    for name, values in expected.items():
        for value in values if isinstance(values, list) else [values]:
            options.append({"optionName": name, "optionValue": value,
                            "combinedForm": "--%s=%s" % (name, value)})
    return [{"structuredCommandLine": {"commandLineLabel": "canonical", "sections": [
        {"sectionLabel": "command options", "optionList": {"option": options}}]}}]


def bep_events(row, pid=100, begin=1000, end=2000, build_id=None):
    return [{"started": {"command": "build", "uuid": build_id or "00000000-0000-0000-0000-%012d" % row["index"],
                         "workspaceDirectory": row["workspace"], "serverPid": str(pid), "startTimeMillis": str(begin)}},
            {"finished": {"overallSuccess": True, "exitCode": {"name": "SUCCESS", "code": 0}, "finishTimeMillis": str(end)}}]


class NativeComparisonTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parent
        self.temporary = tempfile.TemporaryDirectory(prefix=".native-comparison-test-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.task = Path(self.temporary.name).resolve()
        self.root = self.task / "new-arm"
        self.config = {"schema_version": 1, "task_root": str(self.task), "run_root": str(self.root),
            "hosting": "private", "clients": 2, "state": "warm", "scenario": "identical",
            "shared_name": "m1-test", "source_repository": str(self.task / "source"),
            "binary": {"path": str(self.task / "bazel"), "sha256": "a" * 64,
                       "source_commit": "b" * 40, "source_tree_sha256": "c" * 64},
            "repository_seed": {"root": str(self.task / "seed"), "files": {}},
            "guard_evidence": str(self.task / "guard"), "guard_command_sha256": "d" * 64,
            "launch_argv": ["python3", "runner.py", "--manifest", "manifest.json"],
            "sample_interval_seconds": 0.1, "timeout_seconds": 5, "history": dict(n.HISTORY),
            "oracle_report": {"path": str(self.task / "oracle.json"), "sha256": "e" * 64}}
        self.rows = [{"index": i, "workspace": str(self.root / "fixture" / ("w%02d" % i)),
                      "variant": "base", "compilation_mode": "fastbuild", "extra_targets": [],
                      "source_sha256": "s", "build_sha256": "b"} for i in range(2)]
        self.manifest = {"worktrees": self.rows, "source_revision": "rev", "private_tree": "tree",
                         "projection_archive_sha256": "archive", "target": n.fixture.TARGET}
        self.scope = {"membership": "/task.scope", "identity": {"Id": "task.scope", "InvocationID": "nonce"}}

    def arm(self):
        self.root.mkdir()
        return n.Arm(self.config, self.manifest, self.scope)

    def mappings(self, arm):
        return {str(row["index"]): EngineMapping(str(arm.paths["bases"][str(row["index"])]),
                arm.pid_file(row), ProcessIdentity(100 if self.config["hosting"] == "native" else 100 + row["index"], 100000))
                for row in self.rows}

    def test_fresh_root_history_and_reference_mode_are_enforced(self):
        self.assertEqual(n.validate_manifest(self.config), self.root)
        with self.assertRaisesRegex(ValueError, "Reference-only"):
            n.validate_manifest(self.config, True)
        del self.config["oracle_report"]
        self.assertEqual(n.validate_manifest(self.config, True), self.root)
        with self.assertRaisesRegex(ValueError, "independent"):
            n.validate_manifest(self.config)
        self.config["history"]["pool_run"] = 2
        with self.assertRaisesRegex(ValueError, "history"):
            n.validate_manifest(self.config, True)
        self.config["history"] = dict(n.HISTORY)
        self.root.mkdir()
        with self.assertRaisesRegex(ValueError, "new direct child"):
            n.validate_manifest(self.config, True)

    def test_launch_binding_preserves_python_B_and_exact_literal_arguments(self):
        script = self.task / "native_comparison.py"
        args = ["--manifest", str(self.task / "manifest.json"), "--reference-only"]
        for prefix in (["python3"], ["python3", "-B"]):
            argv = [*prefix, str(script), *args]
            n.validate_launch(argv, argv, script, args)
            with self.assertRaisesRegex(ValueError, "pinned literal"):
                n.validate_launch(argv, [*argv, "extra"], script, args)
        unsupported = ["python3", "-I", str(script), *args]
        with self.assertRaisesRegex(ValueError, "unsupported interpreter"):
            n.validate_launch(unsupported, unsupported, script, args)

    def test_runtime_preflight_is_read_once_and_observed_digest_is_recorded(self):
        path = self.task / "host-budget-preflight.json"
        self.config["host_preflight"] = {"path": str(path)}
        now = n.datetime(2026, 11, 8, 12, 0, 10, tzinfo=n.timezone.utc)
        preflight = {"status": "ready", "consecutive_ready_samples": 2,
                    "policy": {"minimum_physical_bytes": 13824 * 1024**2,
                               "minimum_commit_bytes": 14848 * 1024**2, "sample_interval_ms": 2000},
                    "samples": [{"utc": (now - timedelta(seconds=offset)).isoformat(),
                        "available_physical_bytes": 14000 * 1024**2,
                        "available_commit_bytes": 15000 * 1024**2} for offset in (3, 1)]}
        path.write_text(json.dumps(preflight), encoding="utf-8")
        reads, original = [], Path.read_bytes

        def counted(value):
            reads.append(value)
            return original(value)

        with patch.object(Path, "read_bytes", counted):
            evidence = n.read_host_preflight(self.config, now=now)
        self.assertEqual(reads, [path])
        self.assertEqual(evidence["host_preflight_sha256"], n.fixture.sha256(path))
        with self.assertRaisesRegex(ValueError, "stale"):
            n.read_host_preflight(self.config, now=now + timedelta(seconds=70))
        self.config["host_preflight"]["sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            n.read_host_preflight(self.config, now=now)
        self.config["host_preflight"] = {"path": str(self.task / "elsewhere.json")}
        with self.assertRaisesRegex(ValueError, "generated sibling"):
            n.read_host_preflight(self.config, now=now)

    def test_native_never_passes_output_base_and_startup_matches_all_engines(self):
        self.config["hosting"] = "native"
        for count in (1, 2, 10):
            self.config["clients"] = count
            paths = n.layout(self.config, self.rows)
            first = n.startup(self.config, paths, 0)
            self.assertEqual(first, n.startup(self.config, paths, 1))
            self.assertFalse(any(arg.startswith("--output_base") for arg in first))
            self.assertIn("--host_jvm_args=-Xmx%dm" % (768 * count), first)
            self.assertEqual(paths["disk"], paths["control"] / "action-cache")
            self.assertNotEqual(paths["bases"]["0"], paths["bases"]["1"])

    def test_private_matches_native_policy_but_has_private_contents_cache(self):
        paths = n.layout(self.config, self.rows)
        command = n.build_command(self.config, paths, self.rows[0], self.root / "logs")
        self.assertIn("--output_base=" + str(paths["bases"]["0"]), command)
        self.assertIn("--host_jvm_args=-Xmx768m", command)
        self.assertIn("--repo_contents_cache=" + str(paths["bases"]["0"] / "repo-contents-cache"), command)
        self.assertIn("--action_env=TMP=" + str(paths["tmp"]), command)
        self.assertFalse(any(arg.startswith("--profile=") for arg in command))
        self.assertNotIn("--nobuild", command)

    def test_effective_bep_policy_not_only_argv_is_required(self):
        expected = n.effective_policy(n.layout(self.config, self.rows), self.rows[0])
        events = canonical(expected)
        self.assertIn("jobs", n.audit_policy(events, expected))
        events[0]["structuredCommandLine"]["sections"][0]["optionList"]["option"][0]["optionValue"] = "8"
        with self.assertRaisesRegex(ValueError, "jobs"):
            n.audit_policy(events, expected)
        with self.assertRaisesRegex(ValueError, "canonical"):
            n.audit_policy([], expected)
        events = canonical(expected)
        events[0]["structuredCommandLine"]["sections"][0]["optionList"]["option"].append(
            {"optionName": "profile", "optionValue": "/tmp/profile"})
        with self.assertRaisesRegex(ValueError, "override"):
            n.audit_policy(events, expected)

    def test_output_behavior_digest_and_glob_checks_are_all_required(self):
        paths = n.layout(self.config, self.rows)
        row = {**self.rows[0], "expected_glob_sources": ["shared_eval_glob/base.txt"]}
        binary = paths["bases"]["0"] / "execroot/main/bazel-out/bin/normalize_cmd"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"fake executable, never executed")
        glob = binary.parent / "sources.manifest"
        glob.write_bytes(b"shared_eval_glob/base.txt\n")
        behavior = Mock(return_value={"passed": True})

        def attempt(name, *, output=binary, expected=None, glob_contents=None):
            directory = self.root / name
            directory.mkdir()
            if glob_contents is not None:
                glob.write_bytes(glob_contents.encode())
            events = canonical(n.effective_policy(paths, row)) + bep_events(row) + [
                {"namedSetOfFiles": {"files": [{"uri": "file:" + p.as_posix()} for p in (output, glob)]}}]
            (directory / "bep.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
            with patch.object(n.fixture, "verify_program", behavior):
                return n.verify_build(row, {"exit_code": 0, "timed_out": False}, directory, paths,
                    {"base|fastbuild": expected or n.fixture.sha256(binary)}, False,
                    EngineMapping(str(paths["bases"]["0"]), self.task / "pid", ProcessIdentity(100, 42)))

        result = attempt("verify-good")
        self.assertTrue(result["glob_oracle"]["passed"])
        self.assertTrue(result["digest_oracle"]["matches"])
        behavior.assert_called_once()
        with self.assertRaisesRegex(ValueError, "digest"):
            attempt("verify-digest-bad", expected="wrong")
        with self.assertRaisesRegex(ValueError, "Glob"):
            attempt("verify-glob-bad", glob_contents="shared_eval_glob/unexpected.txt\n")
        outside = self.task / "normalize_cmd"
        outside.write_bytes(b"wrong engine")
        with self.assertRaisesRegex(ValueError, "outside its engine"):
            attempt("verify-output-outside", output=outside)

    def guard(self):
        identity = {"unit": "owned.scope", "description": "shared-evaluation-owned:nonce"}
        policy = {"scope": identity, "memory_max_mib": 10240, "cpu_quota_percent": 600,
                  "minimum_free_physical_bytes": 3072 * 1024**2,
                  "minimum_free_commit_bytes": 4096 * 1024**2, "sample_interval_ms": 500,
                  "argv": ["guard", *self.config["launch_argv"]]}
        live = {"Id": "owned.scope", "Description": identity["description"],
                "InvocationID": "invocation", "ControlGroup": "/task/owned.scope", "ActiveState": "active"}
        limits = {"cpu.max": "600000 100000", "memory.max": str(10240 * 1024**2), "memory.swap.max": "0"}
        return policy, live, limits

    def test_guard_requires_nonce_invocation_membership_limits_and_literal_launch(self):
        policy, live, limits = self.guard()
        n.validate_guard(policy, live, live, live["ControlGroup"], limits, self.config["launch_argv"])
        changed = {**live, "InvocationID": "replacement"}
        with self.assertRaisesRegex(ValueError, "identity changed"):
            n.validate_guard(policy, live, changed, live["ControlGroup"], limits, self.config["launch_argv"])
        with self.assertRaisesRegex(ValueError, "exact owned"):
            n.validate_guard(policy, live, live, "/other", limits, self.config["launch_argv"])
        with self.assertRaisesRegex(ValueError, "limits"):
            n.validate_guard(policy, live, live, live["ControlGroup"], {**limits, "memory.swap.max": "max"}, self.config["launch_argv"])
        with self.assertRaisesRegex(ValueError, "launch"):
            n.validate_guard(policy, live, live, live["ControlGroup"], limits, ["different"])

    def reference(self):
        return {"passed": True, "reference_only": True, "performance_eligible": False,
                "hosting": "private", "run_root": str(self.task / "reference"), "scope": self.scope,
                "binary_sha256": self.config["binary"]["sha256"], "comparison_policy_sha256": "policy",
                "fixture": self.manifest, "measured": {"results": [
                    {"variant": "base", "compilation_mode": "fastbuild", "exit_code": 0,
                     "behavior": {"passed": True}, "policy_verified": True, "output": {"sha256": "output"}}]}}

    def test_reference_rejects_binary_policy_variant_and_same_pool_mismatch(self):
        reference = self.reference()
        self.assertEqual(n.validate_reference(reference, self.manifest, self.config, "policy"), {"base|fastbuild": "output"})
        for key, value in (("binary_sha256", "wrong"), ("comparison_policy_sha256", "wrong"),
                           ("run_root", str(self.root)), ("performance_eligible", True), ("reference_only", False)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                n.validate_reference({**reference, key: value}, self.manifest, self.config, "policy")
        missing = copy.deepcopy(self.manifest)
        missing["worktrees"][0]["variant"] = "source"
        with self.assertRaisesRegex(ValueError, "variant"):
            n.validate_reference(reference, missing, self.config, "policy")

    def test_reference_requires_successful_matching_final_external_guard(self):
        reference = self.reference()
        guard = {"status": "completed", "exit_code": 0,
                 "final_scope_cleanup": {"verified": True, "signalled": False},
                 "scope": {"unit": "task.scope", "invocation_id": "nonce"}}
        n.validate_reference_guard(guard, reference)
        with self.assertRaisesRegex(ValueError, "external host guard"):
            n.validate_reference_guard({**guard, "status": "infrastructure_abort"}, reference)
        for cleanup in ({}, {"verified": True, "signalled": True}, {"verified": False, "signalled": False}):
            with self.assertRaisesRegex(ValueError, "external host guard"):
                n.validate_reference_guard({**guard, "final_scope_cleanup": cleanup}, reference)

    def test_pinned_binary_mismatch_refuses_before_fixture_or_output_creation(self):
        binary = Path(self.config["binary"]["path"])
        binary.write_bytes(b"fake binary, never executed")
        del self.config["oracle_report"]
        with patch.object(n, "attest_scope", return_value=self.scope), patch.object(n.fixture, "prepare") as prepare:
            with self.assertRaisesRegex(ValueError, "Pinned candidate"):
                n.run(self.config, reference_only=True)
        prepare.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_preparation_exception_preserves_partial_report_and_prevents_pool_reuse(self):
        binary = Path(self.config["binary"]["path"])
        binary.write_bytes(b"fake binary, never executed")
        self.config["binary"]["sha256"] = n.fixture.sha256(binary)
        del self.config["oracle_report"]
        with patch.object(n, "attest_scope", return_value=self.scope), \
                patch.object(n.m1_resources, "read_cgroup_snapshot", side_effect=[cgroup(100), cgroup(200)]), \
                patch.object(n.fixture, "prepare", side_effect=ValueError("fixture rejected")):
            report = n.run(self.config, reference_only=True)
        self.assertFalse(report["passed"])
        self.assertFalse(report["performance_eligible"])
        self.assertIn("fixture rejected", report["error"])
        self.assertTrue((self.root / "report.json").exists())
        with self.assertRaisesRegex(ValueError, "new direct child"):
            n.validate_manifest(self.config, reference_only=True)

    def test_seed_rejects_traversal_and_hash_mismatch(self):
        source = self.task / "seed"
        source.mkdir()
        (source / "object").write_bytes(b"seed")
        target = self.task / "cache"
        n.seed_repository({"root": str(source), "files": {"object": n.fixture.sha256(source / "object")}}, target)
        self.assertEqual((target / "object").read_bytes(), b"seed")
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            n.seed_repository({"root": str(source), "files": {"../escape": "bad"}}, self.task / "bad-cache")
        with self.assertRaisesRegex(ValueError, "mismatch"):
            n.seed_repository({"root": str(source), "files": {"object": "bad"}}, self.task / "bad-hash")

    def test_process_cpu_deltas_require_fixed_identities_and_known_endpoints(self):
        def sample(ticks, start=10, eligible=True):
            return {"eligible": eligible, "processes": [{"pid": 100, "start_ticks": start, "cpu_ticks": ticks}],
                    "clock_ticks_per_second": 100}
        self.assertEqual(n.cpu_delta(sample(200), sample(250), "warm"), 0.5)
        self.assertEqual(n.cpu_delta(sample(0, eligible=False), sample(250), "restart"), 2.5)
        for end in (sample(190), sample(250, start=11), sample(250, eligible=False)):
            with self.assertRaises(ValueError):
                n.cpu_delta(sample(200), end, "warm")

    def test_scope_counter_reset_replacement_and_oom_reject(self):
        self.assertEqual(n.scope_delta(cgroup(100), cgroup(250)), 0.00015)
        with self.assertRaisesRegex(ValueError, "reset"):
            n.scope_delta(cgroup(100), cgroup(99))
        changed = cgroup(200)
        changed["directory_identity"]["inode"] = 3
        with self.assertRaisesRegex(ValueError, "changed"):
            n.scope_delta(cgroup(), changed)
        changed = cgroup(200)
        changed["memory.events"]["oom"] = 1
        with self.assertRaisesRegex(ValueError, "OOM"):
            n.scope_delta(cgroup(), changed)

    def test_restart_preparation_orders_build_identity_shutdown_per_engine(self):
        self.config["state"] = "restart"
        arm = self.arm()
        events = []
        arm.command = lambda argv, *args: (events.append(("build", argv[argv.index("build") + 1])) or {"exit_code": 0, "timed_out": False})
        arm.discover = lambda rows: {}
        arm.identity = lambda row, phase: events.append(("identity", row["index"]))
        arm.shutdown = lambda rows, phase: events.append(("shutdown", rows[0]["index"]))
        with patch.object(n, "verify_build", side_effect=lambda row, result, *args: result):
            arm.prepare({})
        self.assertEqual([kind for kind, _ in events], ["build", "identity", "shutdown"] * 2)

    def fake_measurement(self, *, exit_code=0, restart=False, blocked_sample=False):
        if restart:
            self.config["state"] = "restart"
        arm = self.arm()
        mappings = self.mappings(arm)
        children, order = [], []
        release_exits, endpoint_frozen = n.threading.Event(), n.threading.Event()
        samplers = []
        arm.verified = {} if restart else dict(mappings)
        arm.admitted = set() if restart else set(mappings)
        arm.discover = lambda rows: ({str(r["index"]): mappings[str(r["index"])] for r in rows}
                                    if children or not restart else {})

        class FakeSampler:
            def __init__(self, *args, **kwargs):
                self.counter = 0
                samplers.append(self)

            def cgroup_snapshot(self):
                self.counter += 100
                return cgroup(self.counter)

            def sample(self, found):
                if blocked_sample and children and not endpoint_frozen.is_set():
                    release_exits.set()
                    if not endpoint_frozen.wait(timeout=2):
                        raise AssertionError("PSS is blocking the independent build-end scope read")
                self.counter += 1
                unique = {m.identity for m in found.values()}
                eligible = len(found) == 2
                return {"eligible": eligible, "identity_continuity_valid": True,
                    "membership_continuity_valid": True, "cgroup": cgroup(self.counter),
                    "clock_ticks_per_second": 100, "pss_bytes": 2000 if eligible else None,
                    "sample_thread_cpu_ns": 1, "processes": [
                        {"pid": ident.pid, "start_ticks": ident.start_ticks, "cpu_ticks": self.counter}
                        for ident in unique], "engines": {
                        str(i): {"errors": {} if str(i) in found else {"mapping": "MissingEngine"}} for i in range(2)}}

        class FakeProcess:
            def __init__(self, *args, **kwargs):
                self.calls, self.returncode = 0, None
                children.append(self)
                order.append("launch")

            def poll(self):
                self.calls += 1
                if self.calls >= 2:
                    self.returncode = exit_code
                return self.returncode

            def terminate(self):
                self.returncode = -15

            def kill(self):
                self.returncode = -9

            def wait(self, **kwargs):
                if blocked_sample and not release_exits.wait(timeout=2):
                    raise AssertionError("Periodic PSS sample did not release fake clients")
                self.returncode = exit_code
                return self.returncode

        def identity(row, phase):
            self.assertTrue((self.root / "measured/build-boundary.json").exists())
            self.assertTrue(all(child.returncode is not None for child in children))
            order.append("identity")
            mapping = mappings[str(row["index"])]
            arm.verified[str(row["index"])] = mapping
            return mapping

        def verify(row, result, directory, *args):
            self.assertTrue((self.root / "measured/build-boundary.json").exists())
            self.assertEqual(order.count("identity"), 2)
            order.append("verify")
            return {**result, "index": row["index"], "bep_identity": n.bep_identity(
                bep_events(row, mappings[str(row["index"])].identity.pid), row, mappings[str(row["index"])])}

        def independent_scope(**kwargs):
            value = samplers[0].cgroup_snapshot()
            endpoint_frozen.set()
            return value

        arm.identity = identity
        patches = [patch.object(n, "ResourceSampler", FakeSampler), patch.object(n.subprocess, "Popen", FakeProcess),
                   patch.object(n, "verify_build", side_effect=verify), patch.object(n.time, "sleep", lambda delay: None),
                   patch.object(n.m1_resources, "read_cgroup_snapshot", side_effect=independent_scope),
                   patch.object(n.time, "CLOCK_BOOTTIME", 7, create=True),
                   patch.object(n.time, "clock_gettime", lambda kind: 1000.0, create=True)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        return arm, order

    def test_verification_only_after_all_clients_exit_and_build_boundary_is_saved(self):
        arm, order = self.fake_measurement()
        result = arm.measured({})
        self.assertTrue(result["passed"])
        self.assertEqual(order, ["launch", "launch", "identity", "identity", "verify", "verify"])
        self.assertTrue(result["engine_mappings_verified_after_window"])
        self.assertGreater(result["end_to_end_scope_cpu_seconds"], result["build_scope_cpu_seconds"])
        self.assertTrue((self.root / "measured/partial-accounting.json").exists())

    def test_restart_does_not_issue_info_before_measured_builds(self):
        arm, order = self.fake_measurement(restart=True)
        result = arm.measured({})
        self.assertTrue(result["passed"])
        self.assertFalse(result["initial"]["eligible"])
        self.assertEqual(order[:2], ["launch", "launch"])
        self.assertGreater(result["daemon_cpu_seconds"], 0)

    def test_blocking_pss_does_not_delay_waiter_exit_or_build_end_scope_read(self):
        arm, order = self.fake_measurement(blocked_sample=True)
        result = arm.measured({})
        self.assertTrue(result["passed"])
        self.assertTrue(result["bep_concurrency"]["verified"])
        labels = [read["label"] for read in result["scope_reads"]]
        self.assertEqual(labels, ["build-start", "build-end", "end-to-end"])

    def test_bep_binds_workspace_pid_uuid_and_successful_positive_interval(self):
        arm = self.arm()
        mapping = self.mappings(arm)["0"]
        events = bep_events(self.rows[0], pid=mapping.identity.pid)
        good = n.bep_identity(events, self.rows[0], mapping)
        self.assertEqual(good["server_pid"], mapping.identity.pid)
        for field, value in (("workspaceDirectory", "wrong"), ("serverPid", "999"),
                             ("uuid", "bad"), ("startTimeMillis", "3000")):
            bad = copy.deepcopy(events)
            bad[0]["started"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                n.bep_identity(bad, self.rows[0], mapping)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            n.bep_identity(events + events[:1], self.rows[0], mapping)
        bad = copy.deepcopy(events)
        bad[1]["finished"]["overallSuccess"] = False
        with self.assertRaisesRegex(ValueError, "successful build"):
            n.bep_identity(bad, self.rows[0], mapping)

    def test_cohort_rejects_duplicate_uuids_and_nonoverlap_but_allows_one_native_pid(self):
        self.config["hosting"] = "native"
        arm = self.arm()
        mappings = self.mappings(arm)
        results = [{"bep_identity": n.bep_identity(bep_events(row), row, mappings[str(row["index"])])}
                   for row in self.rows]
        self.assertTrue(n.cohort_bep_proof(results, self.rows)["verified"])
        duplicate = copy.deepcopy(results)
        duplicate[1]["bep_identity"]["build_uuid"] = duplicate[0]["bep_identity"]["build_uuid"]
        with self.assertRaisesRegex(ValueError, "Duplicate BEP"):
            n.cohort_bep_proof(duplicate, self.rows)
        separate = copy.deepcopy(results)
        separate[1]["bep_identity"].update(start_time_millis=2000, finish_time_millis=3000)
        with self.assertRaisesRegex(ValueError, "overlap"):
            n.cohort_bep_proof(separate, self.rows)

    def test_failed_build_keeps_boundary_and_partial_evidence_without_info_or_verification(self):
        arm, order = self.fake_measurement(exit_code=7)
        with self.assertRaisesRegex(ValueError, "Measured build failed"):
            arm.measured({})
        self.assertEqual(order, ["launch", "launch"])
        partial = json.loads((self.root / "measured/partial-accounting.json").read_text())
        self.assertTrue(partial["build_endpoint_captured"])
        self.assertTrue(all(row["exit_code"] == 7 for row in partial["client_results"]))

    def test_shutdown_never_launches_against_stopped_or_unadmitted_engine(self):
        arm = self.arm()
        arm.command = Mock(side_effect=AssertionError("must not launch"))
        arm.discover = lambda rows: {}
        self.assertEqual(arm.shutdown(arm.rows, "stopped"), [])
        arm.discover = lambda rows: self.mappings(arm)
        with self.assertRaisesRegex(ValueError, "still alive"):
            with patch.object(n.time, "monotonic", side_effect=[0, 11]):
                arm.shutdown(arm.rows, "unadmitted")
        arm.command.assert_not_called()

    def test_native_discovery_deduplicates_pid_and_stat_reads(self):
        self.config["hosting"] = "native"
        arm = self.arm()
        pid_file = arm.pid_file(self.rows[0])
        pid_file.parent.mkdir(parents=True)
        pid_file.write_text("100\n", encoding="utf-8")
        reads, original = [], Path.read_text

        def counted(path, *args, **kwargs):
            reads.append(path)
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", counted), patch.object(n, "read_process_identity", return_value=ProcessIdentity(100, 42)) as stat:
            mappings = arm.discover(self.rows)
        self.assertEqual(len(mappings), 2)
        self.assertEqual(reads.count(pid_file), 1)
        stat.assert_called_once_with(100)

    def test_malformed_pid_file_is_not_cold_start_pending_or_absent(self):
        arm = self.arm()
        pid_file = arm.pid_file(self.rows[0])
        pid_file.parent.mkdir(parents=True)
        for text in ("broken", "0", "-1"):
            with self.subTest(pid=text):
                pid_file.write_text(text, encoding="utf-8")
                self.assertEqual(arm.discover(self.rows), {})
                self.assertEqual(arm.discovery_errors, {"0": "ValueError"})
                with self.assertRaisesRegex(ValueError, "restart/cleanup"):
                    arm.require_absent(self.rows)

    def test_missing_engine_and_native_multiple_pids_are_rejected(self):
        self.config["hosting"] = "native"
        arm = self.arm()
        mappings = self.mappings(arm)
        arm.check_process_count(mappings)
        with self.assertRaisesRegex(ValueError, "every engine"):
            arm.check_process_count({"0": mappings["0"]})
        mappings["1"] = EngineMapping(mappings["1"].output_base, mappings["1"].pid_file, ProcessIdentity(101, 100000))
        with self.assertRaisesRegex(ValueError, "daemon count"):
            arm.check_process_count(mappings)


if __name__ == "__main__":
    unittest.main()
