#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Measure two Datadog Bazel servers sharing SkyCache, with independent feature edits.

Two additional worktrees provide cache-disabled controls. Edited controls have no shared action
disk cache, so a bad result from a SkyCache build cannot satisfy their changed actions. Logs and
raw BEP remain local; inspect BEP environment fields before sharing.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from datadog_probe import process_memory
from storage_preflight import check_storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--resolved-lockfile", required=True, type=Path)
    parser.add_argument("--action-cache", required=True, type=Path)
    parser.add_argument("--lock-file", required=True, type=Path)
    parser.add_argument("--java-home", type=Path,
                        help="Use the same valid JDK distribution for every local_java_repository")
    parser.add_argument("--seed-cache", type=Path,
                        help="Copy a completed cache from other worktrees; require hits in both fresh servers")
    parser.add_argument("--restart-controls", action="store_true",
                        help="Measure cache-off restarts with populated output bases at base, edited and reverted states")
    parser.add_argument("--resume", action="store_true",
                        help="Revalidate saved phases and continue this probe's existing worktrees")
    args = parser.parse_args()
    binary = args.bazel.resolve(strict=True)
    root = args.root.resolve()
    initial_storage = check_storage([root, args.action_cache])
    root.mkdir(parents=True, exist_ok=args.resume)
    requested_revision = subprocess.check_output(
        ["git", "-C", str(args.repository), "rev-parse", args.revision], text=True).strip()
    java_home = args.java_home.resolve(strict=True) if args.java_home else None
    if java_home:
        assert (java_home / "bin/java").is_file(), java_home
    seed_cache = args.seed_cache.resolve(strict=True) if args.seed_cache else None
    seed_publications = {}
    if seed_cache:
        seed_publications = {
            str(p.relative_to(seed_cache)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(seed_cache.rglob("*.manifest"))
        }
        assert seed_publications, "Seed must contain completed publications from other worktrees"
    names = ("a", "b", "control-a", "control-b")
    source_path = Path("cmd/cluster-agent/subcommands/version/command.go")
    originals = {}
    for name in names:
        workspace = root / name
        if not args.resume:
            subprocess.run(["git", "-C", str(args.repository), "worktree", "add", "--detach",
                            str(workspace), args.revision], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            shutil.copy2(args.resolved_lockfile, workspace / "MODULE.bazel.lock")
        actual = subprocess.check_output(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
        assert actual == requested_revision, (workspace, actual)
        originals[name] = subprocess.check_output(
            ["git", "-C", str(workspace), "show", f"HEAD:{source_path}"], text=True)
        assert originals[name].count('version.MakeCommand("Cluster Agent")') == 1
    revision = subprocess.check_output(["git", "-C", str(root / "a"), "rev-parse", "HEAD"], text=True).strip()
    protocol = {
        "binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_revision": revision,
        "lock_sha256": hashlib.sha256(args.resolved_lockfile.read_bytes()).hexdigest(),
        "initial_storage": initial_storage,
        "memory_scope": "combined process-tree RSS/PSS of both servers, clients and descendants; 200 ms samples; excludes page cache and probe",
        "resources": "two servers; each Xmx4g, jobs=2, local_resources memory=3072",
        "controls": "separate worktrees/output bases; edited actions execute without shared disk cache",
        "restart_controls": args.restart_controls,
        "java_home": str(java_home) if java_home else None,
        "seed_cache": str(seed_cache) if seed_cache else None,
        "seed_publications": seed_publications,
        "feature_edit": str(source_path), "phases": [],
    }
    if args.resume:
        saved = json.loads((root / "results.json").read_text())
        for key in ("binary_sha256", "source_revision", "lock_sha256", "resources"):
            assert saved[key] == protocol[key], (key, saved[key], protocol[key])
        assert saved.get("restart_controls", False) == args.restart_controls
        for key in ("java_home", "seed_cache", "seed_publications"):
            assert saved.get(key, {} if key == "seed_publications" else None) == protocol[key], key
        protocol = saved
        protocol.setdefault("resumptions", []).append({
            "completed_phases": [p["phase"] for p in saved["phases"]],
            "storage_preflight": initial_storage,
        })
        for phase in protocol["phases"]:
            if phase["phase"] == "concurrent-edited-writers":
                # The edit phase already published this graph. The next restarted build is a
                # reader, so demanding novel writes here incorrectly rejects deduplication.
                phase["original_phase_label"] = phase["phase"]
                phase["phase"] = "first-restarted-edited-readers"
                protocol["harness_correction"] = (
                    "Require publication during edited-warm-graphs and shared analysis hits "
                    "during its subsequent restarted builds; do not require redundant writes.")
    saved_phases = {p["phase"]: p for p in protocol["phases"]}
    active_clients = {}
    client_lock = threading.Lock()
    markers = {n: f"SkyCache-feature-{n}".encode() for n in ("a", "b")}

    def environment(name, phase):
        return dict(os.environ, PWD=str(root / name), BAZEL_REAL=str(binary),
                    BAZELISK_SKIP_WRAPPER="true", DD_BAZEL_REMOTE_CACHE="off",
                    SKYCACHE_UNRELATED_SESSION=f"{name}-{phase}")

    def startup(name):
        return [str(root / name / "tools/bazel"), "--output_base=" + str(root / ("out-" + name)),
                "--host_jvm_args=-Xmx4g", "--max_idle_secs=120"]

    def shutdown(name):
        subprocess.run(startup(name) + ["shutdown"], cwd=root / name,
                       env=environment(name, "shutdown"), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False, timeout=90)

    def shutdown_pair(pair_names):
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(shutdown, pair_names))

    def pids(pair_names):
        with client_lock:
            result = list(active_clients.values())
        for name in pair_names:
            try:
                result.append(int((root / ("out-" + name) / "server/server.pid.txt").read_text()))
            except (FileNotFoundError, ValueError):
                pass
        return result

    def artifact_evidence(artifact):
        found = {n: False for n in markers}
        digest = hashlib.sha256()
        tail = b""
        with artifact.open("rb") as stream:
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
                window = tail + chunk
                for name, marker in markers.items():
                    found[name] |= marker in window
                tail = window[-64:]
        return {"output_sha256": digest.hexdigest(), "feature_markers": found}

    def build(name, phase, cache, disk_cache):
        output_base = root / ("out-" + name)
        stats = output_base / "local-skycache.json"
        stats.unlink(missing_ok=True)
        argv = startup(name) + [
            "build", "//cmd/cluster-agent:cluster-agent", "--config=no-remote-cache",
            "--jobs=2", "--local_resources=memory=3072", "--lockfile_mode=off",
            "--experimental_strict_repo_env", "--color=no", "--curses=no",
            "--disk_cache=" + (str(args.action_cache.resolve()) if disk_cache else ""),
            "--build_event_json_file=" + str(root / f"{phase}-{name}.bep.json"),
        ]
        if java_home:
            argv += ["--repo_env=JAVA_HOME=" + str(java_home)]
        if cache:
            argv += ["--experimental_local_skycache=" + str(root / "cache"),
                     "--experimental_active_directories=cmd/cluster-agent"]
        log_path = root / f"{phase}-{name}.log"
        started = time.monotonic()
        with log_path.open("w") as log:
            process = subprocess.Popen(argv, cwd=root / name, env=environment(name, phase),
                                       stdout=log, stderr=subprocess.STDOUT)
            with client_lock:
                active_clients[name] = process.pid
            try:
                code = process.wait()
            finally:
                with client_lock:
                    active_clients.pop(name, None)
        seconds = time.monotonic() - started
        text = log_path.read_text()
        hits = re.findall(r"CONFIGURED_TARGET: (\d+)/(\d+)", text)
        row = {
            "worktree": name, "argv": argv, "exit_code": code, "seconds": seconds,
            "analysis_hits": sum(int(a) for a, _ in hits),
            "analysis_hit_summary_present": bool(hits),
            "cache_disabled": "Local SkyCache disabled:" in text,
            "publication_failures": re.findall(r"Local SkyCache publication (?:discarded|skipped): (.*)", text),
        }
        if cache and stats.exists():
            row["skycache"] = json.loads(stats.read_text())
        if code == 0:
            artifacts = list((output_base / "execroot/_main/bazel-out").glob(
                "*/bin/cmd/cluster-agent/cluster-agent_/cluster-agent"))
            if len(artifacts) == 1:
                row.update(artifact_evidence(artifacts[0]))
            else:
                row["artifact_error"] = f"Expected one executable, found {len(artifacts)}"
        return row

    def pair(phase, pair_names, *, cache, expected=None, disk_cache=True,
             require_hits=False, require_writes=False, edited=False):
        if phase in saved_phases:
            record = saved_phases[phase]
            assert {r["worktree"] for r in record["builds"]} == set(pair_names)
            validate(record["builds"], cache, expected, require_hits, require_writes, edited)
            print(json.dumps({"resumed_validated_phase": phase}), flush=True)
            return {r["worktree"].rsplit("-", 1)[-1]: r["output_sha256"]
                    for r in record["builds"]}
        storage = check_storage([root, args.action_cache])
        samples = []
        finished = threading.Event()
        started = time.monotonic()

        def sample():
            while not finished.wait(.2):
                samples.append([time.monotonic() - started, *process_memory(pids(pair_names))])

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(build, name, phase, cache, disk_cache) for name in pair_names]
                rows = [future.result() for future in futures]
        finally:
            finished.set()
            sampler.join()
        record = {
            "phase": phase, "seconds": time.monotonic() - started, "builds": rows,
            "peak_combined_pss_kib": max((s[2] for s in samples), default=0),
            "peak_combined_rss_kib": max((s[1] for s in samples), default=0),
            "storage_preflight": storage,
        }
        protocol["phases"].append(record)
        (root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
        (root / f"{phase}.samples.json").write_text(json.dumps(samples) + "\n")
        print(json.dumps(record), flush=True)
        validate(rows, cache, expected, require_hits, require_writes, edited)
        return {row["worktree"].rsplit("-", 1)[-1]: row["output_sha256"] for row in rows}

    def validate(rows, cache, expected, require_hits, require_writes, edited):
        for row in rows:
            assert row["exit_code"] == 0 and "output_sha256" in row, row
            feature = row["worktree"].rsplit("-", 1)[-1]
            assert row["feature_markers"] == {n: edited and n == feature for n in markers}, row
            if expected:
                assert row["output_sha256"] == expected[feature], row
            if cache:
                assert not row["cache_disabled"] and not row["skycache"]["failed"], row
            if require_hits:
                assert row["analysis_hits"] > 0 and row["skycache"]["shared_reads"] > 0, row
            if require_writes:
                assert row["skycache"]["writes"] > 0 and row["skycache"]["published"], row

    def edit(pair_names):
        for name in pair_names:
            feature = name.rsplit("-", 1)[-1]
            changed = originals[name].replace(
                'version.MakeCommand("Cluster Agent")',
                f'version.MakeCommand("Cluster Agent SkyCache-feature-{feature}")')
            (root / name / source_path).write_text(changed)

    with args.lock_file.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if seed_cache:
                if not args.resume:
                    shutil.copytree(seed_cache, root / "cache")
                    for name, digest in seed_publications.items():
                        assert hashlib.sha256((root / "cache" / name).read_bytes()).hexdigest() == digest
                else:
                    assert (root / "cache").is_dir()
            baseline = pair("control-base", ("control-a", "control-b"), cache=False)
            assert baseline["a"] == baseline["b"]
            shutdown_pair(("control-a", "control-b"))
            if args.restart_controls:
                pair("restarted-control-base", ("control-a", "control-b"), cache=False, expected=baseline)
                shutdown_pair(("control-a", "control-b"))
            if seed_cache:
                pair("concurrent-fresh-cross-worktree-readers", ("a", "b"), cache=True,
                     expected=baseline, require_hits=True)
            else:
                pair("concurrent-base-writers", ("a", "b"), cache=True, expected=baseline, require_writes=True)
            pair("warm-base", ("a", "b"), cache=True, expected=baseline)
            shutdown_pair(("a", "b"))
            pair("restarted-base-readers", ("a", "b"), cache=True, expected=baseline, require_hits=True)
            edit(("a", "b"))
            feature_outputs = pair("edited-warm-graphs", ("a", "b"), cache=True,
                                   disk_cache=False, edited=True, require_writes=True)
            assert feature_outputs["a"] != feature_outputs["b"]
            assert all(feature_outputs[n] != baseline[n] for n in ("a", "b"))
            shutdown_pair(("a", "b"))
            edit(("control-a", "control-b"))
            pair("control-edits-no-disk-cache", ("control-a", "control-b"), cache=False,
                 expected=feature_outputs, disk_cache=False, edited=True)
            shutdown_pair(("control-a", "control-b"))
            if args.restart_controls:
                pair("restarted-control-edited", ("control-a", "control-b"), cache=False,
                     expected=feature_outputs, edited=True)
                shutdown_pair(("control-a", "control-b"))
            pair("first-restarted-edited-readers", ("a", "b"), cache=True, expected=feature_outputs,
                 require_hits=True, edited=True)
            shutdown_pair(("a", "b"))
            pair("restarted-edited-readers", ("a", "b"), cache=True, expected=feature_outputs,
                 require_hits=True, edited=True)
            shutdown_pair(("a", "b"))
            for name in names:
                (root / name / source_path).write_text(originals[name])
            pair("reverted-restarted-readers", ("a", "b"), cache=True, expected=baseline, require_hits=True)
            if args.restart_controls:
                shutdown_pair(("a", "b"))
                pair("reverted-restarted-control", ("control-a", "control-b"), cache=False, expected=baseline)
                shutdown_pair(("control-a", "control-b"))
            protocol["passed"] = True
            (root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
        finally:
            shutdown_pair(names)


if __name__ == "__main__":
    main()
