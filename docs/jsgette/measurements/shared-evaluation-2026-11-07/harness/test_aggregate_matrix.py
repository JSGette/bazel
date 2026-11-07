"""Tiny synthetic evidence checks; no Bazel or runtime access."""
import copy
import io
import json
from pathlib import Path
import tarfile
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

    def bep_archive(self, intervals, duplicate_uuid=False, missing_finish=False):
        path = self.root / "off-run.tar.gz"
        manifest, results = {"members": []}, []
        with tarfile.open(path, "w:gz") as archive:
            for index, (begin, end) in enumerate(intervals):
                workspace = "/fixture/w%02d" % index
                events = [{"started": {"command": "build", "uuid": "00000000-0000-0000-0000-%012d" % (0 if duplicate_uuid else index),
                                        "workspaceDirectory": workspace, "startTimeMillis": str(begin)}}]
                if not missing_finish:
                    events.append({"finished": {"overallSuccess": True, "exitCode": {"name": "SUCCESS"},
                                                 "finishTimeMillis": str(end)}})
                raw = ("\n".join(json.dumps(event) for event in events) + "\n").encode()
                name = "measured/c%02d/bep.jsonl" % index
                info = tarfile.TarInfo(name); info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
                manifest["members"].append({"path": name, "bytes": len(raw), "sha256": aggregate.digest(raw)})
                results.append({"index": index, "workspace": workspace})
        return path, manifest, results

    def fixture(self):
        _, bep_manifest, client_rows = self.bep_archive([(1000, 2000)])
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
                      "results": [{**client_rows[0], "bep": {"build_metrics": metrics}}]}}
        summary = {"report_sha256": "report", "performance_eligible": True, "ineligibility_reasons": [],
                   "cohort_wall_seconds": 4, "clients": [{"index": 0, "remote_analysis_cache_counters": {},
                       "skycache_counters": {}, "command": {"selected_flags": []}}]}
        (self.root / "off-summary.json").write_text(json.dumps({"summarizer_sha256": "summarizer", "runs": [summary]}))
        matrix = {"local_sha256": {"summarize.py": "summarizer"}, "fixtures": {"fixture": {"clients": 1, "scenario": "identical"}},
                  "binary": {"candidate": {"sha256": "binary"}}, "harness_sha256": report["harness_sha256"]}
        item = {"id": "off", "report_sha256": "report", "archive_sha256": "archive", "performance_eligible": True}
        declared = {"fixture": "fixture", "mode": "candidate-off", "state": "restart", "reference": False}
        archive = ({"report.json": report, "measured-resources.json": resources},
                   {"archive_sha256": "archive", "members": [{"path": "report.json", "sha256": "report"}] + bep_manifest["members"]})
        guard = ({"policy.json": caps, "result.json": {"status": "completed", "exit_code": 0,
                  "final_scope_cleanup": {"verified": True}, "scope": {"unit": "run.scope"}}}, {})
        return (item, declared, matrix, caps), [archive, guard]

    def test_actual_action_and_graph_counts_come_from_archived_bep(self):
        args, packed = self.fixture()
        with patch.object(aggregate, "packed", side_effect=packed):
            row = aggregate.inspect(self.root, *args)
        self.assertEqual([row["metrics"][k] for k in ("graph_nodes", "targets_configured", "action_cache_hits", "action_cache_misses", "internal_runners", "other_runners")], [6, 7, 23, 1, 1, 0])
        self.assertEqual(row["concurrency_proof"]["all_clients_overlap_seconds"], 1)

    def test_ten_client_overlap_records_exact_intersection_and_start_spread(self):
        path, manifest, results = self.bep_archive([(1000 + i, 2000 + i) for i in range(10)])
        proof = aggregate.concurrency_proof(path, manifest, 10, results)
        self.assertEqual(proof["maximum_start_time_millis"], 1009)
        self.assertEqual(proof["minimum_finish_time_millis"], 2000)
        self.assertEqual(proof["all_clients_overlap_seconds"], .991)
        self.assertEqual(proof["start_spread_seconds"], .009)
        self.assertEqual(len(proof["unique_build_ids"]), 10)

    def test_serial_commands_fail_even_when_all_ten_daemons_have_positive_pss(self):
        args, packed = self.fixture()
        _, manifest, results = self.bep_archive([(1000 + i * 2000, 2000 + i * 2000) for i in range(10)])
        report, resources = packed[0][0]["report.json"], packed[0][0]["measured-resources.json"]
        resources["engine_expected_count"] = 10
        resources["samples"][0]["engine_pids_sampled"] = 10
        report["measured"]["resources"] = {k: v for k, v in resources.items() if k != "samples"}
        report["measured"]["results"] = results
        report["fixture"]["clients"] = args[2]["fixtures"]["fixture"]["clients"] = 10
        packed[0][1]["members"] = packed[0][1]["members"][:1] + manifest["members"]
        with patch.object(aggregate, "packed", side_effect=packed), self.assertRaisesRegex(ValueError, "all-client overlap"):
            aggregate.inspect(self.root, *args)

    def test_duplicate_uuid_and_corrupt_bep_member_are_rejected(self):
        path, manifest, results = self.bep_archive([(1000, 2000), (1001, 2001)], duplicate_uuid=True)
        with self.assertRaisesRegex(ValueError, "Duplicate BEP build UUIDs"):
            aggregate.concurrency_proof(path, manifest, 2, results)
        path, manifest, results = self.bep_archive([(1000, 2000)])
        manifest["members"][0]["sha256"] = "wrong"
        with self.assertRaisesRegex(ValueError, "BEP member hash mismatch"):
            aggregate.concurrency_proof(path, manifest, 1, results)

    def test_missing_finish_and_nonpositive_single_client_interval_are_rejected(self):
        path, manifest, results = self.bep_archive([(1000, 2000)], missing_finish=True)
        with self.assertRaisesRegex(ValueError, "BEP start/finish"):
            aggregate.concurrency_proof(path, manifest, 1, results)
        path, manifest, results = self.bep_archive([(1000, 1000)])
        with self.assertRaisesRegex(ValueError, "positive interval"):
            aggregate.concurrency_proof(path, manifest, 1, results)

    def test_missing_member_is_rejected(self):
        path, manifest, results = self.bep_archive([(1000, 2000)])
        manifest["members"] = []
        with self.assertRaisesRegex(ValueError, "Missing or duplicate BEP member"):
            aggregate.concurrency_proof(path, manifest, 1, results)

    def test_failed_concurrency_proof_becomes_ineligible_evidence_error(self):
        raw = json.dumps({"rows": [{"id": "off"}]}).encode()
        (self.root / "matrix.json").write_bytes(raw)
        path = self.root / "matrix-progress.json"
        path.write_text(json.dumps({"manifest_sha256": aggregate.digest(raw), "status": "completed",
                                    "rows": [{"id": "off", "status": "checked_and_archived"}]}))
        with patch.object(aggregate, "inspect", side_effect=ValueError("Missing BEP concurrency proof")):
            result = aggregate.snapshot(path)
        self.assertEqual(result["rows"][0]["status"], "evidence_error")
        self.assertFalse(result["rows"][0]["eligible"])
        self.assertEqual(result["coverage"], "partial")

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

    def test_budget_preflight_requires_full_budget_not_only_guard_reserve(self):
        minimum = (10240 + 4096 + 512) * 2**20
        policy = {"memory_max_mib": 10240, "minimum_physical_bytes": (10240 + 3072 + 512) * 2**20,
                  "minimum_commit_bytes": minimum}
        sample = {"available_physical_bytes": minimum, "available_commit_bytes": minimum}
        evidence = {"status": "ready", "policy": policy, "samples": [sample, sample]}
        name = "off-host-budget-preflight.json"
        item = {"id": "off", "host_budget_preflight": name}
        for sufficient in (True, False):
            if not sufficient:
                evidence["policy"]["minimum_commit_bytes"] = 4 * 2**30
            raw = json.dumps(evidence).encode(); (self.root / name).write_bytes(raw)
            item["host_budget_preflight_sha256"] = aggregate.digest(raw)
            args = (self.root, item, {"require_budget_headroom": True}, {"memory_max_mib": 10240})
            if sufficient:
                self.assertEqual(aggregate.host_budget_evidence(*args)["final_samples"], [sample, sample])
            else:
                with self.assertRaisesRegex(ValueError, "thresholds differ"):
                    aggregate.host_budget_evidence(*args)

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
