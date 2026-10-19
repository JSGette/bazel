#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Compare real rules_go builds using a repository's Bazel wrapper, without clean.

Run each engine serially with a fresh --output, the same --bazel and disk cache.
The cold run has a fresh graph/output base, but warm repository/action/OS caches.
Outputs are relative paths below this run's execroot/_main/bazel-out directory.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time

from profile_bazel import process_memory, sidecar_io


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", type=Path, required=True)
    parser.add_argument("--skystore", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disk-cache", type=Path, required=True)
    parser.add_argument("--engine", choices=["inmemory", "tiered_go"], required=True)
    parser.add_argument("--resident-nodes", type=int, default=256)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--edits-only", action="store_true",
                        help="Run cold build and edit/revert correctness checks without warm timing")
    parser.add_argument("--edit-file", type=Path)
    parser.add_argument("--bzl-edit-file", type=Path)
    parser.add_argument("--source-replace", nargs=2, metavar=("OLD", "NEW"))
    parser.add_argument("--starlark-replace", nargs=2, metavar=("OLD", "NEW"))
    parser.add_argument("--artifact", action="append", default=[],
                        help="Glob under this run's bazel-out for output hash comparison")
    parser.add_argument("targets", nargs="+")
    args = parser.parse_args()
    if platform.system() != "Linux" or args.repeats < 1:
        parser.error("Linux and positive repeats are required")
    if args.engine == "tiered_go" and args.skystore is None:
        parser.error("tiered_go requires --skystore")
    workspace = args.workspace.resolve(strict=True)
    binary = args.bazel.resolve(strict=True)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    base = output / "output-base"
    env = dict(os.environ, BAZEL_REAL=str(binary), BAZELISK_SKIP_WRAPPER="true",
               DD_BAZEL_REMOTE_CACHE="off")
    startup = [str(workspace / "tools/bazel"), "--output_base=" + str(base),
               "--host_jvm_args=-Xmx6g", "--max_idle_secs=120"]
    options = ["--experimental_skyframe_engine=" + args.engine]
    if args.skystore:
        options += ["--experimental_skystore_binary=" + str(args.skystore.resolve(strict=True)),
                    "--experimental_skyframe_resident_nodes=" + str(args.resident_nodes)]
    build = ["build", *options, "--config=no-remote-cache", "--jobs=4",
             "--local_resources=memory=4096", "--disk_cache=" + str(args.disk_cache.resolve()),
             *args.targets]
    records = []

    def pid():
        try:
            return int((base / "server/server.pid.txt").read_text())
        except (FileNotFoundError, ValueError):
            return 0

    def save():
        (output / "runs.json").write_text(json.dumps(records, indent=2) + "\n")

    def command(name, arguments, artifacts=False):
        samples = []
        before = sidecar_io(base)
        stop = threading.Event()
        argv = startup + arguments
        with (output / (name + ".log")).open("w") as log:
            start = time.monotonic()
            process = subprocess.Popen(argv, cwd=workspace, env=env,
                                       stdout=log, stderr=subprocess.STDOUT)

            def sample():
                while not stop.is_set():
                    samples.append((time.monotonic() - start, *process_memory([pid(), process.pid])))
                    stop.wait(0.1)

            sampler = threading.Thread(target=sample)
            sampler.start()
            try:
                code = process.wait()
            finally:
                stop.set()
                sampler.join()
            elapsed = time.monotonic() - start
        record = dict(name=name, argv=argv, exit_code=code, wall_seconds=elapsed,
                      peak_tree_pss_kib=max((s[2] for s in samples), default=0),
                      peak_tree_rss_kib=max((s[1] for s in samples), default=0),
                      retained_tree_memory_kib=process_memory([pid()])[:2])
        stats = base / "skyframe-tiering.json"
        if stats.exists():
            record["tiering"] = json.loads(stats.read_text())
        if artifacts and code == 0 and args.engine == "tiered_go":
            tier = record.get("tiering", {})
            if not tier.get("enabled") or tier.get("engine") != "tiered_go" or tier.get("cold_nodes", 0) == 0:
                raise RuntimeError("The requested Go paging mode did not actually page nodes")
            if tier["resident_nodes"] - tier["pinned_nodes"] > args.resident_nodes:
                raise RuntimeError("Eligible completed-node working set exceeds its limit")
        after = sidecar_io(base)
        if after:
            record["sidecar_io"] = after
            if before.get("pid") == after["pid"]:
                record["sidecar_io_delta"] = {k: after[k] - before[k] for k in after if k != "pid"}
        if artifacts and code == 0:
            root = base / "execroot/_main/bazel-out"
            record["artifacts"] = {}
            for pattern in args.artifact:
                matches = list(root.glob(pattern))
                if not matches:
                    raise RuntimeError("No built artifact matched " + pattern)
                for path in matches:
                    record["artifacts"][str(path.relative_to(root))] = digest(path)
        records.append(record)
        save()
        (output / (name + ".samples.json")).write_text(json.dumps(samples) + "\n")
        print(name, "exit", code, "wall", round(elapsed, 3), flush=True)
        if code:
            raise RuntimeError("Bazel failed: " + str(output / (name + ".log")))
        return record

    def jcmd(name, *arguments):
        # Locate the JDK actually running this server, independently of toolchain labels.
        executable = Path(f"/proc/{pid()}/exe").resolve(strict=True).with_name("jcmd")
        with (output / (name + ".log")).open("w") as log:
            subprocess.run([str(executable), str(pid()), *arguments], check=True,
                           stdout=log, stderr=subprocess.STDOUT)

    metadata = dict(binary=str(binary), binary_sha256=digest(binary), engine=args.engine,
                    workspace_commit=subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                              cwd=workspace, text=True).strip(),
                    kernel=platform.release(), memory=Path("/proc/meminfo").read_text(),
                    timing="Fresh graph, warm repository/action/OS caches; 100ms process-tree sampling",
                    memory_scope="JVM, launcher and children including Rust; excludes filesystem page cache",
                    source_status=subprocess.check_output(["git", "status", "--porcelain"],
                                                          cwd=workspace, text=True))
    (output / "environment.json").write_text(json.dumps(metadata, indent=2) + "\n")
    try:
        initial = command("cold-build", build, artifacts=True)
        if not args.edits_only:
            command("warmup", build)
            for i in range(args.repeats):
                command(f"warm-{i + 1}", build, artifacts=True)
            jcmd("heap-before-reload", "GC.class_histogram")
            records.append(dict(name="post-gc", tree_memory_kib=process_memory([pid()])[:2]))
            save()
            command("after-gc", build, artifacts=True)
        for name, filename, comment, replacement in [
            ("source", args.edit_file, b"\n// Skyframe incremental benchmark.\n", args.source_replace),
            ("starlark", args.bzl_edit_file, b"\n# Skyframe incremental benchmark.\n", args.starlark_replace),
        ]:
            if filename is None:
                continue
            path = (workspace / filename).resolve(strict=True)
            path.relative_to(workspace)  # Never edit files outside this checkout.
            original = path.read_bytes()
            if replacement:
                old, new = (text.encode() for text in replacement)
                if original.count(old) != 1 or old == new:
                    raise RuntimeError("Semantic edit must replace exactly one distinct occurrence")
                modified = original.replace(old, new)
            else:
                modified = original + comment
            try:
                path.write_bytes(modified)
                edited = command(name + "-edit", build, artifacts=True)
                if replacement and args.artifact and edited["artifacts"] == initial["artifacts"]:
                    raise RuntimeError("Semantic edit did not change the output; check invalidation")
            finally:
                path.write_bytes(original)
            reverted = command(name + "-revert", build, artifacts=True)
            if args.artifact and reverted["artifacts"] != initial["artifacts"]:
                raise RuntimeError("Reverted build did not restore the initial output")
        jcmd("heap-final", "GC.class_histogram")
        records.append(dict(name="post-gc-final", tree_memory_kib=process_memory([pid()])[:2]))
        save()
        command("counts", ["dump", *options, "--skyframe=count"])
    finally:
        subprocess.run(startup + ["shutdown"], cwd=workspace, env=env,
                       stdout=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
