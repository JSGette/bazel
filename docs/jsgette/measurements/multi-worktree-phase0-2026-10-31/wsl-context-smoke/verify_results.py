#!/usr/bin/env python3
"""Verify a completed smoke archive and derive observations, not speedups."""

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def verify(root):
    summary = json.loads((root / "summary.json").read_text())
    manifest = json.loads((root / "manifest.json").read_text())
    if summary.get("failure") or summary.get("cleanup_failed"):
        raise ValueError("Run failed or cleanup failed; excluded from successful observations")
    if not summary["cohorts"]:
        raise ValueError("Empty run")
    count = 0
    groups = {}
    runners = Counter()
    metrics = []
    for cohort in summary["cohorts"]:
        n = cohort["concurrency"]
        assert cohort["valid"] and cohort["peak_overlapping_command_intervals"] == n
        assert len(set(cohort["distinct_server_pids"])) == n
        group = groups.setdefault(n, {"commands": 0, "cohorts": 0, "coldgraph_seconds": [],
                                      "second_warm_repeat_seconds": [], "disk_hits_in_coldgraph": 0,
                                      "peak_sampled_server_rss_bytes": 0})
        # The one-worker prime has its own role; keep it outside the n=1 group.
        is_prime = cohort["case"] == "initial_cold"
        if not is_prime:
            group["cohorts"] += 1
            group["peak_sampled_server_rss_bytes"] = max(
                group["peak_sampled_server_rss_bytes"],
                cohort["memory"]["peak_aggregate_server_rss_bytes"])
        for record in cohort["commands"]:
            count += 1
            directory = root / cohort["name"] / record["worker"]
            saved = json.loads((directory / "result.json").read_text())
            assert saved == record and record["exit_code"] == 0 and not record["timed_out"]
            validation = record["validation"]
            actual, expected = validation["actual_content"].encode(), validation["expected_content"].encode()
            assert actual == expected and validation["valid"]
            assert hashlib.sha256(actual).hexdigest() == validation["actual_sha256"]
            assert hashlib.sha256(expected).hexdigest() == validation["expected_sha256"]
            events = [json.loads(line) for line in (directory / "bep.jsonl").read_text().splitlines()]
            finished = [event["finished"] for event in events if "finished" in event]
            assert len(finished) == 1 and finished[0]["exitCode"].get("code", 0) == 0
            with gzip.open(directory / "profile.json.gz", "rt") as stream:
                profile = json.load(stream)
            assert isinstance(profile.get("traceEvents"), list) and profile["traceEvents"]
            build_metrics = [event["buildMetrics"] for event in events if "buildMetrics" in event]
            assert len(build_metrics) == 1
            action = build_metrics[0].get("actionSummary", {})
            timing = build_metrics[0].get("timingMetrics", {})
            per_command_runners = {item["name"]: item["count"] for item in action.get("runnerCount", [])}
            runners.update(per_command_runners)
            metrics.append({"cohort": cohort["name"], "worker": record["worker"],
                            "wall_seconds": record["wall_seconds"],
                            "bazel_timing_metrics": timing, "runners": per_command_runners})
            if not is_prime:
                group["commands"] += 1
                if cohort["case"] == "coldgraph_hotdisk":
                    group["coldgraph_seconds"].append(record["wall_seconds"])
                    group["disk_hits_in_coldgraph"] += per_command_runners.get("disk cache hit", 0)
                if cohort["case"] == "warm_identical" and cohort["repetition"] == 1:
                    group["second_warm_repeat_seconds"].append(record["wall_seconds"])
    for cleanup in summary["shutdown"]:
        assert cleanup.get("exit_code") == 0 and cleanup.get("after") is None and not cleanup.get("error")
    return {"schema_version": 1, "verified_build_commands": count,
            "verified_cohorts": len(summary["cohorts"]),
            "successful_server_shutdowns": len(summary["shutdown"]),
            "fixture_source_sha256": manifest["fixture_source_sha256"],
            "harness_sha256": manifest["harness_sha256"],
            "groups_excluding_prime": {n: group for n, group in groups.items() if group["cohorts"]},
            "runner_counts": dict(runners),
            "command_metrics": metrics,
            "limitations": ["Smoke observations, not a comparison of implementations or qualified latency estimates",
                            "Server-RSS samples exclude clients/action children and can miss peaks",
                            "BEP CPU and phase counters have Bazel-defined scopes; not whole-host CPU",
                            "Only the synthetic build fixture was executed; representative tests remain pending"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = verify(arguments.results)
    if arguments.output:
        arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "command_metrics"}, indent=2))
