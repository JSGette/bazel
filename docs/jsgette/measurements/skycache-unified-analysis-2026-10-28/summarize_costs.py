#!/usr/bin/env python3
"""Extract existing cost/ownership evidence without inventing exclusive CPU attribution."""

import argparse
import csv
import gzip
import json
from pathlib import Path

from paired_baseline import digest, validate_bep_terminal


def histogram_bounds(distribution):
    """Conservative bounds on summed samples; never convert overlap into elapsed/CPU time."""
    count = lower = upper = 0
    for bucket in distribution.get("histogramBucket", []):
        size = int(bucket.get("count", 0))
        minimum, maximum = int(bucket.get("min", 0)), int(bucket.get("max", 0))
        if size < 0 or minimum < 0 or maximum < minimum:
            raise ValueError("invalid latency histogram")
        count += size
        lower += size * minimum
        upper += size * maximum
    return count, lower, upper


def interval_union(intervals):
    """Union of observed wall intervals across threads; not critical path or CPU time."""
    total = 0
    end = None
    for start, stop in sorted(intervals):
        if stop < start:
            raise ValueError("negative profile duration")
        if end is None or start > end:
            total += stop - start
        elif stop > end:
            total += stop - end
        end = stop if end is None else max(end, stop)
    return total


def profile_spans(profile):
    groups = {}
    for event in profile.get("traceEvents", []):
        if event.get("ph") != "X":
            continue
        start, duration = event["ts"], event["dur"]
        if duration < 0:
            raise ValueError("negative profile duration")
        names = ["category:" + event.get("cat", "unspecified")]
        if event.get("name") in (
            "compute main repo mapping", "local skycache publication", "local skycache final validation",
            "local skycache witness manifest", "local skycache seal", "local skycache install",
            "local skycache close stores",
            "materializeNodeGraph", "computeLeafIdsAndUploadOrder", "driveUploads", "clearDirectDeps",
        ):
            names.append("name:" + event["name"])
        for name in names:
            groups.setdefault(name, []).append((start, start + duration))
    return [(name, len(spans), sum(stop - start for start, stop in spans), interval_union(spans))
            for name, spans in sorted(groups.items())]


