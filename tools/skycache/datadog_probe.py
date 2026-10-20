#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Measure local SkyCache on Datadog cluster-agent in an isolated Git worktree (Linux).

Requires a local Datadog checkout, a compatible resolved MODULE lockfile, a custom Bazel binary,
and a reusable action-cache directory. Preserves logs, memory samples, exact commands and hashes.
The ordinary action cache and filesystem caches are warm; this is a graph-cold/warm comparison.
Raw BEP files can contain client environment values: inspect before sharing them.
"""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import argparse
import threading
import time
from storage_preflight import check_storage

def process_memory(roots):
    """Sum live process RSS/PSS, following descendants."""
    # /proc/PID/task/PID/children only includes children forked by the leader thread.
    # JVM ProcessBuilder runs on command threads; discover children by process PPID instead.
    children_by_parent = {}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            fields = (proc / "stat").read_text().rpartition(")")[2].split()
            children_by_parent.setdefault(int(fields[1]), []).append(int(proc.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError, IndexError):
            continue
    pending = list(roots)
    seen = set()
    rss = pss = 0
    while pending:
        pid = pending.pop()
        if pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        base = Path("/proc") / str(pid)
        try:
            pending.extend(children_by_parent.get(pid, []))
            fields = {}
            for line in (base / "smaps_rollup").read_text().splitlines():
                key, _, value = line.partition(":")
                if key in ("Rss", "Pss"):
                    fields[key] = int(value.split()[0])
            rss += fields.get("Rss", 0)
            pss += fields.get("Pss", 0)
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            # Processes can exit between discovery and sampling.
            continue
    return rss, pss, len(seen)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", required=True, type=Path)
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path, help="New directory, preserved with raw results")
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--resolved-lockfile", required=True, type=Path)
    parser.add_argument("--action-cache", required=True, type=Path)
    parser.add_argument("--lock-file", required=True, type=Path, help="Host measurement coordination lock")
    args = parser.parse_args()
    root = args.root.resolve()
    initial_storage = check_storage([root, args.action_cache])
    root.mkdir(parents=True, exist_ok=False)
    binary = args.bazel.resolve(strict=True)
    original = args.repository.resolve(strict=True)
    workspace = root / "workspace"
    subprocess.run(["git", "-C", str(original), "worktree", "add", "--detach", str(workspace), args.revision], check=True)
    shutil.copy2(args.resolved_lockfile, workspace / "MODULE.bazel.lock")
    environment = dict(os.environ, BAZEL_REAL=str(binary), BAZELISK_SKIP_WRAPPER="true", DD_BAZEL_REMOTE_CACHE="off")
    environment.pop("PWD", None)
    environment.pop("OLDPWD", None)
    output_base = root / "output-base"
    startup = [str(workspace / "tools/bazel"), "--output_base=" + str(output_base),
               "--host_jvm_args=-Xmx4g", "--max_idle_secs=120"]
    build = ["build", "--config=no-remote-cache", "--jobs=4", "--local_resources=memory=4096",
             "--lockfile_mode=off", "--disk_cache=" + str(args.action_cache.resolve()),
             "//cmd/cluster-agent:cluster-agent", "--color=no", "--curses=no"]
    protocol = {"binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "source_commit": subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip(),
                "lock_sha256": hashlib.sha256((workspace / "MODULE.bazel.lock").read_bytes()).hexdigest(),
                "source_status": subprocess.check_output(["git", "-C", str(workspace), "status", "--short"], text=True),
                "memory_scope": "process-tree RSS/PSS, sampled every 200 ms; excludes filesystem page cache",
                "initial_storage": initial_storage, "runs": []}
    lock = args.lock_file.open("a")
    fcntl.flock(lock, fcntl.LOCK_EX)

    def pid():
        try:
            return int((output_base / "server/server.pid.txt").read_text())
        except (FileNotFoundError, ValueError):
            return 0

    def shutdown():
        subprocess.run(startup + ["shutdown"], cwd=workspace, env=environment, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def command(label, cache):
        storage = check_storage([root, args.action_cache])
        stats = output_base / "local-skycache.json"
        # A server crash can skip final statistics. Never attribute an earlier command's file.
        stats.unlink(missing_ok=True)
        argv = startup + build + (["--experimental_local_skycache=" + str(root / "cache"),
                                  "--experimental_active_directories=cmd/cluster-agent"] if cache else [])
        argv += ["--build_event_json_file=" + str(root / (label + ".bep.json"))]
        samples = []
        finished = threading.Event()
        start = time.monotonic()
        with (root / (label + ".log")).open("w") as log:
            process = subprocess.Popen(argv, cwd=workspace, env=environment, stdout=log, stderr=subprocess.STDOUT)
            def sample():
                while not finished.wait(.2):
                    samples.append([time.monotonic() - start, *process_memory([pid(), process.pid])])
            thread = threading.Thread(target=sample, daemon=True)
            thread.start()
            try:
                code = process.wait()
            finally:
                finished.set()
                thread.join()
        row = {"label": label, "argv": argv, "exit_code": code, "seconds": time.monotonic() - start,
               "storage_preflight": storage,
               "peak_tree_rss_kib": max((s[1] for s in samples), default=0),
               "peak_tree_pss_kib": max((s[2] for s in samples), default=0)}
        log_text = (root / (label + ".log")).read_text()
        if cache and stats.exists():
            row["skycache"] = json.loads(stats.read_text())
        hits = re.findall(r"CONFIGURED_TARGET: (\d+)/(\d+)", log_text)
        progress_hits = re.findall(r"(\d+) remote cache hits", log_text)
        row["analysis_hits"] = (
            sum(int(a) for a, _ in hits) if hits
            else max((int(a) for a in progress_hits), default=0)
        )
        row["analysis_hits_source"] = (
            "final configured-target summary" if hits else
            "progress output (may include other node types)" if progress_hits else "none"
        )
        row["cache_disabled"] = "Local SkyCache disabled:" in log_text
        row["publication_failures"] = re.findall(r"Local SkyCache publication (?:discarded|skipped): (.*)", log_text)
        if code == 0:
            artifacts = list((output_base / "execroot/_main/bazel-out").glob("*/bin/cmd/cluster-agent/cluster-agent_/cluster-agent"))
            assert len(artifacts) == 1, artifacts
            with artifacts[0].open("rb") as stream:
                row["output_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
        protocol["runs"].append(row)
        (root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
        (root / (label + ".samples.json")).write_text(json.dumps(samples) + "\n")
        print(json.dumps(row), flush=True)
        assert code == 0 and (not cache or not row["cache_disabled"]), label
        if len(protocol["runs"]) > 1:
            assert row["output_sha256"] == protocol["runs"][0]["output_sha256"], label
        return row

    try:
        command("control-cold", False)
        command("control-warm", False)
        shutdown()
        writer = command("writer-cold", True)
        assert writer["skycache"]["published"] and not writer["skycache"]["failed"], "writer did not publish"
        command("writer-warm", True)
        shutdown()
        reader = command("reader-restart", True)
        assert reader["analysis_hits"] > 0, "reader did not reuse analysis"
    finally:
        shutdown()
        lock.close()


if __name__ == "__main__":
    main()
