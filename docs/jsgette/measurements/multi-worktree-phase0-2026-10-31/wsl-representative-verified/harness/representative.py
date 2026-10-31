#!/usr/bin/env python3
"""Run the pinned public DataDog test cohort in isolated, concurrent worktrees."""

import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import threading
import time

from run import (Worker, command, digest, enable_child_subreaper, owned_directory,
                 owned_daemon_identity, process_identity, server_sample,
                 wait_for_shutdown_client, write_json)
from representative_results import profile_summary, retain_tests, verify_run

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE.parent / "representative-workload.json").read_text())


def wait_with_timeout(process, timeout):
    """Wait promptly on exit; Popen.wait(timeout=...) polls in up to 50 ms steps."""
    expired = threading.Event()

    def stop_process():
        if process.poll() is not None:
            return
        expired.set()
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()

    watchdog = threading.Timer(timeout, stop_process)
    watchdog.start()
    try:
        code = process.wait()
    finally:
        watchdog.cancel()
        watchdog.join()
    return code, expired.is_set()


class ProcessTreeSampler:
    """Diagnostic-only PSS/RSS sampling, scoped to recorded clients and servers."""

    def __init__(self, workers, clients, path):
        self.workers, self.clients, self.path = workers, clients, path
        self.stop = threading.Event()
        self.peaks = {"pss_bytes": 0, "rss_bytes": 0, "private_bytes": 0}
        self.error = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            with self.path.open("w") as stream:
                while not self.stop.is_set():
                    roots = list(self.clients.values()) + [owned_daemon_identity(w.output_base) for w in self.workers]
                    processes = {}
                    for path in Path("/proc").iterdir():
                        if path.name.isdigit():
                            identity = process_identity(int(path.name))
                            if identity:
                                processes[identity["pid"]] = identity
                    selected = {r["pid"] for r in roots if r and r["pid"] in processes
                                and processes[r["pid"]]["starttime_ticks"] == r["starttime_ticks"]}
                    while True:
                        children = {pid for pid, p in processes.items() if p["ppid"] in selected}
                        if children <= selected:
                            break
                        selected |= children
                    totals = dict.fromkeys(self.peaks, 0)
                    samples = []
                    for pid in sorted(selected):
                        try:
                            fields = {}
                            for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
                                if ":" in line:
                                    key, value = line.split(":", 1)
                                    fields[key] = int(value.split()[0]) * 1024
                            current = process_identity(pid)
                            if not current or current["starttime_ticks"] != processes[pid]["starttime_ticks"]:
                                continue
                            values = {"pss_bytes": fields["Pss"], "rss_bytes": fields["Rss"],
                                      "private_bytes": sum(fields.get(k, 0) for k in
                                                           ("Private_Clean", "Private_Dirty", "Private_Hugetlb"))}
                            for key, value in values.items():
                                totals[key] += value
                            samples.append({**processes[pid], **values})
                        except (OSError, ValueError, KeyError):
                            continue  # Exited or unreadable processes are not counted as zero memory.
                    for key in totals:
                        self.peaks[key] = max(self.peaks[key], totals[key])
                    stream.write(json.dumps({"monotonic_ns": time.monotonic_ns(),
                                             "selected_processes": len(selected),
                                             "sampled_processes": samples, **totals}) + "\n")
                    self.stop.wait(0.2)
        except BaseException as error:
            self.error = str(error)

    def finish(self):
        self.stop.set()
        self.thread.join()
        if self.error or not self.peaks["pss_bytes"]:
            raise RuntimeError("Process-tree sampling failed: " + str(self.error))
        return {"sampled_peaks": self.peaks,
                "scope": "owned Bazel servers, clients, and their observed descendants; ~200 ms samples; misses short-lived processes and peaks"}


