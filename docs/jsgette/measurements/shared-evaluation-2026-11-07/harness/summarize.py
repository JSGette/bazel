"""Summarize M0 evidence without declaring a milestone or pooling unlike runs.

Examples:
  python3 summarize.py --root /mnt/shared-evaluation-20261107 \
    --exclude '*qualification*' --output-json summary.json --output-md summary.md
  python3 summarize.py --runs /path/to/run/report.json /path/to/other/run \
    --output-json selected.json --output-md selected.md

Excluded inputs remain in the JSON inventory. Failed/malformed selected reports
remain visible. Missing measurements are null, never inferred to be zero.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import fnmatch
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re


CAVEATS = [
    "This summary does not decide or pass M0 or any later milestone.",
    "Cold-engine means new output bases; repository, action, and SkyCache contents may remain warm.",
    "Warm uses a prior preparation build; restart stops its daemons but retains that output-base state.",
    "BEP analysis/execution sums are sums of per-client elapsed durations, not CPU; phases may overlap.",
    "Action CPU includes only explicitly reported user/system durations and is not whole-build CPU.",
    "Cgroup CPU includes harness, clients, daemons, and descendants; engine CPU is sampled daemon ticks.",
    "Engine PSS excludes clients and compiler workers; sampled peaks may miss short-lived maxima.",
    "Scope memory is charged bytes including page cache; first-touch ownership can bias run order.",
    "Scope lifetime memory peak includes preparation; component peaks are non-simultaneous and must not be added.",
    "Profile span sums can overlap and nest across threads; they are not CPU or critical-path estimates.",
    "No cross-run timing averages, speedups, or conclusions are computed. Review provenance before comparison.",
]


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() else result


def seconds(value):
    """Read protobuf Duration JSON; do not guess units for bare numbers."""
    if isinstance(value, str) and re.fullmatch(r"-?\d+(?:\.\d+)?s", value):
        return number(value[:-1])
    if isinstance(value, dict) and ("seconds" in value or "nanos" in value):
        whole, fraction = number(value.get("seconds", 0)), number(value.get("nanos", 0))
        return whole + fraction / 1e9 if whole is not None and fraction is not None else None
    return None


def measured_sum(values):
    values = list(values)
    present = [value for value in values if value is not None]
    return {"sum": sum(present) if present else None,
            "complete_sum": sum(present) if values and len(present) == len(values) else None,
            "reported": len(present), "expected": len(values)}


def scalars(value, prefix=""):
    """Keep actual numeric/boolean counters; omit text and array payloads."""
    result = {}
    if isinstance(value, dict):
        for key, child in value.items():
            name = prefix + "." + key if prefix else key
            if isinstance(child, dict):
                result.update(scalars(child, name))
            elif isinstance(child, bool):
                result[name] = child
            elif not isinstance(child, (list, tuple)) and number(child) is not None:
                result[name] = number(child)
    return result


def cache_classes(report):
    inventory = report.get("cache_before_measured") or report.get("cache_before") or {}
    result = {}
    for name in ("repository", "disk", "skycache"):
        item = inventory.get(name) or {}
        if item.get("enabled") is False:
            state = "disabled"
        elif item.get("exists") is False:
            state = "absent"
        elif (number(item.get("files_observed")) or 0) > 0 or (number(item.get("symlinks_observed")) or 0) > 0:
            state = "populated"
        elif item.get("truncated") is False and item.get("files_observed") == 0:
            state = "empty"
        else:
            state = "unknown"
        result[name] = state
    publications = inventory.get("skycache_publications") or {}
    result["skycache_publications"] = publications.get("state", "unknown")
    return result


def profile_summary(path):
    result = {"path": str(path), "available": path.is_file()}
    if not result["available"]:
        return result
    result["sha256"] = digest(path)
    try:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        events = data.get("traceEvents", []) if isinstance(data, dict) else data
        totals, counts = defaultdict(float), Counter()
        omitted = Counter()
        for event in events:
            duration = number(event.get("dur"))
            if event.get("ph") != "X" or duration is None or duration < 0:
                omitted[str(event.get("ph", "missing"))] += 1
                continue
            category = str(event.get("cat") or "uncategorized")
            totals[category] += duration / 1e6
            counts[category] += 1
        result.update(categories=[{"category": key, "elapsed_span_seconds_sum": totals[key],
                                   "complete_span_count": counts[key]}
                                  for key in sorted(totals, key=lambda key: (-totals[key], key))],
                      unaggregated_events_by_phase=dict(omitted),
                      units="Chrome trace complete-event dur in microseconds converted to seconds",
                      interpretation="Overlapping/nested elapsed spans; not CPU or critical path")
    except (OSError, ValueError, TypeError, AttributeError) as error:
        result["error"] = type(error).__name__ + ": " + str(error)
    return result


def local_evidence(report_path, row, filename):
    index = row.get("index")
    if isinstance(index, int):
        local = report_path.parent / "measured" / ("c%02d" % index) / filename
        if local.exists():
            return local
    command = row.get("command")
    return Path(command).parent / filename if command else report_path.parent / filename


def client_summary(row, report_path, profiles):
    metrics = row.get("bep", {}).get("build_metrics") or {}
    timing = metrics.get("timingMetrics") or {}
    graph = metrics.get("buildGraphMetrics") or {}
    action = metrics.get("actionSummary") or {}
    action_rows = action.get("actionData") or []
    action_cpu = [{"mnemonic": item.get("mnemonic"),
                   "actions_executed": number(item.get("actionsExecuted")),
                   "user_seconds": seconds(item.get("userTime")),
                   "system_seconds": seconds(item.get("systemTime"))} for item in action_rows]
    for item in action_cpu:
        user, system = item["user_seconds"], item["system_seconds"]
        item["user_plus_system_seconds"] = user + system if user is not None and system is not None else None
    counts = {}
    for field in ("builtValues", "evaluatedValues"):
        by_function = {item.get("skyfunctionName"): number(item.get("count"))
                       for item in graph.get(field, [])}
        counts[field] = {key: by_function.get(key) for key in
                         ("CONFIGURED_TARGET", "BUILD_CONFIGURATION", "BUILD_CONFIGURATION_KEY", "ACTION_EXECUTION")}
    command_path = local_evidence(report_path, row, "command.json")
    command = {"path": str(command_path), "available": command_path.is_file()}
    if command["available"]:
        command["sha256"] = digest(command_path)
        try:
            args = json.loads(command_path.read_text(encoding="utf-8")).get("argv", [])
            flag_names = ("--jobs", "--loading_phase_threads", "--host_jvm_args", "--local_resources",
                          "--worker_max_instances", "--disk_cache", "--repository_cache",
                          "--remote_cache", "--remote_executor", "--experimental_local_skycache",
                          "--experimental_remote_skycache", "--experimental_analysis_cache_idle_retention",
                          "--experimental_active_directories", "--lockfile_mode", "--compilation_mode")
            command["selected_flags"] = [arg for arg in args if arg.split("=", 1)[0] in flag_names]
        except (OSError, ValueError, TypeError) as error:
            command["error"] = str(error)
    result = {
        "index": row.get("index"), "variant": row.get("variant"),
        "compilation_mode": row.get("compilation_mode"), "oracle_key": row.get("oracle_key"),
        "exit_code": row.get("exit_code"), "timed_out": row.get("timed_out"),
        "bep_finished": row.get("bep", {}).get("finished"),
        "correctness_failure": row.get("correctness_failure"),
        "behavior_passed": row.get("behavior", {}).get("passed"),
        "digest_oracle": row.get("digest_oracle"),
        "glob_oracle": row.get("glob_oracle"),
        "output_sha256": row.get("output", {}).get("sha256"),
        "client_wall_seconds": number(row.get("wall_seconds")),
        "analysis_elapsed_seconds": number(timing.get("analysisPhaseTimeInMs")),
        "execution_elapsed_seconds": number(timing.get("executionPhaseTimeInMs")),
        "bep_cpu_seconds": number(timing.get("cpuTimeInMs")),
        "bep_wall_seconds": number(timing.get("wallTimeInMs")),
        "target_metrics": scalars(metrics.get("targetMetrics", {})),
        "package_metrics": scalars(metrics.get("packageMetrics", {})),
        "graph_counts": scalars({key: value for key, value in graph.items() if not isinstance(value, (dict, list))}),
        "selected_skyfunction_counts": counts,
        "action_counts": {key: number(action.get(key)) for key in
                          ("actionsCreated", "actionsCreatedNotIncludingAspects", "actionsExecuted")},
        "action_cpu": {"reported_mnemonics": action_cpu,
                       "user_seconds": measured_sum(item["user_seconds"] for item in action_cpu),
                       "system_seconds": measured_sum(item["system_seconds"] for item in action_cpu),
                       "user_plus_system_seconds": measured_sum(item["user_plus_system_seconds"] for item in action_cpu),
                       "interpretation": "Reported subset only; missing duration is unknown, not zero"},
        "remote_analysis_cache_counters": scalars(metrics.get("remoteAnalysisCacheStatistics", {})),
        "skycache_counters": {name: scalars(value) for name, value in row.get("skycache", {}).items()},
        "command": command,
    }
    for key in ("analysis_elapsed_seconds", "execution_elapsed_seconds", "bep_cpu_seconds", "bep_wall_seconds"):
        if result[key] is not None:
            result[key] /= 1000
    if profiles:
        result["profile"] = profile_summary(local_evidence(report_path, row, "profile.json.gz"))
    return result


def aggregate_counter_maps(maps):
    maps = list(maps)
    keys = sorted({key for mapping in maps for key in mapping})
    return {key: measured_sum(number(mapping.get(key)) for mapping in maps) for key in keys}


def summarize(path, profiles):
    result = {"report": str(path), "run_id": path.parent.name, "performance_eligible": False}
    try:
        result["report_sha256"] = digest(path)
        report = json.loads(path.read_text(encoding="utf-8"))
        fixture = report.get("fixture") or {}
        measured = report.get("measured") or {}
        resources = measured.get("resources") or {}
        rows = measured.get("results") or []
        clients = [client_summary(row, path, profiles) for row in rows]
        reasons = []
        if not report.get("harness_sha256") or not report.get("binary_sha256"):
            reasons.append("binary_or_harness_provenance_missing")
        if not fixture.get("source_revision") or not fixture.get("projection_archive_sha256"):
            reasons.append("workload_provenance_missing")
        if report.get("passed") is not True or measured.get("passed") is not True:
            reasons.append("report_or_measured_cohort_failed")
        if len(rows) != fixture.get("clients"):
            reasons.append("measured_client_count_incomplete")
        if resources.get("cgroup_accounted") is not True or resources.get("engine_membership_verified") is not True:
            reasons.append("cgroup_accounting_or_membership_not_verified")
        if measured.get("accounting_passed") is not True:
            reasons.append("strict_accounting_pass_missing_or_false")
        if resources.get("engine_expected_count") != fixture.get("clients"):
            reasons.append("expected_engine_count_not_attested")
        if report.get("source_state_unchanged") is not True:
            reasons.append("source_state_not_verified")
        if report.get("analysis_only"):
            reasons.append("analysis_only_has_no_execution_oracle")
        for row in clients:
            if row["exit_code"] != 0 or row["bep_finished"] is not True or row["correctness_failure"]:
                reasons.append("client_failed:" + str(row["index"]))
            oracle = row["digest_oracle"] or {}
            if oracle.get("available") is not True or oracle.get("matches") is not True:
                reasons.append("digest_oracle_missing_or_failed:" + str(row["index"]))
            if row["behavior_passed"] is not True:
                reasons.append("behavior_oracle_missing_or_failed:" + str(row["index"]))
            if fixture.get("scenario") == "glob" and (row.get("glob_oracle") or {}).get("passed") is not True:
                reasons.append("glob_oracle_missing_or_failed:" + str(row["index"]))
            if row["command"].get("available") is not True or row["command"].get("error"):
                reasons.append("command_evidence_missing_or_invalid:" + str(row["index"]))
        if not rows:
            reasons.append("no_measured_clients")
        if number(resources.get("scope_cpu_seconds")) is None:
            reasons.append("scope_cpu_missing")
        if number(resources.get("engine_sampled_peak_pss_bytes")) is None:
            reasons.append("engine_pss_missing")
        state = report.get("cache_state")
        label = {"cold": "cold-engine",
                 "warm": "warm-engine after preparation",
                 "restart": "restarted-engine; prepared output-base/cache state retained"}.get(state, "unknown")
        cache_states = cache_classes(report)
        label += "; repo=" + cache_states["repository"] + ", action=" + cache_states["disk"] + ", analysis=" + cache_states["skycache"]
        fingerprint = {"harness_sha256": report.get("harness_sha256"),
                       "binary_sha256": report.get("binary_sha256"), "mode": report.get("mode"),
                       "source_revision": fixture.get("source_revision"), "target": fixture.get("target"),
                       "projection_archive_sha256": fixture.get("projection_archive_sha256"),
                       "private_tree": fixture.get("private_tree"),
                       "clients": fixture.get("clients"), "scenario": fixture.get("scenario"),
                       "cache_state": state, "cache_classes": cache_states,
                       "extra_bazel_arguments": report.get("extra_bazel_arguments"),
                       "worktree_inputs": [{key: row.get(key) for key in
                           ("variant", "compilation_mode", "source_sha256", "build_sha256", "changed_file", "changed_sha256", "extra_targets")}
                           for row in fixture.get("worktrees", [])],
                       "analysis_only": report.get("analysis_only")}
        sums = {key: measured_sum(row[key] for row in clients) for key in
                ("analysis_elapsed_seconds", "execution_elapsed_seconds", "bep_cpu_seconds", "bep_wall_seconds")}
        result.update(report_passed=report.get("passed"), measured_passed=measured.get("passed"),
                      error=report.get("error"), cleanup_error=report.get("cleanup_error"),
                      source_state_unchanged=report.get("source_state_unchanged"),
                      mode=report.get("mode"), cache_state=state, cache_classification=label,
                      cache_classes_before_measured=cache_states,
                      cache_inventories={key: report.get(key) for key in ("cache_before", "cache_before_measured", "cache_after")},
                      fresh_caches_requested=report.get("fresh_caches_requested"),
                      cache_notes=report.get("notes", []), clients_expected=fixture.get("clients"),
                      scenario=fixture.get("scenario"), variant_note=fixture.get("variant_note"),
                      analysis_only=report.get("analysis_only"),
                      variant_counts=dict(Counter(str(row.get("variant")) for row in clients)),
                      compilation_mode_counts=dict(Counter(str(row.get("compilation_mode")) for row in clients)),
                      provenance=fingerprint, environment_digest=report.get("environment_digest"),
                      oracle_provenance=report.get("oracle_provenance") or report.get("oracle_report"),
                      oracle_provenance_note="Per-client matching is harness-attested; inspect recorded oracle identity when available",
                      provenance_stratum_sha256=hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest(),
                      cohort_wall_seconds=number(measured.get("wall_seconds")),
                      resources={key: value for key, value in resources.items() if key != "samples"},
                      client_elapsed_sums=sums,
                      graph_count_sums=aggregate_counter_maps(row["graph_counts"] for row in clients),
                      target_metric_sums=aggregate_counter_maps(row["target_metrics"] for row in clients),
                      action_count_sums=aggregate_counter_maps(row["action_counts"] for row in clients),
                      action_reported_user_seconds_sum=measured_sum(row["action_cpu"]["user_seconds"]["sum"] for row in clients),
                      action_reported_system_seconds_sum=measured_sum(row["action_cpu"]["system_seconds"]["sum"] for row in clients),
                      clients=clients, performance_eligible=not reasons,
                      ineligibility_reasons=reasons)
    except (OSError, ValueError, TypeError, AttributeError, KeyError) as error:
        result.update(error=type(error).__name__ + ": " + str(error),
                      ineligibility_reasons=["summary_input_error"])
    return result


def cell(value, digits=3):
    if value is None:
        return "unknown"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value).replace("|", "\\|").replace("\n", " ")


def markdown(data):
    lines = ["# Shared evaluation M0 evidence summary", "",
             "No milestone verdict is inferred. Eligibility denotes complete performance evidence checks, not an architecture gate.", "",
             "## Measured cohorts", "",
             "| Run | Mode | N | Scenario | Cache class | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine peak PSS MiB |",
             "| --- | --- | ---: | --- | --- | --- | ---: | ---: | ---: | ---: |"]
    for run in data["runs"]:
        r = run.get("resources") or {}
        pss = number(r.get("engine_sampled_peak_pss_bytes"))
        values = [run["run_id"], run.get("mode"), run.get("clients_expected"), run.get("scenario"),
                  run.get("cache_classification"), run["performance_eligible"], run.get("cohort_wall_seconds"),
                  r.get("scope_cpu_seconds"), r.get("engine_observed_cpu_seconds"), pss / 2**20 if pss is not None else None]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines += ["", "## Client duration and work counts", "",
              "Sums are across clients. Analysis/execution durations can overlap and are not CPU. Unknown means incomplete or absent reporting.", "",
              "| Run | Analysis elapsed sum s | Execution elapsed sum s | Graph nodes sum | Targets configured sum | Actions executed sum | Reported action user CPU s | Reported action system CPU s |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for run in data["runs"]:
        def summed(group, key):
            return run.get(group, {}).get(key, {}).get("complete_sum")
        values = [run["run_id"], summed("client_elapsed_sums", "analysis_elapsed_seconds"),
                  summed("client_elapsed_sums", "execution_elapsed_seconds"),
                  summed("graph_count_sums", "postInvocationSkyframeNodeCount"),
                  summed("target_metric_sums", "targetsConfigured"), summed("action_count_sums", "actionsExecuted"),
                  run.get("action_reported_user_seconds_sum", {}).get("sum"),
                  run.get("action_reported_system_seconds_sum", {}).get("sum")]
        lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines += ["", "## Recorded cache counters by client", "",
              "Counters are copied from the named invocation statistics. Missing fields remain unknown; no cache hit is inferred from build success.", "",
              "| Run/client | Variant / configuration | BEP hit counters | Shared reads | Validation records | Witnesses checked | Rejected roots | Published / failed |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |"]
    for run in data["runs"]:
        for client in run.get("clients", []):
            cache = client["skycache_counters"].get("local-skycache.json", {})
            validation = client["skycache_counters"].get("local-skycache-validation.json", {})
            hits = [key + "=" + cell(value) for key, value in client["remote_analysis_cache_counters"].items()
                    if "hit" in key.lower()]
            values = [run["run_id"] + "/" + str(client["index"]), cell(client["variant"]) + " / " + cell(client["compilation_mode"]),
                      "; ".join(hits) if hits else None, cache.get("shared_reads"),
                      validation.get("records_read"), validation.get("witnesses_checked"),
                      validation.get("rejected_roots"), cell(cache.get("published")) + " / " + cell(cache.get("failed"))]
            lines.append("| " + " | ".join(cell(value) for value in values) + " |")
    lines += ["", "## Evidence qualifications", ""]
    for run in data["runs"]:
        reasons = run.get("ineligibility_reasons") or ["complete recorded checks; no milestone verdict"]
        lines.append("- **" + cell(run["run_id"]) + "**: " + "; ".join(reasons) + ". Report SHA-256: `" + str(run.get("report_sha256", "unavailable")) + "`.")
        if run.get("error"):
            lines.append("  Error: " + cell(run["error"]))
    if data["excluded"]:
        lines += ["", "## Explicit exclusions", ""]
        for item in data["excluded"]:
            lines.append("- " + cell(item["report"]) + ": pattern `" + item["pattern"] + "`; recorded pass=" + cell(item.get("report_passed")) + ".")
    if data["discovery_errors"]:
        lines += ["", "## Discovery errors", ""] + ["- " + cell(error) for error in data["discovery_errors"]]
    lines += ["", "## Interpretation limits", ""] + ["- " + note for note in CAVEATS]
    lines += ["", "Full per-client counters, profile spans, hashes, command references, and provenance strata are in the accompanying JSON.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, help="Recursively discover report.json under this directory")
    parser.add_argument("--runs", nargs="+", type=Path, help="Exact run directories or report.json files")
    parser.add_argument("--exclude", action="append", default=[], help="Explicit glob matched against full path or run ID")
    parser.add_argument("--skip-profiles", action="store_true")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()
    if not args.root and not args.runs:
        parser.error("Supply --root and/or --runs")
    paths, discovery_errors = set(), []
    if args.root:
        if not args.root.is_dir():
            parser.error("--root is not a directory")
        for directory, _, names in os.walk(args.root, onerror=lambda error: discovery_errors.append(str(error))):
            if "report.json" in names:
                paths.add((Path(directory) / "report.json").resolve())
    for path in args.runs or []:
        paths.add((path / "report.json" if path.is_dir() else path).resolve())
    output_paths = {args.output_json.resolve(), args.output_md.resolve()}
    if len(output_paths) != 2 or output_paths & paths:
        parser.error("Output paths must be distinct and may not overwrite input reports")
    runs, excluded = [], []
    for path in sorted(paths):
        pattern = next((pattern for pattern in args.exclude
                        if fnmatch.fnmatchcase(str(path), pattern) or fnmatch.fnmatchcase(path.parent.name, pattern)), None)
        if pattern:
            item = {"report": str(path), "pattern": pattern}
            try:
                item["sha256"] = digest(path)
                report = json.loads(path.read_text(encoding="utf-8"))
                item.update(report_passed=report.get("passed"), error=report.get("error"))
            except (OSError, ValueError, TypeError, AttributeError) as error:
                item["error"] = str(error)
            excluded.append(item)
        else:
            runs.append(summarize(path, not args.skip_profiles))
    harness_variants = {}
    strata = defaultdict(list)
    for run in runs:
        provenance = run.get("provenance", {})
        hashes = provenance.get("harness_sha256")
        if hashes:
            identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
            harness_variants[identity] = hashes
        if run.get("provenance_stratum_sha256"):
            strata[run["provenance_stratum_sha256"]].append(run["run_id"])
    data = {"schema_version": 1, "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "summarizer_sha256": digest(__file__), "runs": runs, "excluded": excluded,
            "discovery_errors": discovery_errors, "harness_variants": harness_variants,
            "multiple_harness_versions": len(harness_variants) > 1,
            "provenance_strata": dict(strata), "automatic_timing_comparisons": False,
            "milestone_verdict": "not_assessed", "notes": CAVEATS}
    for path in (args.output_json, args.output_md):
        path.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    args.output_md.write_text(markdown(data), encoding="utf-8")
    print(json.dumps({"runs": len(runs), "performance_eligible": sum(run["performance_eligible"] for run in runs),
                      "excluded": len(excluded), "discovery_errors": len(discovery_errors),
                      "json": str(args.output_json), "markdown": str(args.output_md),
                      "milestone_verdict": "not_assessed"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