def collect(root):
    summary = json.loads((root / "summary.json").read_text())
    if summary.get("status") != "passed" or not summary.get("runs"):
        raise ValueError("cost extraction requires a complete passed cohort; retain failures separately")
    phases, ownership = [], []
    for run in summary["runs"]:
        if (run["metrics"].get("exit_code") != 0 or run["metrics"].get("timed_out")
                or run.get("validation", {}).get("passed") is not True):
            raise ValueError("cost extraction requires successful validated runs")
        if not {"metrics.json", "build.bep.json", "build.profile.gz"}.issubset(run.get("raw_artifacts", {})):
            raise ValueError("required consumed artifact hashes are missing")
        directory = root / "runs" / run["label"]
        for name, expected in run.get("raw_artifacts", {}).items():
            if digest(directory / name) != expected:
                raise ValueError(f"raw artifact checksum mismatch: {run['label']}/{name}")
        for name, expected in run.get("diagnostics_sha256", {}).items():
            if digest(directory / "diagnostics" / name) != expected:
                raise ValueError(f"diagnostic checksum mismatch: {run['label']}/{name}")
        prefix = [run["label"], run["arm"], run["pair"], run["measured"]]

        def exact(metric, kind, unit, value):
            if value is not None:
                phases.append(prefix + [metric, kind, unit, 1, value, value])

        metrics = json.loads((directory / "metrics.json").read_text())
        if metrics != run["metrics"]:
            raise ValueError("run metrics differ from summary")
        exact("command", "elapsed_wall", "seconds", metrics["elapsed_seconds"])
        resources = metrics.get("resources", {})
        for name in ("user_cpu_seconds", "system_cpu_seconds"):
            exact(name, "child_rusage_cpu", "seconds", resources.get(name))
        exact("ru_maxrss", "maximum_child_process_rss", resources.get("ru_maxrss_unit"),
              resources.get("ru_maxrss_raw"))
        events = [json.loads(line) for line in (directory / "build.bep.json").read_text().splitlines() if line]
        validate_bep_terminal(events)
        builds = [event["buildMetrics"] for event in events if "buildMetrics" in event]
        if len(builds) != 1:
            raise ValueError("expected exactly one buildMetrics event")
        build = builds[0]
        for name, value in build.get("timingMetrics", {}).items():
            if name in ("cpuTimeInMs", "wallTimeInMs", "analysisPhaseTimeInMs", "executionPhaseTimeInMs"):
                exact(name, "bep_timing", "milliseconds", int(value))
        for name, value in build.get("memoryMetrics", {}).items():
            if name in ("usedHeapSizePostBuild", "peakPostGcHeapSize"):
                exact(name, "bep_heap", "bytes", int(value))
        stats = build.get("remoteAnalysisCacheStatistics", {})
        for name in ("cacheHits", "cacheMisses", "valueStoreValueBytesReceived", "valueStoreValueBytesSent",
                     "valueStoreWriteOps", "valueStoreReadOpsSuccessful", "valueStoreReadOpsNotFound",
                     "analysisCacheOps", "serializationExceptionCount"):
            if name in stats:
                exact(name, "bep_cache_counter", "bytes" if "Bytes" in name else "count", int(stats[name]))
        for item in stats.get("latencyBySkyfunction", []):
            metric = ":".join((item["skyfunction"], item.get("outcome", "OUTCOME_UNSPECIFIED"),
                               item.get("phase", "PHASE_UNSPECIFIED")))
            phases.append(prefix + [metric, "summed_retrieval_latency_bounds", "microseconds",
                                    *histogram_bounds(item.get("latency", {}))])
        for name, value in stats.get("uploadPhaseTimings", {}).items():
            exact(name, "upload_wall_phase", "microseconds", int(value))
        for name, distribution in stats.items():
            if name.endswith("LatencyMicros") and isinstance(distribution, dict):
                phases.append(prefix + [name, "summed_service_latency_bounds", "microseconds",
                                        *histogram_bounds(distribution)])
        with gzip.open(directory / "build.profile.gz", "rt") as stream:
            profile = json.load(stream)
        for name, count, duration_sum, union in profile_spans(profile):
            phases.append(prefix + [name, "observed_span_sum_including_nesting", "microseconds", count,
                                    duration_sum, duration_sum])
            phases.append(prefix + [name, "observed_wall_interval_union", "microseconds", count, union, union])
        path = directory / "diagnostics/local-skycache-ownership.json"
        if path.exists():
            if "local-skycache-ownership.json" not in run.get("diagnostics_sha256", {}):
                raise ValueError("consumed ownership snapshot has no recorded checksum")
            snapshot = json.loads(path.read_text())
            for boundary in ("before_finish", "after_session_close"):
                for name, value in sorted(snapshot[boundary].items()):
                    ownership.append(prefix + [boundary, name, value])
        path = directory / "diagnostics/local-skycache-phases.json"
        if path.exists():
            if "local-skycache-phases.json" not in run.get("diagnostics_sha256", {}):
                raise ValueError("consumed local phase timings have no recorded checksum")
            timing = json.loads(path.read_text())
            if timing.get("enabled"):
                if timing.get("unit") != "nanoseconds":
                    raise ValueError("unsupported local timing unit")
                for name, value in sorted(timing["phases"].items()):
                    exact("local_finish:" + name, "inclusive_elapsed_wall", "nanoseconds", value)
    return phases, ownership


def write_csv(path, columns, rows):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True, help="new directory")
    args = parser.parse_args()
    phases, ownership = collect(args.run_dir.resolve())
    args.out_dir.mkdir(exist_ok=False)
    prefix = ["run", "arm", "pair", "measured"]
    write_csv(args.out_dir / "phase-costs.csv", prefix + ["metric", "kind", "unit", "samples", "lower", "upper"], phases)
    write_csv(args.out_dir / "ownership.csv", prefix + ["boundary", "metric", "value"], ownership)
    (args.out_dir / "interpretation.json").write_text(json.dumps({
        "input_summary_sha256": digest(args.run_dir / "summary.json"),
        "extractor_sha256": digest(__file__),
        "phase_rows": len(phases), "ownership_rows": len(ownership),
        "limitations": [
            "Missing fields are unavailable, not zero.",
            "Retrieval/service latency sums overlap across roots and phases; they are not exclusive CPU or command wall time.",
            "Trace sums include nesting; interval unions remove overlap only within the named group. Groups can overlap.",
            "Profiles can omit short events; these are observed spans, not complete attribution or a critical path.",
            "Ownership counts/references and accounted metadata are not measured retained heap.",
            "Producer rows are separate and never included in a consumer-only speedup estimate.",
        ],
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
