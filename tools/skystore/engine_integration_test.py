#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Linux end-to-end correctness and recovery tests for the optional idle value tier."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", type=Path, required=True)
    parser.add_argument("--skystore", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    workspace = root / "workspace"
    workspace.mkdir()
    data = workspace / "data"
    data.mkdir()
    for i in range(200):
        (data / f"long-filename-for-tiering-{i:04}.txt").write_text(f"{i:04}\n")
    (workspace / "MODULE.bazel").write_text("module(name='tiering_test')\n")
    defs = workspace / "defs.bzl"
    defs.write_text('MARKER = "initial"\n')
    build_file = workspace / "BUILD.bazel"
    build_file.write_text(
        'load(":defs.bzl", "MARKER")\n'
        'genrule(name="result", srcs=glob(["data/*.txt"]), outs=["result.txt"], '
        'cmd="echo " + MARKER + " > $@; cat $(SRCS) >> $@")\n'
        'genrule(name="bad", outs=["bad.txt"], cmd="exit 1")\n'
        'filegroup(name="cycle", srcs=[":cycle"])\n'
    )
    output_base = root / "output-base"
    startup = [str(args.bazel.resolve()), "--host_jvm_args=-Xmx2g",
               "--output_base=" + str(output_base)]
    tier_flags = ["--experimental_skyframe_engine=tiered_idle",
                  "--experimental_skystore_binary=" + str(args.skystore.resolve())]
    cache_flags = ["--config=efcache", "--bes_backend="] if args.cache else []
    records = []
    marker = "initial"

    def stats():
        return json.loads((output_base / "skyframe-tiering.json").read_text())

    def run(name, command="build", extra=(), expected=0, tier=True, targets=("//:result",)):
        argv = startup + [command] + (tier_flags if tier else [])
        if command in ("build", "test"):
            argv += cache_flags + ["--jobs=4"]
        argv += list(extra) + list(targets)
        start = time.monotonic()
        result = subprocess.run(argv, cwd=workspace, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (root / (name + ".log")).write_text(result.stdout)
        record = {"name": name, "exit_code": result.returncode,
                  "wall_seconds": time.monotonic() - start}
        if (output_base / "skyframe-tiering.json").exists():
            record["tiering"] = stats()
        records.append(record)
        (root / "results.json").write_text(json.dumps(records, indent=2) + "\n")
        assert result.returncode == expected, (name, result.returncode, result.stdout[-4000:])
        if expected == 0 and command == "build":
            actual = (workspace / "bazel-bin/result.txt").read_text()
            wanted = marker + "\n" + "".join(p.read_text() for p in sorted(data.glob("*.txt")))
            assert actual == wanted, (name, "output mismatch")
            record["output_sha256"] = hashlib.sha256(actual.encode()).hexdigest()
        print(name, "PASS", flush=True)
        return record

    try:
        run("cold")
        assert stats()["detached_values"] > 0, "test must exercise real value spilling"
        count = stats()["detached_values"]
        run("warm")
        assert stats()["restored_values"] == count
        (data / "long-filename-for-tiering-0001.txt").write_text("edited\n")
        run("source-edit")
        (data / "new-file-in-glob.txt").write_text("new\n")
        run("glob-addition")
        (data / "long-filename-for-tiering-0002.txt").unlink()
        run("glob-deletion")
        marker = "changed-bzl"
        defs.write_text('MARKER = "changed-bzl"\n')
        run("bzl-edit")
        marker = "changed-build"
        build_file.write_text(build_file.read_text().replace('+ MARKER +', '+ "changed-build" +'))
        run("build-edit")
        run("flag-change", extra=["--define=tier_test=changed"])
        run("keep-going-error", extra=["--keep_going"], targets=["//:result", "//:bad"], expected=1)
        run("after-error")
        run("cycle", targets=["//:cycle"], expected=1)
        run("after-cycle")
        run("query", command="query", targets=["deps(//:result)"])
        run("after-query")
        old = stats()
        os.kill(old["sidecar_pid"], signal.SIGKILL)
        run("sidecar-killed")
        assert stats()["recovery_count"] > old["recovery_count"]
        old = stats()
        cmdline = Path(f'/proc/{old["sidecar_pid"]}/cmdline').read_bytes().split(b"\0")
        spill = Path(os.fsdecode(cmdline[2]))
        assert spill.parent.name.startswith("bazel-skystore-") and spill.name == "values"
        with spill.open("r+b") as f:
            first = f.read(1)
            assert first, "corruption test needs stored bytes"
            f.seek(0)
            f.write(bytes([first[0] ^ 0x80]))
        run("spill-corruption")
        assert stats()["recovery_count"] > old["recovery_count"]
        run("mode-inmemory", tier=False)
        assert not stats()["enabled"]
        run("mode-tiered-again")
        run("drop-state", extra=["--nokeep_state_after_build"])
        assert stats()["detached_values"] == 0
        run("after-drop-state")
        run("shutdown", command="shutdown", tier=False, targets=[])
        run("server-restart")
        run("clean", command="clean", targets=[])
        run("after-clean")
        (root / "results.json").write_text(json.dumps(records, indent=2) + "\n")
    finally:
        subprocess.run(startup + ["shutdown"], cwd=workspace,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


if __name__ == "__main__":
    main()
