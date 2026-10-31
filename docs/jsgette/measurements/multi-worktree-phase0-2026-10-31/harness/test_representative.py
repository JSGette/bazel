"""Failure-sensitive checks for real-test evidence, independent of Bazel itself."""

import copy
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from representative import wait_with_timeout
from representative_results import inspect_events, retain_tests, test_xml as inspect_xml

TARGET = "//pkg/util/strings:strings_test"


def events():
    return [{"finished": {"exitCode": {"code": 0}}},
            {"id": {"testSummary": {"label": TARGET}},
             "testSummary": {"overallStatus": "PASSED", "totalRunCount": 1}},
            {"buildMetrics": {"targetMetrics": {"targetsConfigured": 100}}}]


class RepresentativeTests(unittest.TestCase):
    def test_watchdog_preserves_success_and_nonzero_exit_status(self):
        for code in (0, 42):
            process = subprocess.Popen([sys.executable, "-c", f"raise SystemExit({code})"])
            self.assertEqual(wait_with_timeout(process, 10), (code, False))

    def test_watchdog_terminates_hung_client_and_records_timeout(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        code, timed_out = wait_with_timeout(process, 0.1)
        self.assertTrue(timed_out)
        self.assertNotEqual(code, 0)
        self.assertIsNotNone(process.poll())

    def test_zero_exit_with_no_tests_or_partial_target_set_is_rejected(self):
        for data, requested in ((events()[:1], [TARGET]),
                                (events(), [TARGET, "//pkg/metrics:metrics_test"])):
            with self.assertRaises(ValueError):
                inspect_events(data, requested)

    def test_all_nonzero_exits_including_test_failure_are_rejected(self):
        for code in (1, 2, 3, 4):
            data = events()
            data[0]["finished"]["exitCode"]["code"] = code
            with self.assertRaises(ValueError):
                inspect_events(data, [TARGET])

    def test_cached_pass_cannot_satisfy_execution_requirement(self):
        data = events()
        data[1]["testSummary"]["totalNumCached"] = 1
        self.assertEqual(inspect_events(data, [TARGET])["tests"][TARGET]["executed_runs"], 0)
        with self.assertRaises(ValueError):
            inspect_events(data, [TARGET], require_execution=True)

    def test_failed_zero_run_and_duplicate_summaries_are_rejected(self):
        for field, value in (("overallStatus", "FAILED"), ("totalRunCount", 0), ("totalNumCached", 2)):
            data = events()
            data[1]["testSummary"][field] = value
            with self.assertRaises(ValueError):
                inspect_events(data, [TARGET])
        data = events()
        data.append(copy.deepcopy(data[1]))
        with self.assertRaises(ValueError):
            inspect_events(data, [TARGET])

    def test_xml_requires_executed_cases_and_no_failures(self):
        for data in (b"<testsuite/>", b"<testsuite><testcase><skipped/></testcase></testsuite>",
                     b"<testsuite><testcase><failure/></testcase></testsuite>",
                     b"<testsuite><testcase><error/></testcase></testsuite>"):
            with self.assertRaises(ValueError):
                inspect_xml(data)

    def test_xml_identity_ignores_declared_incidental_time_but_preserves_names(self):
        first = inspect_xml(b'<testsuite time="1"><testcase name="A" classname="p" time="1"/></testsuite>')
        second = inspect_xml(b'<testsuite time="2"><testcase name="A" classname="p" time="2"/></testsuite>')
        changed = inspect_xml(b'<testsuite><testcase name="B" classname="p"/></testsuite>')
        self.assertEqual(first, second)
        self.assertNotEqual(first["case_identity_sha256"], changed["case_identity_sha256"])

    def test_evidence_is_copied_before_output_base_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "original.xml"
            source.write_text('<testsuite><testcase name="A"/></testsuite>')
            directory = root / "result"
            directory.mkdir()
            data = events() + [{"id": {"testResult": {"label": TARGET}},
                                "testResult": {"status": "PASSED", "testActionOutput": [
                                    {"name": "test.xml", "uri": source.as_uri()}]}}]
            (directory / "bep.jsonl").write_text("\n".join(json.dumps(e) for e in data))
            result = retain_tests(directory, [TARGET], require_execution=True)
            source.unlink()
            retained = directory / result["artifacts"][0]["file"]
            self.assertEqual(inspect_xml(retained.read_bytes())["passed_cases"], 1)

    def test_success_summary_without_xml_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bep.jsonl").write_text("\n".join(json.dumps(e) for e in events()))
            with self.assertRaises(ValueError):
                retain_tests(root, [TARGET])


if __name__ == "__main__":
    unittest.main()
