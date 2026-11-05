"""Four persistent Windows Bazel servers rebuilding divergent Datadog overlays."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from bazel_stress_metrics import (cohort_job_pids, govern_current_process, hash_target_outputs,
                                  is_cohort_job_member, peak_job_commit_bytes, read_metrics)
from datadog_cases import clean_env, git
import datadog_stress_target as fixture


TARGETS = (fixture.TARGET, fixture.SPAWN_TARGET)
BASE = (("base", "base"),) * 4
DIVERGE = (("source", "base"), ("build", "source"), ("base", "build"), ("source", "source"))
ROTATE = (("build", "source"), ("source", "base"), ("source", "build"), ("base", "base"))
WAVES = (("cold", BASE), ("noop_1", BASE), ("noop_2", BASE),
         ("diverge", DIVERGE), ("divergent_noop", DIVERGE),
         ("rotate", ROTATE), ("restore", BASE), ("restored_noop", BASE))


def save(path, report):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def sample_rss(stop, stats):
    # Parent-PID traversal loses detached Bazel servers once the client exits.
    # Use the kernel's job membership list instead. Shared resident pages may
    # appear in several processes, and short peaks can fall between samples.
    try:
        import psutil
        while not stop.is_set():
            total = count = 0
            for pid in cohort_job_pids():
                try:
                    total += psutil.Process(pid).memory_info().rss
                    count += 1
                except psutil.NoSuchProcess:
                    pass
            stats["samples"] += 1
            stats["peak_observed_members"] = max(stats["peak_observed_members"], count)
            stats["peak_sampled_job_rss_bytes"] = max(stats["peak_sampled_job_rss_bytes"] or 0, total)
            stop.wait(.10)
    except Exception as exc:
        stats.update(sampler_error=str(exc), peak_sampled_job_rss_bytes=None)


def run(args):
    root, repo = args.worker, args.repo
    budget = govern_current_process(8, 8192)
    version = git(repo, "show", fixture.REVISION + ":.bazelversion").decode().strip()
    env = dict(clean_env(), USE_BAZEL_VERSION=version, DD_BAZEL_REMOTE_CACHE="off")
    actual_version = subprocess.check_output([args.bazel, "--version"], env=env, text=True).strip()
    if actual_version != "bazel " + version:
        raise ValueError(f"unexpected Bazel version {actual_version}")
    report = dict(revision=fixture.REVISION, targets=TARGETS, version=actual_version,
                  host=platform.platform(), resource_budget=budget,
                  isolation="4 persistent servers, private output bases, no disk/remote action cache",
                  waves=[], shutdown=[], completed=False)
    save(args.output, report)
    oracle = {}
    workspaces = [root / f"w{i}" for i in range(4)]
    for ws in workspaces:
        fixture.materialize(repo, ws, "base")
    for target in TARGETS:
        oracle[target] = {}
        for variant in fixture.VARIANTS:
            data = fixture.expected_output(repo, variant, target)
            oracle[target][variant] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    report["expected_outputs"] = oracle
    server_pids = {}
    startup = [[args.bazel, "--ignore_all_rc_files", f"--output_user_root={root / 'i'}",
                f"--output_base={root / f'o{i}'}", "--max_idle_secs=300",
                "--host_jvm_args=-Xms64m", "--host_jvm_args=-Xmx640m",
                "--host_jvm_args=-XX:ActiveProcessorCount=2"] for i in range(4)]
    common = ["build", *TARGETS, *fixture.BUILD_FLAGS, "--jobs=2", "--loading_phase_threads=2",
              "--local_resources=cpu=2", "--local_resources=memory=2048",
              "--color=no", "--curses=no", "--noshow_progress", "--disk_cache=",
              f"--repository_cache={root / 'r'}"]
    # On Windows, four launchers racing to create a missing output_user_root
    # can fail with ERROR_ALREADY_EXISTS. Pre-extract only the shared binary in
    # a separate batch output base. All four measured JVMs and bases are fresh.
    (root / "i").mkdir()
    (root / "r").mkdir()
    subprocess.run([args.bazel, "--batch", "--ignore_all_rc_files", f"--output_user_root={root / 'i'}",
                    f"--output_base={root / 'prep'}", "--host_jvm_args=-Xmx256m",
                    "--host_jvm_args=-XX:ActiveProcessorCount=2", "info", "install_base"],
                   cwd=workspaces[0], env=env, check=True, capture_output=True, timeout=90)
    report["install_preparation"] = "shared binary pre-extracted outside timings in a separate batch output base"

    def build(i, name, barrier, origin):
        stem = root / f"{name}_{i}"
        bep, profile, log = stem.with_suffix(".jsonl"), stem.with_suffix(".json.gz"), stem.with_suffix(".log")
        cmd = [*startup[i], *common, f"--build_event_json_file={bep}", f"--profile={profile}"]
        barrier.wait(timeout=30)
        start = time.perf_counter()
        with log.open("wb") as f:
            try:
                rc = subprocess.run(cmd, cwd=workspaces[i], env=env, stdout=f,
                                    stderr=subprocess.STDOUT, timeout=180).returncode
            except subprocess.TimeoutExpired:
                rc = -999
        end = time.perf_counter()
        row = {"view": i, "exit": rc, "start_s": round(start - origin, 6),
               "end_s": round(end - origin, 6), "client_ms": round((end - start) * 1000, 3)}
        if rc:
            row["diagnostic"] = log.read_text(encoding="utf-8", errors="replace")[-2000:]
        return row

    def shutdown(i):
        try:
            result = subprocess.run([*startup[i], "shutdown"], cwd=workspaces[i], env=env,
                                    capture_output=True, timeout=45)
            return {"view": i, "exit": result.returncode,
                    "diagnostic": result.stderr.decode(errors="replace")[-500:] if result.returncode else None}
        except subprocess.TimeoutExpired:
            return {"view": i, "exit": -999}

    with ThreadPoolExecutor(max_workers=4) as pool:
        try:
            for name, variants in WAVES:
                for ws, edits in zip(workspaces, variants):
                    for target, variant in zip(TARGETS, edits):
                        fixture.apply_variant(repo, ws, variant, target)
                barrier = threading.Barrier(4)
                stop = threading.Event()
                rss = {"samples": 0, "peak_observed_members": 0, "peak_sampled_job_rss_bytes": None,
                       "interval_ms": 100}
                thread = threading.Thread(target=sample_rss, args=(stop, rss), daemon=True)
                thread.start()
                origin = time.perf_counter()
                try:
                    jobs = [pool.submit(build, i, name, barrier, origin) for i in range(4)]
                    rows = [job.result() for job in jobs]
                finally:
                    stop.set()
                    thread.join()
                wave = {"name": name, "clients": rows, "memory": rss,
                        "makespan_ms": round(1000 * (max(r["end_s"] for r in rows)
                                                    - min(r["start_s"] for r in rows)), 3)}
                for i, (edits, row) in enumerate(zip(variants, rows)):
                    stem = root / f"{name}_{i}"
                    bep = stem.with_suffix(".jsonl")
                    m = read_metrics(bep, stem.with_suffix(".json.gz"))
                    row.update(variants=dict(zip(TARGETS, edits)), metrics=m)
                    pid = m["server_pid"]
                    row["server_in_job"] = is_cohort_job_member(pid)
                    if name == "cold":
                        server_pids[i] = pid
                    row["same_server"] = pid is not None and pid == server_pids.get(i)
                    row["outputs"] = {}
                    try:
                        for target in TARGETS:
                            row["outputs"][target] = hash_target_outputs(bep, target)
                    except (OSError, ValueError, KeyError) as exc:
                        row["verification_error"] = str(exc)
                    row["valid"] = (
                        row["exit"] == 0 and m["finished"] and m["exit_code"] == 0
                        and m["build_tool_version"] == version and m["metrics_present"]
                        and row["server_in_job"] and row["same_server"]
                        and all(list(row["outputs"].get(t, {}).values()) == [oracle[t][v]]
                                for t, v in row["variants"].items())
                    )
                wave["passed"] = all(r["valid"] for r in rows) and len(set(server_pids.values())) == 4
                wave["peak_job_commit_bytes_so_far"] = peak_job_commit_bytes()
                report["waves"].append(wave)
                save(args.output, report)
                print(name, wave["makespan_ms"], "ms,", "pass" if wave["passed"] else "failed", flush=True)
                if not wave["passed"]:
                    raise RuntimeError("warm stress validation failed; see saved report")
        finally:
            report["shutdown"] = list(pool.map(shutdown, range(4)))
            report["peak_job_commit_bytes"] = peak_job_commit_bytes()
            save(args.output, report)
    if any(r["exit"] for r in report["shutdown"]):
        raise RuntimeError("one or more owned servers did not shut down cleanly")
    report["completed"] = True
    save(args.output, report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("this experiment measures a Windows job with a hard aggregate limit")
    if args.worker:
        run(args)
        return
    bazel = shutil.which(args.bazel)
    if not bazel:
        parser.error("Bazel unavailable")
    with tempfile.TemporaryDirectory(prefix="di-") as root:
        # Job lifetime ends before scratch cleanup. A killed worker also releases
        # lingering descendants thanks to the Windows job's kill-on-close limit.
        subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--bazel", bazel,
                        "--repo", str(args.repo.resolve()), "--output", str(args.output.resolve()),
                        "--worker", root], check=True)


if __name__ == "__main__":
    main()
