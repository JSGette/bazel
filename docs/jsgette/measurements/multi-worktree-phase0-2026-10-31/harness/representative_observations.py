#!/usr/bin/env python3
"""Derive descriptive baseline observations; never infer a sharing speedup."""

import argparse
import json
from pathlib import Path
import re
import statistics

from representative_results import verify_run


def histogram(path):
    classes = []
    for line in path.read_text().splitlines():
        match = re.match(r"\s*\d+:\s+(\d+)\s+(\d+)\s+(.+)", line)
        if match:
            instances, size, name = match.groups()
            classes.append({"instances": int(instances), "shallow_bytes": int(size), "class": name})
    if not classes:
        raise ValueError("No live class histogram rows")
    return {"total_shallow_bytes": sum(c["shallow_bytes"] for c in classes),
            "total_instances": sum(c["instances"] for c in classes),
            "largest_classes": sorted(classes, key=lambda c: c["shallow_bytes"], reverse=True)[:25],
            "starlark_class_shallow_bytes": sum(c["shallow_bytes"] for c in classes if c["class"].startswith("net.starlark.java.")),
            "skyframe_class_shallow_bytes": sum(c["shallow_bytes"] for c in classes if c["class"].startswith("com.google.devtools.build.skyframe.")),
            "scope": "live shallow sizes; class prefixes do not attribute arrays/strings or establish deep retained ownership"}


def observations(root):
    verified = verify_run(root)
    manifest = json.loads((root / "manifest.json").read_text())
    summary = json.loads((root / "summary.json").read_text())
    groups, executed, cached, test_oracle = {}, 0, 0, {}
    for cohort in summary["cohorts"]:
        for record in cohort["commands"]:
            directory = root / cohort["name"] / record["worker"]
            events = [json.loads(line) for line in (directory / "bep.jsonl").read_text().splitlines()]
            canonical = [e["structuredCommandLine"] for e in events
                         if e.get("structuredCommandLine", {}).get("commandLineLabel") == "canonical"]
            if len(canonical) != 1:
                raise ValueError("Missing canonical command options")
            options = {v["optionName"]: v.get("optionValue", "")
                       for section in canonical[0].get("sections", [])
                       for v in section.get("optionList", {}).get("option", [])}
            if any(name not in options or options[name] != "" for name in ("remote_cache", "remote_executor")):
                raise ValueError("Private cache/executor was not disabled")
            if not record["diagnostic"] and options.get("generate_json_trace_profile") != "no":
                raise ValueError("Latency command did not disable tracing")
            for label, test in record["validation"]["tests"].items():
                executed += test["executed_runs"]
                cached += test["cached_runs"]
                if test_oracle.setdefault(label, test["xml"]) != test["xml"]:
                    raise ValueError("Test-case identities differ between worktrees")
        if cohort["case"] == "dependencies_and_tests":
            continue
        n = str(cohort["concurrency"])
        group = groups.setdefault(n, {"latency_batches": [], "diagnostics": {}, "heap_histograms": {}})
        if cohort["case"] == "warm_latency":
            group["latency_batches"].append({"repetition": cohort["repetition"], "batch_seconds": cohort["batch_seconds"],
                                             "command_seconds": [r["wall_seconds"] for r in cohort["commands"]]})
        if any(r["diagnostic"] for r in cohort["commands"]):
            group["diagnostics"][cohort["case"]] = {
                "process_tree_memory": cohort["memory"],
                "commands": [{"worker": r["worker"], "wall_seconds": r["wall_seconds"],
                              "memory_metrics": r["validation"]["metrics"].get("memoryMetrics", {}),
                              "timing_metrics": r["validation"]["metrics"].get("timingMetrics", {}),
                              "package_metrics": r["validation"]["metrics"].get("packageMetrics", {}),
                              "graph_metrics": r["validation"]["metrics"].get("buildGraphMetrics", {}),
                              "profile": r["validation"]["profile"]} for r in cohort["commands"]]}
    for n, group in groups.items():
        batches = group["latency_batches"]
        commands = [seconds for b in batches for seconds in b["command_seconds"]]
        group["latency_descriptive"] = {"batches": len(batches), "commands": len(commands),
            "median_command_seconds": statistics.median(commands), "min_command_seconds": min(commands),
            "max_command_seconds": max(commands), "median_batch_seconds": statistics.median(b["batch_seconds"] for b in batches)}
        if manifest.get("matrix_version", 1) == 2:
            for index in range(int(n)):
                worker = f"n{n}-w{index}"
                directory = root / "heap" / worker
                command = json.loads((directory / "command.json").read_text())
                if command["exit_code"] != 0:
                    raise ValueError("Heap capture failed")
                group["heap_histograms"][worker] = histogram(directory / "histogram.txt")
    return {"verification": verified, "executed_test_runs": executed, "cached_test_runs": cached,
            "test_oracle": test_oracle, "bazel_sha256": manifest["bazel_sha256"],
            "source_commit": manifest["source_commit"], "groups": groups,
            "limitations": ["Descriptive baseline, not a candidate comparison or performance qualification",
                            "Warm graph is not a guarantee of JIT/OS steady state; raw order is retained",
                            "Batches share resources; per-command observations are correlated",
                            "Trace spans overlap; memory sampling misses short processes/spikes; shallow heap sizes are not deep ownership",
                            "BEP CPU metrics are Bazel-defined, not complete host/process-tree CPU accounting"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = observations(args.results)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verification": result["verification"], "executed_test_runs": result["executed_test_runs"],
                      "cached_test_runs": result["cached_test_runs"],
                      "latency": {n: g["latency_descriptive"] for n, g in result["groups"].items()}}, indent=2))
