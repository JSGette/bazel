"""Offline provenance/command checks only; no runtime, Bazel or generated arm."""
import copy
import hashlib
import unittest

import native_comparison
import prepare_native_n10 as prepare


def candidate():
    before = {"members_verified": 10, "archived_bytes": 123456, "tracked_content_sha256": "1" * 64,
              "override_paths": [prepare.TEST_PATH], "generated_extras_not_source_verified": True}
    after = {**before, "tracked_content_sha256": "2" * 64,
             "override_paths": sorted([prepare.TEST_PATH, *prepare.PRODUCT_FILES])}
    report = {"status": "build_and_focused_tests_passed_no_gate_verdict", "source_commit": prepare.n2.SOURCE_COMMIT,
        "source_tree": prepare.n2.SOURCE_TREE, "source_archive_sha256": prepare.BASE_ARCHIVE,
        "source_is_base_plus_qualification_test_and_cap10_patch": True, "admission_cap": 10, "base_admission_cap": 2,
        "root": str(prepare.Q), "build_state_root": str(prepare.S),
        "prior_report": {"path": str(prepare.Q2 / "report.json"), "sha256": prepare.PRIOR_REPORT},
        "scope_before": {"name": "/task/build.scope"}, "scope_after": {"name": "/task/build.scope"},
        "commands": [{"name": name, "returncode": 0} for name in ("patch-check", "patch-apply", "build", "tests", "shutdown")],
        "qualification_test_patch": {"path": prepare.TEST_PATH, "old_sha256": prepare.OLD_TEST,
            "new_sha256": prepare.NEW_TEST, "base_source_fields_exclude_this_patch": True},
        "cap10_patch": {"commit": prepare.PRODUCT_COMMIT, "parent": prepare.PRODUCT_PARENT, "commit_tree": prepare.PRODUCT_TREE,
            "sha256": prepare.PRODUCT_PATCH, "files": [{"path": name, **dict(zip(
                ("old_sha256", "new_sha256", "old_bytes", "new_bytes"), value))} for name, value in prepare.PRODUCT_FILES.items()]},
        "source_before": before, "source_after_patch": after, "source_final": copy.deepcopy(after),
        "candidate": {"destination": str(prepare.Q / "candidate/bazel-native-cap10"),
                      "sha256": prepare.CANDIDATE_SHA256, "bytes": prepare.CANDIDATE_BYTES},
        "product_binary_sha256_before": prepare.OLD_BINARY, "product_binary_sha256_after_build": prepare.CANDIDATE_SHA256,
        "product_binary_sha256_after_tests": prepare.CANDIDATE_SHA256,
        "helper_sha256": {"/helpers/" + name: "3" * 64 for name in
                          ("build_native_cap10.py", "native_source_build.py", "requalify_native_cap2.py")}}
    guard = {"status": "completed", "exit_code": 0, "scope": {"unit": "build.scope"},
             "final_scope_cleanup": {"verified": True, "present": False, "signalled": False}}
    return report, guard


