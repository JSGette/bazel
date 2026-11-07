"""Read a snapshot of explicit completed batch archives; never access runtime paths or decide a gate."""
import argparse, hashlib, json, re, tarfile, zipfile
from datetime import datetime, timezone
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def packed(path, names):
    manifest = json.loads(Path(str(path) + ".json").read_text())
    require(digest(path.read_bytes()) == manifest["archive_sha256"], "Archive hash mismatch")
    members = {m["path"]: m for m in manifest["members"]}
    names = names + (["output-base-binding.json"] if "output-base-binding.json" in members else [])
    with (zipfile.ZipFile(path) if path.suffix == ".zip" else tarfile.open(path)) as archive:
        result = {}
        for name in names:
            data = archive.read(name) if path.suffix == ".zip" else archive.extractfile(name).read()
            require(len(data) == members[name]["bytes"] and digest(data) == members[name]["sha256"], "Member hash mismatch: " + name)
            result[name] = json.loads(data)
    return result, manifest


def ratio(after, before):
    return after / before if isinstance(after, (int, float)) and isinstance(before, (int, float)) and before > 0 else None


def complete(values):
    return sum(values) if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values) else None


def inspect(batch, item, declared, matrix, progress):
    archive, manifest = packed(batch / (item["id"] + "-run.tar.gz"), ["report.json", "measured-resources.json"])
    require(next(m["sha256"] for m in manifest["members"] if m["path"] == "report.json") == item["report_sha256"], "Report hash differs from progress")
    require(manifest["archive_sha256"] == item["archive_sha256"], "Archive differs from progress")
    report, resources = archive["report.json"], archive["measured-resources.json"]
    summary_path = batch / (item["id"] + "-summary.json")
    document = json.loads(summary_path.read_text()); summary = document["runs"][0]
    require(document["summarizer_sha256"] == matrix["local_sha256"]["summarize.py"] and summary["report_sha256"] == item["report_sha256"], "Summary provenance differs")
    guard, _ = packed(batch / (item["id"] + "-guard.zip"), ["policy.json", "result.json"])
    policy, outcome = guard["policy.json"], guard["result.json"]
    require(outcome["status"] == "completed" and outcome["exit_code"] == 0 and outcome["final_scope_cleanup"]["verified"], "Guard or cleanup failed")
    require(resources["cgroup"].split("/")[-1] == outcome["scope"]["unit"], "Guard/report scope mismatch")
    caps = {k: policy[k] for k in ("memory_max_mib", "cpu_quota_percent")}
    require(all(caps[k] == progress[k] for k in caps), "Resource caps differ from batch")
    fixture = matrix["fixtures"][declared["fixture"]]; count = fixture["clients"]
    require(report["mode"] == declared["mode"] and report["cache_state"] == declared["state"] and report["fixture"]["clients"] == count and report["fixture"]["scenario"] == fixture["scenario"], "Declared row identity differs")
    binary = matrix["binary"]["vanilla" if declared["mode"] == "vanilla" else "candidate"]["sha256"]
    require(report["binary_sha256"] == binary and report["harness_sha256"] == matrix["harness_sha256"], "Binary/harness identity differs")
    require(report["passed"] and report["measured"]["passed"] and report["measured"]["accounting_passed"] and resources["cgroup_accounted"] and resources["engine_membership_verified"], "Build/accounting failed")
    require(report["measured"]["resources"] == {k: v for k, v in resources.items() if k != "samples"}, "Resource sidecar differs from report")
    coverage = sum(s["engine_pids_sampled"] == count and (s["engine_pss_bytes"] or 0) > 0 for s in resources["samples"])
    require(coverage > 0 and resources["engine_expected_count"] == count, "No complete positive engine PSS sample")
    require(item["performance_eligible"] == summary["performance_eligible"], "Summary eligibility differs from progress")
    require(summary["performance_eligible"] or (declared["reference"] and all(re.fullmatch(r"digest_oracle_missing_or_failed:\d+", reason) for reason in summary["ineligibility_reasons"])), "Correctness/eligibility checks incomplete")
    scalar = lambda group, key: summary.get(group, {}).get(key, {}).get("complete_sum")
    bep = [r["bep"]["build_metrics"] for r in report["measured"]["results"]]
    actions = [b.get("actionSummary", {}) for b in bep]; runners = [{r["name"]: r["count"] for r in a.get("runnerCount", [])} for a in actions]
    values = lambda group, key: complete([int(b[group][key]) if key in b.get(group, {}) else None for b in bep])
    counters = [{"index": c["index"], "bep": c["remote_analysis_cache_counters"], "skycache": c["skycache_counters"]} for c in summary["clients"]]
    metrics = {"wall_s": summary["cohort_wall_seconds"], "scope_cpu_s": resources["scope_cpu_seconds"], "engine_cpu_s": resources["engine_observed_cpu_seconds"], "engine_pss_bytes": resources["engine_sampled_peak_pss_bytes"], "analysis_elapsed_s": scalar("client_elapsed_sums", "analysis_elapsed_seconds"), "execution_elapsed_s": scalar("client_elapsed_sums", "execution_elapsed_seconds"), "analysis_cache_hits": complete([c["bep"].get("cacheHits") for c in counters])}
    metrics.update(graph_nodes=values("buildGraphMetrics", "postInvocationSkyframeNodeCount"), targets_configured=values("targetMetrics", "targetsConfigured"), actions_executed=values("actionSummary", "actionsExecuted"), action_cache_hits=complete([a.get("actionCacheStatistics", {}).get("hits") for a in actions]), action_cache_misses=complete([a.get("actionCacheStatistics", {}).get("misses") for a in actions]), internal_runners=complete([r.get("internal", 0) if r else None for r in runners]), other_runners=complete([r["total"] - r.get("internal", 0) if "total" in r else None for r in runners]))
    protocol = {k: report.get(k) for k in ("harness_sha256", "preparation_protocol", "output_base_pool", "output_base_pool_binding_sha256", "environment_digest", "oracle_report", "cache_before_measured")}
    flags = [c["command"].get("selected_flags", []) for c in summary["clients"]]
    common_flags = sorted({tuple(f for f in row if f.split("=", 1)[0] not in ("--experimental_local_skycache", "--experimental_remote_skycache", "--experimental_analysis_cache_idle_retention")) for row in flags})
    binding = archive.get("output-base-binding.json")
    require(not report.get("output_base_root") or (binding and digest(json.dumps(binding, sort_keys=True).encode()) == report["output_base_pool_binding_sha256"]), "Pool binding evidence missing or changed")
    protocol.update(caps=caps, selected_flags=flags, common_flags=common_flags, extra_bazel_arguments=report.get("extra_bazel_arguments", []), resource_binding=(binding or {}).get("resource_flags"))
    source = {k: report["fixture"].get(k) for k in ("source_revision", "projection_archive_sha256", "private_tree", "target")}
    return {"id": item["id"], "status": "verified", "mode": report["mode"], "state": declared["state"], "scenario": fixture["scenario"], "clients": count, "reference": declared["reference"], "eligible": summary["performance_eligible"] and not declared["reference"], "ineligibility_reasons": summary["ineligibility_reasons"], "binary_sha256": binary, "report_sha256": item["report_sha256"], "summary_sha256": digest(summary_path.read_bytes()), "archive_sha256": manifest["archive_sha256"], "source": source, "metrics": metrics, "protocol": protocol, "positive_full_engine_samples": coverage, "checked_guard_scope": outcome["scope"], "accounting_verified": True, "cache_counters": counters, "action_summaries": actions, "correctness": [{k: c.get(k) for k in ("index", "behavior_passed", "digest_oracle", "glob_oracle")} for c in summary["clients"]]}


