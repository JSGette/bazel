#!/usr/bin/env python3
"""Run the manual provider identity conformance fixture, never a benchmark."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET


TARGET = "//src/test/java/com/google/devtools/build/lib/collect/nestedset:ProviderIdentityReproTest"
CLASS = "com.google.devtools.build.lib.collect.nestedset.ProviderIdentityReproTest"
RED_CASES = {
    "independentRestoresPreserveSharedProvider",
    "originalAndRestoredBranchesPreserveSharedProvider",
}
ALL_CASES = RED_CASES | {
    "originalBranchesDeduplicateSharedProvider",
    "distinctEqualContentOriginsRemainDistinctAfterRestore",
}
MARKER = "SKYCACHE_PROVIDER_IDENTITY expected=3 actual=4"


def classify(xml, bazel_exit, expect_known_failure):
    """Require the complete test set and exact assertion signature, not just nonzero exit."""
    if bazel_exit not in (0, 3):
        return "infrastructure_failure", 2
    cases = xml.findall(".//testcase")
    if (
        len(cases) != len(ALL_CASES)
        or {case.get("name") for case in cases} != ALL_CASES
        or any(case.get("classname") != CLASS for case in cases)
        or any(case.find("skipped") is not None or case.get("status") == "notrun" for case in cases)
    ):
        return "incomplete_or_unexpected_test_set", 2
    failed = {}
    for case in cases:
        failures = case.findall("failure")
        if case.findall("error") or len(failures) > 1:
            return "unexpected_failure", 2
        if failures:
            failure = failures[0]
            failed[case.get("name")] = failure.get("message", "") + (failure.text or "")
    if bazel_exit == 0 and not failed:
        return ("unexpected_conformance_pass", 1) if expect_known_failure else ("conformant", 0)
    if bazel_exit == 3 and set(failed) == RED_CASES and all(MARKER in text for text in failed.values()):
        return ("known_failure_reproduced", 0) if expect_known_failure else ("identity_defect", 1)
    return "unexpected_failure", 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", required=True, help="Bazel executable to test")
    parser.add_argument("--output-base", required=True, type=Path, help="Dedicated Bazel output base")
    parser.add_argument("--output-dir", required=True, type=Path, help="New result directory")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[5])
    parser.add_argument("--startup-arg", action="append", default=[])
    parser.add_argument("--bazel-arg", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--expect-known-failure", action="store_true",
        help="Accept only the two exact marked known failures; does not certify eligibility",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    source = repo / "src/test/java/com/google/devtools/build/lib/collect/nestedset/ProviderIdentityReproTest.java"
    if not source.is_file() or not (repo / "MODULE.bazel").is_file():
        parser.error("--repo must contain the promoted fixture and MODULE.bazel")
    bazel = shutil.which(args.bazel)
    if bazel is None:
        parser.error("--bazel executable was not found")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    command = [
        bazel, *args.startup_arg, f"--output_base={args.output_base.resolve()}",
        "test", *args.bazel_arg, TARGET,
        "--nocache_test_results", "--test_filter=", "--runs_per_test=1",
        "--flaky_test_attempts=1", "--test_sharding_strategy=disabled", "--test_output=all",
        f"--symlink_prefix={output}/bazel-",
    ]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (output / "command.json").write_text(json.dumps({
        "command": command, "cwd": str(repo), "revision": revision,
        "fixture_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "expect_known_failure": args.expect_known_failure,
    }, indent=2) + "\n")
    started = time.monotonic()
    bazel_exit = None
    status, exit_code = "infrastructure_failure", 2
    detail = None
    try:
        with (output / "build.log").open("w") as log:
            run = subprocess.run(
                command, cwd=repo, stdout=log, stderr=subprocess.STDOUT,
                timeout=args.timeout_seconds, check=False,
            )
        bazel_exit = run.returncode
        test_dir = output / "bazel-testlogs" / TARGET.removeprefix("//").replace(":", "/")
        for name in ("test.xml", "test.log"):
            if (test_dir / name).is_file():
                shutil.copy2(test_dir / name, output / name)
        xml = ET.parse(output / "test.xml").getroot()
        status, exit_code = classify(xml, bazel_exit, args.expect_known_failure)
    except (OSError, ET.ParseError, subprocess.TimeoutExpired) as error:
        detail = str(error)
    result = {
        "status": status, "bazel_exit": bazel_exit, "runner_exit": exit_code,
        "elapsed_seconds": time.monotonic() - started, "detail": detail,
        "eligibility_gate_passed": False,
    }
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
