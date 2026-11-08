"""Summarize the three explicit N2 role archives on C:, without a gate verdict."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tarfile


ROLES = ("ref", "private", "native")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def interval(value):
    return (list(value) if isinstance(value, (list, tuple)) and len(value) == 2
            and all(number(v) is not None for v in value) and value[0] <= value[1] else None)


def ratio_bounds(candidate, baseline):
    candidate, baseline = interval(candidate), interval(baseline)
    if candidate is None or baseline is None or baseline[0] <= 0:
        return {"status": "unknown", "bounds": None,
                "reason": "Requires valid intervals and a positive baseline lower bound"}
    return {"status": "known", "bounds": [candidate[0] / baseline[1], candidate[1] / baseline[0]],
            "interpretation": "native/private diagnostic ratio; upper endpoint is conservative; no gate verdict"}


def json_file(path, provenance, limit=4 * 1024**2):
    require(path.stat().st_size <= limit, "Oversized JSON: " + str(path))
    raw = path.read_bytes()
    provenance[str(path)] = sha(raw)
    return json.loads(raw.decode("utf-8-sig")), raw


def archive_inputs(directory, report_raw, provenance):
    path = directory / "runtime.tar.gz"
    manifest, _ = json_file(Path(str(path) + ".json"), provenance, 16 * 1024**2)
    require(manifest.get("passed") is True, "Archive does not attest a passed diagnostic")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    require(actual == manifest["archive_sha256"], "Runtime archive SHA mismatch")
    provenance[str(path)] = actual
    members = {entry["path"]: entry for entry in manifest["members"]}
    require(len(members) == len(manifest["members"]), "Duplicate manifest members")
    selected = {}
    with tarfile.open(path) as archive:
        names = archive.getnames()
        for name in ("report.json", "manifest.json", "measured/samples.jsonl"):
            require(names.count(name) == 1 and name in members, "Missing/duplicate archived " + name)
            member, expected = archive.getmember(name), members[name]
            require(member.isfile() and member.size == expected["bytes"]
                    and 0 < member.size <= 64 * 1024**2, "Invalid/oversized archived " + name)
            raw = archive.extractfile(member).read()
            require(sha(raw) == expected["sha256"], "Member SHA mismatch: " + name)
            selected[name] = raw
    require(selected["report.json"] == report_raw, "Copied and archived reports differ")
    return json.loads(selected["manifest.json"]), selected["manifest.json"], [
        json.loads(line) for line in selected["measured/samples.jsonl"].splitlines() if line.strip()]


def sample_coverage(samples, clients, hosting):
    expected_processes = 1 if hosting == "native" else clients
    complete, pending, invalid, peaks, durations = 0, 0, [], [], []
    seen_complete = False
    for index, sample in enumerate(samples):
        processes = sample.get("processes", [])
        identities = [(p.get("pid"), p.get("start_ticks")) for p in processes]
        engines = sample.get("engines", {})
        engine_identities = {name: (row.get("pid"), row.get("start_ticks"))
                             for name, row in engines.items()}
        reciprocal = all("engines" not in process or (
            isinstance(process["engines"], list) and all(type(name) is str for name in process["engines"])
            and len(process["engines"]) == len(set(process["engines"]))
            and set(process["engines"]) == {name for name, identity in engine_identities.items()
                if identity == (process.get("pid"), process.get("start_ticks"))}) for process in processes)
        pss = [number(p.get("pss_bytes")) for p in processes]
        valid = (sample.get("eligible") is True and sample.get("expected_engine_count") == clients
                 and sample.get("valid_engine_count") == clients and len(processes) == expected_processes
                 and sample.get("identity_continuity_valid") is True
                 and sample.get("membership_continuity_valid") is True
                 and sample.get("cgroup", {}).get("eligible") is True and not sample.get("discovery_errors")
                 and len(set(identities)) == len(identities) and set(engines) == {str(i) for i in range(clients)}
                 and set(engine_identities.values()) == set(identities)
                 and len(set(engine_identities.values())) == expected_processes and reciprocal
                 and len({pid for pid, _ in identities}) == expected_processes
                 and all(number(pid) is not None and pid > 0 and number(ticks) is not None for pid, ticks in identities)
                 and all(not p.get("errors") for p in processes)
                 and all(not row.get("errors") and (row.get("pid"), row.get("start_ticks")) in identities
                         for row in engines.values())
                 and all(value is not None and value > 0 for value in pss)
                 and sample.get("pss_bytes") == sum(value for value in pss if value is not None))
        if valid:
            complete += 1
            seen_complete = True
            peaks.append(sample["pss_bytes"])
        elif (not seen_complete and sample.get("eligible") is False
              and sample.get("identity_continuity_valid") is True
              and sample.get("membership_continuity_valid") is True
              and sample.get("cgroup", {}).get("eligible") is True
              and not sample.get("discovery_errors")
              and any(row.get("errors") for row in engines.values())
              and all(error == "MissingEngine" for row in engines.values()
                      for error in row.get("errors", {}).values())):
            pending += 1
        else:
            invalid.append(index)
        duration = number(sample.get("sample_duration_ns"))
        if duration is not None:
            durations.append(duration / 1e9)
    return {"samples": len(samples), "complete_unique_process_samples": complete,
            "pending_startup_samples": pending, "invalid_sample_indices": invalid,
            "expected_unique_processes": expected_processes,
            "verified_peak_pss_bytes": max(peaks) if peaks else None,
            "max_sample_read_seconds": max(durations) if durations else None,
            "interpretation": "Sequential unique-process PSS reads, including post-build final sample; not retained heap or exact simultaneous peak"}


def delta(before, after):
    a, b = number(before), number(after)
    return b - a if a is not None and b is not None and b >= a else None


def pressure_summary(start, end):
    valid = (start.get("eligible") is True and end.get("eligible") is True
             and start.get("directory_identity") is not None
             and start.get("directory_identity") == end.get("directory_identity"))
    counters = {}
    for group, keys in (("cpu.stat", ("nr_periods", "nr_throttled", "throttled_usec")),
                        ("memory.events", ("low", "high", "max", "oom", "oom_kill")),
                        ("memory.stat", ("pgfault", "pgmajfault", "pgscan", "pgsteal"))):
        counters[group] = {key: delta((start.get(group) or {}).get(key), (end.get(group) or {}).get(key))
                           if valid else None for key in keys}
    pressure = {group: {kind: {"total_stall_usec_delta": delta(
        ((start.get(group) or {}).get(kind) or {}).get("total"),
        ((end.get(group) or {}).get(kind) or {}).get("total")) if valid else None,
        "end_avg10": ((end.get(group) or {}).get(kind) or {}).get("avg10")}
        for kind in ("some", "full")} for group in ("cpu.pressure", "memory.pressure", "io.pressure")}
    return {"endpoints_valid": valid, "counter_deltas": counters, "pressure": pressure,
            "end_memory_bytes": {key: (end.get("memory.stat") or {}).get(key)
                                 for key in ("anon", "file", "kernel", "shmem")},
            "end_memory_current_bytes": end.get("memory.current"),
            "end_scope_peak_bytes_includes_preparation": end.get("memory.peak")}


def result_proof(results, clients, reference):
    require(len(results) == clients and sorted(row["index"] for row in results) == list(range(clients)),
            "Measured results do not cover expected engines")
    intervals, outputs = [], []
    for row in results:
        require(row.get("exit_code") == 0 and row.get("timed_out") is False
                and row.get("behavior", {}).get("passed") is True and row.get("policy_verified") is True,
                "Build behavior/policy proof missing")
        require(reference or (row.get("digest_oracle", {}).get("required") is True
                              and row["digest_oracle"].get("matches") is True), "Independent digest oracle missing")
        identity = row["bep_identity"]
        require(identity["index"] == row["index"] and re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identity["build_uuid"]), "Invalid BEP identity")
        require(0 < identity["start_time_millis"] < identity["finish_time_millis"], "Invalid BEP interval")
        require(re.fullmatch(r"[0-9a-f]{64}", row["output"]["sha256"]), "Missing output digest")
        intervals.append(identity)
        outputs.append({"index": row["index"], "sha256": row["output"]["sha256"],
                        "behavior_passed": True, "digest_oracle": row["digest_oracle"],
                        "client_wall_seconds": delta(row.get("started_ns"), row.get("exited_ns")) / 1e9
                        if delta(row.get("started_ns"), row.get("exited_ns")) is not None else None})
    require(len({row["build_uuid"] for row in intervals}) == clients, "Duplicate BEP UUID")
    overlap = (min(row["finish_time_millis"] for row in intervals)
               - max(row["start_time_millis"] for row in intervals)) / 1000
    require(overlap > 0, "No all-client BEP overlap")
    return {"all_clients_overlap_seconds": overlap, "unique_build_uuids": clients,
            "intervals": intervals, "outputs": outputs}


def validate_result_bindings(intervals, fixture_rows, final):
    final_pids = {row["pid"] for row in final["processes"]}
    require(all(row["workspace"] == fixture_rows[row["index"]]["workspace"]
                and row["server_pid"] in final_pids
                and row["server_pid"] == final["engines"][str(row["index"])]["pid"]
                for row in intervals), "BEP workspace/PID proof differs")


def summarize(directory, role):
    provenance, errors = {}, []
    result = {"role": role, "diagnostic_only": True, "performance_eligible": False,
              "milestone_verdict": None, "errors": errors, "input_sha256": provenance}
    if not (directory / "report.json").is_file():
        result.update(status="missing_report", diagnostic_verified=False)
        return result
    report, raw = json_file(directory / "report.json", provenance)
    measured = report.get("measured", {})
    accounting = measured.get("build_harness_cpu_accounting", {})
    def cpu_bounds(name, status):
        value = interval(accounting.get(name)) if accounting.get(status) == "known" else None
        return [v / 1e9 for v in value] if value is not None else None
    reads = {row["label"]: row["snapshot"] for row in measured.get("scope_reads", [])}
    preparation = [{"index": row.get("index"), "exit_code": row.get("exit_code"),
                    "client_wall_seconds": delta(row.get("started_ns"), row.get("exited_ns")) / 1e9
                    if delta(row.get("started_ns"), row.get("exited_ns")) is not None else None}
                   for row in report.get("preparation", [])]
    result.update(status="reported_pass" if report.get("passed") else "reported_failure",
        reported_errors={key: report[key] for key in ("error", "cleanup_error", "lifecycle_accounting_error") if key in report},
        hosting=report.get("hosting"), state=report.get("state"), clients=report.get("clients"),
        raw_scope_cpu_seconds=number(measured.get("build_scope_cpu_seconds")),
        python_harness_cpu_seconds_bounds=cpu_bounds("harness_cpu_ns_bounds", "status"),
        non_harness_cpu_seconds_bounds=cpu_bounds("non_harness_scope_cpu_ns_bounds", "non_harness_status"),
        cpu_accounting_status=accounting,
        daemon_cpu_seconds=number(measured.get("daemon_cpu_seconds")),
        daemon_cpu_endpoint_note=measured.get("daemon_cpu_endpoint_note"),
        reported_peak_daemon_pss_bytes=number(measured.get("sampled_peak_daemon_pss_bytes")),
        build_clients_wall_seconds=number(measured.get("build_clients_wall_seconds")),
        end_to_end_wall_seconds=number(measured.get("end_to_end_wall_seconds")),
        end_to_end_scope_cpu_seconds=number(measured.get("end_to_end_scope_cpu_seconds")),
        observer_sample_thread_cpu_seconds=number(measured.get("observer_sample_thread_cpu_ns")),
        observer_scope_thread_cpu_seconds=number(measured.get("observer_scope_thread_cpu_ns")),
        pressure=pressure_summary(reads.get("build-start", {}), reads.get("build-end", {})),
        preparation={"build_commands": preparation, "scope_cpu_seconds": None,
                     "reason": "No separate preparation scope endpoints; command wall excludes identity/shutdown calls"},
        lifecycle_scope_cpu_seconds=number(report.get("lifecycle_scope_cpu_seconds")),
        lifecycle_scope_peak_bytes=report.get("lifecycle_scope_end", {}).get("memory.peak"),
        metric_notes=report.get("metric_notes", []))
    for name in ("observer_sample_thread_cpu_seconds", "observer_scope_thread_cpu_seconds"):
        if result[name] is not None:
            result[name] /= 1e9
    try:
        guard, _ = json_file(directory / "guard/result.json", provenance)
        policy, policy_raw = json_file(directory / "guard/policy.json", provenance)
        scope = report["scope"]
        require(guard.get("status") == "completed" and guard.get("exit_code") == 0
                and guard["final_scope_cleanup"].get("verified") is True
                and guard["final_scope_cleanup"].get("present") is False
                and guard["final_scope_cleanup"].get("signalled") is False, "Final guard not successful/absent")
        require(guard["scope"]["unit"] == scope["identity"]["Id"] == policy["scope"]["unit"]
                and guard["scope"]["invocation_id"] == scope["identity"]["InvocationID"]
                and sha(policy_raw) == scope["guard_policy_sha256"], "Guard/report/policy binding differs")
        config, config_raw, samples = archive_inputs(directory, raw, provenance)
        require(sha(config_raw) == report["manifest_sha256"]
                and policy["command_sha256"] == config["guard_command_sha256"], "Manifest/command binding differs")
        result["pss_coverage"] = sample_coverage(samples, report["clients"], report["hosting"])
        require(len(samples) == measured["samples"] and not result["pss_coverage"]["invalid_sample_indices"]
                and result["pss_coverage"]["verified_peak_pss_bytes"] == result["reported_peak_daemon_pss_bytes"]
                and result["reported_peak_daemon_pss_bytes"] is not None, "PSS sample coverage/peak differs")
        require(report.get("passed") is True and measured.get("passed") is True
                and report.get("owned_daemons_absent") is True and report.get("source_and_binary_unchanged") is True
                and report.get("reference_only") is (role == "ref")
                and report.get("clients") == 2 and report.get("state") == "restart"
                and report.get("hosting") == ("native" if role == "native" else "private"), "Role/run correctness contract failed")
        result["output_and_overlap_proof"] = result_proof(measured["results"], report["clients"], role == "ref")
        fixture_rows = {row["index"]: row for row in report["fixture"]["worktrees"]}
        validate_result_bindings(result["output_and_overlap_proof"]["intervals"], fixture_rows, measured["final"])
        require(result["pressure"]["endpoints_valid"], "Build scope endpoints are incomplete")
        result["matching_contract"] = {key: report.get(key) for key in
            ("candidate_manifest_sha256", "comparison_policy_sha256", "harness_sha256", "history_contract",
             "sample_interval_seconds", "state", "scenario", "clients")}
        result["matching_contract"].update(scope_limits=scope["limits"],
            fixture={key: report["fixture"][key] for key in
                     ("source_revision", "projection_archive_sha256", "private_tree", "target")},
            fixture_inputs=[{key: row.get(key) for key in ("index", "variant", "behavior_variant",
                "compilation_mode", "source_sha256", "build_sha256", "extra_targets")}
                for row in sorted(fixture_rows.values(), key=lambda row: row["index"])])
        result["oracle_report_sha256"] = config.get("oracle_report", {}).get("sha256")
    except (OSError, ValueError, KeyError, TypeError, tarfile.TarError) as error:
        errors.append(type(error).__name__ + ": " + str(error))
    result["diagnostic_verified"] = not errors and report.get("passed") is True
    return result


def comparisons(rows):
    indexed = {row["role"]: row for row in rows}
    ref, private, native = (indexed[role] for role in ROLES)
    reason = None
    if not all(row.get("diagnostic_verified") for row in (ref, private, native)):
        reason = "All three roles must have verified archived diagnostics"
    elif private["matching_contract"] != native["matching_contract"]:
        reason = "Private/native contracts differ; no automatic timing comparison"
    elif any(row.get("oracle_report_sha256") != next(value for path, value in ref["input_sha256"].items()
             if path.endswith("report.json")) for row in (private, native)):
        reason = "Comparison oracle differs from this reference"
    result = {"diagnostic_only": True, "milestone_verdict": None, "blocked_reason": reason}
    if reason is None:
        result["non_harness_cpu_native_over_private"] = ratio_bounds(
            native["non_harness_cpu_seconds_bounds"], private["non_harness_cpu_seconds_bounds"])
        for key in ("raw_scope_cpu_seconds", "daemon_cpu_seconds", "build_clients_wall_seconds", "reported_peak_daemon_pss_bytes"):
            result[key + "_native_over_private"] = ratio_bounds(
                [native[key], native[key]], [private[key], private[key]])
    return result


def markdown(summary):
    def show(value):
        return "unknown" if value is None else (f"{value:.3f}" if type(value) in (int, float) else str(value))
    lines = ["# Native N2 diagnostics", "", "Single trials; no performance or milestone verdict. Reference is correctness-only.", "",
             "| Role | Verified | Build wall s | Raw scope CPU s | Python CPU bounds s | Non-harness CPU bounds s | Daemon CPU s | PSS MiB | Complete samples |",
             "| --- | --- | ---: | ---: | --- | --- | ---: | ---: | --- |"]
    for row in summary["roles"]:
        coverage = row.get("pss_coverage", {})
        bounds = lambda key: "unknown" if row.get(key) is None else "–".join(show(v) for v in row[key])
        pss = row.get("reported_peak_daemon_pss_bytes")
        lines.append("| " + " | ".join([row["role"], str(row["diagnostic_verified"]), show(row.get("build_clients_wall_seconds")),
            show(row.get("raw_scope_cpu_seconds")), bounds("python_harness_cpu_seconds_bounds"), bounds("non_harness_cpu_seconds_bounds"),
            show(row.get("daemon_cpu_seconds")), show(pss / 1024**2 if pss is not None else None),
            f"{coverage.get('complete_unique_process_samples', 'unknown')}/{coverage.get('samples', 'unknown')}"]) + " |")
    for row in summary["roles"]:
        if row.get("errors"):
            lines += ["", row["role"] + " evidence errors: " + "; ".join(row["errors"])]
    lines += ["", "## Preparation and lifecycle", "",
              "| Role | Preparation command walls s | Lifecycle scope CPU s | All-client BEP overlap s |",
              "| --- | --- | ---: | ---: |"]
    for row in summary["roles"]:
        prep = row.get("preparation", {}).get("build_commands", [])
        lines.append("| " + " | ".join([row["role"], ", ".join(show(p["client_wall_seconds"]) for p in prep) or "unknown",
            show(row.get("lifecycle_scope_cpu_seconds")), show(row.get("output_and_overlap_proof", {}).get("all_clients_overlap_seconds"))]) + " |")
    lines += ["", "Preparation CPU is not separately measured. Lifecycle CPU includes fixture/seed work, preparation, measured work, verification and shutdown.",
              "Non-harness bounds include clients/daemons/compiler descendants and do not remove observer perturbation. Daemon endpoints extend beyond the frozen build scope window.",
              "PSS is a sampled unique-process peak including boundary reads; cgroup peaks include preparation. Full pressure, output/UUID proof and input hashes are in the JSON.", "",
              "Comparison: `" + json.dumps(summary["comparison"], sort_keys=True) + "`", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--series", default="native-n2-probe01")
    parser.add_argument("--output", type=Path, required=True, help="New C: output prefix; writes .json and .md")
    args = parser.parse_args()
    directory, output = args.evidence_root.resolve(strict=True), args.output.resolve()
    require(directory.drive.upper() == output.drive.upper() == "C:", "Only preserved C: inputs/outputs are supported")
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,31}", args.series), "Invalid series")
    paths = [output.with_suffix(suffix) for suffix in (".json", ".md")]
    require(all(not path.exists() for path in paths), "Output exists; refusing overwrite")
    rows = [summarize(directory / (args.series + "-" + role), role) for role in ROLES]
    summary = {"schema_version": 1, "series": args.series, "diagnostic_only": True, "performance_eligible": False,
               "milestone_verdict": None, "analyzer_sha256": sha(Path(__file__).read_bytes()),
               "roles": rows, "comparison": comparisons(rows)}
    for path, text in zip(paths, (json.dumps(summary, indent=2) + "\n", markdown(summary))):
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    print(json.dumps({"outputs": [str(path) for path in paths], "verified_roles": sum(row["diagnostic_verified"] for row in rows),
                      "milestone_verdict": None}))


if __name__ == "__main__":
    main()
