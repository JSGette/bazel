#!/usr/bin/env python3
"""Supplement a completed baseline with CPU lifetimes and physical file storage.

Reuses its stopped output bases and caches. These are diagnostic observations,
not fresh-output-base latency trials. Linux child accounting is read only after
all clients and adopted servers have been waited for.
"""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import resource
import shutil
import stat
import subprocess
import threading
import time

from representative import wait_with_timeout
from representative_results import retain_tests, verify_run
from run import (command, digest, enable_child_subreaper, owned_daemon_identity,
                 owned_directory, process_identity, wait_for_shutdown_client, write_json)


def cpu_snapshot():
    return {scope: {"user_seconds": usage.ru_utime, "system_seconds": usage.ru_stime}
            for scope, usage in (("harness", resource.getrusage(resource.RUSAGE_SELF)),
                                 ("waited_descendants", resource.getrusage(resource.RUSAGE_CHILDREN)))}


def children():
    return sorted({int(pid) for task in Path("/proc/self/task").iterdir()
                   for pid in (task / "children").read_text().split()})


def storage(roots):
    """lstat, no symlink traversal; count hard links once across all named roots."""
    seen, totals = set(), {}
    for name, root in roots.items():
        counts = {"allocated_bytes": 0, "apparent_bytes": 0, "inodes": 0}
        pending = [root]
        while pending:
            path = pending.pop()
            info = path.lstat()
            key = info.st_dev, info.st_ino
            if key in seen:
                continue
            seen.add(key)
            counts["allocated_bytes"] += info.st_blocks * 512
            counts["apparent_bytes"] += info.st_size
            counts["inodes"] += 1
            if stat.S_ISDIR(info.st_mode):
                pending.extend(path.iterdir())
        totals[name] = counts
    return totals


def replay(saved, directory, require_execution, barrier):
    directory.mkdir(parents=True)
    excluded = ("--build_event_json_file=", "--profile=", "--memory_profile=",
                "--record_full_profiler_data", "--experimental_profile_additional_tasks=",
                "--cache_test_results=", "--nocache_test_results")
    argv = [arg for arg in saved["argv"] if not arg.startswith(excluded)] + [
        f"--build_event_json_file={directory / 'bep.jsonl'}", "--generate_json_trace_profile=no",
        "--nocache_test_results" if require_execution else "--cache_test_results=yes"]
    write_json(directory / "command.json", {"argv": argv, "cwd": saved["cwd"]})
    barrier.wait()
    start = time.monotonic_ns()
    with (directory / "command.log").open("wb") as log:
        process = subprocess.Popen(argv, cwd=saved["cwd"], stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT)
        code, timed_out = wait_with_timeout(process, 1800)
    end = time.monotonic_ns()
    if code or timed_out:
        raise RuntimeError(f"Command failed: {directory}, exit={code}, timeout={timed_out}")
    targets = [arg for arg in argv if arg.startswith("//")]
    validation = retain_tests(directory, targets, require_execution)
    result = {"exit_code": code, "start_monotonic_ns": start, "end_monotonic_ns": end,
              "require_execution": require_execution, "validation": validation}
    write_json(directory / "result.json", result)
    return result