def acceptance(binary):
    run_id = "native-acceptance-cap10-01"
    root = prepare.R / run_id
    unit, invocation = "shared-eval-guard-" + run_id + "-" + "a" * 32 + ".scope", "b" * 32
    command = ["python3", "-B", str(prepare.ACCEPTANCE_HARNESS), "--bazel", binary["path"], "--root", str(root),
        "--expected-engine-cap", "10", "--heap-mib", "7680", "--processors", "2", "--tmp-root", str(root / "tmp")]
    command_sha = prepare.sha(prepare.encoded(command))
    checks = sorted(prepare.ACCEPTANCE_CHECKS)
    names = ["a", "b", *("engine-%02d" % i for i in range(2, 10))]
    bases = {name: str(root / "state/shared_servers/v3/native-acceptance/engines" /
                      hashlib.md5(str(root / name).encode(), usedforsecurity=False).hexdigest()) for name in names}
    records = {
        "results.json": {"success": True, "run_completed": True, "cleanup_errors": [], "run_error": None, "missing_checks": [],
            "expected_engine_cap": 10, "heap_mib": 7680, "processors": 2, "binary_sha256": binary["sha256"],
            "harness_sha256": prepare.ACCEPTANCE_HARNESS_SHA, "checks": checks, "required_checks": checks,
            "temporary_root": str(root / "tmp"), "repository_cache": str(root / "repository-cache"),
            "startup": [binary["path"], "--output_user_root=" + str(root / "state"),
                        "--host_jvm_args=-Xmx7680m", "--host_jvm_args=-XX:ActiveProcessorCount=2"],
            "output_bases": bases, "daemon_identities": [[10, 100], [11, 200]], "server_pid": 10},
        "control.json": {"run_id": run_id, "status": "completed_no_gate_verdict", "launcher_exit_code": 0,
            "memory_max_mib": 10240, "cpu_quota_percent": 600, "command_sha256": command_sha},
        "guard/result.json": {"status": "completed", "exit_code": 0, "scope": {"unit": unit, "invocation_id": invocation},
            "final_scope_cleanup": {"verified": True, "present": False, "signalled": False}},
        "guard/policy.json": {"scope": {"unit": unit}, "memory_max_mib": 10240, "cpu_quota_percent": 600,
            "sample_interval_ms": 500, "command_sha256": command_sha, "argv": ["guard-launcher", *command]},
        "guard/scope-identity.json": {"Id": unit, "InvocationID": invocation, "ControlGroup": "/task/" + unit},
        "command.json": command,
    }
    hashes = {name: prepare.sha(prepare.encoded(value)) for name, value in records.items()}
    records["runtime.tar.gz.json"] = {"passed": True, "candidate_report_sha256": "c" * 64,
        "candidate_sha256": binary["sha256"], "harness_sha256": prepare.ACCEPTANCE_HARNESS_SHA,
        "final_scope_absent": True, "final_scope_unsignalled": True, "recorded_daemons_absent": True,
        "required_checks": checks, "completed_checks": checks,
        **{key: hashes[name] for key, name in (("guard_result_sha256", "guard/result.json"),
            ("guard_policy_sha256", "guard/policy.json"), ("scope_identity_sha256", "guard/scope-identity.json"),
            ("command_sha256", "command.json"))}}
    return records, hashes, run_id


