#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Verify identity when separate publications contribute branches of a provider diamond.

Usage: partitioned_provider_identity_probe.py BAZEL NEW_DIRECTORY [no-runfiles] [warm-left]
The optional no-runfiles control removes the identity-bearing provider field.
warm-left first evaluates the reader's left branch against an empty cache, then exposes
the other writers' publications without restarting its server. All actions run locally.
The protocol is saved before any output-equivalence assertion.
"""

from pathlib import Path
import json
import os
import subprocess
import sys
import time

binary = Path(sys.argv[1])
root = Path(sys.argv[2])
root.mkdir(parents=True, exist_ok=False)
seed = root / "seed"
seed.mkdir()
(seed / "MODULE.bazel").write_text('module(name="identity_probe")\n')
(seed / "BUILD").write_text("")
(seed / "defs.bzl").write_text('''
Data = provider()
Graph = provider(fields = ["values"])
def _lib(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".data")
    ctx.actions.write(out, ctx.label.name)
    data = Data(label = ctx.label, source = ctx.file.src,
                file = out, runfiles = ctx.runfiles(files = [ctx.file.src]))
    return [DefaultInfo(files = depset([out])),
            Graph(values = depset([data], transitive = [d[Graph].values for d in ctx.attr.deps]))]
lib = rule(implementation = _lib, attrs = {
    "src": attr.label(allow_single_file = True),
    "deps": attr.label_list(providers = [Graph]),
})
def _probe(ctx):
    graph = depset(transitive = [d[Graph].values for d in ctx.attr.deps])
    values = graph.to_list()
    lines = ["count=" + str(len(values))]
    by_label = {}
    for value in values:
        label = str(value.label)
        if label in by_label:
            previous = by_label[label]
            lines.append("duplicate=" + label)
            for name in ["label", "source", "file", "runfiles"]:
                lines.append(name + "=" + str(getattr(previous, name) == getattr(value, name)))
        by_label[label] = value
    out = ctx.actions.declare_file(ctx.label.name + ".txt")
    args = ctx.actions.args()
    args.add(out)
    args.add_all(graph, map_each = _format)
    ctx.actions.run_shell(outputs = [out], inputs = [ctx.file.src] + [v.file for v in values],
        arguments = [args], command = 'out="$1"; shift; printf "count=%s\\n" "$#" > "$out"; printf "%s\\n" "$@" >> "$out"')
    return [DefaultInfo(files = depset([out]))]
def _format(value):
    return str(value.label)
probe = rule(implementation = _probe, attrs = {
    "deps": attr.label_list(providers = [Graph]), "src": attr.label(allow_single_file = True)})
''')
if "no-runfiles" in sys.argv[3:]:
    definition = seed / "defs.bzl"
    definition.write_text(definition.read_text().replace(
        "runfiles = ctx.runfiles(files = [ctx.file.src])", "runfiles = None"))
for name, deps in [("leaf", []), ("left", ["//leaf:lib"]), ("right", ["//leaf:lib"])]:
    package = seed / name
    package.mkdir()
    (package / "input").write_text(name)
    (package / "BUILD").write_text(
        f'load("//:defs.bzl", "lib")\nlib(name="lib", src="input", deps={deps!r}, visibility=["//visibility:public"])\n')
(seed / "app").mkdir()
(seed / "app/input").write_text("active source")
(seed / "app/BUILD").write_text('load("//:defs.bzl", "probe")\nprobe(name="probe", src="input", deps=["//left:lib", "//right:lib"])\n')
(seed / "app/BUILD").write_text((seed / "app/BUILD").read_text() +
    'probe(name="left_probe", src="input", deps=["//left:lib"])\n' +
    'probe(name="right_probe", src="input", deps=["//right:lib"])\n')
for argv in (["git", "init", "-q"], ["git", "add", "."],
             ["git", "-c", "user.name=SkyCache probe", "-c", "user.email=probe@localhost",
              "commit", "-qm", "provider identity fixture"]):
    subprocess.run(argv, cwd=seed, check=True)

import fcntl
import re
import shutil
results = []
shared_cache = root / "shared-cache"
shared_cache.mkdir()
def copy_publications(cache):
    # Simulate independent writers finishing without seeing each other's publications.
    # These are copies of complete immutable publications, not modified product data.
    for path in cache.rglob("*"):
        if path.is_file():
            destination = shared_cache / path.relative_to(cache)
            assert not destination.exists(), destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def build(role, target, startup, workspace, cache):
    argv = startup + ["build", "//app:" + target, "--jobs=2", "--lockfile_mode=off",
        "--experimental_strict_repo_env", "--experimental_local_skycache=" + str(cache),
        "--experimental_active_directories=app", "--color=no", "--curses=no"]
    environment = dict(os.environ, PWD=str(workspace))
    started = time.monotonic()
    with (root / (role + ".log")).open("w") as log:
        result = subprocess.run(argv, cwd=workspace, env=environment, stdout=log, stderr=subprocess.STDOUT)
    row = {"role": role, "target": target, "argv": argv,
           "exit_code": result.returncode, "seconds": time.monotonic() - started}
    output_base = Path(next(a.removeprefix("--output_base=") for a in startup if a.startswith("--output_base=")))
    if result.returncode == 0:
        output = next((output_base / "execroot/_main/bazel-out").glob("*/bin/app/" + target + ".txt"))
        row["output"] = output.read_text()
    stats = output_base / "local-skycache.json"
    if stats.exists():
        row["skycache"] = json.loads(stats.read_text())
    text = (root / (role + ".log")).read_text()
    row["analysis_hits"] = sum(int(hit) for hit in re.findall(r"CONFIGURED_TARGET: (\d+)/\d+", text))
    results.append(row)
    (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(row), flush=True)
    assert row["exit_code"] == 0, row
    return row


with open("/home/jgett/REPOS/.work/skyframe-measurements.lock", "a") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    for name, target in [("left", "left_probe"), ("right", "right_probe"), ("reader", "probe")]:
        workspace = root / name
        subprocess.run(["git", "-C", str(seed), "worktree", "add", "--detach", str(workspace)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cache = shared_cache if name == "reader" else root / ("cache-" + name)
        startup = [str(binary), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
                   "--host_jvm_args=-Xmx2g", "--output_base=" + str(root / ("out-" + name))]
        environment = dict(os.environ, PWD=str(workspace))
        try:
            if name == "reader":
                if "warm-left" in sys.argv[3:]:
                    warm = build("warm-left", "left_probe", startup, workspace, cache)
                    assert warm["analysis_hits"] == 0 and warm["skycache"]["shared_reads"] == 0, warm
                    assert warm["output"] == "count=2\n@@//leaf:lib\n@@//left:lib\n", warm
                copy_publications(root / "cache-left")
                copy_publications(root / "cache-right")
            row = build(name, target, startup, workspace, cache)
            if name != "reader":
                assert row["skycache"]["published"] and row["skycache"]["shared_reads"] == 0, row
                assert row["output"] == f"count=2\n@@//leaf:lib\n@@//{name}:lib\n", row
        finally:
            subprocess.run(startup + ["shutdown"], cwd=workspace, env=environment,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    expected = "count=3\n@@//leaf:lib\n@@//left:lib\n@@//right:lib\n"
    assert results[-1]["analysis_hits"] > 0 and results[-1]["skycache"]["shared_reads"] > 0, results
    assert results[-1]["output"] == expected, results