def snapshot(path):
    raw = path.read_bytes(); progress = json.loads(raw); batch = path.parent
    matrix_raw = (batch / "matrix.json").read_bytes(); matrix = json.loads(matrix_raw)
    require(digest(matrix_raw) == progress["manifest_sha256"], "Matrix manifest differs from progress")
    seen = {row["id"]: row for row in progress["rows"]}; rows = []
    for declared in matrix["rows"]:
        item = seen.get(declared["id"], {"id": declared["id"], "status": "not_started"})
        if item["status"] != "checked_and_archived":
            rows.append({"id": item["id"], "status": item["status"], "error": item.get("error")}); continue
        try:
            rows.append(inspect(batch, item, declared, matrix, progress))
        except (OSError, ValueError, KeyError, IndexError, TypeError, tarfile.TarError, zipfile.BadZipFile) as error:
            rows.append({"id": item["id"], "status": "evidence_error", "error": str(error)})
    indexed = {r["id"]: r for r in rows}
    for declared in matrix["rows"]:
        row = indexed[declared["id"]]; reference = indexed.get(declared.get("oracle"), {})
        if row["status"] == "verified" and not declared.get("reference"):
            if reference.get("status") != "verified" or not reference.get("reference") or (row["protocol"].get("oracle_report") or {}).get("sha256") != reference.get("report_sha256"):
                row.update(status="evidence_error", eligible=False, error="Independent oracle does not match a verified reference archive")
    anchors = {(r["mode"], r["clients"]): r for r in rows if r.get("eligible") and r["scenario"] == "identical" and r["state"] == "restart"}
    require(len(anchors) == sum(r.get("eligible", False) and r["scenario"] == "identical" and r["state"] == "restart" for r in rows), "Multiple anchors per cell; select a single-trial batch explicitly")
    pairs, scaling = [], []
    matched = lambda a, b: a["binary_sha256"] == b["binary_sha256"] and a["source"] == b["source"] and all(a["protocol"][k] == b["protocol"][k] for k in ("caps", "harness_sha256", "preparation_protocol", "common_flags", "extra_bazel_arguments", "resource_binding"))
    for n in (1, 10):
        off, on = anchors.get(("candidate-off", n)), anchors.get(("skycache", n))
        if off and on and matched(off, on):
            pairs.append({"off": off["id"], "on": on["id"], "clients": n, "single_trial_on_over_off": {k: ratio(on["metrics"][k], off["metrics"][k]) for k in ("scope_cpu_s", "engine_cpu_s", "engine_pss_bytes", "graph_nodes")}})
    for mode in ("vanilla", "candidate-off", "skycache"):
        one, ten = anchors.get((mode, 1)), anchors.get((mode, 10))
        if one and ten and matched(one, ten):
            scaling.append({"mode": mode, "n1": one["id"], "n10": ten["id"], "single_trial_n10_over_n1": {k: ratio(ten["metrics"][k], one["metrics"][k]) for k in ("engine_cpu_s", "engine_pss_bytes", "graph_nodes")}})
    return {"schema_version": 1, "snapshot_utc": datetime.now(timezone.utc).isoformat(), "aggregator_sha256": digest(Path(__file__).read_bytes()), "progress_sha256": digest(raw), "matrix_sha256": digest(matrix_raw), "batch_status": progress["status"], "coverage": "completed" if all(r["status"] == "verified" for r in rows) else "partial", "milestone_verdict": "not_assessed", "rows": rows, "candidate_on_off": pairs, "n1_n10_scaling": scaling}


