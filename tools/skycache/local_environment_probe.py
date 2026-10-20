#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Check actual cache reuse and outputs across relevant/irrelevant client environments."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from storage_preflight import check_storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--lock-file", required=True, type=Path)
    args = parser.parse_args()
    binary = args.bazel.resolve(strict=True)
    root = args.root.resolve()
    initial_storage = check_storage([root])
    root.mkdir(parents=True, exist_ok=False)
    seed = root / "seed"
    seed.mkdir()
    (seed / "MODULE.bazel").write_text(
        'module(name="skycache_environment_probe")\n'
        'env_repo = use_repo_rule("//:environment.bzl", "env_repo")\n'
        'env_repo(name="environment")\n'
    )
    (seed / "BUILD.bazel").write_text('exports_files(["environment.bzl"])\n')
    (seed / "environment.bzl").write_text(
        'def _impl(ctx):\n'
        '    ctx.file("BUILD.bazel", \'exports_files(["value"])\\n\')\n'
        '    ctx.file("value", ctx.getenv("SKYCACHE_PROBE_VALUE", "repo-default") + "\\n")\n'
        'env_repo = repository_rule(implementation=_impl, environ=["SKYCACHE_PROBE_VALUE"])\n'
    )
    (seed / "lib").mkdir()
    command = 'cat $(SRCS) > "$@"; printf \'%s\\n\' "$${SKYCACHE_PROBE_VALUE:-action-default}" >> "$@"'
    (seed / "lib/BUILD.bazel").write_text(
        'genrule(name="payload", srcs=["@environment//:value"], outs=["payload.txt"], '
        f'cmd={json.dumps(command)}, visibility=["//visibility:public"])\n'
    )
    (seed / "app").mkdir()
    (seed / "app/BUILD.bazel").write_text(
        'genrule(name="all", srcs=["//lib:payload"], outs=["all.txt"], cmd="cat $(SRCS) > $@")\n'
    )

    def git(*arguments):
        subprocess.run(["git", "-C", str(seed), *arguments], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    git("init", "--quiet")
    git("add", ".")
    git("-c", "user.name=SkyCache Probe", "-c", "user.email=skycache-probe@example.invalid",
        "commit", "--quiet", "-m", "Environment compatibility fixture")
    for name in ("a", "b"):
        git("worktree", "add", "--detach", str(root / name), "HEAD")

    def startup(name):
        return [str(binary), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
                "--host_jvm_args=-Xmx2g", "--output_base=" + str(root / ("out-" + name))]

    def shutdown(name):
        subprocess.run(startup(name) + ["shutdown"], cwd=root / name, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)

    cases = [
        ("strict-unrelated", [], True, ["repo-default\naction-default\n"] * 2),
        ("repository-inherited", ["--repo_env=SKYCACHE_PROBE_VALUE"], False,
         ["one\naction-default\n", "two\naction-default\n"]),
        ("repository-fixed", ["--repo_env=SKYCACHE_PROBE_VALUE=fixed"], True,
         ["fixed\naction-default\n"] * 2),
        ("action-inherited", ["--action_env=SKYCACHE_PROBE_VALUE"], False,
         ["repo-default\none\n", "repo-default\ntwo\n"]),
        ("separate-repo-action-scopes",
         ["--repo_env=SKYCACHE_PROBE_VALUE=fixed", "--action_env=SKYCACHE_PROBE_VALUE"], False,
         ["fixed\none\n", "fixed\ntwo\n"]),
        ("test-inherited", ["--test_env=SKYCACHE_PROBE_VALUE"], False,
         ["repo-default\naction-default\n"] * 2),
        ("pwd-explicit", ["--repo_env=PWD"], False, ["repo-default\naction-default\n"] * 2),
        ("nonstrict", ["--noexperimental_strict_repo_env"], False,
         ["one\naction-default\n", "two\naction-default\n"]),
        ("bzlmod-client", [], False, ["repo-default\naction-default\n"] * 2),
    ]
    protocol = {"binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "purpose": "environment correctness, not a timing benchmark",
                "initial_storage": initial_storage, "cases": []}
    with args.lock_file.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            for label, options, should_hit, expected in cases:
                record = {"case": label, "reader_should_hit": should_hit,
                          "storage_preflight": check_storage([root]), "builds": []}
                for index, name in enumerate(("a", "b")):
                    environment = dict(os.environ, PWD=str(root / name),
                                       SKYCACHE_UNRELATED_SESSION=f"{label}-{name}",
                                       SKYCACHE_PROBE_VALUE=("one" if name == "a" else "two"))
                    environment.pop("BZLMOD_ALLOW_YANKED_VERSIONS", None)
                    if label == "bzlmod-client" and name == "a":
                        environment["BZLMOD_ALLOW_YANKED_VERSIONS"] = "all"
                    output_base = root / ("out-" + name)
                    stats = output_base / "local-skycache.json"
                    stats.unlink(missing_ok=True)
                    argv = startup(name) + [
                        "build", "//app:all", "--jobs=2", "--local_resources=memory=2048",
                        "--experimental_strict_repo_env", "--incompatible_strict_action_env",
                        "--incompatible_repo_env_ignores_action_env", "--lockfile_mode=off",
                        "--experimental_local_skycache=" + str(root / "cache" / label),
                        "--experimental_active_directories=app", "--color=no", "--curses=no",
                        *options,
                    ]
                    log_path = root / f"{label}-{name}.log"
                    started = time.monotonic()
                    with log_path.open("w") as log:
                        result = subprocess.run(argv, cwd=root / name, env=environment,
                                                stdout=log, stderr=subprocess.STDOUT)
                    assert result.returncode == 0, log_path
                    text = log_path.read_text()
                    payload = (root / name / "bazel-bin/app/all.txt").read_bytes()
                    row = {"worktree": name, "argv": argv, "exit_code": result.returncode,
                           "seconds": time.monotonic() - started,
                           "analysis_hits": sum(int(a) for a, _ in re.findall(
                               r"CONFIGURED_TARGET: (\d+)/(\d+)", text)),
                           "skycache": json.loads(stats.read_text()),
                           "output_sha256": hashlib.sha256(payload).hexdigest(),
                           "output": payload.decode()}
                    record["builds"].append(row)
                    assert payload == expected[index].encode(), (label, name, row)
                    assert not row["skycache"]["failed"], row
                    shutdown(name)
                protocol["cases"].append(record)
                (root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
                print(json.dumps(record), flush=True)
                assert record["builds"][0]["skycache"]["published"], record
                assert (record["builds"][1]["analysis_hits"] > 0) == should_hit, record
            protocol["passed"] = True
            (root / "results.json").write_text(json.dumps(protocol, indent=2) + "\n")
        finally:
            for name in ("a", "b"):
                shutdown(name)


if __name__ == "__main__":
    main()
