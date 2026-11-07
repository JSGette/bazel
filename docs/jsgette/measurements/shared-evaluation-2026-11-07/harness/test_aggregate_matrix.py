"""Tiny synthetic evidence checks; no Bazel or runtime access."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import aggregate_matrix as aggregate


class AggregateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="aggregate-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self):
        caps = {"memory_max_mib": 10240, "cpu_quota_percent": 600}
        resources = {"cgroup": "/sys/fs/cgroup/run.scope", "cgroup_accounted": True,
                     "engine_membership_verified": True, "engine_expected_count": 1,
                     "scope_cpu_seconds": 3, "engine_observed_cpu_seconds": 2,
                     "engine_sampled_peak_pss_bytes": 100,
                     "samples": [{"engine_pids_sampled": 1, "engine_pss_bytes": 100}]}
        metrics = {"buildGraphMetrics": {"postInvocationSkyframeNodeCount": "6"},
                   "targetMetrics": {"targetsConfigured": "7"},
                   "actionSummary": {"actionsExecuted": "1", "actionCacheStatistics": {"hits": 23, "misses": 1},
                                     "runnerCount": [{"name": "total", "count": 1}, {"name": "internal", "count": 1}]}}
        report = {"mode": "candidate-off", "cache_state": "restart", "binary_sha256": "binary",
                  "harness_sha256": {"baseline.py": "harness"}, "fixture": {"clients": 1, "scenario": "identical"},
                  "passed": True, "measured": {"passed": True, "accounting_passed": True,
                      "resources": {k: v for k, v in resources.items() if k != "samples"},
                      "results": [{"bep": {"build_metrics": metrics}}]}}
        summary = {"report_sha256": "report", "performance_eligible": True, "ineligibility_reasons": [],
                   "cohort_wall_seconds": 4, "clients": [{"index": 0, "remote_analysis_cache_counters": {},
                       "skycache_counters": {}, "command": {"selected_flags": []}}]}
        (self.root / "off-summary.json").write_text(json.dumps({"summarizer_sha256": "summarizer", "runs": [summary]}))
        matrix = {"local_sha256": {"summarize.py": "summarizer"}, "fixtures": {"fixture": {"clients": 1, "scenario": "identical"}},
                  "binary": {"candidate": {"sha256": "binary"}}, "harness_sha256": report["harness_sha256"]}
        item = {"id": "off", "report_sha256": "report", "archive_sha256": "archive", "performance_eligible": True}
        declared = {"fixture": "fixture", "mode": "candidate-off", "state": "restart", "reference": False}
        archive = ({"report.json": report, "measured-resources.json": resources},
                   {"archive_sha256": "archive", "members": [{"path": "report.json", "sha256": "report"}]})
        guard = ({"policy.json": caps, "result.json": {"status": "completed", "exit_code": 0,
                  "final_scope_cleanup": {"verified": True}, "scope": {"unit": "run.scope"}}}, {})
        return (item, declared, matrix, caps), [archive, guard]

    def test_actual_action_and_graph_counts_come_from_archived_bep(self):
        args, packed = self.fixture()
        with patch.object(aggregate, "packed", side_effect=packed):
            row = aggregate.inspect(self.root, *args)
        self.assertEqual([row["metrics"][k] for k in ("graph_nodes", "targets_configured", "action_cache_hits", "action_cache_misses", "internal_runners", "other_runners")], [6, 7, 23, 1, 1, 0])

    def test_unrelated_successful_guard_cannot_attest_report(self):
        args, packed = self.fixture()
        packed[1][0]["result.json"]["scope"]["unit"] = "other.scope"
        with patch.object(aggregate, "packed", side_effect=packed), self.assertRaisesRegex(ValueError, "scope mismatch"):
            aggregate.inspect(self.root, *args)

    def test_partial_or_missing_pss_does_not_pass(self):
        args, packed = self.fixture()
        packed[0][0]["measured-resources.json"]["samples"][0]["engine_pids_sampled"] = 0
        with patch.object(aggregate, "packed", side_effect=packed), self.assertRaisesRegex(ValueError, "complete positive"):
            aggregate.inspect(self.root, *args)

    def test_tampered_archive_is_rejected(self):
        path = self.root / "guard.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("result.json", "{}")
        Path(str(path) + ".json").write_text(json.dumps({"archive_sha256": "wrong", "members": []}))
        with self.assertRaisesRegex(ValueError, "Archive hash mismatch"):
            aggregate.packed(path, ["result.json"])

    def test_partial_snapshot_excludes_running_row_and_cross_binary_effect(self):
        matrix = {"rows": [{"id": "reference", "reference": True}, {"id": "off", "oracle": "reference"}, {"id": "on", "oracle": "reference"}, {"id": "running"}]}
        raw = json.dumps(matrix).encode(); (self.root / "matrix.json").write_bytes(raw)
        progress = {"manifest_sha256": aggregate.digest(raw), "status": "running",
                    "rows": [{"id": name, "status": "checked_and_archived"} for name in ("reference", "off", "on")]
                            + [{"id": "running", "status": "running"}]}
        path = self.root / "matrix-progress.json"; path.write_text(json.dumps(progress))
        off = {"id": "off", "status": "verified", "eligible": True, "mode": "candidate-off", "clients": 1,
               "scenario": "identical", "state": "restart", "binary_sha256": "first", "source": {}, "protocol": {"oracle_report": {"sha256": "reference"}}}
        on = {**copy.deepcopy(off), "id": "on", "mode": "skycache", "binary_sha256": "different"}
        reference = {**copy.deepcopy(off), "id": "reference", "reference": True, "eligible": False, "report_sha256": "reference"}
        with patch.object(aggregate, "inspect", side_effect=[reference, off, on]) as inspect:
            result = aggregate.snapshot(path)
        self.assertEqual(inspect.call_count, 3)
        self.assertEqual(result["coverage"], "partial")
        self.assertEqual(result["candidate_on_off"], [])
        self.assertEqual(result["rows"][-1]["status"], "running")
        self.assertEqual(result["milestone_verdict"], "not_assessed")


if __name__ == "__main__":
    unittest.main()