def run(baseline, results):
    verify_run(baseline)
    manifest = json.loads((baseline / "manifest.json").read_text())
    configuration = manifest["configuration"]
    state = Path(configuration["state_dir"])
    if not (state / ".phase0-harness-owned.json").is_file():
        raise ValueError("Missing baseline state ownership marker")
    for name in ("bazel", "bazelisk"):
        if digest(Path(configuration[name]).read_bytes()) != manifest[f"{name}_sha256"]:
            raise ValueError(f"Changed {name} executable")
    if not enable_child_subreaper()["enabled"]:
        raise RuntimeError("Linux child subreaper is required")
    os.sched_setaffinity(0, manifest["cpu_affinity"])
    results = owned_directory(results, "baseline-resources")
    snapshots = results / "harness"
    snapshots.mkdir()
    for name in ("baseline_resources.py", "representative.py", "representative_results.py", "run.py"):
        shutil.copy2(Path(__file__).with_name(name), snapshots / name)
    write_json(results / "manifest.json", {
        "baseline_manifest": manifest, "baseline_directory": str(baseline),
        "scope": "two lifetimes per group; one cold-graph uncached test command and three warm cached commands per lifetime; includes startup, shutdown and helpers",
        "cache_state": "retained output bases, action and download caches; server/graph cold at each lifetime; OS caches uncontrolled",
        "cpu_method": "getrusage SELF and CHILDREN deltas after waiting for all owned descendants; no per-command CPU attribution",
        "storage_method": "lstat st_blocks * 512; symlinks not followed, hard links counted once; excludes filesystem metadata, compression/reflink and host VHD allocation"})
    records, failure = [], None
    try:
        for count in (1, 2, 4):
            saved = [json.loads((baseline / f"n{count}-coldgraph_hotdisk-r0" / f"n{count}-w{i}" / "command.json").read_text())
                     for i in range(count)]
            outputs = [state / "output-bases" / f"n{count}-w{i}" for i in range(count)]
            for item, output in zip(saved, outputs):
                if f"--output_base={output}" not in item["argv"] or owned_daemon_identity(output):
                    raise ValueError("Unexpected output base or running baseline server")
                if command(["git", "rev-parse", "HEAD"], item["cwd"]) != manifest["source_commit"]:
                    raise ValueError("Changed worktree commit")
                for name, sha in manifest["inputs_sha256"].items():
                    if digest((Path(item["cwd"]) / name).read_bytes()) != sha:
                        raise ValueError(f"Changed input: {name}")
            for repetition in range(2):
                if children():
                    raise RuntimeError("Unaccounted children before measurement")
                before, start = cpu_snapshot(), time.monotonic_ns()
                record = {"concurrency": count, "repetition": repetition, "commands": [], "shutdown": []}
                records.append(record)
                try:
                    for step in range(4):
                        barrier = threading.Barrier(count)
                        with concurrent.futures.ThreadPoolExecutor(max_workers=count) as pool:
                            futures = [pool.submit(replay, item, results / f"n{count}-r{repetition}-step{step}" / f"w{i}", step == 0, barrier)
                                       for i, item in enumerate(saved)]
                            record["commands"].append([future.result() for future in futures])
                finally:
                    for i, (item, output) in enumerate(zip(saved, outputs)):
                        identity = owned_daemon_identity(output)
                        if identity is None:
                            record["shutdown"].append({"error": "missing owned daemon"})
                            continue
                        argv = item["argv"][:item["argv"].index("test")] + ["shutdown"]
                        with (results / f"shutdown-n{count}-r{repetition}-w{i}.log").open("wb") as log:
                            process = subprocess.Popen(argv, cwd=item["cwd"], stdout=log, stderr=subprocess.STDOUT)
                            code, reaping = wait_for_shutdown_client(process, identity, 60)
                        record["shutdown"].append({"before": identity, "exit_code": code, "reaping": reaping,
                                                   "after": process_identity(identity["pid"])})
                    after = cpu_snapshot()
                    record.update(wall_seconds=(time.monotonic_ns() - start) / 1e9,
                                  remaining_children=children(),
                                  cpu={scope: {key: after[scope][key] - before[scope][key] for key in after[scope]} for scope in after})
                if record["remaining_children"] or any(s.get("exit_code") != 0 or s["after"] is not None for s in record["shutdown"]):
                    raise RuntimeError("Incomplete child accounting or shutdown")
                print(json.dumps({key: record[key] for key in ("concurrency", "repetition", "wall_seconds", "cpu")}), flush=True)
        write_json(results / "storage.json", storage({name: state / name for name in ("output-bases", "cache", "bazel-user-root", "worktrees", "home")}))
    except BaseException as error:
        failure = f"{type(error).__name__}: {error}"
        raise
    finally:
        write_json(results / "summary.json", {"failure": failure, "lifetimes": records})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    run(args.baseline.resolve(), args.results.resolve())
