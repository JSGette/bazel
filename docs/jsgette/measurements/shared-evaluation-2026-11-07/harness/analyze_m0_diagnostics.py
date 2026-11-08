"""Bounded offline analysis of explicitly supplied diagnostic archives. No runtime access."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import tarfile


class Pairs(list):
    """Keep JSON object pairs, including duplicate printed SkyKey names."""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def validated_archive(path, expected):
    manifest = json.loads(Path(str(path) + ".json").read_text())
    actual = sha(path.read_bytes())
    assert actual == expected == manifest["archive_sha256"], "Archive digest mismatch"
    records = {m["path"]: m for m in manifest["members"]}
    assert len(records) == len(manifest["members"]) <= 300
    assert sum(m["bytes"] for m in records.values()) < 128 * 1024**2
    data = {}
    with tarfile.open(path) as archive:
        members = archive.getmembers()
        assert len(members) == len(records) and {m.name for m in members} == set(records)
        for member in members:
            assert member.isfile() and member.size <= 32 * 1024**2
            content = archive.extractfile(member).read()
            assert len(content) == records[member.name]["bytes"] and sha(content) == records[member.name]["sha256"]
            data[member.name] = content
    return data, {"path": str(path.resolve()), "sha256": actual, "members_verified": len(data),
                  "uncompressed_bytes_verified": sum(map(len, data.values()))}


def leaf_category(owner, method):
    """First matching rule; leaf only, deliberately no caller/context attribution."""
    if not owner and not method:
        return "Unknown: no frame"
    if "libjvm" in owner:
        return "JVM runtime/native (includes JIT and GC)"
    if owner == "." or method.startswith("/"):
        return "Unknown/unresolved native or generated stub"
    if any(token in owner for token in (".so", "vdso", "[kernel]")):
        return "Other native/system library"
    if owner.startswith("net/starlark/"):
        return "Starlark"
    if owner.startswith("com/google/devtools/build/"):
        if "/serialization/" in owner or "AnalysisCache" in owner or "/actions/cache/" in owner:
            return "Bazel serialization/cache"
        if "/skyframe/" in owner:
            return "Bazel graph engine/functions"
        if "/vfs/" in owner or "/unix/" in owner:
            return "Bazel filesystem"
        if any(x in owner for x in ("/analysis/", "/packages/", "/cmdline/")):
            return "Bazel analysis/packages/labels"
        if any(x in owner for x in ("/actions/", "/exec/", "/sandbox/")):
            return "Bazel actions/execution"
        return "Other Bazel"
    if any(owner.startswith(x) for x in ("java/lang/Class", "java/lang/invoke/", "java/lang/reflect/", "jdk/internal/loader/")):
        return "JDK loading/reflection/method handles"
    if any(owner.startswith(x) for x in ("java/lang/String", "java/util/regex/", "java/math/", "sun/security/")):
        return "JDK strings/regex/numbers/crypto"
    if owner.startswith("java/util/"):
        return "JDK collections/concurrency/utilities"
    if owner.startswith(("java/", "javax/", "jdk/", "sun/")):
        return "Other JDK"
    if owner.startswith(("com/google/common/", "com/google/protobuf/")):
        return "Guava/protobuf"
    return "Unknown/other Java library"


def cpu_samples(data, phase):
    events = json.loads(data[phase + "-jfr-decoded/stdout.log"])["recording"]["events"]
    summary = data[phase + "-jfr-summary/stdout.log"].decode()
    reported = {name: int(count) for name, count in re.findall(r"^\s+(jdk\.(?:ExecutionSample|NativeMethodSample))\s+(\d+)\s+\d+", summary, re.M)}
    types, categories, leaves, threads, depths = Counter(), Counter(), Counter(), Counter(), Counter()
    absent = truncated = 0
    for event in events:
        types[event["type"]] += 1
        assert event["type"] in ("jdk.ExecutionSample", "jdk.NativeMethodSample")
        values = event["values"]; stack = values.get("stackTrace") or {}; frames = stack.get("frames") or []
        truncated += bool(stack.get("truncated")); absent += not bool(frames); depths[len(frames)] += 1
        method = frames[0].get("method", {}) if frames else {}
        owner = (method.get("type") or {}).get("name", ""); name = method.get("name", "")
        categories[leaf_category(owner, name)] += 1; leaves[owner + "." + name] += 1
        thread = values.get("sampledThread") or {}; threads[thread.get("javaName") or thread.get("osName") or "unknown"] += 1
    assert all(types[k] == v for k, v in reported.items()) and sum(reported.values()) == len(events)
    assert sum(categories.values()) == len(events)
    timestamps = sorted(e["values"]["startTime"] for e in events)
    span = (datetime.fromisoformat(timestamps[-1]) - datetime.fromisoformat(timestamps[0])).total_seconds() if timestamps else None
    return {"event_counts": dict(types), "summary_event_counts": reported, "decoded_count_matches_summary": True,
            "samples": len(events), "missing_stack_samples": absent, "reported_truncated_stack_samples": truncated,
            "decoded_frame_depths": dict(depths), "first_sample_utc": timestamps[0] if timestamps else None,
            "last_sample_utc": timestamps[-1] if timestamps else None, "sample_timestamp_span_seconds_not_cpu": span,
            "leaf_categories": [{"category": k, "samples": v, "sample_percent": v * 100 / len(events)} for k, v in categories.most_common()],
            "top_leaf_frames": leaves.most_common(25), "thread_sample_counts": threads.most_common(),
            "jfr_summary_raw": summary, "cpu_seconds_derived_from_samples": None}


def skyvalue_memory(content):
    pairs = json.loads(content, object_pairs_hook=Pairs)
    assert isinstance(pairs, Pairs)
    labels = defaultdict(list); groups = defaultdict(lambda: {"rows": 0, "objects": 0, "estimated_bytes": 0})
    for label, fields in pairs:
        assert isinstance(fields, Pairs) and len(dict(fields)) == len(fields)
        value = dict(fields); assert set(value) == {"objects", "bytes"}
        labels[label].append(value)
        group = groups[label.split(":", 1)[0]]
        group["rows"] += 1; group["objects"] += value["objects"]; group["estimated_bytes"] += value["bytes"]
    duplicate = [{"printed_name": k, "entries": v} for k, v in labels.items() if len(v) > 1]
    total = sum(g["estimated_bytes"] for g in groups.values())
    return {"entry_pairs_processed_without_deduplication": len(pairs), "distinct_printed_names": len(labels),
            "duplicate_printed_name_groups": duplicate, "duplicate_excess_entries": len(pairs) - len(labels),
            "estimated_unique_reachable_value_payload_bytes": total,
            "estimated_objects": sum(g["objects"] for g in groups.values()),
            "bytes_lost_by_last_wins_json_parser": total - sum(v[-1]["bytes"] for v in labels.values()),
            "groups": [{"skyfunction": k, **v} for k, v in sorted(groups.items(), key=lambda p: -p[1]["estimated_bytes"])],
            "interpretation": "Sum all per-SkyKey rows from full traversal with global object-identity deduplication; printed names are not unique identities. Estimated reachable value payload, not whole evaluator, retained/dominator size, or per-query reclaimable memory."}


def analyze(path, expected):
    data, validation = validated_archive(path, expected)
    report = json.loads(data["report.json"]); assert report["passed"] and report["diagnostic_only"]
    phases = []
    for phase in report["phases"]:
        name = phase["phase"]; resource = json.loads(data[name + "-resources.json"])
        assert {k: v for k, v in resource.items() if k != "samples"} == phase["resources"]
        metrics = phase["correctness"]["bep"]["build_metrics"]
        timing = metrics.get("timingMetrics", {}); action = metrics.get("actionSummary", {})
        current = [s["cgroup"].get("memory.current") for s in resource["samples"] if s.get("cgroup")]
        result = {"phase": name, "passed": phase["passed"], "command_wall_seconds": phase["command"]["wall_seconds"],
                  **phase["resources"], "scope_sampled_peak_charged_bytes": max(v for v in current if v is not None),
                  "bep_timing_raw": timing, "action_summary": action, "graph_metrics": metrics.get("buildGraphMetrics"),
                  "skycache": phase["correctness"].get("skycache"), "correctness": {k: v for k, v in phase["correctness"].items() if k not in ("bep", "skycache")}}
        if name in ("restart", "warm"):
            result["profiler"] = cpu_samples(data, name)
        phases.append(result)
    return {"arm": report["arm"], "archive": validation, "passed": report["passed"], "diagnostic_only": True,
            "diagnostic_gaps": report["diagnostic_gaps"], "binary_sha256": report["binary_sha256"],
            "script_sha256": report["script_sha256"], "harness_sha256": report["harness_sha256"],
            "scope_limits": report["scope_limits"], "cache_policy": report["cache_policy"], "temporary_policy": report["temporary_policy"],
            "phases": phases, "heap_after_gc_display_only": data["heap-after-gc/stdout.log"].decode().strip(),
            "skyvalue_memory": skyvalue_memory(data["skyvalue-memory/stdout.log"])}


def markdown(result):
    lines = ["# M0 diagnostic evidence", "", "Instrumented single trials; no milestone verdict or statistical claim. CPU values below are measured process/cgroup counters. Profiler samples are not converted to CPU seconds; analysis/execution timing fields remain elapsed durations.", "", "| Arm / phase | Wall s | JVM CPU s | Scope CPU s | JVM peak PSS MiB | Observer CPU s | CPU samples |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for arm in result["arms"]:
        for p in arm["phases"]:
            lines.append(f"| {arm['arm']} / {p['phase']} | {p['command_wall_seconds']:.3f} | {p['engine_cpu_seconds']:.3f} | {p['scope_cpu_seconds']:.3f} | {p['engine_sampled_peak_pss_bytes']/2**20:.3f} | {p['observer_thread_cpu_seconds']:.3f} | {p.get('profiler',{}).get('samples','unprofiled')} |")
    for arm in result["arms"]:
        m = arm["skyvalue_memory"]
        lines += ["", f"## {arm['arm'].upper()} value-memory evidence", "", f"Heap after forced GC was displayed as **{arm['heap_after_gc_display_only']}** (rounded diagnostic text). All {m['entry_pairs_processed_without_deduplication']:,} value-dump pairs sum to **{m['estimated_unique_reachable_value_payload_bytes']:,} estimated bytes** ({m['estimated_unique_reachable_value_payload_bytes']/2**20:.2f} MiB). {len(m['duplicate_printed_name_groups'])} printed-name collision groups contain {m['duplicate_excess_entries']} excess entries; an ordinary last-wins JSON parser would lose {m['bytes_lost_by_last_wins_json_parser']:,} bytes. These are estimated unique reachable value payloads, not retained/dominator sizes or total evaluator memory.", "", "| Skyfunction | Rows | Estimated MiB |", "| --- | ---: | ---: |"]
        lines += [f"| {g['skyfunction']} | {g['rows']} | {g['estimated_bytes']/2**20:.3f} |" for g in m["groups"][:10]]
        for p in arm["phases"]:
            if "profiler" not in p:
                continue
            cpu = p["profiler"]
            lines += ["", f"### {arm['arm']} / {p['phase']} leaf samples", "", f"Decoded {cpu['samples']} CPU events, matching the JFR event summary; missing stacks {cpu['missing_stack_samples']}. This validates event decoding, not a percentage of JVM CPU captured. Samples expose at most {max(map(int,cpu['decoded_frame_depths']))} decoded frames. Categories use only the leaf frame and are mutually exclusive; caller work remains unattributed.", "", "| Leaf category | Samples | Sample % |", "| --- | ---: | ---: |"]
            lines += [f"| {g['category']} | {g['samples']} | {g['sample_percent']:.2f} |" for g in cpu["leaf_categories"]]
    lines += ["", "## Interpretation limits", ""] + ["- " + note for note in result["limits"]]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path); parser.add_argument("--sha256", action="append", required=True)
    parser.add_argument("--output-prefix", type=Path, required=True); args = parser.parse_args()
    assert len(args.archives) == len(args.sha256)
    result = {"analyzer_sha256": sha(Path(__file__).read_bytes()), "milestone_verdict": "not_assessed",
              "arms": [analyze(p, h) for p, h in zip(args.archives, args.sha256)], "limits": [
        "CPU profiling covers the command interval, not necessarily JVM startup or all process CPU. No sample-period multiplication, elapsed-to-CPU conversion, or inference that unprofiled CPU is zero.",
        "Leaf-frame categories are naming heuristics, not logical query ownership. JVM native frames include JIT/GC/runtime; utility/native frames may serve graph work. Thread totals are a separate view and must not be added to categories.",
        "Warm profiles have few samples and short windows; percentages are unstable. These diagnostic trials include observer/profiler overhead and are not replacements for the uninstrumented matrix.",
        "Scope CPU includes processes beyond the JVM; waited-child rusage and observer timings are not an additive process-role partition. PSS is sampled whole-JVM residency, not Java heap. Scope memory includes page cache.",
        "Memory analysis uses object-pairs parsing and sums every emitted row. Original raw dump is preserved in the hash-verified archive; collision groups retain every value entry in this analysis.",
        "Inspected checkout SkyframeMemoryDumper.java:280-351 uses one global object-identity set, excludes pre-seen builtins, traverses SkyValues, and prints diagnostic canonical names. Traversal attribution can move between rows; wrappers, graph keys/edges, JVM native memory and all roots are not a retained-memory partition.",
        "The heap display is rounded and occurs before the subsequent value-dump command. Value bytes and heap/PSS cannot be subtracted to infer exact missing graph overhead."]}
    for suffix, content in ((".json", json.dumps(result, indent=2) + "\n"), (".md", markdown(result))):
        with Path(str(args.output_prefix) + suffix).open("x", encoding="utf-8") as output:
            output.write(content)
    print(json.dumps({"arms": [a["arm"] for a in result["arms"]], "milestone_verdict": "not_assessed"}))


if __name__ == "__main__":
    main()
