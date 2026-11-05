"""Run a fixed burst of real Datadog Bazel builds at different concurrency limits."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time

sys.dont_write_bytecode = True
from bazel_stress_metrics import govern_current_process, peak_job_commit_bytes, read_metrics
from datadog_cases import clean_env, git
from measure_process import measure


VIEWS = ("base", "source", "build", "base", "source", "build", "base", "base")
HERE = Path(__file__).resolve()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def sha_file(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def logged_spawns(path):
    # Bazel writes consecutive JSON objects, including cache retrievals.
    data = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    rows = []
    while data.strip():
        data = data.lstrip()
        spawn, end = decoder.raw_decode(data)
        metrics = spawn.get("metrics", {})
        rows.append({"mnemonic": spawn.get("mnemonic"), "runner": spawn.get("runner"),
                     "cache_hit": spawn.get("cacheHit"), "digest": spawn.get("digest", {}).get("hash"),
                     "start_time": metrics.get("startTime"), "execution_wall_time": metrics.get("executionWallTime")})
        data = data[end:]
    return rows


def cohort(cfg):
    target = importlib.import_module(cfg["module"])
    root, run_dir = Path(cfg["root"]), Path(cfg["run_dir"])
    cap, views = cfg["cap"], cfg["views"]
    governor = govern_current_process(cfg["cpus"], cfg["memory_mib"])
    slots = max(1, cfg["cpus"] // cap)
    ram_hint = max(256, cfg["memory_mib"] // cap)
    first = threading.Barrier(min(cap, len(views)), timeout=90)
    env = clean_env()
    env["USE_BAZEL_VERSION"] = cfg["version"]
    env["DD_BAZEL_REMOTE_CACHE"] = "off"
    if cfg["module"] == "datadog_spawn_target" and os.name == "nt":
        git_bash = Path(env.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        if git_bash.is_file():
            env["BAZEL_SH"] = str(git_bash)
    t0 = time.monotonic()

    def build(i):
        ws = root / f"w{i}"
        base = run_dir / f"o{i}"
        flags = [cfg["bazel"], "--batch", "--ignore_all_rc_files",
                 f"--output_user_root={root / 'install'}", f"--output_base={base}",
                 "--host_jvm_args=-Xms64m", f"--host_jvm_args=-Xmx{cfg['heap_mib']}m",
                 f"--host_jvm_args=-XX:ActiveProcessorCount={slots}"]
        bep = run_dir / f"b{i}.jsonl"
        profile = run_dir / f"p{i}.json.gz"
        flags += ["build", target.TARGET, *target.BUILD_FLAGS, f"--jobs={slots}",
                  f"--loading_phase_threads={slots}", f"--local_resources=cpu={slots}",
                  f"--local_resources=memory={ram_hint}", "--color=no", "--curses=no", "--noshow_progress",
                  f"--repository_cache={root / 'downloads'}", f"--disk_cache={cfg['disk_cache']}",
                  f"--build_event_json_file={bep}", f"--profile={profile}"]
        spawn_log = run_dir / f"s{i}.json"
        if cfg["module"] == "datadog_spawn_target":
            flags.append(f"--execution_log_json_file={spawn_log}")
        if i < min(cap, len(views)):
            first.wait()
        start = time.monotonic()
        log = run_dir / f"b{i}.log"
        with log.open("wb") as out:
            proc = subprocess.Popen(flags, cwd=ws, env=env, stdout=out, stderr=subprocess.STDOUT)
            try:
                code = proc.wait(timeout=cfg["timeout_s"])
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                code = -999
        end = time.monotonic()
        row = {"view": i, "variant": views[i], "exit": code, "pid": proc.pid,
               "start_s": round(start-t0, 4), "finish_s": round(end-t0, 4),
               "client_s": round(end-start, 4)}
        if spawn_log.is_file() and code == 0:
            row["spawn_log"] = logged_spawns(spawn_log)
        if code:
            row["diagnostic"] = log.read_text(encoding="utf-8", errors="replace")[-3000:]
        else:
            try:
                artifact = target.artifact(ws, base)
                row.update(artifact_sha256=sha_file(artifact), artifact_bytes=artifact.stat().st_size)
            except (ValueError, OSError) as exc:
                row["diagnostic"] = str(exc)
        return row

    with ThreadPoolExecutor(max_workers=cap) as pool:
        rows = list(pool.map(build, range(len(views))))
    events = [(b[k], d) for b in rows for k,d in (("start_s", 1), ("finish_s", -1))]
    active = peak = 0
    for _, delta in sorted(events):
        active += delta
        peak = max(peak, active)
    return {"governor": governor, "slots_per_client": slots, "ram_hint_mib_per_client": ram_hint,
            "heap_mib_per_client": cfg["heap_mib"], "builds": rows, "peak_overlapping_clients": peak,
            "batch_s": max(b["finish_s"] for b in rows) - min(b["start_s"] for b in rows),
            "peak_job_commit_bytes": peak_job_commit_bytes()}


def run_group(args, target, root, name, cap, views, cache, known):
    with tempfile.TemporaryDirectory(prefix="r", dir=root) as wave:
        directory = Path(wave)
        cfg = dict(root=str(root), run_dir=str(directory), module=args.target_module, cap=cap,
                   views=views, cpus=args.cpus, memory_mib=args.memory_mib, heap_mib=args.heap_mib,
                   timeout_s=args.timeout, bazel=args.bazel, version=args.version, disk_cache=str(cache))
        spec = directory / "run.json"
        write_json(spec, cfg)
        stdout, resources = measure([sys.executable, "-B", str(HERE), "--child", str(spec)], interval_ms=100)
        trial = json.loads(stdout)
        trial.update(name=name, concurrency=cap, coordinator_measurement=resources)
        for i, row in enumerate(trial["builds"]):
            m = read_metrics(directory / f"b{i}.jsonl", directory / f"p{i}.json.gz")
            row["bazel"] = m
            completed = any(t["label"] == target.TARGET and t["success"] for t in m["targets"])
            expected = known.get(row["variant"])
            row["valid"] = (row["exit"] == 0 and m["finished"] and m["exit_code"] == 0 and completed
                            and row.get("artifact_sha256") is not None and
                            (expected is None or row["artifact_sha256"] == expected))
        trial["passed"] = all(b["valid"] for b in trial["builds"])
        return trial


def suite(args):
    target = importlib.import_module(args.target_module)
    repo = args.repo.resolve()
    assert git(repo, "rev-parse", "--verify", target.REVISION + "^{commit}").decode().strip() == target.REVISION
    args.version = git(repo, "show", target.REVISION + ":.bazelversion").decode().strip()
    cmd = shutil.which(args.bazel)
    if not cmd:
        raise ValueError("Bazel executable unavailable")
    args.bazel = os.path.abspath(cmd)
    env = dict(clean_env(), USE_BAZEL_VERSION=args.version)
    ver = subprocess.check_output([args.bazel, "--version"], env=env, text=True).strip()
    if ver != f"bazel {args.version}":
        raise ValueError(f"pinned Bazel required, got {ver}")
    report = {"revision": target.REVISION, "target": target.TARGET, "module": args.target_module,
              "bazel": ver, "host": platform.platform(), "physical_host_logical_cpus": os.cpu_count(),
              "aggregate_budget": {"cpus": args.cpus, "job_commit_mib": args.memory_mib},
              "mode": "separate batch Bazel for every request; new output base every invocation",
              "client_timeout_s": args.timeout, "variants": VIEWS, "trials": []}
    known = {}
    def save():
        if args.output:
            write_json(args.output, report)
    with tempfile.TemporaryDirectory(prefix="ds-") as name:
        root = Path(name)
        for i, variant in enumerate(VIEWS):
            ws = root / f"w{i}"
            target.materialize(repo, ws, variant)
            # Distinct non-build edits keep equivalent target inputs in independent views.
            (ws / ".stress-view").write_text(f"private draft {i}\n", encoding="utf-8")
        if hasattr(target, "expected_output"):
            known = {v: hashlib.sha256(target.expected_output(repo, v)).hexdigest() for v in set(VIEWS)}
        print("Building isolated base, source-edit and BUILD-edit references", file=sys.stderr, flush=True)
        # Each reference request has its own output base and runs alone. No disk action cache.
        refs = run_group(args, target, root, "isolated_references", 1, VIEWS[:3], "", known)
        report["references"] = refs
        save()
        if not refs["passed"]:
            raise RuntimeError("isolated build failed; see the recorded reference diagnostics")
        known = {r["variant"]: r["artifact_sha256"] for r in refs["builds"]}
        if len(set(known.values())) != 3:
            raise AssertionError("the three edit cases must yield distinguishable verified outputs")
        report["expected_sha256"] = known
        for repeat in range(args.rounds):
            capacities = args.capacities if repeat % 2 == 0 else list(reversed(args.capacities))
            for cap in capacities:
                print(f"Round {repeat+1}, eight views, up to {cap} concurrent Bazel builds", file=sys.stderr, flush=True)
                with tempfile.TemporaryDirectory(prefix="dc", dir=root) as cache:
                    wave = run_group(args, target, root, f"r{repeat+1}_cap{cap}", cap, VIEWS, cache, known)
                    report["trials"].append(wave)
                    save()
                    if not wave["passed"]:
                        raise RuntimeError("cohort failed; evidence saved")
                    if cap == max(args.capacities) and repeat == args.rounds-1:
                        # A late fresh output base reuses the same shared disk action cache.
                        late = run_group(args, target, root, "late_base_fresh_output", 1, VIEWS[:1], cache, known)
                        report["late"] = late
                        save()
                        if not late["passed"]:
                            raise RuntimeError("late build disagreed with isolated answer")
    report["completed"] = True
    save()
    return report


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", type=Path)
    cli.add_argument("--bazel", default="bazel")
    cli.add_argument("--target-module", default="datadog_stress_target")
    cli.add_argument("--cpus", type=int, default=8)
    cli.add_argument("--memory-mib", type=int, default=8192)
    cli.add_argument("--heap-mib", type=int, default=640)
    cli.add_argument("--capacities", type=int, nargs="+", default=[1, 2, 4, 8])
    cli.add_argument("--rounds", type=int, default=1)
    cli.add_argument("--timeout", type=int, default=180)
    cli.add_argument("--output", type=Path)
    cli.add_argument("--child", type=Path, help=argparse.SUPPRESS)
    args = cli.parse_args()
    if args.child:
        print(json.dumps(cohort(json.loads(args.child.read_text(encoding="utf-8")))))
    else:
        if not args.repo or args.rounds < 1 or any(c not in (1, 2, 4, 8) for c in args.capacities):
            cli.error("provide a repo, positive rounds and capacities from 1, 2, 4, 8")
        result = suite(args)
        if not args.output:
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
