import json
from pathlib import Path
import tempfile
import unittest

from paired_baseline import digest
from summarize_costs import collect, histogram_bounds, interval_union, profile_spans, write_csv


class CostExtractionTest(unittest.TestCase):
    def test_csv_uses_git_stable_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "costs.csv"
            write_csv(output, ["phase", "value"], [["seal", 1]])
            self.assertEqual(output.read_bytes(), b"phase,value\nseal,1\n")

    def test_histogram_zero_defaults_and_weighted_bounds(self):
        self.assertEqual(histogram_bounds({"histogramBucket": [
            {"max": "1", "count": "3"}, {"min": "10", "max": "20", "count": "2"},
        ]}), (5, 20, 43))

    def test_reversed_histogram_is_rejected(self):
        with self.assertRaises(ValueError):
            histogram_bounds({"histogramBucket": [{"min": "3", "max": "2", "count": "1"}]})

    def test_union_removes_nested_and_parallel_overlap(self):
        self.assertEqual(interval_union([(10, 20), (0, 12), (2, 4), (30, 35), (35, 40)]), 30)
        self.assertEqual(interval_union([]), 0)

    def test_negative_span_is_rejected(self):
        with self.assertRaises(ValueError):
            interval_union([(3, 2)])

    def test_profile_keeps_sum_separate_from_union(self):
        events = [
            {"ph": "X", "cat": "test", "ts": 0, "dur": 10, "tid": 1},
            {"ph": "X", "cat": "test", "ts": 5, "dur": 10, "tid": 2},
            {"ph": "M", "cat": "test", "name": "thread_name"},
        ]
        self.assertEqual(profile_spans({"traceEvents": events}), [("category:test", 2, 20, 15)])

    def test_incomplete_or_failed_cohort_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for status in ("running", "failed", "passed"):
                (root / "summary.json").write_text(json.dumps({"status": status, "runs": []}))
                with self.assertRaisesRegex(ValueError, "complete passed cohort"):
                    collect(root)

    def test_unvalidated_run_and_missing_hashes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = {"label": "producer", "metrics": {"exit_code": 0, "timed_out": False}}
            (root / "summary.json").write_text(json.dumps({"status": "passed", "runs": [run]}))
            with self.assertRaisesRegex(ValueError, "successful validated runs"):
                collect(root)
            run["validation"] = {"passed": True}
            (root / "summary.json").write_text(json.dumps({"status": "passed", "runs": [run]}))
            with self.assertRaisesRegex(ValueError, "artifact hashes"):
                collect(root)

    def test_changed_raw_artifact_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = root / "runs/producer"
            data.mkdir(parents=True)
            hashes = {}
            for name in ("metrics.json", "build.bep.json", "build.profile.gz"):
                (data / name).write_bytes(b"original")
                hashes[name] = digest(data / name)
            run = {"label": "producer", "metrics": {"exit_code": 0, "timed_out": False},
                   "validation": {"passed": True}, "raw_artifacts": hashes}
            (root / "summary.json").write_text(json.dumps({"status": "passed", "runs": [run]}))
            (data / "metrics.json").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                collect(root)


if __name__ == "__main__":
    unittest.main()
