#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Linux action-graph paging, incremental correctness and lifecycle regression matrix."""

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", type=Path, required=True)
    parser.add_argument("--skystore", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", action="store_true")
    parser.add_argument("--vanilla", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    workspace.mkdir()
    data = workspace / "data"
    data.mkdir()
    count = 32
    for i in range(count):
        (data / f"i{i}.txt").write_text(f"input-{i}\n")
    (workspace / "MODULE.bazel").write_text("module(name='lazy_graph_test')\n")
    defs = workspace / "defs.bzl"
    defs.write_text('MARKER = "initial"\n')
    build = workspace / "BUILD.bazel"
    lines = ['load(":defs.bzl", "MARKER")']
    for i in range(count):
        lines.extend([
            f'genrule(name="a{i}", srcs=glob(["data/i{i}*.txt"]), outs=["a{i}.out"], cmd="cat $(SRCS) > $@")',
            f'genrule(name="b{i}", srcs=[":a{i}"], outs=["b{i}.out"], cmd="cat $(SRCS) > $@; echo " + MARKER + " >> $@")',
        ])
    for name, indexes in [("left", range(count // 2)), ("right", range(count // 2, count)), ("all", range(count))]:
        lines.append(f'filegroup(name="{name}", srcs={[f":b{i}" for i in indexes]!r})')
    lines += [
        'genrule(name="bad", outs=["bad.out"], cmd="exit 1")',
        'genrule(name="slow", outs=["slow.out"], cmd="sleep 60; touch $@")',
        'filegroup(name="cycle", srcs=[":cycle"])',
    ]
    build.write_text("\n".join(lines) + "\n")
    binary = root / "skystore"
    shutil.copy2(args.skystore.resolve(), binary)
    output_base = root / "output-base"
    startup = [str(args.bazel.resolve()), "--host_jvm_args=-Xmx2g", "--output_base=" + str(output_base)]
    tier_flags = ["--experimental_skyframe_engine=tiered_genrule",
                  "--experimental_skyframe_resident_nodes=2",
                  "--experimental_skystore_binary=" + str(binary)]
    cache_flags = ["--config=efcache", "--bes_backend="] if args.cache else []
    records = []
    marker = "initial"

    def stats():
        return json.loads((output_base / "skyframe-tiering.json").read_text())

    def run(name, command="build", extra=(), expected=0, tier=True,
            targets=("//:all",), indexes=None):
        argv = startup + [command] + (tier_flags if tier and not args.vanilla else [])
        if command in ("build", "test"):
            argv += cache_flags + ["--jobs=4"]
        argv += list(extra) + list(targets)
        start = time.monotonic()
        result = subprocess.run(argv, cwd=workspace, text=True, timeout=180,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (root / (name + ".log")).write_text(result.stdout)
        record = {"name": name, "exit_code": result.returncode,
                  "wall_seconds": time.monotonic() - start}
        if (output_base / "skyframe-tiering.json").exists():
            record["tiering"] = stats()
        if command == "aquery":
            record["action_keys"] = sorted(re.findall(r'action_key: "([^"]+)"', result.stdout))
            assert len(record["action_keys"]) == count * 2, (name, "missing action keys")
        records.append(record)
        (root / "results.json").write_text(json.dumps(records, indent=2) + "\n")
        assert result.returncode == expected, (name, result.returncode, result.stdout[-5000:])
        if expected == 0 and command == "build":
            hashes = {}
            for i in range(count) if indexes is None else indexes:
                actual = (workspace / f"bazel-bin/b{i}.out").read_text()
                wanted = "".join(p.read_text() for p in sorted(data.glob(f"i{i}*.txt"))) + marker + "\n"
                assert actual == wanted, (name, i, actual, wanted)
                hashes[str(i)] = hashlib.sha256(actual.encode()).hexdigest()
            record["output_sha256"] = hashes
        (root / "results.json").write_text(json.dumps(records, indent=2) + "\n")
        print(name, "PASS", flush=True)
        return record

    def check_paging():
        if not args.vanilla:
            state = stats()
            assert state["enabled"] and state["cold_nodes"] > 0, state
            assert state["resident_nodes"] - state["pinned_nodes"] <= 2, state

    try:
        run("cold")
        check_paging()
        run("warm")
        run("action-keys", command="aquery", extra=["--output=textproto"], targets=["deps(//:all)"])
        run("left", targets=["//:left"], indexes=range(count // 2))
        run("right", targets=["//:right"], indexes=range(count // 2, count))
        check_paging()
        (data / "i0.txt").write_text("edited-cold-input\n")
        run("source-edit")
        (data / "i0extra.txt").write_text("new-glob-member\n")
        run("glob-addition")
        (data / "i0extra.txt").unlink()
        run("glob-deletion")
        marker = "changed-bzl"
        defs.write_text('MARKER = "changed-bzl"\n')
        run("bzl-edit")
        marker = "changed-build"
        build.write_text(build.read_text().replace('+ MARKER +', '+ "changed-build" +'))
        run("build-edit")
        run("flag-change", extra=["--define=tier_test=changed"])
        run("keep-going-error", extra=["--keep_going"], targets=["//:all", "//:bad"], expected=1)
        run("after-error")
        run("cycle", targets=["//:cycle"], expected=1)
        run("after-cycle")
        before_query = stats() if not args.vanilla else None
        run("query", command="query", targets=["deps(//:all)"])
        if before_query:
            assert stats()["page_ins"] == before_query["page_ins"], "query loaded action nodes"
        run("after-query")
        if not args.vanilla:
            old = stats()["sidecar_pid"]
            os.kill(old, signal.SIGKILL)
            run("sidecar-killed")
            assert stats()["sidecar_pid"] != old
            run("mode-inmemory", tier=False)
            assert not stats()["enabled"]
            run("mode-tiered-again")
            check_paging()
            # A generation reset must tolerate unavailable storage without crashing the server.
            binary.rename(root / "skystore.hidden")
            run("restart-failure", extra=["--nokeep_state_after_build"])
            assert not stats()["enabled"]
            (root / "skystore.hidden").rename(binary)
            run("restart-recovery")
            check_paging()
        run("drop-state", extra=["--nokeep_state_after_build"])
        run("after-drop-state")
        run("shutdown", command="shutdown", tier=False, targets=[])
        run("server-restart")
        run("clean", command="clean", targets=[])
        run("after-clean")
        check_paging()
    finally:
        subprocess.run(startup + ["shutdown"], cwd=workspace, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