class RepresentativeRun:
    def __init__(self, args):
        self.args = args
        self.source = Path(args.repository).resolve()
        self.bazel = Path(args.bazel).resolve()
        self.bazelisk = Path(args.bazelisk).resolve()
        if command(["git", "rev-parse", "HEAD"], self.source) != LOCK["commit"]:
            raise ValueError("Repository must be at the pinned workload commit")
        state, results = Path(args.state_dir).resolve(), Path(args.results_dir).resolve()
        if state == results or state in results.parents or results in state.parents:
            raise ValueError("State and results must be separate, non-nested directories")
        if not enable_child_subreaper()["enabled"]:
            raise RuntimeError("Linux child subreaper is required")
        available = sorted(os.sched_getaffinity(0))
        if len(available) < 4:
            raise ValueError("This baseline requires four available logical CPUs")
        os.sched_setaffinity(0, available[:4])
        self.state = owned_directory(state, "representative-state")
        self.results = owned_directory(results, "representative-results")
        for name in ("home", "cache", "worktrees"):
            (self.state / name).mkdir()
        self.environment = {"HOME": str(self.state / "home"), "PATH": "/usr/local/bin:/usr/bin:/bin",
                            "LANG": "C.UTF-8", "DD_BAZEL_REMOTE_CACHE": "off",
                            "XDG_CACHE_HOME": str(self.state / "cache"),
                            "USE_BAZEL_VERSION": str(self.bazel)}
        self.active, self.resources, self.identities = {}, {}, {}
        self.records, self.shutdown_records = [], []
        self.test_oracle = None

    def startup(self, worker):
        return (["/usr/bin/env", "-i"] + [f"{k}={v}" for k, v in self.environment.items()]
                + [str(self.bazelisk), "--nosystem_rc", "--nohome_rc",
                   f"--output_user_root={self.state / 'bazel-user-root'}",
                   f"--output_base={worker.output_base}", "--host_jvm_args=-Xmx2048m",
                   f"--host_jvm_args=-XX:ActiveProcessorCount={self.resources[worker.name]}",
                   "--max_idle_secs=600"])

    def create_workers(self, prefix, count):
        workers = []
        for index in range(count):
            name = f"{prefix}-w{index}"
            worktree = self.state / "worktrees" / name
            command(["git", "worktree", "add", "--detach", str(worktree), LOCK["commit"]], self.source)
            if (worktree / "user.bazelrc").exists():
                raise ValueError("Unexpected user.bazelrc in fresh worktree")
            worker = Worker(name, worktree, self.state / "output-bases" / name)
            self.resources[name] = 4 // count
            workers.append(worker)
        return workers

    def invoke(self, worker, directory, barrier, clients, diagnostic, require_execution, targets):
        directory.mkdir()
        jobs = self.resources[worker.name]
        argv = self.startup(worker) + ["test"] + targets + [
            "--config=no-remote-cache", "--remote_cache=", "--remote_executor=",
            f"--jobs={jobs}", "--loading_phase_threads=1", f"--local_resources=memory={1536 * jobs}",
            f"--repository_cache={self.state / 'cache/repository-cache'}",
            f"--repo_contents_cache={self.state / 'cache/repository-contents'}",
            "--noskip_incompatible_explicit_targets", "--test_output=errors",
            "--nocache_test_results" if require_execution else "--cache_test_results=yes",
            f"--build_event_json_file={directory / 'bep.jsonl'}", "--color=no", "--curses=no",
            "--noshow_progress"]
        if diagnostic:
            argv += [f"--profile={directory / 'profile.json.gz'}",
                     f"--memory_profile={directory / 'memory.txt'}",
                     "--record_full_profiler_data",
                     "--experimental_profile_additional_tasks=skyfunction",
                     "--experimental_profile_additional_tasks=starlark_parser"]
        else:
            argv += ["--generate_json_trace_profile=no"]
        write_json(directory / "command.json", {"argv": argv, "cwd": str(worker.worktree)})
        before = server_sample(worker.output_base)
        barrier.wait(timeout=30)
        start = time.monotonic_ns()
        code, timed_out = None, False
        with (directory / "command.log").open("wb") as log:
            process = subprocess.Popen(argv, cwd=worker.worktree, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            clients[worker.name] = process_identity(process.pid)
            code, timed_out = wait_with_timeout(process, self.args.timeout_seconds)
        end = time.monotonic_ns()
        after = server_sample(worker.output_base)
        validation, error = None, None
        try:
            if code != 0 or timed_out:
                raise ValueError(f"Bazel exit {code}; timeout={timed_out}")
            validation = retain_tests(directory, targets, require_execution)
            if diagnostic:
                validation["profile"] = profile_summary(directory / "profile.json.gz")
                if not (directory / "memory.txt").stat().st_size:
                    raise ValueError("Missing memory profile")
            identity = (after["pid"], after["starttime_ticks"]) if after else None
            if identity is None or self.identities.setdefault(worker.name, identity) != identity:
                raise ValueError("Missing/restarted server")
        except (ValueError, OSError, KeyError, TypeError) as exc:
            error = str(exc)
        record = {"worker": worker.name, "targets": targets, "exit_code": code,
                  "timed_out": timed_out, "start_monotonic_ns": start, "end_monotonic_ns": end,
                  "wall_seconds": (end - start) / 1e9, "server_before": before, "server": after,
                  "diagnostic": diagnostic, "require_execution": require_execution,
                  "validation": validation, "error": error}
        write_json(directory / "result.json", record)
        return record

    def cohort(self, workers, case, repetition=0, diagnostic=False, require_execution=False):
        name = f"{workers[0].name.rsplit('-w', 1)[0]}-{case}-r{repetition}"
        directory = self.results / name
        directory.mkdir()
        self.active.update({w.name: w for w in workers})
        clients, barrier = {}, threading.Barrier(len(workers) + 1)
        sampler = ProcessTreeSampler(workers, clients, directory / "process-memory.jsonl") if diagnostic else None
        if sampler:
            sampler.thread.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as pool:
                futures = [pool.submit(self.invoke, w, directory / w.name, barrier, clients,
                                       diagnostic, require_execution,
                                       [LOCK["test_cohort"][i]] if case == "mixed_targets" and i < 3
                                       else LOCK["test_cohort"]) for i, w in enumerate(workers)]
                barrier.wait(timeout=30)
                records = [future.result() for future in futures]
        finally:
            memory = sampler.finish() if sampler else None
        overlap = min(r["end_monotonic_ns"] for r in records) - max(r["start_monotonic_ns"] for r in records)
        pids = [r["server"]["pid"] for r in records if r["server"]]
        valid = (all(r["error"] is None for r in records) and overlap > 0
                 and len(set(pids)) == len(workers))
        if valid:
            for record in records:
                oracle = {label: test["xml"] for label, test in record["validation"]["tests"].items()}
                if self.test_oracle is None:
                    self.test_oracle = oracle
                valid = valid and all(value == self.test_oracle.get(label) for label, value in oracle.items())
        cohort = {"name": name, "case": case, "repetition": repetition, "concurrency": len(workers),
                  "valid": valid, "common_overlap_seconds": overlap / 1e9, "memory": memory,
                  "batch_seconds": (max(r["end_monotonic_ns"] for r in records)
                                    - min(r["start_monotonic_ns"] for r in records)) / 1e9,
                  "commands": records}
        self.records.append(cohort)
        write_json(directory / "cohort.json", cohort)
        print(json.dumps({"cohort": name, "valid": valid,
                          "seconds": [round(r["wall_seconds"], 3) for r in records]}), flush=True)
        if not valid:
            raise RuntimeError(f"Representative cohort failed: {name}")

    def capture_heap(self, workers):
        # Deliberately after all latency batches: live histograms force GC.
        for worker in workers:
            directory = self.results / "heap" / worker.name
            directory.mkdir(parents=True)
            identity = owned_daemon_identity(worker.output_base)
            if not identity or (identity["pid"], identity["starttime_ticks"]) != self.identities[worker.name]:
                raise RuntimeError("Server identity changed before heap capture")
            jcmd = Path(f"/proc/{identity['pid']}/exe").resolve().with_name("jcmd")
            argv = [str(jcmd), str(identity["pid"]), "GC.class_histogram"]
            with (directory / "histogram.txt").open("wb") as log:
                process = subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, timeout=120)
            write_json(directory / "command.json", {"argv": argv, "exit_code": process.returncode,
                                                     "server": identity, "scope": "live JVM object shallow sizes after forced GC; not deep ownership or total process memory"})
            if process.returncode:
                raise RuntimeError("Heap histogram failed")

    def shutdown(self, workers):
        for worker in workers:
            if worker.name not in self.active:
                continue
            daemon = owned_daemon_identity(worker.output_base)
            record = {"worker": worker.name, "before": daemon}
            try:
                with (self.results / f"shutdown-{worker.name}.log").open("wb") as log:
                    process = subprocess.Popen(self.startup(worker) + ["shutdown"], cwd=worker.worktree,
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                    code, reaping = wait_for_shutdown_client(process, daemon, 60)
                record.update(exit_code=code, reaping=reaping,
                              after=process_identity(daemon["pid"]) if daemon else owned_daemon_identity(worker.output_base))
            except subprocess.TimeoutExpired:
                record.update(exit_code=None, error="shutdown timeout", after=owned_daemon_identity(worker.output_base))
            evidence = self.results / "worktree-state" / worker.name
            evidence.mkdir(parents=True)
            lock = worker.worktree / "MODULE.bazel.lock"
            if lock.exists():
                shutil.copy2(lock, evidence / lock.name)
            diff = command(["git", "diff", "--name-only", "--", ".", ":(exclude)MODULE.bazel.lock"], worker.worktree)
            record["unexpected_source_changes"] = diff
            self.shutdown_records.append(record)
            del self.active[worker.name]

    def run(self):
        failure = None
        try:
            snapshots = self.results / "harness"
            snapshots.mkdir()
            for name in ("representative.py", "representative_results.py", "run.py"):
                shutil.copy2(HERE / name, snapshots / name)
            shutil.copy2(HERE.parent / "representative-workload.json", self.results / "representative-workload.json")
            inputs = {name: digest((self.source / name).read_bytes()) for name in LOCK["sources"]}
            write_json(self.results / "manifest.json", {
                "schema_version": 1, "matrix_version": 2, "source_commit": LOCK["commit"], "inputs_sha256": inputs,
                "bazel_sha256": digest(self.bazel.read_bytes()), "bazelisk_sha256": digest(self.bazelisk.read_bytes()),
                "configuration": vars(self.args), "environment_allowlist": self.environment,
                "cpu_affinity": sorted(os.sched_getaffinity(0)), "system": platform.uname()._asdict(),
                "budget": "4 logical CPUs via inherited affinity; 4 aggregate jobs; 6 GiB aggregate action memory estimate; 2 GiB heap/server",
                "cache_policy": "empty experiment-owned caches; prime cohort; fresh graph/output bases for each group; no private cache/executor",
                "latency_policy": "blocking waitpid with separate timeout watchdog; warmups excluded; no JSON trace, forced GC or process sampler in latency cohorts; BEP retained"})
            prime = self.create_workers("prime", 1)
            self.cohort(prime, "dependencies_and_tests", diagnostic=True, require_execution=True)
            self.shutdown(prime)
            for count in self.args.concurrency:
                workers = self.create_workers(f"n{count}", count)
                self.cohort(workers, "coldgraph_hotdisk", diagnostic=True)
                self.cohort(workers, "executed_tests", diagnostic=True, require_execution=True)
                self.cohort(workers, "warmup")
                for repetition in range(self.args.repetitions):
                    self.cohort(workers, "warm_latency", repetition)
                self.cohort(workers, "mixed_targets", diagnostic=True, require_execution=True)
                self.cohort(workers, "full_target_restore", diagnostic=True, require_execution=True)
                self.cohort(workers, "warm_diagnostic", diagnostic=True)
                self.capture_heap(workers)
                self.shutdown(workers)
        except BaseException as exc:
            failure = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self.shutdown(list(self.active.values()))
            if any(r.get("exit_code") != 0 or r.get("after") is not None or r.get("unexpected_source_changes")
                   for r in self.shutdown_records):
                failure = failure or "Owned-server cleanup failed or source changed"
            write_json(self.results / "summary.json", {"failure": failure, "cohorts": self.records,
                                                       "shutdown": self.shutdown_records})
        print(json.dumps(verify_run(self.results)), flush=True)


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("repository", "bazel", "bazelisk", "state-dir", "results-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--concurrency", default="1,2,4")
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    args = parser.parse_args()
    try:
        args.concurrency = [int(n) for n in args.concurrency.split(",")]
        if (not args.concurrency or len(set(args.concurrency)) != len(args.concurrency)
                or any(n not in (1, 2, 4) for n in args.concurrency)
                or args.repetitions < 1 or args.timeout_seconds < 1):
            raise ValueError()
    except ValueError:
        parser.error("Use unique concurrency values from 1,2,4 and positive repetitions/timeout")
    return args


if __name__ == "__main__":
    RepresentativeRun(arguments()).run()
