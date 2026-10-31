"""Check real test execution and retain portable evidence for the pinned workload."""

from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import unquote, urlparse
import xml.etree.ElementTree as ET


def test_xml(data):
    root = ET.fromstring(data)
    cases = list(root.iter("testcase"))
    if not cases or any(list(root.iter(tag)) for tag in ("failure", "error")):
        raise ValueError("Test XML is empty or reports failures/errors")
    passed = [(item.get("classname", ""), item.get("name", ""))
              for item in cases if item.find("skipped") is None]
    if not passed:
        raise ValueError("All test cases were skipped")
    return {"passed_cases": len(passed), "skipped_cases": len(cases) - len(passed),
            "case_identity_sha256": hashlib.sha256(json.dumps(sorted(passed)).encode()).hexdigest()}


def inspect_events(events, targets, require_execution=False):
    finished = [event["finished"] for event in events if "finished" in event]
    if len(finished) != 1 or finished[0].get("exitCode", {}).get("code", 0) != 0:
        raise ValueError("Missing successful build completion")
    tests = {}
    for event in events:
        if "testSummary" not in event:
            continue
        label = event["id"]["testSummary"]["label"]
        summary = event["testSummary"]
        runs, cached = summary.get("totalRunCount", 0), summary.get("totalNumCached", 0)
        if (label in tests or summary.get("overallStatus") != "PASSED"
                or runs < 1 or cached < 0 or cached > runs
                or (require_execution and cached != 0)):
            raise ValueError(f"Missing, failed, duplicate or unexpectedly cached tests: {label}")
        tests[label] = {"runs": runs, "cached_runs": cached, "executed_runs": runs - cached}
    if set(tests) != set(targets):
        raise ValueError(f"Test target mismatch: expected {sorted(targets)}, got {sorted(tests)}")
    metrics = [event["buildMetrics"] for event in events if "buildMetrics" in event]
    if len(metrics) != 1:
        raise ValueError("Missing build metrics")
    return {"tests": tests, "metrics": metrics[0]}


def retain_tests(directory, targets, require_execution=False):
    events = [json.loads(line) for line in (directory / "bep.jsonl").read_text().splitlines()]
    result = inspect_events(events, targets, require_execution)
    xml_labels = set()
    retained = []
    for event in events:
        if "testResult" not in event:
            continue
        label = event["id"]["testResult"]["label"]
        if label not in result["tests"] or event["testResult"].get("status") != "PASSED":
            raise ValueError("Unexpected or unsuccessful test attempt")
        for output in event["testResult"].get("testActionOutput", []):
            if output.get("name") not in ("test.xml", "test.log"):
                continue
            uri = urlparse(output.get("uri", ""))
            if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
                raise ValueError("Expected local test artifact URI")
            source = Path(unquote(uri.path))
            target = directory / "test-artifacts" / str(len(retained)) / output["name"]
            target.parent.mkdir(parents=True)
            shutil.copy2(source, target)
            data = target.read_bytes()
            retained.append({"label": label, "file": str(target.relative_to(directory)),
                             "sha256": hashlib.sha256(data).hexdigest()})
            if output["name"] == "test.xml":
                result["tests"][label]["xml"] = test_xml(data)
                xml_labels.add(label)
    if xml_labels != set(targets):
        raise ValueError("Missing XML evidence for a requested test target")
    result["artifacts"] = retained
    return result


def profile_summary(path):
    with gzip.open(path, "rt") as stream:
        events = json.load(stream)["traceEvents"]
    categories, skyfunctions = Counter(), Counter()
    for event in events:
        if event.get("ph") != "X":
            continue
        categories[event.get("cat", "uncategorized")] += event.get("dur", 0)
        if event.get("cat") == "skyfunction":
            skyfunctions[event.get("name", "unknown")] += event.get("dur", 0)
    return {"category_thread_microseconds": dict(categories),
            "skyfunction_thread_microseconds": dict(skyfunctions),
            "scope": "summed traced spans; nested/parallel spans overlap, not wall time or exclusive CPU"}


def verify_run(root):
    summary = json.loads((root / "summary.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    names = {"prime-dependencies_and_tests-r0"}
    for n in manifest["configuration"]["concurrency"]:
        cases = ["coldgraph_hotdisk", "executed_tests", "warmup", "warm_diagnostic"]
        if manifest.get("matrix_version", 1) == 2:
            cases += ["mixed_targets", "full_target_restore"]
        names.update(f"n{n}-{case}-r0" for case in cases)
        names.update(f"n{n}-warm_latency-r{r}" for r in range(manifest["configuration"]["repetitions"]))
    if summary["failure"] or not summary["cohorts"] or not summary["shutdown"]:
        raise ValueError("Incomplete or failed run")
    if len(summary["cohorts"]) != len(names) or {c["name"] for c in summary["cohorts"]} != names:
        raise ValueError("Missing or duplicate planned cohorts")
    commands = 0
    identities = {}
    for cohort in summary["cohorts"]:
        records = cohort["commands"]
        if (not cohort["valid"] or len(records) != cohort["concurrency"]
                or len({r["server"]["pid"] for r in records}) != cohort["concurrency"]
                or min(r["end_monotonic_ns"] for r in records) <= max(r["start_monotonic_ns"] for r in records)):
            raise ValueError("Invalid cohort")
        for record in cohort["commands"]:
            directory = root / cohort["name"] / record["worker"]
            if record != json.loads((directory / "result.json").read_text()) or record["exit_code"] != 0:
                raise ValueError("Command record mismatch/failure")
            identity = record["server"]["pid"], record["server"]["starttime_ticks"]
            if identities.setdefault(record["worker"], identity) != identity:
                raise ValueError("Server restarted during a worktree's run")
            events = [json.loads(line) for line in (directory / "bep.jsonl").read_text().splitlines()]
            inspected = inspect_events(events, record["targets"], record["require_execution"])
            for label, test in inspected["tests"].items():
                for key, value in test.items():
                    if record["validation"]["tests"][label][key] != value:
                        raise ValueError("Test summary mismatch")
            xml_labels = set()
            for artifact in record["validation"]["artifacts"]:
                data = (directory / artifact["file"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
                    raise ValueError("Test artifact digest mismatch")
                if artifact["file"].endswith(".xml"):
                    xml_labels.add(artifact["label"])
                    if test_xml(data) != record["validation"]["tests"][artifact["label"]]["xml"]:
                        raise ValueError("Test XML mismatch")
            if xml_labels != set(record["targets"]):
                raise ValueError("Missing retained test XML")
            if record["diagnostic"]:
                profile_summary(directory / "profile.json.gz")
            commands += 1
    if len(summary["shutdown"]) != len(identities) or {s["worker"] for s in summary["shutdown"]} != set(identities):
        raise ValueError("Missing or duplicate shutdown evidence")
    for shutdown in summary["shutdown"]:
        if shutdown.get("exit_code") != 0 or shutdown.get("after") is not None or shutdown.get("unexpected_source_changes"):
            raise ValueError("Owned-server cleanup failed")
    return {"verified_commands": commands, "cohorts": len(summary["cohorts"]),
            "successful_shutdowns": len(summary["shutdown"])}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    print(json.dumps(verify_run(parser.parse_args().results), indent=2))
