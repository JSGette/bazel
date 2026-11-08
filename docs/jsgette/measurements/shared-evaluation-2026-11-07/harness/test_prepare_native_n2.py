"""Copied-report dictionaries only: no runtime, source scan, filesystem or build."""

import copy
import unittest

import prepare_native_n2 as prepare


class CandidateProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.root = str(prepare.R / "native-cap2-requalification-v1")
        self.state = str(prepare.R / "m1-native-build-v2")
        self.guard = {"status": "completed", "exit_code": 0, "scope": {"unit": "owned.scope"},
                      "final_scope_cleanup": {"verified": True, "signalled": False, "present": False}}
        self.report = {
            "status": "build_and_focused_tests_passed_no_gate_verdict", "root": self.root,
            "unchanged_admission_cap": 2, "source_commit": prepare.SOURCE_COMMIT,
            "source_tree": prepare.SOURCE_TREE, "source_archive_sha256": "a" * 64,
            "candidate": {"destination": self.root + "/candidate/bazel-native-cap2", "sha256": "b" * 64},
            "commands": [{"name": name, "returncode": 0} for name in ("build", "tests", "shutdown")],
            "scope_before": {"name": "/task/owned.scope"}, "scope_after": {"name": "/task/owned.scope"},
        }

    def qualify(self):
        self.report.update(
            source_is_base_plus_qualification_test_patch=True, build_state_root=self.state,
            prior_report={"path": self.state + "/report.json", "sha256": "c" * 64},
            qualification_test_patch={"path": "src/test/shell/integration/shared_server_test.sh",
                "patch_file": self.root + "/input-test.sh", "old_sha256": "d" * 64, "new_sha256": "e" * 64,
                "scope": "Only installation mismatch test idle timeout", "base_source_fields_exclude_this_patch": True},
            product_binary_sha256_before="b" * 64, product_binary_sha256_after="b" * 64,
            base_source_verification={"members_verified": 2, "archived_bytes": 42, "tracked_content_sha256": "1" * 64},
            qualified_source_verification={"members_verified": 2, "archived_bytes": 42, "tracked_content_sha256": "2" * 64},
            launcher_sha256="3" * 64, build_helper_sha256="4" * 64)

    def test_plain_build_preserves_original_source_pin_contract(self):
        binary, evidence = prepare.candidate_provenance(self.report, self.guard)
        self.assertIsNone(evidence)
        self.assertEqual(binary["source_tree_sha256_kind"], "git-archive-tar-bytes")
        self.assertNotIn("qualification_patch", binary)

    def test_requalification_preserves_patch_and_separates_base_archive(self):
        self.qualify()
        original = copy.deepcopy(self.report)
        binary, evidence = prepare.candidate_provenance(self.report, self.guard)
        self.assertEqual(binary["source_tree_sha256_kind"], "base-git-archive-tar-bytes")
        self.assertEqual(binary["source_tree_sha256"], original["source_archive_sha256"])
        self.assertEqual(binary["qualification_patch"], original["qualification_test_patch"])
        self.assertTrue(binary["product_binary_unchanged_by_qualification"])
        self.assertEqual(evidence["prior_report"], original["prior_report"])
        self.assertEqual(evidence["qualified_source_verification"], original["qualified_source_verification"])
        self.assertEqual(self.report, original)

    def test_patch_cannot_be_undeclared_or_missing(self):
        self.qualify()
        self.report.pop("source_is_base_plus_qualification_test_patch")
        with self.assertRaisesRegex(ValueError, "explicitly declared"):
            prepare.candidate_provenance(self.report, self.guard)
        self.report["source_is_base_plus_qualification_test_patch"] = True
        self.report.pop("qualification_test_patch")
        with self.assertRaisesRegex(ValueError, "explicitly declared"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_patch_cannot_claim_base_archive_includes_changed_test(self):
        self.qualify()
        self.report["qualification_test_patch"]["base_source_fields_exclude_this_patch"] = False
        with self.assertRaisesRegex(ValueError, "exclude itself"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_product_change_or_missing_unchanged_proof_rejects(self):
        self.qualify()
        for value in (None, "9" * 64):
            self.report["product_binary_sha256_after"] = value
            with self.assertRaisesRegex(ValueError, "unchanged-binary proof"):
                prepare.candidate_provenance(self.report, self.guard)

    def test_missing_or_different_verification_coverage_rejects(self):
        self.qualify()
        self.report["qualified_source_verification"]["members_verified"] = 3
        with self.assertRaisesRegex(ValueError, "coverage changed"):
            prepare.candidate_provenance(self.report, self.guard)
        self.report.pop("base_source_verification")
        with self.assertRaisesRegex(ValueError, "source verification missing"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_other_test_or_malformed_patch_digest_rejects(self):
        self.qualify()
        self.report["qualification_test_patch"]["new_sha256"] = "unknown"
        with self.assertRaisesRegex(ValueError, "test digest missing"):
            prepare.candidate_provenance(self.report, self.guard)
        self.report["qualification_test_patch"]["path"] = "src/main/java/Product.java"
        with self.assertRaisesRegex(ValueError, "identify the test"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_wrong_prior_report_or_unsuccessful_guard_rejects(self):
        self.qualify()
        self.report["prior_report"]["path"] = self.root + "/report.json"
        with self.assertRaisesRegex(ValueError, "lineage missing"):
            prepare.candidate_provenance(self.report, self.guard)
        self.guard["final_scope_cleanup"]["signalled"] = True
        with self.assertRaisesRegex(ValueError, "final external guard"):
            prepare.candidate_provenance(self.report, self.guard)

    def test_guard_requires_explicit_final_absence(self):
        for present in (True, None):
            self.guard["final_scope_cleanup"]["present"] = present
            with self.assertRaisesRegex(ValueError, "final external guard"):
                prepare.candidate_provenance(self.report, self.guard)
        self.guard["final_scope_cleanup"].pop("present")
        with self.assertRaisesRegex(ValueError, "final external guard"):
            prepare.candidate_provenance(self.report, self.guard)


if __name__ == "__main__":
    unittest.main()