class N10PreparationTests(unittest.TestCase):
    def setUp(self):
        self.report, self.guard = candidate()
        self.binary, _ = prepare.candidate_provenance(self.report, self.guard)

    def test_base_and_both_patches_remain_distinct_and_visible(self):
        original = copy.deepcopy(self.report)
        binary, proof = prepare.candidate_provenance(self.report, self.guard)
        self.assertEqual(binary["source_tree_sha256_kind"], "base-git-archive-tar-bytes")
        self.assertEqual(binary["source_tree_sha256"], prepare.BASE_ARCHIVE)
        self.assertEqual(binary["qualification_patch"], self.report["qualification_test_patch"])
        self.assertEqual(binary["cap10_patch"], self.report["cap10_patch"])
        self.assertNotEqual(binary["source_git_tree_sha1"], binary["cap10_patch"]["commit_tree"])
        self.assertEqual(proof["source_final"], self.report["source_after_patch"])
        self.assertEqual(self.report, original)

    def test_cap2_or_failed_or_unpatched_report_rejects(self):
        for changes in ({"admission_cap": 2}, {"status": "failed"},
                        {"source_is_base_plus_qualification_test_and_cap10_patch": False},
                        {"source_archive_sha256": "0" * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                prepare.candidate_provenance({**self.report, **changes}, self.guard)

    def test_failed_or_duplicate_build_commands_reject(self):
        for name in ("patch-check", "patch-apply", "build", "tests", "shutdown"):
            changed = copy.deepcopy(self.report)
            next(row for row in changed["commands"] if row["name"] == name)["returncode"] = 1
            with self.subTest(name=name), self.assertRaises(ValueError):
                prepare.candidate_provenance(changed, self.guard)
        self.report["commands"].append({"name": "tests", "returncode": 0})
        with self.assertRaises(ValueError):
            prepare.candidate_provenance(self.report, self.guard)

    def test_changed_product_or_inherited_patch_rejects(self):
        self.report["cap10_patch"]["files"][0]["new_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "four pinned files"):
            prepare.candidate_provenance(self.report, self.guard)
        self.report, _ = candidate()
        self.report["qualification_test_patch"]["new_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "test-only patch"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_source_final_drift_or_missing_override_rejects(self):
        self.report["source_final"]["tracked_content_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "override coverage"):
            prepare.candidate_provenance(self.report, self.guard)
        self.report, _ = candidate()
        self.report["source_before"]["override_paths"] = []
        with self.assertRaisesRegex(ValueError, "override coverage"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_old_or_changed_binary_and_nonabsent_guard_reject(self):
        self.report["product_binary_sha256_after_tests"] = prepare.OLD_BINARY
        with self.assertRaisesRegex(ValueError, "candidate identity"):
            prepare.candidate_provenance(self.report, self.guard)
        self.report, _ = candidate()
        self.guard["final_scope_cleanup"]["present"] = True
        with self.assertRaisesRegex(ValueError, "final external guard"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_complete_matching_cap10_acceptance_is_required(self):
        records, hashes, run_id = acceptance(self.binary)
        proof = prepare.validate_acceptance(records, hashes, "c" * 64, self.binary, run_id)
        self.assertEqual(proof["complete_checks"], 21)
        self.assertTrue(proof["final_scope_absent"])

    def test_acceptance_cannot_reuse_cap2_or_another_candidate(self):
        for field, value in (("expected_engine_cap", 2), ("binary_sha256", "0" * 64), ("heap_mib", 1536)):
            records, hashes, run_id = acceptance(self.binary)
            records["results.json"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                prepare.validate_acceptance(records, hashes, "c" * 64, self.binary, run_id)

    def test_acceptance_incomplete_checks_or_live_scope_reject(self):
        records, hashes, run_id = acceptance(self.binary)
        records["results.json"]["checks"] = records["results.json"]["checks"][:-1]
        with self.assertRaisesRegex(ValueError, "check set"):
            prepare.validate_acceptance(records, hashes, "c" * 64, self.binary, run_id)
        records, hashes, run_id = acceptance(self.binary)
        records["guard/result.json"]["final_scope_cleanup"].pop("present")
        with self.assertRaisesRegex(ValueError, "final external guard"):
            prepare.validate_acceptance(records, hashes, "c" * 64, self.binary, run_id)

    def test_acceptance_guard_command_report_and_output_bindings_reject(self):
        for mutate in (
            lambda r: r["runtime.tar.gz.json"].update(candidate_report_sha256="0" * 64),
            lambda r: r["runtime.tar.gz.json"].update(guard_result_sha256="0" * 64),
            lambda r: r["control.json"].update(command_sha256="0" * 64),
            lambda r: r["command.json"].append("--unexpected"),
            lambda r: r["guard/scope-identity.json"].update(InvocationID="0" * 32),
            lambda r: r["results.json"]["output_bases"].update(b=r["results.json"]["output_bases"]["a"]),
        ):
            records, hashes, run_id = acceptance(self.binary)
            mutate(records)
            with self.assertRaises(ValueError):
                prepare.validate_acceptance(records, hashes, "c" * 64, self.binary, run_id)

    def test_three_roles_have_fixed_n10_restart_protocol_and_no_hash_cycle(self):
        for role in ("reference", "private", "native"):
            config, command, stage = prepare.make_manifest(role, "native-n10-probe01", "/source/repository",
                self.binary, {"root": "/seed", "files": {}}, {}, "/mnt/c/evidence")
            self.assertEqual((config["clients"], config["state"], config["scenario"], config["sample_interval_seconds"]),
                             (10, "restart", "identical", 0.5))
            self.assertEqual(config["guard_command_sha256"], prepare.sha(command))
            self.assertNotIn("sha256", config["host_preflight"])
            self.assertEqual(config["host_preflight"]["path"], str(prepare.PurePosixPath(config["guard_evidence"]).parent / "host-budget-preflight.json"))
            self.assertEqual(stage["outer_guard_timeout_seconds"], 7200)
            paths = native_comparison.layout(config, [{"index": i, "workspace": "/w%d" % i} for i in range(10)])
            startup = native_comparison.startup(config, paths, 0)
            self.assertIn("--host_jvm_args=-XX:ActiveProcessorCount=2", startup)
            self.assertIn("--host_jvm_args=-Xmx%dm" % (7680 if role == "native" else 768), startup)
            self.assertEqual(any(flag.startswith("--output_base=") for flag in startup), role != "native")

    def test_reference_requires_ten_independent_input_rows(self):
        expected = {"source_revision": "base", "inputs": {"variant": "base"}}
        report = {"fixture": {"source_revision": "base", "clients": 10, "scenario": "identical",
                              "worktrees": [{"index": i, "variant": "base"} for i in range(10)]}}
        prepare.check_fixture(report, expected)
        report["fixture"]["clients"] = 2
        report["fixture"]["worktrees"] = report["fixture"]["worktrees"][:2]
        with self.assertRaises(ValueError):
            prepare.check_fixture(report, expected)


if __name__ == "__main__":
    unittest.main()
