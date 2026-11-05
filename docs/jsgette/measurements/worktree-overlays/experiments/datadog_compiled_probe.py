"""Qualify Datadog's compiled eBPF include processor in two isolated Bazel workspaces."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from bazel_stress_metrics import govern_current_process, hash_target_outputs, peak_job_commit_bytes, read_metrics
from datadog_build_stress import logged_spawns
from datadog_cases import clean_env, git
from datadog_stress_target import BUILD_FLAGS, COMPILED_TARGET, REVISION, apply_variant, materialize


def verify_program(executable, variant, scratch):
    """One input includes three real headers. Each binary skips a different one."""
    filenames = {"base": "vmlinux.h", "source": "vmlinux-source.h", "build": "vmlinux-build.h"}
    scratch.mkdir()
    for kind, name in filenames.items():
        (scratch / name).write_bytes(f"/* {kind} */\n".encode())
    (scratch / "input.c").write_bytes(b"".join(
        f'#include "{name}"\n'.encode() for name in filenames.values()))
    subprocess.run([str(executable), "input.c", "output.c"], cwd=scratch, check=True, timeout=10)
    produced = (scratch / "output.c").read_bytes()
    expected = b"".join(f"/* {name} */\n".encode() for name in filenames if name != variant)
    if produced != expected:
        raise ValueError(f"Wrong {variant} behavior: {produced!r} != {expected!r}")
    return produced.decode()


def qualify(args, root, report):
    if shutil.disk_usage(root).free < 10 * 1024**3:
        raise RuntimeError("Compiler output bases need at least 10 GiB of free scratch space")
    if os.name == "nt" or hasattr(os, "sched_setaffinity"):
        report["governor"] = govern_current_process(8, 8192)
    repo = args.repo.resolve()
    env = clean_env()
    version = git(repo, "show", REVISION + ":.bazelversion").decode().strip()
    env["USE_BAZEL_VERSION"] = version
    if os.name == "nt":
        git_bash = Path(env.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if git_bash.is_file():
            env["BAZEL_SH"] = str(git_bash)
    bazel = shutil.which(args.bazel)
    report["bazel"] = subprocess.check_output([bazel, "--version"], env=env, text=True).strip()
    if report["bazel"] != "bazel " + version:
        raise RuntimeError(f"Need Bazel {version}, found {report['bazel']}")
    count = 4 if args.concurrent_cached else 2
    for i in range(count):
        materialize(repo, root / f"w{i}", "base", COMPILED_TARGET)
    (root / "install").mkdir()
    if args.concurrent_cached:
        (root / "disk-cache").mkdir()
        subprocess.run([bazel, "--batch", "--ignore_all_rc_files",
                        f"--output_user_root={root / 'install'}", f"--output_base={root / 'launcher'}",
                        "help", "startup_options"], cwd=root / "w0", env=env,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True, timeout=60)
        report["launcher_preflight"] = "help startup_options prepares Bazel/JDK installation; no target build"
        report["disk_action_cache_initially_empty"] = not any((root / "disk-cache").iterdir())
    origin = time.monotonic()
    edit_start = threading.Barrier(count, timeout=30)
    cache = str(root / "disk-cache") if args.concurrent_cached else ""

    def build(num, i, variant):
        ws, out = root / f"w{i}", root / f"o{i}"
        apply_variant(repo, ws, variant, COMPILED_TARGET)
        print(f"Building workspace {i} variant {variant}", file=sys.stderr, flush=True)
        bep, spawns = root / f"b{num}.jsonl", root / f"s{num}.json"
        flags = ["--batch", "--ignore_all_rc_files", "--host_jvm_args=-Xmx768m",
                 f"--output_user_root={root / 'install'}", f"--output_base={out}", "build",
                 *BUILD_FLAGS, "--@rules_go//go/config:pure=true", "--jobs=2", "--noshow_progress",
                 f"--disk_cache={cache}", f"--build_event_json_file={bep}",
                 f"--execution_log_json_file={spawns}", COMPILED_TARGET]
        if args.repository_cache:
            flags.insert(-1, f"--repository_cache={args.repository_cache.resolve()}")
        if args.concurrent_cached or variant != "base":
            edit_start.wait()
        start = time.monotonic() - origin
        run = subprocess.run([bazel, *flags], env=env, cwd=ws, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=args.timeout)
        finish = time.monotonic() - origin
        metrics = read_metrics(bep)
        row = {"ordinal": num, "workspace": i, "variant": variant, "exit": run.returncode,
               "start_s": round(start, 4), "finish_s": round(finish, 4),
               **{k: metrics[k] for k in ("packages_loaded", "targets_configured", "wall_ms",
                  "actions_executed", "runner_counts", "reported_executed_spawns", "disk_cache_hits")}}
        report["builds"].append(row)
        if run.returncode:
            row["diagnostic"] = run.stdout.decode(errors="replace")[-3000:]
            raise RuntimeError(row["diagnostic"])
        outputs = hash_target_outputs(bep, COMPILED_TARGET)
        if len(outputs) != 1:
            raise ValueError(f"Unexpected outputs: {outputs}")
        name, fingerprint = next(iter(outputs.items()))
        local = list((out / "execroot/_main/bazel-out").glob("*/bin/" + name))
        if len(local) != 1:
            raise ValueError(f"Cannot resolve executable {name}: {local}")
        row.update(output=name, **fingerprint, program_output=verify_program(local[0], variant, root / f"p{num}"))
        row["spawns"] = logged_spawns(spawns)
    if args.concurrent_cached:
        plans = [(0, 0, "base"), (1, 1, "base"), (2, 2, "source"), (3, 3, "build")]
    else:
        build(0, 0, "base")
        build(1, 1, "base")
        plans = [(2, 0, "source"), (3, 1, "build")]
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(build, *plan) for plan in plans]
        for future in futures:
            future.result()
    report["builds"].sort(key=lambda row: row["ordinal"])
    concurrent = report["builds"] if args.concurrent_cached else report["builds"][2:]
    report["concurrent_clients"] = len(concurrent)
    report["concurrent_overlap_s"] = round(min(r["finish_s"] for r in concurrent)
                                          - max(r["start_s"] for r in concurrent), 4)
    if report["concurrent_overlap_s"] <= 0:
        raise ValueError("The concurrent builds did not overlap")
    if args.concurrent_cached:
        for row in report["builds"]:
            row["matches_isolated_reference"] = row["sha256"] == report["reference_sha256"][row["variant"]]
            if not row["matches_isolated_reference"]:
                raise ValueError(f"Concurrent binary did not match isolated {row['variant']} reference")
    hashes = [r["sha256"] for r in report["builds"]]
    report["base_bytes_match_across_private_output_bases"] = hashes[0] == hashes[1]
    report["three_distinct_semantically_verified_outputs"] = len(set([hashes[0], *hashes[2:]])) == 3
    if not report["three_distinct_semantically_verified_outputs"]:
        raise ValueError("Distinct behavioral edits did not produce distinct binaries")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--repository-cache", type=Path, help="Optional private Bazel downloads cache")
    parser.add_argument("--preflight-record", type=Path, help="Include a prior toolchain failure record")
    parser.add_argument("--concurrent-cached", action="store_true",
                        help="Four fresh concurrent base/base/source/BUILD workspaces sharing one empty disk cache")
    parser.add_argument("--references", type=Path, help="Completed isolated qualification result for the cached arm")
    parser.add_argument("--prior-attempt", type=Path, help="Preserve a prior failed experiment inside this report")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=240, help="Seconds per build")
    args = parser.parse_args()
    report = {"revision": REVISION, "target": COMPILED_TARGET, "host": platform.platform(),
              "flags": [*BUILD_FLAGS, "--@rules_go//go/config:pure=true", "--jobs=2", "--disk_cache="],
              "mode": "two isolated cold base builds followed by concurrent source and BUILD edits; no disk action cache",
              "builds": [], "passed": False}
    if args.concurrent_cached:
        if not args.references:
            parser.error("--concurrent-cached requires --references")
        refs = json.loads(args.references.read_text(encoding="utf-8"))
        if not refs["passed"] or refs["revision"] != REVISION or refs["target"] != COMPILED_TARGET:
            raise ValueError("References must be a successful qualification of the same pinned target")
        report["reference_sha256"] = {r["variant"]: r["sha256"] for r in refs["builds"]}
        report["mode"] = "four fresh simultaneous workspaces sharing one initially empty disk action cache"
        report["flags"][-1] = "--disk_cache=<private initially empty shared cache>"
    if args.preflight_record:
        report["default_cgo_preflight"] = json.loads(args.preflight_record.read_text(encoding="utf-8"))
    if args.prior_attempt:
        report["prior_attempt"] = json.loads(args.prior_attempt.read_text(encoding="utf-8"))
    try:
        with tempfile.TemporaryDirectory(prefix="dd-cp-") as private:
            qualify(args, Path(private), report)
        report["passed"] = True
    finally:
        report["peak_job_commit_bytes"] = peak_job_commit_bytes()
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
