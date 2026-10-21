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
import signal
import subprocess
import threading
import time
import uuid

from datadog_probe import process_memory
from storage_preflight import check_storage



def relocate_copied_symlinks(destination, mappings):
    """Repair absolute links to the copied fixture's known roots without following links.

    Stopped output bases may contain repository aliases and overlay links to their old workspace
    or output base. Preserve unrelated absolute links, relative links and ordinary file contents.
    This prepares a benchmark fixture; it does not change SkyCache path or input validation.
    """
    destination = Path(destination).absolute()
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("Copied output base must be a real directory")
    destination = destination.resolve(strict=True)
    roots = [(Path(old).resolve(strict=True), Path(new).resolve(strict=True))
             for old, new in mappings]
    if any(old == new or destination.is_relative_to(old) for old, new in roots):
        raise ValueError("Source and copied fixture roots must be separate")
    if (destination / "server/server.pid.txt").exists():
        raise ValueError("Copied output base must have no live server")
    roots.sort(key=lambda pair: len(pair[0].parts), reverse=True)
    changes = []
    pending = [destination]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    path = Path(entry.path)
                    original = path.readlink()
                    if not original.is_absolute():
                        continue
                    target = Path(os.path.normpath(original))
                    replacement = next((new / target.relative_to(old)
                                        for old, new in roots if target.is_relative_to(old)), None)
                    if replacement is None:
                        continue
                    temporary = path.with_name(".skycache-relocate-" + uuid.uuid4().hex)
                    # Cleanup owns this temporary only after creation succeeds.
                    temporary.symlink_to(replacement)
                    try:
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    changes.append({"path": str(path.relative_to(destination)),
                                    "before": str(original), "after": str(replacement)})
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
    return sorted(changes, key=lambda row: row["path"])


