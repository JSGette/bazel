"""Protect the known-failure runner from treating unrelated failures as evidence."""

import unittest
import xml.etree.ElementTree as ET

from run import ALL_CASES, CLASS, MARKER, RED_CASES, classify


def report(known_failure=True):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite")
    for name in sorted(ALL_CASES):
        case = ET.SubElement(suite, "testcase", name=name, classname=CLASS)
        if known_failure and name in RED_CASES:
            ET.SubElement(case, "failure", message=MARKER)
    return root


class ClassificationTest(unittest.TestCase):
    def test_exact_known_failure_requires_explicit_opt_in(self):
        self.assertEqual(classify(report(), 3, True), ("known_failure_reproduced", 0))
        self.assertEqual(classify(report(), 3, False), ("identity_defect", 1))

    def test_build_failure_cannot_reuse_stale_red_xml(self):
        self.assertEqual(classify(report(), 1, True), ("infrastructure_failure", 2))

    def test_unrelated_assertion_is_not_accepted(self):
        xml = report()
        xml.find(".//failure").set("message", "precondition failed")
        self.assertEqual(classify(xml, 3, True), ("unexpected_failure", 2))

    def test_extra_failure_is_not_accepted(self):
        xml = report()
        case = next(case for case in xml.findall(".//testcase") if case.get("name") not in RED_CASES)
        ET.SubElement(case, "failure", message=MARKER)
        self.assertEqual(classify(xml, 3, True), ("unexpected_failure", 2))

    def test_skipped_control_is_not_accepted(self):
        xml = report()
        ET.SubElement(xml.find(".//testcase"), "skipped")
        self.assertEqual(classify(xml, 3, True), ("incomplete_or_unexpected_test_set", 2))

    def test_missing_control_is_not_accepted(self):
        xml = report()
        suite = xml.find("testsuite")
        suite.remove(suite.find("testcase"))
        self.assertEqual(classify(xml, 3, True), ("incomplete_or_unexpected_test_set", 2))

    def test_runtime_error_is_not_accepted(self):
        xml = report()
        ET.SubElement(xml.find(".//testcase"), "error", message=MARKER)
        self.assertEqual(classify(xml, 3, True), ("unexpected_failure", 2))

    def test_fixed_behavior_does_not_silently_remain_expected_red(self):
        self.assertEqual(classify(report(False), 0, True), ("unexpected_conformance_pass", 1))
        self.assertEqual(classify(report(False), 0, False), ("conformant", 0))


if __name__ == "__main__":
    unittest.main()
