#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Linux-only engine comparison: separate timing runs from diagnostic profiles."""

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


def sidecar_io(output_base):
    """Linux sidecar I/O counters; read_bytes excludes reads served by the guest page cache."""
    try:
        state = json.loads((output_base / "skyframe-tiering.json").read_text())
        pid = state.get("sidecar_pid", 0)
        if not pid:
            return {}
        counters = {key: int(value) for key, value in
                    (line.split(":") for line in Path(f"/proc/{pid}/io").read_text().splitlines())}
        return {"pid": pid, **counters}
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return {}


def evict_spill_pages(output_base, *, evict=True):
    """Evict only this run's spill file from the Linux page cache, never global caches."""
    import ctypes
    import mmap
    import os

    state = json.loads((output_base / "skyframe-tiering.json").read_text())
    pid = state["sidecar_pid"]
    command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    spill = Path(os.fsdecode(command[2]))
    assert spill.parent.name.startswith("bazel-skystore-") and spill.name == "values"
    libc = ctypes.CDLL(None, use_errno=True)

    def resident_pages(fd, size):
        if size == 0:
            return 0
        count = (size + mmap.PAGESIZE - 1) // mmap.PAGESIZE
        with mmap.mmap(fd, size, flags=mmap.MAP_PRIVATE,
                       prot=mmap.PROT_READ | mmap.PROT_WRITE) as mapping:
            address = ctypes.addressof(ctypes.c_char.from_buffer(mapping))
            vector = (ctypes.c_ubyte * count)()
            result = libc.mincore(ctypes.c_void_p(address), ctypes.c_size_t(size), vector)
            if result:
                raise OSError(ctypes.get_errno(), "mincore failed")
            return sum(bool(value & 1) for value in vector)

    with spill.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        before = resident_pages(stream.fileno(), size)
        os.fsync(stream.fileno())
        if evict:
            os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        else:
            while stream.read(1024 * 1024):
                pass
        after = resident_pages(stream.fileno(), size)
    return {"condition": "evicted" if evict else "warmed", "file_bytes": size, "resident_pages_before": before,
            "resident_pages_after": after, "page_bytes": mmap.PAGESIZE,
            "note": "Linux guest page-cache eviction; WSL host/device caches are uncontrolled."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jcmd", type=Path, required=True)
    parser.add_argument("--startup-arg", action="append", default=[])
    parser.add_argument("--build-arg", action="append", default=[])
    parser.add_argument("--command-arg", action="append", default=[],
                        help="Option preserved across build, clean and dump commands (e.g. graph engine)")
    parser.add_argument("--guest-evict-spill", action="store_true",
                        help="Also measure reads after evicting this run's spill file from guest page cache")
    parser.add_argument("--alternate-target", action="append", default=[],
                        help="Measure repeated builds of each disjoint target after warm all-target runs")
    parser.add_argument("--edit-file", type=Path, help="Fixture input to edit and restore for incremental runs")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--execute", action="store_true", help="Execute actions instead of analysis only")
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
    phase = "build" if args.execute else "analysis"

    def server_pid():
        try:
            return int((output_base / "server" / "server.pid.txt").read_text())
        except (FileNotFoundError, ValueError):
            return 0

    def command(name, arguments, measure=True):
        argv = startup + arguments[:1] + args.command_arg + arguments[1:]
        io_before = sidecar_io(output_base)
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
        stats = output_base / "skyframe-tiering.json"
        if stats.exists():
            record["tiering"] = json.loads(stats.read_text())
        io_after = sidecar_io(output_base)
        if io_after:
            record["sidecar_io"] = io_after
            if io_before.get("pid") == io_after["pid"]:
                record["sidecar_io_delta"] = {key: io_after[key] - io_before[key]
                                              for key in io_after if key != "pid"}
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
        "workload": "full build with action execution" if args.execute else "analysis only (--nobuild); no action execution",
        "cold_definition": "fresh output base and server; repository and OS caches may be warm",
        "ram_definition": "sampled sum of launcher, server, and descendants; RSS can double-count shared pages; PSS apportions them",
        "profiles": "diagnostic pass after timing runs; memory_profile and class histogram force GC",
        "targets": args.targets,
        "startup_args": args.startup_arg,
        "build_args": args.build_arg,
        "command_args": args.command_arg,
    }
    import hashlib
    with binary.open("rb") as binary_file:
        metadata["binary_sha256"] = hashlib.file_digest(binary_file, "sha256").hexdigest()
    (output / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    build = ["build", *([] if args.execute else ["--nobuild"]), "--jobs=4", "--local_resources=memory=6144", *args.build_arg]
    try:
        command("cold-" + phase, build + args.targets)
        for i in range(2):
            command("warmup-" + str(i + 1), build + args.targets, measure=False)
        for i in range(args.repeats):
            command("warm-" + phase + "-" + str(i + 1), build + args.targets)
        for index in range(args.repeats):
            for target_index, target in enumerate(args.alternate_target):
                command(f"disjoint-{index + 1}-{target_index + 1}", build + [target])
        if args.edit_file:
            edit_file = args.edit_file.resolve(strict=True)
            original = edit_file.read_bytes()
            try:
                edit_file.write_bytes(original + b"incremental-edit\n")
                command("source-edit", build + args.targets)
            finally:
                edit_file.write_bytes(original)
            command("source-revert", build + args.targets)
        if args.guest_evict_spill:
            evictions = []
            for index in range(args.repeats):
                conditions = ["guest-warm", "guest-evicted"] if index % 2 == 0 else ["guest-evicted", "guest-warm"]
                for position, condition in enumerate(conditions):
                    diagnostic(f"{condition}-gc-{index + 1}", ["GC.run"])
                    eviction = evict_spill_pages(output_base, evict=condition == "guest-evicted")
                    evictions.append(eviction)
                    (output / "guest-cache-evictions.json").write_text(json.dumps(evictions, indent=2) + "\n")
                    targets = ([args.alternate_target[position % len(args.alternate_target)]]
                               if args.alternate_target else args.targets)
                    command(f"{condition}-{index + 1}", build + targets)
        diagnostic("jvm-version", ["VM.version"])
        command("profile-reset", ["clean"], measure=False)
        diagnostic("jfr-start", [
            "JFR.start", "name=SkystoreBaseline", "settings=profile",
            "filename=" + str(output / (phase + ".jfr"))
        ])
        command("profile-" + phase, build + [
            "--profile=" + str(output / (phase + ".trace.json.gz")),
            "--noslim_profile",
            "--memory_profile=" + str(output / (phase + ".memory.txt")),
        ] + args.targets)
        diagnostic("jfr-stop", ["JFR.stop", "name=SkystoreBaseline"])
        diagnostic("heap-histogram", ["GC.class_histogram"])
        retained = process_memory([server_pid()])
        (output / "post-gc-memory.json").write_text(json.dumps({
            "tree_rss_kib": retained[0], "tree_pss_kib": retained[1],
            "process_count": retained[2],
            "note": "After diagnostic recording and full-GC class histogram; includes sidecar."
        }, indent=2) + "\n")
        command("skyframe-counts", ["dump", "--skyframe=count"], measure=False)
        command("skyframe-values", ["dump", "--memory=full,summary"], measure=False)
        warm = [r for r in records if r["name"].startswith("warm-" + phase + "-")]
        summary = {
            "cold_run": records[0],
            "warm_median_wall_seconds": statistics.median(r["wall_seconds"] for r in warm),
            "warm_min_wall_seconds": min(r["wall_seconds"] for r in warm),
            "warm_max_wall_seconds": max(r["wall_seconds"] for r in warm),
            "warm_median_peak_tree_rss_kib": statistics.median(r["peak_tree_rss_kib"] for r in warm),
            "warm_median_peak_tree_pss_kib": statistics.median(r["peak_tree_pss_kib"] for r in warm),
            "warm_samples": len(warm),
            "limitation": "One cold run per invocation; repository/OS/remote caches can be warm. Edit scenarios require separate runs.",
        }
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    finally:
        # Stop only the server owned by this newly created output directory.
        subprocess.run(startup + ["shutdown"], cwd=workspace, stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