def watch_storage(paths, finished, active_clients, client_lock, errors, interval=10):
    """Cancel only this probe's live build clients if capacity cannot be verified mid-phase."""
    while not finished.wait(interval):
        try:
            check_storage(paths)
        except Exception as error:
            errors.append({"error": str(error), "observed_unix": time.time()})
            with client_lock:
                clients = list(active_clients.values())
            for pid in clients:
                try:
                    os.kill(pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
            return


def check_build_budget(phases, next_builds, limit):
    """Count saved invocations before launching a phase, including already completed work."""
    used = sum(len(phase["builds"]) for phase in phases)
    if used + next_builds > limit:
        raise ValueError(f"Validation build limit {limit} would be exceeded: {used}+{next_builds}")


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
    parser.add_argument("--base-only", action="store_true",
                        help="Stop after fresh, warm and restarted base builds; omit edit/revert phases")
    parser.add_argument("--restart-controls", action="store_true",
                        help="Measure cache-off restarts with populated output bases at base, edited and reverted states")
    parser.add_argument("--cross-revision-edits", action="store_true",
                        help="First import edited worktrees from baseline-only publications, then test warm reverts/edits")
    parser.add_argument("--cross-revision-smoke", action="store_true",
                        help="Diagnostic: stop after the first warm revert and keep the action disk cache enabled")
    parser.add_argument("--build-timeout-seconds", type=int, default=1800,
                        help="Cancel an owned build client after this deadline; a timeout fails the gate")
    parser.add_argument("--server-idle-seconds", type=int, default=3600,
                        help="Keep warm graphs alive while the independent worktree prepares dependencies")
    parser.add_argument("--resume", action="store_true",
                        help="Revalidate saved phases and continue this probe's existing worktrees")
    parser.add_argument("--reuse-worktrees", action="store_true",
                        help="Reuse this probe's existing worktrees/output bases; preserve old reports")
    parser.add_argument("--results-root", type=Path,
                        help="Separate directory for new reports, logs and memory samples")
    parser.add_argument("--single-seed-writer", action="store_true",
                        help="Seed with A then read with B before concurrent phases, reducing duplicate disk data")
    parser.add_argument("--collector-jar", type=Path,
                        help="After the full matrix, collect inactive data and verify rebuild/reuse")
    parser.add_argument("--reuse-output-bases-from", type=Path,
                        help="Copy stopped output bases from a completed probe into a new fixture")
    parser.add_argument("--repository-cache", type=Path,
                        help="Place native repository downloads and shared contents on this guarded filesystem")
    parser.add_argument("--disable-repository-cache", action="store_true",
                        help="Keep repository downloads in the selected output bases, not the default cache")
    parser.add_argument("--validation-build-limit", type=int, choices=(16, 32), default=32,
                        help="16 keeps controls, warm reuse, edits/reverts and collection recovery; 32 retains repeated restarts")
    args = parser.parse_args()
    compact = args.validation_build_limit == 16
    if compact and (not args.single_seed_writer or not args.restart_controls
                    or not args.cross_revision_edits or not args.collector_jar
                    or args.seed_cache or args.base_only or args.cross_revision_smoke):
        parser.error("16-build validation requires single-seed-writer, restart-controls, cross-revision-edits and collector-jar, without seed-cache/base-only/cross-revision-smoke")
    if args.cross_revision_smoke and (not args.cross_revision_edits or args.base_only):
        parser.error("--cross-revision-smoke requires --cross-revision-edits without --base-only")
    if args.build_timeout_seconds < 1:
        parser.error("--build-timeout-seconds must be positive")
    if args.server_idle_seconds < args.build_timeout_seconds:
        parser.error("--server-idle-seconds must cover the independent build timeout")
    if args.repository_cache and args.disable_repository_cache:
        parser.error("--repository-cache cannot be combined with --disable-repository-cache")
    binary = args.bazel.resolve(strict=True)
    root = args.root.resolve()
    repository_cache = args.repository_cache.resolve() if args.repository_cache else None
    write_paths = [root, args.action_cache] + ([repository_cache] if repository_cache else [])
    initial_storage = check_storage(write_paths)
    root.mkdir(parents=True, exist_ok=args.resume or args.reuse_worktrees)
    results_root = args.results_root.resolve() if args.results_root else root
    if args.reuse_worktrees and results_root == root:
        parser.error("--reuse-worktrees requires a separate --results-root")
    if args.single_seed_writer and args.seed_cache:
        parser.error("--single-seed-writer cannot be combined with --seed-cache")
    if args.reuse_worktrees and args.seed_cache:
        parser.error("--reuse-worktrees cannot overwrite its cache using --seed-cache")
    if args.collector_jar and (not args.java_home or args.base_only or args.cross_revision_smoke):
        parser.error("--collector-jar requires --java-home and the full matrix")
    if results_root != root:
        results_root.mkdir(parents=True, exist_ok=args.resume)
    if args.reuse_worktrees:
        previous = json.loads((root / "results.json").read_text())
        assert previous.get("passed"), "Only reuse a previously completed probe fixture"
    collector_jar = args.collector_jar.resolve(strict=True) if args.collector_jar else None
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
    output_origin = args.reuse_output_bases_from.resolve(strict=True) if args.reuse_output_bases_from else None
    if output_origin:
        if args.reuse_worktrees or output_origin == root:
            parser.error("--reuse-output-bases-from requires a separate new fixture")
        origin_report = json.loads((output_origin / "results.json").read_text())
        assert origin_report.get("passed") and origin_report["source_revision"] == requested_revision
    names = ("a", "b", "control-a", "control-b")
    source_path = Path("cmd/cluster-agent/subcommands/version/command.go")
    originals = {}
    output_relocations = {}
    for name in names:
        workspace = root / name
        if not (args.resume or args.reuse_worktrees):
            subprocess.run(["git", "-C", str(args.repository), "worktree", "add", "--detach",
                            str(workspace), args.revision], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            shutil.copy2(args.resolved_lockfile, workspace / "MODULE.bazel.lock")
        if output_origin and not args.resume:
            origin = output_origin / ("out-" + name)
            destination = root / ("out-" + name)
            assert origin.is_dir() and not destination.exists()
            assert not (origin / "server/server.pid.txt").exists(), "Source server must be stopped"
            with (results_root / ("copy-output-base-" + name + ".log")).open("w") as log:
                subprocess.run(["rsync", "-a", "--stats", "--exclude=/server/",
                    "--exclude=/java.log.*", "--exclude=/command.log", "--exclude=/jvm.out",
                    str(origin) + "/", str(destination) + "/"],
                    stdout=log, stderr=subprocess.STDOUT, check=True)
            output_relocations[name] = relocate_copied_symlinks(destination, [
                (output_origin / name, workspace), (origin, destination)])
        actual = subprocess.check_output(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
        assert actual == requested_revision, (workspace, actual)
        originals[name] = subprocess.check_output(
            ["git", "-C", str(workspace), "show", f"HEAD:{source_path}"], text=True)
        assert originals[name].count('version.MakeCommand("Cluster Agent")') == 1
        if args.reuse_worktrees and not args.resume:
            assert (workspace / source_path).read_text() == originals[name], workspace
            assert (workspace / "MODULE.bazel.lock").read_bytes() == args.resolved_lockfile.read_bytes(), workspace
            modified = subprocess.check_output(
                ["git", "-C", str(workspace), "diff", "--name-only", "HEAD"], text=True).splitlines()
            assert set(modified) <= {"MODULE.bazel.lock"}, (workspace, modified)
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
        "cross_revision_edits": args.cross_revision_edits,
        "cross_revision_smoke": args.cross_revision_smoke,
        "build_timeout_seconds": args.build_timeout_seconds,
        "server_idle_seconds": args.server_idle_seconds,
        "base_only": args.base_only,
        "java_home": str(java_home) if java_home else None,
        "seed_cache": str(seed_cache) if seed_cache else None,
        "seed_publications": seed_publications,
        "reuse_worktrees": args.reuse_worktrees,
        "output_bases_copied_from": str(output_origin) if output_origin else None,
        "copied_output_symlink_relocations": output_relocations,
        "disable_repository_cache": args.disable_repository_cache,
        "repository_cache": str(repository_cache) if repository_cache else None,
        "reused_fixture": str(root) if args.reuse_worktrees else None,
        "single_seed_writer": args.single_seed_writer,
        "collector_jar": str(collector_jar) if collector_jar else None,
        "collector_jar_sha256": hashlib.sha256(collector_jar.read_bytes()).hexdigest() if collector_jar else None,
        "seed_strategy": "One writer followed by independent B reader before concurrent phases" if args.single_seed_writer else "Two simultaneous writers",
        "feature_edit": str(source_path), "phases": [],
    }
    if args.resume:
        saved = json.loads((results_root / "results.json").read_text())
        for key in ("binary_sha256", "source_revision", "lock_sha256", "resources"):
            assert saved[key] == protocol[key], (key, saved[key], protocol[key])
        assert saved.get("restart_controls", False) == args.restart_controls
        assert saved.get("base_only", False) == args.base_only
        assert saved.get("cross_revision_edits", False) == args.cross_revision_edits
        assert saved.get("cross_revision_smoke", False) == args.cross_revision_smoke
        assert saved.get("build_timeout_seconds", 1800) == args.build_timeout_seconds
        assert saved.get("server_idle_seconds", 120) == args.server_idle_seconds
        for key in ("java_home", "seed_cache", "seed_publications", "reuse_worktrees", "single_seed_writer", "collector_jar", "collector_jar_sha256", "output_bases_copied_from", "disable_repository_cache", "repository_cache"):
            assert saved.get(key, {} if key == "seed_publications" else False if key in ("reuse_worktrees", "single_seed_writer", "disable_repository_cache") else None) == protocol[key], key
        assert saved.get("validation_build_limit", 32) == args.validation_build_limit
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
    protocol["validation_build_limit"] = args.validation_build_limit
    check_build_budget(protocol["phases"], 0, args.validation_build_limit)
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
                "--host_jvm_args=-Xmx4g", "--max_idle_secs=" + str(args.server_idle_seconds)]

    def shutdown(name):
        subprocess.run(startup(name) + ["shutdown"], cwd=root / name,
                       env=environment(name, "shutdown"), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True, timeout=90)

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
        validation_stats = output_base / "local-skycache-validation.json"
        validation_stats.unlink(missing_ok=True)
        argv = startup(name) + [
            "build", "//cmd/cluster-agent:cluster-agent", "--config=no-remote-cache",
            "--jobs=2", "--local_resources=memory=3072", "--lockfile_mode=off",
            "--experimental_strict_repo_env", "--color=no", "--curses=no",
            "--disk_cache=" + (str(args.action_cache.resolve()) if disk_cache else ""),
            "--build_event_json_file=" + str(results_root / f"{phase}-{name}.bep.json"),
        ]
        if repository_cache:
            argv += ["--repository_cache=" + str(repository_cache),
                     "--repo_contents_cache=" + str(repository_cache / "contents")]
        elif args.disable_repository_cache:
            argv += ["--repository_cache="]
        if java_home:
            argv += ["--repo_env=JAVA_HOME=" + str(java_home)]
        if cache:
            argv += ["--experimental_local_skycache=" + str(root / "cache"),
                     "--experimental_active_directories=cmd/cluster-agent"]
        else:
            argv += ["--experimental_local_skycache="]
        log_path = results_root / f"{phase}-{name}.log"
        started = time.monotonic()
        with log_path.open("w") as log:
            process = subprocess.Popen(argv, cwd=root / name, env=environment(name, phase),
                                       stdout=log, stderr=subprocess.STDOUT)
            with client_lock:
                active_clients[name] = process.pid
            timed_out = False
            try:
                try:
                    code = process.wait(timeout=args.build_timeout_seconds)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    process.send_signal(signal.SIGINT)
                    try:
                        code = process.wait(timeout=90)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        code = process.wait()
                    if code == 0:
                        code = 124
            finally:
                with client_lock:
                    active_clients.pop(name, None)
        seconds = time.monotonic() - started
        text = log_path.read_text()
        hits = re.findall(r"CONFIGURED_TARGET: (\d+)/(\d+)", text)
        row = {
            "worktree": name, "argv": argv, "exit_code": code, "seconds": seconds,
            "timed_out": timed_out,
            "server_started": "Starting local Bazel server" in text,
            "analysis_hits": sum(int(a) for a, _ in hits),
            "analysis_hit_summary_present": bool(hits),
            "cache_disabled": "Local SkyCache disabled:" in text,
            "publication_failures": re.findall(r"Local SkyCache publication (?:discarded|skipped): (.*)", text),
        }
        if cache and stats.exists():
            row["skycache"] = json.loads(stats.read_text())
        if cache and validation_stats.exists():
            row["dependency_validation"] = json.loads(validation_stats.read_text())
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
        check_build_budget(protocol["phases"], len(pair_names), args.validation_build_limit)
        storage = check_storage(write_paths)
        samples = []
        finished = threading.Event()
        started = time.monotonic()

        def sample():
            while not finished.wait(.2):
                samples.append([time.monotonic() - started, *process_memory(pids(pair_names))])

        storage_errors = []
        guard = threading.Thread(target=watch_storage,
            args=(write_paths, finished, active_clients, client_lock, storage_errors),
            daemon=True)
        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        guard.start()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(build, name, phase, cache, disk_cache) for name in pair_names]
                rows = [future.result() for future in futures]
        finally:
            finished.set()
            sampler.join()
            guard.join()
        record = {
            "phase": phase, "seconds": time.monotonic() - started, "builds": rows,
            "peak_combined_pss_kib": max((s[2] for s in samples), default=0),
            "peak_combined_rss_kib": max((s[1] for s in samples), default=0),
            "storage_preflight": storage,
            "storage_guard_interval_seconds": 10,
            "storage_guard_errors": storage_errors,
        }
        protocol["phases"].append(record)
        (results_root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
        (results_root / f"{phase}.samples.json").write_text(json.dumps(samples) + "\n")
        print(json.dumps({"phase": phase, "seconds": record["seconds"],
                          "peak_combined_pss_kib": record["peak_combined_pss_kib"],
                          "builds": [{k: row.get(k) for k in
                            ("worktree", "exit_code", "seconds", "analysis_hits", "output_sha256")}
                            for row in rows]}), flush=True)
        assert not storage_errors, storage_errors
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
                assert not row["publication_failures"], row
            if not cache:
                assert row["analysis_hits"] == 0, row
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

    log_offsets = {str(p): p.stat().st_size for name in names
                   for p in (root / ("out-" + name)).glob("java.log.*")}
    legacy_before = {str(p.relative_to(root / "cache")): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (root / "cache/publications-v3").rglob("*.manifest")}
    protocol["legacy_publications_before"] = legacy_before

    def collect_and_rebuild(baseline):
        shutdown_pair(names)
        storage = check_storage(write_paths)
        argv = [str(java_home / "bin/java"), "-Xms64m", "-Xmx256m", "-jar", str(collector_jar),
                str(root / "cache"), "1", "0"]
        completed = subprocess.run(argv, capture_output=True, text=True, timeout=120)
        (results_root / "forced-collection.log").write_text(completed.stdout + completed.stderr)
        completed.check_returncode()
        stats = json.loads(completed.stdout)
        protocol["forced_collection"] = {"argv": argv, "stats": stats, "storage_preflight": storage}
        (results_root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
        assert stats["status"] == "complete" and stats["active_slots"] == 0, stats
        assert stats["deleted_publications"] > 0 and stats["known_bytes_retained"] == 0, stats
        assert not list((root / "cache/leased-v1/publications-v3").rglob("*.manifest"))
        print(json.dumps({"forced_collection": stats}), flush=True)
        pair("post-collection-writer", ("a",), cache=True, expected=baseline, require_writes=True)
        pair("post-collection-independent-reader", ("b",), cache=True, expected=baseline, require_hits=True)
        if not compact:
            shutdown_pair(("a", "b"))
            pair("post-collection-concurrent-readers", ("a", "b"), cache=True, expected=baseline, require_hits=True)

    with args.lock_file.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if args.reuse_worktrees:
                shutdown_pair(names)
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
            elif args.single_seed_writer:
                pair("single-base-writer", ("a",), cache=True, expected=baseline, require_writes=True)
                pair("first-independent-reader", ("b",), cache=True, expected=baseline, require_hits=True)
            else:
                pair("concurrent-base-writers", ("a", "b"), cache=True, expected=baseline, require_writes=True)
            pair("warm-base", ("a", "b"), cache=True, expected=baseline)
            shutdown_pair(("a", "b"))
            if not compact:
                pair("restarted-base-readers", ("a", "b"), cache=True, expected=baseline, require_hits=True)
            if args.base_only:
                protocol["passed"] = True
                (results_root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
                return
            if args.cross_revision_edits:
                shutdown_pair(("a", "b"))
                if "baseline_publications_before_edits" not in protocol:
                    protocol["baseline_publications_before_edits"] = {
                        str(p.relative_to(root / "cache")): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in sorted((root / "cache").rglob("*.manifest"))
                    }
                    assert protocol["baseline_publications_before_edits"]
                    (results_root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
                edit(("a", "b"))
                cold_features = pair("first-cross-revision-readers", ("a", "b"), cache=True,
                                     disk_cache=args.cross_revision_smoke, edited=True,
                                     require_hits=True, require_writes=True)
                assert cold_features["a"] != cold_features["b"]
                assert all(cold_features[n] != baseline[n] for n in ("a", "b"))
                for name in ("a", "b"):
                    (root / name / source_path).write_text(originals[name])
                pair("reverted-warm-graphs", ("a", "b"), cache=True, expected=baseline)
                if args.cross_revision_smoke:
                    protocol["passed"] = True
                    (results_root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
                    return
            if compact:
                feature_outputs = cold_features
            else:
                edit(("a", "b"))
                feature_outputs = pair("edited-warm-graphs", ("a", "b"), cache=True,
                                       disk_cache=False, edited=True, require_writes=not args.cross_revision_edits)
                if args.cross_revision_edits:
                    assert feature_outputs == cold_features
            assert feature_outputs["a"] != feature_outputs["b"]
            assert all(feature_outputs[n] != baseline[n] for n in ("a", "b"))
            shutdown_pair(("a", "b"))
            edit(("control-a", "control-b"))
            pair("control-edits-no-disk-cache", ("control-a", "control-b"), cache=False,
                 expected=feature_outputs, disk_cache=False, edited=True)
            shutdown_pair(("control-a", "control-b"))
            if not compact:
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
            if not compact:
                pair("reverted-restarted-readers", ("a", "b"), cache=True, expected=baseline, require_hits=True)
                if args.restart_controls:
                    shutdown_pair(("a", "b"))
                    pair("reverted-restarted-control", ("control-a", "control-b"), cache=False, expected=baseline)
                    shutdown_pair(("control-a", "control-b"))
            if collector_jar:
                collect_and_rebuild(baseline)
            if compact:
                assert sum(len(p["builds"]) for p in protocol["phases"]) == 16
            protocol["passed"] = True
            (results_root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
        except BaseException:
            protocol["passed"] = False
            raise
        finally:
            failures = []
            with ThreadPoolExecutor(max_workers=4) as pool:
                pending = {n: pool.submit(shutdown, n) for n in names}
                for name, future in pending.items():
                    try:
                        future.result()
                    except Exception as error:
                        failures.append({"worktree": name, "error": str(error)})
            protocol["shutdown_failures"] = failures
            if not failures:
                for name in names:
                    (root / name / source_path).write_text(originals[name])
            protocol["sources_restored"] = all(
                (root / name / source_path).read_text() == originals[name] for name in names)
            diagnostics = []
            for name in names:
                for path in sorted((root / ("out-" + name)).glob("java.log.*")):
                    with path.open("rb") as stream:
                        stream.seek(log_offsets.get(str(path), 0))
                        text = stream.read().decode(errors="replace")
                    lines = [line for line in text.splitlines() if any(word in line for word in
                             ("Timeout waiting", "Skycache invalidation counts", "Invalidation lookup took"))]
                    if lines:
                        diagnostics.append({"worktree": name, "log": path.name, "lines": lines})
            protocol["server_diagnostics"] = diagnostics
            protocol["invalidation_timeouts"] = sum("Timeout waiting" in line
                for item in diagnostics for line in item["lines"])
            legacy_after = {str(p.relative_to(root / "cache")): hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in (root / "cache/publications-v3").rglob("*.manifest")}
            protocol["legacy_publications_preserved"] = legacy_after == legacy_before
            if failures or not protocol["sources_restored"] or legacy_after != legacy_before or protocol["invalidation_timeouts"]:
                protocol["passed"] = False
            (results_root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
            assert not failures and protocol["sources_restored"] and legacy_after == legacy_before
            assert protocol["invalidation_timeouts"] == 0, diagnostics


if __name__ == "__main__":
    main()
