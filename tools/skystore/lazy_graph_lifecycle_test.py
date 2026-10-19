#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Linux lifecycle checks for interrupted cleanup and paged/edgeless mode changes."""

import argparse
import json
import os
from pathlib import Path
import shlex
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
    output_base = root / "output-base"
    marker = root / "slow-action-started"
    (workspace / "MODULE.bazel").write_text("module(name='lazy_graph_lifecycle_test')\n")
    slow_command = "touch " + shlex.quote(str(marker)) + "; sleep 300; echo slow > $@"
    (workspace / "BUILD.bazel").write_text(
        'genrule(name="fast", outs=["fast.txt"], cmd="echo fast > $@")\n'
        'genrule(name="slow", outs=["slow.txt"], cmd='
        + json.dumps(slow_command)
        + ', tags=["local", "no-cache"])\n'
    )
    startup = [
        str(args.bazel.resolve()),
        "--host_jvm_args=-Xmx2g",
        "--output_base=" + str(output_base),
    ]
    tier_flags = [
        "--experimental_skyframe_engine=tiered_genrule",
        "--experimental_skystore_binary=" + str(args.skystore.resolve()),
        "--experimental_skyframe_resident_nodes=0",
    ]
    cache_flags = ["--config=efcache", "--bes_backend="] if args.cache else []
    records = []
    active_client = None

    def stats():
        return json.loads((output_base / "skyframe-tiering.json").read_text())

    def server_pid():
        return int((output_base / "server/server.pid.txt").read_text().strip())

    def live_server(expected_pid):
        actual = server_pid()
        assert actual == expected_pid, ("server PID changed", expected_pid, actual)
        os.kill(actual, 0)
        return actual

    def save(record):
        records.append(record)
        (root / "results.json").write_text(json.dumps(records, indent=2) + "\n")

    def argv(extra, target):
        return startup + ["build"] + tier_flags + cache_flags + [
            "--jobs=2",
            "--noshow_progress",
        ] + list(extra) + [target]

    def run(name, extra=()):
        start = time.monotonic()
        result = subprocess.run(
            argv(extra, "//:fast"), cwd=workspace, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
        )
        (root / (name + ".log")).write_text(result.stdout)
        record = {
            "name": name,
            "exit_code": result.returncode,
            "wall_seconds": time.monotonic() - start,
        }
        if (output_base / "skyframe-tiering.json").exists():
            record["tiering"] = stats()
        save(record)
        assert result.returncode == 0, (name, result.returncode, result.stdout[-4000:])
        assert (workspace / "bazel-bin/fast.txt").read_text() == "fast\n"
        print(name, "PASS", flush=True)
        return record

    try:
        run("initial-paged")
        assert stats()["enabled"] and stats()["cold_nodes"] > 0
        original_server = server_pid()
        start = time.monotonic()
        with (root / "interrupted-drop-state.log").open("w") as log:
            active_client = subprocess.Popen(
                argv(["--nokeep_state_after_build"], "//:slow"),
                cwd=workspace, stdout=log, stderr=subprocess.STDOUT,
            )
            deadline = time.monotonic() + 180
            while not marker.exists():
                assert active_client.poll() is None, "build exited before slow action started"
                assert time.monotonic() < deadline, "slow action did not start"
                time.sleep(0.05)
            marker_seen_seconds = time.monotonic() - start
            live_server(original_server)
            active_client.send_signal(signal.SIGINT)
            exit_code = active_client.wait(timeout=90)
            active_client = None
        record = {
            "name": "interrupted-drop-state",
            "exit_code": exit_code,
            "wall_seconds": time.monotonic() - start,
            "action_started_seconds": marker_seen_seconds,
            "server_pid_before": original_server,
            "tiering": stats(),
        }
        save(record)
        assert exit_code == 8, ("expected Bazel interrupted exit 8", exit_code)
        record["server_pid_after"] = live_server(original_server)
        if record["tiering"]["enabled"]:
            # Bazel may consume the action interruption before command cleanup. In that case
            # resetting can safely create a fresh empty generation instead of taking the
            # interrupted-thread fallback. Record which path this runtime actually exercised.
            assert record["tiering"]["nodes"] == 0, record
            record["cleanup_path"] = "fresh_empty_paged_generation"
        else:
            assert record["tiering"]["reason"] == "interrupted", record
            record["cleanup_path"] = "interrupted_fallback"
        print("interrupted-drop-state PASS", flush=True)
        run("after-interruption")
        assert stats()["enabled"] and stats()["cold_nodes"] > 0
        live_server(original_server)
        run("edgeless", ["--notrack_incremental_state", "--nokeep_state_after_build"])
        assert not stats()["enabled"] and stats()["reason"] == "edgeless"
        live_server(original_server)
        run("incremental-again", ["--track_incremental_state"])
        assert stats()["enabled"] and stats()["cold_nodes"] > 0
        live_server(original_server)
        (root / "results.json").write_text(json.dumps(records, indent=2) + "\n")
    finally:
        if active_client is not None and active_client.poll() is None:
            active_client.send_signal(signal.SIGINT)
            try:
                active_client.wait(timeout=30)
            except subprocess.TimeoutExpired:
                active_client.kill()
                active_client.wait(timeout=10)
        subprocess.run(
            startup + ["shutdown"], cwd=workspace,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=90,
        )


if __name__ == "__main__":
    main()