def markdown(data):
    cell = lambda x: "unknown" if x is None else (f"{x:.3f}" if isinstance(x, float) else str(x)).replace("|", "\\|")
    lines = ["# M0 matrix snapshot", "", f"Coverage: **{data['coverage']}**. Batch: `{data['batch_status']}`. No automatic milestone verdict.", "", "Each comparison is one trial per arm: descriptive ratios only, no uncertainty estimate or statistical claim. Candidate on/off comparisons use the same binary and only identical/restart anchors; vanilla comparisons span different source versions. Inspect pool history, flags and environment provenance before interpreting ratios.", "", "Scope CPU includes clients/descendants; engine CPU is sampled JVM ticks. Analysis/execution durations are elapsed time, not CPU. PSS excludes compiler workers; page-cache ownership and preparation affect scope memory.", "", "| Row | Mode / N | Eligible | Wall s | Scope CPU s | Engine CPU s | Engine PSS MiB | Graph nodes | Targets configured |", "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in data["rows"]:
        if row["status"] == "verified":
            m = row["metrics"]; values = [row["id"], f"{row['mode']} / {row['clients']}", row["eligible"], m["wall_s"], m["scope_cpu_s"], m["engine_cpu_s"], m["engine_pss_bytes"] / 2**20, m["graph_nodes"], m["targets_configured"]]
            lines.append("| " + " | ".join(map(cell, values)) + " |")
    lines += ["", "| Row | Analysis elapsed s | Execution elapsed s | Executed actions | Action-cache hits | Action-cache misses | Internal runners | Other runners | Analysis-cache hits |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in data["rows"]:
        if row["status"] == "verified":
            lines.append("| " + " | ".join(map(cell, [row["id"]] + [row["metrics"][k] for k in ("analysis_elapsed_s", "execution_elapsed_s", "actions_executed", "action_cache_hits", "action_cache_misses", "internal_runners", "other_runners", "analysis_cache_hits")])) + " |")
    for title, records in (("Same-binary on/off anchors", data["candidate_on_off"]), ("N1 to N10 scaling", data["n1_n10_scaling"])):
        lines += ["", "## " + title, "", "```json", json.dumps(records, indent=2), "```"]
    lines += ["", "## Missing, active or failed rows", ""] + ["- " + r["id"] + ": " + r["status"] + (" — " + r["error"] if r.get("error") else "") for r in data["rows"] if r["status"] != "verified"]
    lines += ["", "JSON preserves checked archive/report hashes, guard/accounting checks, complete-PSS sample counts, correctness oracles, caps, harness, preparation/pool history, effective flags and per-client cache counters. Reference rows remain correctness-only.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("progress", type=Path); parser.add_argument("--output-json", type=Path, required=True); parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args(); data = snapshot(args.progress)
    require(args.output_json.resolve() != args.output_md.resolve(), "Output paths must differ")
    require(not args.output_json.exists() and not args.output_md.exists(), "Outputs must be new")
    for path, content in ((args.output_json, json.dumps(data, indent=2) + "\n"), (args.output_md, markdown(data))):
        with path.open("x", encoding="utf-8") as target:
            target.write(content)
    print(json.dumps({"coverage": data["coverage"], "verified": sum(r["status"] == "verified" for r in data["rows"]), "milestone_verdict": "not_assessed"}))


if __name__ == "__main__":
    main()
