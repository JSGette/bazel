#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Verify depset command-line expansion after sharing a graph between two real worktrees.

Usage: provider_identity_probe.py BAZEL NEW_DIRECTORY [no-runfiles]
The optional no-runfiles control removes the identity-bearing provider field.
Both builds execute the output action locally, without a shared action cache.
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
    out = ctx.actions.declare_file("result.txt")
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
if len(sys.argv) > 3 and sys.argv[3] == "no-runfiles":
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
for argv in (["git", "init", "-q"], ["git", "add", "."],
             ["git", "-c", "user.name=SkyCache probe", "-c", "user.email=probe@localhost",
              "commit", "-qm", "provider identity fixture"]):
    subprocess.run(argv, cwd=seed, check=True)
results = []
for name in ("writer", "reader"):
    workspace = root / name
    subprocess.run(["git", "-C", str(seed), "worktree", "add", "--detach", str(workspace)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    startup = [str(binary), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
               "--host_jvm_args=-Xmx2g", "--output_base=" + str(root / ("out-" + name))]
    argv = startup + ["build", "//app:probe", "--jobs=2", "--lockfile_mode=off",
        "--experimental_strict_repo_env", "--experimental_local_skycache=" + str(root / "cache"),
        "--experimental_active_directories=app", "--color=no", "--curses=no"]
    started = time.monotonic()
    environment = dict(os.environ, PWD=str(workspace))
    with (root / f"{name}.log").open("w") as log:
        result = subprocess.run(argv, cwd=workspace, env=environment, stdout=log, stderr=subprocess.STDOUT)
    row = {"role": name, "exit_code": result.returncode, "seconds": time.monotonic() - started,
           "argv": argv}
    if result.returncode == 0:
        output = next((root / ("out-" + name) / "execroot/_main/bazel-out").glob("*/bin/app/result.txt"))
        row["output"] = output.read_text()
    row["skycache"] = json.loads((root / ("out-" + name) / "local-skycache.json").read_text())
    results.append(row)
    (root / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    subprocess.run(startup + ["shutdown"], cwd=workspace, env=environment,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps(row), flush=True)
assert all(row["exit_code"] == 0 and not row["skycache"]["failed"] for row in results), results
assert results[0]["output"].startswith("count=3\n"), results
assert results[1]["skycache"]["shared_reads"] > 0, results
assert results[0]["output"] == results[1]["output"], results
