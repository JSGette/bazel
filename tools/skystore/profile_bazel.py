#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Linux-only analysis baseline: separate timing runs from diagnostic profiles."""

import argparse
import csv
import json
from pathlib import Path
import platform
import statistics
import subprocess
import threading
import time


def process_memory(roots):
    """Sum live process RSS/PSS, following descendants (including a sidecar)."""
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
            children = (base / "task" / str(pid) / "children").read_text().split()
            pending.extend(map(int, children))
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
    parser.add_argument("--bazel", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jcmd", type=Path, required=True)
    parser.add_argument("--startup-arg", action="append", default=[])
    parser.add_argument("--build-arg", action="append", default=[])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("targets", nargs="+")
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Linux /proc is required")
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    binary = args.bazel.resolve(strict=True)
    workspace = args.workspace.resolve(strict=True)
    jcmd = args.jcmd.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    output_base = output / "output-base"
    startup = [
        str(binary),
        "--output_base=" + str(output_base),
        "--host_jvm_args=-Xmx4g",
        *args.startup_arg,
    ]
    records = []

    def server_pid():
        try:
            return int((output_base / "server" / "server.pid.txt").read_text())
        except (FileNotFoundError, ValueError):
            return 0

    def command(name, arguments, measure=True):
        argv = startup + arguments
        samples = []
        with (output / (name + ".log")).open("w") as log:
            start = time.monotonic()
            proc = subprocess.Popen(argv, cwd=workspace, stdout=log, stderr=subprocess.STDOUT)
            stop_sampling = threading.Event()

            def sample():
                while not stop_sampling.is_set():
                    rss, pss, count = process_memory([proc.pid, server_pid()])
                    samples.append((time.monotonic() - start, rss, pss, count))
                    stop_sampling.wait(0.05)

            sampler = threading.Thread(target=sample)
            if measure:
                sampler.start()
            proc.wait()
            elapsed = time.monotonic() - start
            stop_sampling.set()
            if measure:
                sampler.join()
            samples = [s for s in samples if s[0] <= elapsed]
        # The daemon remains alive after the launcher exits.
        retained = process_memory([server_pid()])
        record = {
            "name": name,
            "argv": argv,
            "exit_code": proc.returncode,
            "wall_seconds": elapsed,
            "peak_tree_rss_kib": max((s[1] for s in samples), default=None),
            "peak_tree_pss_kib": max((s[2] for s in samples), default=None),
            "post_command_tree_rss_kib": retained[0],
            "post_command_tree_pss_kib": retained[1],
        }
        records.append(record)
        if measure:
            with (output / (name + ".samples.csv")).open("w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["elapsed_seconds", "tree_rss_kib", "tree_pss_kib", "process_count"])
                writer.writerows(samples)
        (output / "runs.json").write_text(json.dumps(records, indent=2) + "\n")
        print(name, "exit", proc.returncode, "wall", round(elapsed, 3), "seconds", flush=True)
        if proc.returncode:
            raise RuntimeError("Bazel failed; inspect " + str(output / (name + ".log")))
        return record

    def diagnostic(name, arguments):
        with (output / (name + ".log")).open("w") as log:
            subprocess.run(
                [str(jcmd), str(server_pid()), *arguments],
                cwd=workspace, stdout=log, stderr=subprocess.STDOUT, check=True
            )

    metadata = {
        "kernel": platform.release(),
        "machine": platform.machine(),
        "cpu": next(
            (line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
             if line.startswith("model name")), "unknown"
        ),
        "memory_total_kib": int(Path("/proc/meminfo").read_text().splitlines()[0].split()[1]),
        "workspace_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=workspace, text=True
        ).strip(),
        "binary": str(binary),
        "sample_interval_seconds": 0.05,
        "workload": "analysis only (--nobuild); no action execution",
        "cold_definition": "fresh output base and server; repository and OS caches may be warm",
        "ram_definition": "sampled sum of launcher, server, and descendants; RSS can double-count shared pages; PSS apportions them",
        "profiles": "diagnostic pass after timing runs; memory_profile and class histogram force GC",
        "targets": args.targets,
        "startup_args": args.startup_arg,
        "build_args": args.build_arg,
    }
    import hashlib
    with binary.open("rb") as binary_file:
        metadata["binary_sha256"] = hashlib.file_digest(binary_file, "sha256").hexdigest()
    (output / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    build = ["build", "--nobuild", "--jobs=4", "--local_resources=memory=6144", *args.build_arg]
    try:
        command("cold-analysis", build + args.targets)
        for i in range(2):
            command("warmup-" + str(i + 1), build + args.targets, measure=False)
        for i in range(args.repeats):
            command("warm-analysis-" + str(i + 1), build + args.targets)
        diagnostic("jvm-version", ["VM.version"])
        command("profile-reset", ["clean"], measure=False)
        diagnostic("jfr-start", [
            "JFR.start", "name=SkystoreBaseline", "settings=profile",
            "filename=" + str(output / "analysis.jfr")
        ])
        command("profile-analysis", build + [
            "--profile=" + str(output / "analysis.trace.json.gz"),
            "--noslim_profile",
            "--memory_profile=" + str(output / "analysis.memory.txt"),
        ] + args.targets)
        diagnostic("jfr-stop", ["JFR.stop", "name=SkystoreBaseline"])
        diagnostic("heap-histogram", ["GC.class_histogram"])
        command("skyframe-counts", ["dump", "--skyframe=count"], measure=False)
        command("skyframe-values", ["dump", "--memory=full,summary"], measure=False)
        warm = [r for r in records if r["name"].startswith("warm-analysis-")]
        summary = {
            "cold_analysis": records[0],
            "warm_median_wall_seconds": statistics.median(r["wall_seconds"] for r in warm),
            "warm_min_wall_seconds": min(r["wall_seconds"] for r in warm),
            "warm_max_wall_seconds": max(r["wall_seconds"] for r in warm),
            "warm_median_peak_tree_rss_kib": statistics.median(r["peak_tree_rss_kib"] for r in warm),
            "warm_median_peak_tree_pss_kib": statistics.median(r["peak_tree_pss_kib"] for r in warm),
            "warm_samples": len(warm),
            "limitation": "One cold run, analysis-only workload, no incremental-edit or execution measurements.",
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    finally:
        # Stop only the server owned by this newly created output directory.
        subprocess.run(startup + ["shutdown"], cwd=workspace, stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
