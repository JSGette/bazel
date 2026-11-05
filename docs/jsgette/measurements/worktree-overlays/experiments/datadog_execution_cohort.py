"""Compare independent and shared REAPI execution on fresh concurrent Agent builds."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from collections import Counter, defaultdict
import hashlib
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
import uuid

import psutil

from bazel_stress_metrics import (cohort_job_cpu_seconds, govern_current_process, hash_target_outputs,
                                  is_cohort_job_member, peak_job_commit_bytes, read_metrics)
from datadog_cases import clean_env
from datadog_execution_target import FLAGS, REVISION, TARGET, materialize, verify_program


HERE = Path(__file__).resolve().parent
VIEWS = ("base", "source", "build", "base", "source", "build", "base", "base")
COMPILERS = {"GoCompilePkg", "GoLink", "GoStdlib", "GoToolchainBinaryBuild"}


def kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        descendants = psutil.Process(proc.pid).children(recursive=True)
        for child in reversed(descendants):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        proc.kill()
        proc.wait(timeout=15)
    except psutil.NoSuchProcess:
        proc.wait(timeout=15)


def read_spawns(path):
    # Bazel writes one or more pretty-printed JSON objects with potentially large
    # input lists. Advance an offset instead of copying the remaining log per entry.
    data = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    rows = []
    offset = 0
    while offset < len(data):
        if data[offset].isspace():
            offset += 1
            continue
        spawn, offset = decoder.raw_decode(data, offset)
        rows.append({"mnemonic": spawn.get("mnemonic"), "runner": spawn.get("runner"),
                     "digest": spawn.get("digest", {}).get("hash"), "cache_hit": spawn.get("cacheHit")})
    return rows


@contextmanager
def worker(args, root, report):
    ready = root / "ready.json"
    events = root / "events.jsonl"
    log = root / "worker.log"
    cmd = [args.worker_python, "-B", str(HERE / "reapi_worker.py"),
           "--root", str(root / "worker"), "--jobs", str(args.jobs), "--mode", args.mode,
           "--ready-file", str(ready), "--events", str(events)]
    with log.open("wb") as output:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT)
        try:
            until = time.monotonic() + 60
            while time.monotonic() < until and proc.poll() is None:
                if ready.is_file():
                    try:
                        endpoint = json.loads(ready.read_text(encoding="utf-8"))["endpoint"]
                        break
                    except (json.JSONDecodeError, KeyError):
                        pass
                time.sleep(.05)
            else:
                raise RuntimeError("worker failed to start: " + log.read_text(errors="replace")[-3000:])
            report["worker_in_job"] = is_cohort_job_member(proc.pid)
            if report["worker_in_job"] is not True:
                raise RuntimeError("worker is outside the measured job")
            yield endpoint
        finally:
            if proc.poll() is None:
                try:
                    proc.stdin.write(b"stop\n")
                    proc.stdin.flush()
                    proc.wait(timeout=25)
                except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                    kill_tree(proc)
            proc.stdin.close()
            report["worker_exit"] = proc.returncode
            report["worker_diagnostic"] = log.read_text(encoding="utf-8", errors="replace")[-1500:]
            if events.is_file():
                report["events"] = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line]


def verify_build(root, row, refs):
    i = row["client"]
    log = (root / f"b{i}.log").read_text(encoding="utf-8", errors="replace")
    row["diagnostic"] = log[-1400:]
    row["transient_retry"] = "Found transient remote cache error" in log
    if row["exit"]:
        return
    bep = root / f"b{i}.jsonl"
    metrics = read_metrics(bep)
    row["metrics"] = {k: metrics[k] for k in ("exit_code", "packages_loaded", "targets_configured",
                                              "actions_executed", "runner_counts", "post_build_skyframe_nodes")}
    outputs = hash_target_outputs(bep, TARGET)
    if len(outputs) != 1:
        raise ValueError(f"Unexpected default outputs for client {i}: {outputs}")
    name, fingerprint = next(iter(outputs.items()))
    binary, = (root / f"o{i}/execroot/_main/bazel-out").glob("*/bin/" + name)
    row.update(fingerprint, output=name, program_output=verify_program(binary, row["variant"], root / f"v{i}"))
    row["matches_reference"] = row["sha256"] == refs[row["variant"]]
    row["spawns"] = read_spawns(root / f"b{i}.spawns")


def summarize(report):
    checked = list(report["builds"])
    if "cancellation" in report:
        origin = report["cancellation"]["origin"]
        if checked[origin]["exit"] == 0:
            raise RuntimeError("The withdrawn client unexpectedly succeeded")
        checked = [r for r in checked if r["client"] != origin] + [report["late"]]
    for row in checked:
        if row["exit"] or row["transient_retry"] or not row.get("matches_reference") or not row["in_job"]:
            raise RuntimeError(f"Build failed qualification in client {row['client']}: {row['diagnostic']}")
    physical = defaultdict(Counter)
    by_event = Counter()
    starts = set()
    for event in report.get("events", []):
        kind = event["event"]
        by_event[kind] += 1
        if kind == "start":
            starts.add(event["digest"])
            physical[event["mnemonic"]][event["digest"]] += 1
    report["event_counts"] = dict(by_event)
    report["worker_starts_by_mnemonic"] = {
        name: {"physical": sum(keys.values()), "unique_digests": len(keys),
               "duplicate_starts": sum(keys.values()) - len(keys)}
        for name, keys in sorted(physical.items())}
    if not physical["GoCompilePkg"] or not physical["GoLink"]:
        raise RuntimeError("Worker recorded no real direct Go package compilation or link")
    if report["mode"] == "shared" and any(max(counts.values(), default=0) > 1 for counts in physical.values()):
        raise RuntimeError("Shared execution repeated an action digest in this successful cohort")
    for row in checked:
        go = [s for s in row["spawns"] if s["mnemonic"] in COMPILERS]
        if not {"GoCompilePkg", "GoLink"}.issubset(s["mnemonic"] for s in go):
            raise RuntimeError("Expected Go compiler and linker in each client execution log")
        if any(s["runner"] not in ("remote", "remote cache hit") or s["digest"] not in starts for s in go):
            raise RuntimeError(f"Direct compiler execution evidence disagrees with worker starts for client {row['client']}")
    if "cancellation" in report:
        key = report["cancellation"]["digest"]
        late = [s for s in report["late"]["spawns"] if s["mnemonic"] == "GoStdlib" and s["digest"] == key]
        if physical["GoStdlib"][key] != 1 or len(late) != 1 or late[0]["runner"] != "remote cache hit":
            raise RuntimeError("Cancelled-origin action must execute once and materialize a late cache hit")


def run(args, root, report):
    version = subprocess.check_output([args.bazel, "--version"], text=True).strip()
    if version != "bazel 9.1.0":
        raise ValueError("Pinned Bazel 9.1.0 required: " + version)
    report["bazel"] = version
    references = json.loads(args.references.read_text(encoding="utf-8"))
    if not references["passed"] or references["revision"] != REVISION or references["target"] != TARGET:
        raise ValueError("A passed, same-revision and same-target isolated reference is required")
    refs = {r["variant"]: r["sha256"] for r in references["builds"]}
    if set(refs) != {"base", "source", "build"} or len(set(refs.values())) != 3:
        raise ValueError("Expected three semantically distinct isolated binary references")
    report["reference_sha256"] = refs
    views = VIEWS[:args.clients]
    workspaces = [root / f"w{i}" for i in range(len(views))]
    for i, (ws, variant) in enumerate(zip(workspaces, views)):
        materialize(args.repo, ws, variant)
        (ws / ".execution-cohort-view").write_text(f"private view {i}\n", encoding="utf-8")
    env = clean_env()
    env["USE_BAZEL_VERSION"] = "9.1.0"
    bash = Path(env.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
    if bash.is_file():
        env["BAZEL_SH"] = str(bash)
    install = root / "install"
    install.mkdir()
    prefix = [args.bazel, "--batch", "--ignore_all_rc_files", f"--output_user_root={install}",
              f"--host_jvm_args=-Xmx{args.heap_mib}m"]
    subprocess.run([*prefix, f"--output_base={root/'launcher'}", "help", "startup_options"],
                   cwd=workspaces[0], env=env, timeout=120, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    report["governor"] = govern_current_process(args.cpus, args.memory_mib)
    if not report["governor"]["aggregate_memory_limit_enforced"]:
        raise RuntimeError("This matrix requires the measured Windows job budget")
    slots = max(1, args.cpus // args.clients)
    report["client_jobs"] = slots
    barrier = threading.Barrier(args.clients)
    clients = {}
    invocation_ids = [str(uuid.uuid4()) for _ in range(args.clients + 1)] if args.cancel_origin else []
    with worker(args, root, report) as endpoint:
        base_flags = [*FLAGS, "--noshow_progress", "--disk_cache=", f"--jobs={slots}",
                      f"--loading_phase_threads={slots}", f"--remote_executor={endpoint}", f"--remote_cache={endpoint}",
                      "--remote_instance_name=local", "--remote_local_fallback=false", "--remote_retries=0",
                      "--remote_timeout=240s", f"--remote_download_outputs={args.download_outputs}",
                      "--remote_max_connections=2", "--remote_max_concurrency_per_connection=4",
                      "--remote_verify_downloads", "--noremote_cache_async",
                      "--nobuild_event_json_file_path_conversion"]
        if args.repository_cache:
            base_flags.append(f"--repository_cache={args.repository_cache}")
        if args.cancel_origin:
            # The final worker publishes results itself and exposes a read-only public AC.
            base_flags.append("--noremote_upload_local_results")
        epoch = time.monotonic()

        def build(i, variant, synchronized=True):
            flags = [*prefix, f"--host_jvm_args=-XX:ActiveProcessorCount={slots}",
                     f"--output_base={root/f'o{i}'}", "build", TARGET, *base_flags,
                     f"--build_event_json_file={root/f'b{i}.jsonl'}", f"--execution_log_json_file={root/f'b{i}.spawns'}"]
            if invocation_ids:
                flags.append(f"--invocation_id={invocation_ids[i]}")
            if synchronized:
                barrier.wait(timeout=45)
            start = time.monotonic() - epoch
            with (root / f"b{i}.log").open("wb") as log:
                proc = subprocess.Popen(flags, cwd=workspaces[i], env=env, stdout=log, stderr=subprocess.STDOUT)
                member = is_cohort_job_member(proc.pid)
                clients[i] = proc
                try:
                    rc = proc.wait(timeout=args.timeout)
                except subprocess.TimeoutExpired:
                    kill_tree(proc)
                    rc = -999
            return {"client": i, "variant": variant, "exit": rc, "in_job": member,
                    "start_s": round(start, 4), "finish_s": round(time.monotonic() - epoch, 4)}

        report["before_clients_peak_commit_bytes"] = peak_job_commit_bytes()
        begin_cpu = cohort_job_cpu_seconds()
        with ThreadPoolExecutor(max_workers=args.clients) as pool:
            futures = [pool.submit(build, i, variant) for i, variant in enumerate(views)]
            if args.cancel_origin:
                from datadog_execution_cancellation import withdraw
                report["invocation_ids"] = invocation_ids
                report["cancellation"] = withdraw(root, invocation_ids[:args.clients], clients, kill_tree)
            rows = [future.result() for future in futures]
        report["builds"] = rows
        report["peak_job_commit_bytes"] = peak_job_commit_bytes()
        report["batch_cpu_seconds"] = round(cohort_job_cpu_seconds() - begin_cpu, 3)
        report["batch_s"] = round(max(r["finish_s"] for r in rows) - min(r["start_s"] for r in rows), 4)
        report["all_client_overlap_s"] = round(min(r["finish_s"] for r in rows) - max(r["start_s"] for r in rows), 4)
        if args.cancel_origin:
            variant = views[report["cancellation"]["origin"]]
            late = root / f"w{len(views)}"
            materialize(args.repo, late, variant)
            (late / ".execution-cohort-view").write_text("late private view\n", encoding="utf-8")
            workspaces.append(late)
            report["late"] = build(len(views), variant, synchronized=False)
    for row in [*rows, *([report["late"]] if args.cancel_origin else [])]:
        verify_build(root, row, refs)
    if report["worker_exit"] or report["all_client_overlap_s"] <= 0:
        raise RuntimeError("Worker failed to terminate cleanly or clients did not overlap")
    summarize(report)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", type=Path, required=True)
    cli.add_argument("--bazel", default="bazel")
    cli.add_argument("--worker-python", default=sys.executable)
    cli.add_argument("--references", type=Path, required=True)
    cli.add_argument("--scratch", type=Path)
    cli.add_argument("--repository-cache", type=Path)
    cli.add_argument("--clients", type=int, choices=(1, 4, 8), required=True)
    cli.add_argument("--mode", choices=("shared", "control"), required=True)
    cli.add_argument("--cancel-origin", action="store_true",
                     help="Functional four-client proof; terminate the first attached GoStdlib requester")
    cli.add_argument("--cpus", type=int, default=8)
    cli.add_argument("--memory-mib", type=int, default=8192)
    cli.add_argument("--jobs", type=int, default=8)
    cli.add_argument("--heap-mib", type=int, default=512)
    cli.add_argument("--download-outputs", choices=("toplevel", "all"), default="toplevel")
    cli.add_argument("--timeout", type=int, default=540)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    if args.cancel_origin and (args.clients != 4 or args.mode != "shared"):
        cli.error("--cancel-origin requires four clients and shared execution")
    args.repo = args.repo.resolve()
    args.bazel = shutil.which(args.bazel)
    if not args.bazel:
        cli.error("Bazel executable unavailable")
    if args.repository_cache:
        args.repository_cache = args.repository_cache.resolve()
    report = {"passed": False, "revision": REVISION, "host": platform.platform(), "target": TARGET,
              "mode": args.mode, "clients": args.clients, "worker_jobs": args.jobs,
              "heap_mib_per_jvm": args.heap_mib, "download_outputs": args.download_outputs,
              "source_sha256": {name: hashlib.sha256((HERE / name).read_bytes()).hexdigest()
                                for name in ("reapi_worker.py", "reapi_worker_store.py",
                                             "datadog_execution_target.py", "datadog_execution_cohort.py")}}
    if args.cancel_origin:
        helper = HERE / "datadog_execution_cancellation.py"
        report["source_sha256"][helper.name] = hashlib.sha256(helper.read_bytes()).hexdigest()
    try:
        with tempfile.TemporaryDirectory(prefix="rex-", dir=args.scratch) as path:
            run(args, Path(path), report)
        report["passed"] = True
    except BaseException as exc:
        report["error"] = repr(exc)
        raise
    finally:
        args.output.write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    main()
