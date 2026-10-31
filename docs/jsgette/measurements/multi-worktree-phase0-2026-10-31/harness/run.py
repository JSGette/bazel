#!/usr/bin/env python3
"""Small reproducible Bazel worktree baseline; this is a smoke workload only."""

import argparse
import concurrent.futures
import ctypes
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid


HERE = Path(__file__).resolve().parent
MARKER = ".phase0-harness-owned.json"
CASES = ("warm_identical", "source_divergence", "build_divergence",
         "bzl_divergence", "missing_file_added", "glob_added", "symlink_retarget")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def owned_directory(path, role):
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)
    if any(path.iterdir()):
        raise ValueError(f"{role} directory must be empty: {path}")
    write_json(path / MARKER, {"role": role, "run_id": str(uuid.uuid4())})
    return path


def command(argv, cwd=None):
    return subprocess.run(argv, cwd=cwd, check=True, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def read_optional(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def enable_child_subreaper():
    """Adopt daemonized descendants before any subprocess is started.

    Container PID 1 may not reap orphaned JVMs. We subsequently wait only for
    known owned daemon PIDs; never use waitpid(-1), which could steal clients.
    """
    status = {"enabled": False, "platform": platform.system(), "error": None}
    if status["platform"] != "Linux":
        status["error"] = "Linux PR_SET_CHILD_SUBREAPER is required by this harness"
        return status
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER failed")
        enabled = ctypes.c_int()
        if libc.prctl(37, ctypes.addressof(enabled), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_GET_CHILD_SUBREAPER failed")
        status["enabled"] = enabled.value == 1
        if not status["enabled"]:
            status["error"] = "Kernel did not confirm child subreaper status"
    except (OSError, AttributeError) as error:
        status["error"] = str(error)
    return status


def process_identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "state": fields[0], "ppid": int(fields[1]),
                "starttime_ticks": int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


def owned_daemon_identity(output_base):
    pid_text = read_optional(output_base / "server/server.pid.txt")
    return process_identity(int(pid_text)) if pid_text and pid_text.isdigit() else None


def reap_owned_daemon(identity):
    if identity is None:
        return {"outcome": "no-known-daemon"}
    pid = identity["pid"]
    current = process_identity(pid)
    if current is None:
        return {"pid": pid, "outcome": "already-gone"}
    if current["starttime_ticks"] != identity["starttime_ticks"]:
        return {"pid": pid, "outcome": "identity-changed-not-reaped"}
    try:
        reaped_pid, status = os.waitpid(pid, os.WNOHANG)
        return {"pid": pid, "outcome": "reaped" if reaped_pid else "running",
                "wait_status": status if reaped_pid else None}
    except ChildProcessError:
        return {"pid": pid, "outcome": "not-child"}


def wait_for_shutdown_client(process, daemon_identity, timeout_seconds):
    deadline = time.monotonic() + timeout_seconds
    outcomes = []
    previous = None
    while True:
        outcome = reap_owned_daemon(daemon_identity)
        if outcome != previous:
            outcomes.append(outcome)
            previous = outcome
        code = process.poll()
        if code is not None:
            return code, outcomes
        if time.monotonic() >= deadline:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise subprocess.TimeoutExpired(process.args, timeout_seconds, output=outcomes)
        # Short polling interval permits adoption/reaping while the Bazel client
        # is waiting for its server PID to disappear.
        threading.Event().wait(0.05)


def java_trust_options(path):
    return ["--host_jvm_args=-Djavax.net.ssl.trustStore=" + str(path)] if path else []


def tree_digest(root):
    entries = []
    for path in sorted(root.rglob("*")):
        if ".git" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            entries.append((str(path.relative_to(root)), "symlink", os.readlink(path)))
        elif path.is_file():
            entries.append((str(path.relative_to(root)), "file", digest(path.read_bytes())))
    return digest(json.dumps(entries, separators=(",", ":")).encode())


def fixture_input_snapshot(worktree):
    """Hash only fixture inputs, including absent optional inputs and symlinks."""
    paths = {str(path.relative_to(HERE / "fixture"))
             for path in (HERE / "fixture").rglob("*")
             if path.is_file() or path.is_symlink()}
    paths.update(("optional.txt", "extras/extra.txt"))
    records = []
    for relative in sorted(paths):
        path = worktree / relative
        if path.is_symlink():
            records.append({"path": relative, "type": "symlink", "target": os.readlink(path)})
        elif path.is_file():
            data = path.read_bytes()
            records.append({"path": relative, "type": "file", "sha256": digest(data), "bytes": len(data)})
        elif path.exists():
            records.append({"path": relative, "type": "unexpected-nonfile"})
        else:
            records.append({"path": relative, "type": "missing"})
    return {"inputs": records, "sha256": digest(json.dumps(records, sort_keys=True, separators=(",", ":")).encode())}


@dataclasses.dataclass(frozen=True)
class ExpectedState:
    source: str = "base-source\n"
    build_message: str = "base-build"
    bzl_version: str = "base-bzl"
    payload: str = "payload-a\n"
    added_extra: str = ""
    optional: str = ""

    def output_bytes(self):
        # Oracle is defined from the fixture specification, never actual output.
        return (f"message={self.build_message}|{self.bzl_version}\n" + self.source
                + self.payload + "base-extra\n" + self.added_extra + self.optional).encode()


def expected_state(case, index):
    if case == "source_divergence":
        return ExpectedState(source=f"source-worktree-{index}\n")
    if case == "build_divergence":
        return ExpectedState(build_message=f"build-worktree-{index}")
    if case == "bzl_divergence":
        return ExpectedState(bzl_version=f"bzl-worktree-{index}")
    if case == "missing_file_added":
        return ExpectedState(optional=f"optional-worktree-{index}\n")
    if case == "glob_added":
        return ExpectedState(added_extra=f"extra-worktree-{index}\n")
    if case == "symlink_retarget":
        return ExpectedState(payload="payload-b\n")
    return ExpectedState()


def set_fixture_state(worktree, expected):
    (worktree / "source.txt").write_text(expected.source)
    build = (HERE / "fixture/BUILD.bazel").read_text()
    (worktree / "BUILD.bazel").write_text(build.replace('"base-build"', json.dumps(expected.build_message)))
    bzl = (HERE / "fixture/fixture.bzl").read_text()
    (worktree / "fixture.bzl").write_text(bzl.replace('"base-bzl"', json.dumps(expected.bzl_version)))
    for relative, content in (("optional.txt", expected.optional),
                              ("extras/extra.txt", expected.added_extra)):
        path = worktree / relative
        if content:
            path.write_text(content)
        else:
            path.unlink(missing_ok=True)
    link = worktree / "link.txt"
    target = "payload_b.txt" if expected.payload == "payload-b\n" else "payload_a.txt"
    if not link.is_symlink() or os.readlink(link) != target:
        link.unlink(missing_ok=True)
        link.symlink_to(target)


def validate_output(exit_code, output_path, expected):
    actual = output_path.read_bytes() if output_path.is_file() else None
    expected_bytes = expected.output_bytes()
    return {
        "valid": exit_code == 0 and actual == expected_bytes,
        "expected_sha256": digest(expected_bytes),
        "actual_sha256": digest(actual) if actual is not None else None,
        "expected_content": expected_bytes.decode(),
        "actual_content": actual.decode(errors="replace") if actual is not None else None,
        "expected_bytes": len(expected_bytes),
        "actual_bytes": len(actual) if actual is not None else None,
    }


def cohort_is_valid(records, expected_count, overlap):
    if len(records) != expected_count or overlap != expected_count:
        return False
    if not all(record["validation"]["valid"] and not record["timed_out"]
               and all(record["evidence_nonempty"].values()) for record in records):
        return False
    pids = [record["server"]["pid"] for record in records if record["server"]]
    return len(pids) == expected_count and len(set(pids)) == expected_count


def shutdown_failed(record):
    return record.get("exit_code") != 0 or record.get("after") is not None


def server_sample(output_base):
    pid_text = read_optional(output_base / "server/server.pid.txt")
    if not pid_text or not pid_text.isdigit():
        return None
    pid = int(pid_text)
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        rss = next(int(line.split()[1]) * 1024 for line in status.splitlines()
                   if line.startswith("VmRSS:"))
        # Process start time lets readers detect PID reuse; do not dump cmdline/env.
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "starttime_ticks": int(stat[19]), "rss_bytes": rss}
    except (OSError, ValueError, StopIteration, IndexError):
        return None


class Sampler:
    def __init__(self, workers, path):
        self.workers, self.path = workers, path
        self.stop = threading.Event()
        self.samples = []
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        with self.path.open("w") as stream:
            while True:
                servers = []
                for worker in self.workers:
                    sample = server_sample(worker.output_base)
                    if sample:
                        servers.append({"worker": worker.name, **sample})
                value = {"unix_ns": time.time_ns(), "monotonic_ns": time.monotonic_ns(),
                         "servers": servers, "server_count": len(servers),
                         "aggregate_server_rss_bytes": sum(p["rss_bytes"] for p in servers)}
                self.samples.append(value)
                stream.write(json.dumps(value) + "\n")
                if self.stop.wait(0.05):
                    break

    def finish(self):
        self.stop.set()
        self.thread.join()
        return {"scope": "owned Bazel server JVM RSS only; excludes client and action children",
                "sampling": "approximately 50 ms; can miss short peaks",
                "sample_count": len(self.samples),
                "peak_aggregate_server_rss_bytes": max(
                    (s["aggregate_server_rss_bytes"] for s in self.samples), default=0),
                "max_observed_server_count": max((s["server_count"] for s in self.samples), default=0)}


@dataclasses.dataclass
class Worker:
    name: str
    worktree: Path
    output_base: Path


class Harness:
    def __init__(self, arguments):
        self.subreaper = enable_child_subreaper()
        self.arguments = arguments
        self.bazel = Path(arguments.bazel).resolve()
        self.state = owned_directory(Path(arguments.state_dir), "state")
        self.results = owned_directory(Path(arguments.results_dir), "results")
        self.repository = self.state / "repository"
        self.all_workers = []
        self.active_workers = {}
        self.records = []
        self.shutdown_records = []
        self.java_trust_store = Path(arguments.java_trust_store).resolve() if arguments.java_trust_store else None

    def startup(self, worker):
        return [str(self.bazel), "--ignore_all_rc_files",
                "--output_user_root=" + str(self.state / "bazel-user-root"),
                "--output_base=" + str(worker.output_base),
                f"--host_jvm_args=-Xmx{self.arguments.heap_mb}m", "--max_idle_secs=600"] + java_trust_options(self.java_trust_store)

    def setup(self):
        shutil.copytree(HERE / "fixture", self.repository, symlinks=True)
        command(["git", "init", "--initial-branch=main", str(self.repository)])
        command(["git", "-C", str(self.repository), "add", "."])
        command(["git", "-C", str(self.repository), "-c", "user.name=Phase Zero Harness",
                 "-c", "user.email=phase0@example.invalid", "commit", "-m", "Deterministic baseline fixture"])
        version = command([str(self.bazel), "--ignore_all_rc_files", "--batch",
                           "--output_user_root=" + str(self.state / "bazel-user-root")]
                          + java_trust_options(self.java_trust_store) + ["version"])
        manifest = {"schema_version": 1, "workload": "tiny deterministic Starlark build smoke; no tests",
                    "bazel_path": str(self.bazel), "bazel_sha256": digest(self.bazel.read_bytes()),
                    "bazel_version": version, "fixture_commit": command(["git", "-C", str(self.repository), "rev-parse", "HEAD"]),
                    "fixture_source_sha256": tree_digest(HERE / "fixture"),
                    "harness_sha256": digest(Path(__file__).read_bytes()),
                    "child_subreaper": self.subreaper,
                    "java_trust_store": {"path": str(self.java_trust_store),
                                         "sha256": digest(self.java_trust_store.read_bytes())} if self.java_trust_store else None,
                    "configuration": vars(self.arguments), "system": platform.uname()._asdict(),
                    "python_version": sys.version, "git_version": command(["git", "--version"]),
                    "host_tools": [{"requested_path": name, "resolved_path": str(Path(name).resolve()),
                                    "sha256": digest(Path(name).resolve().read_bytes())}
                                   for name in ("/bin/sh", "/bin/cat")],
                    "cpu_count": os.cpu_count(), "cgroup_memory_max": read_optional("/sys/fs/cgroup/memory.max"),
                    "cgroup_cpu_max": read_optional("/sys/fs/cgroup/cpu.max"),
                    "environment_allowlist": {k: os.environ[k] for k in ("LANG", "LC_ALL", "TZ", "JAVA_HOME") if k in os.environ},
                    "cache_policy": "common disk and repository caches; distinct persistent output bases",
                    "phase_semantics": "prime once; fresh output bases for each concurrency group; edited cases reset fixture to specified state"}
        write_json(self.results / "manifest.json", manifest)

    def create_workers(self, prefix, count):
        workers = []
        for index in range(count):
            name = f"{prefix}-w{index}"
            worktree = self.state / "worktrees" / name
            worktree.parent.mkdir(exist_ok=True)
            command(["git", "-C", str(self.repository), "worktree", "add", "--detach", str(worktree), "HEAD"])
            worker = Worker(name, worktree, self.state / "output-bases" / name)
            workers.append(worker)
            self.all_workers.append(worker)
        return workers

    def run_one(self, worker, expected, cohort_dir, jobs, barrier):
        worker_dir = cohort_dir / worker.name
        worker_dir.mkdir()
        argv = self.startup(worker) + ["build", "//:check", f"--jobs={jobs}",
                "--loading_phase_threads=1", "--incompatible_autoload_externally=",
                "--disk_cache=" + str(self.state / "disk-cache"),
                "--repository_cache=" + str(self.state / "repository-cache"),
                "--build_event_json_file=" + str(worker_dir / "bep.jsonl"),
                "--profile=" + str(worker_dir / "profile.json.gz"),
                "--noshow_progress", "--color=no", "--curses=no"]
        write_json(worker_dir / "command.json", {"argv": argv, "cwd": str(worker.worktree)})
        server_before = server_sample(worker.output_base)
        input_snapshot = fixture_input_snapshot(worker.worktree)
        barrier.wait(timeout=30)
        start_unix, start = time.time_ns(), time.monotonic_ns()
        timed_out = False
        with (worker_dir / "command.log").open("wb") as log:
            process = subprocess.Popen(argv, cwd=worker.worktree, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=self.arguments.timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.terminate()
                try:
                    code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = process.wait()
        end, end_unix = time.monotonic_ns(), time.time_ns()
        validation = validate_output(code, worker.worktree / "bazel-bin/check.out", expected)
        record = {"worker": worker.name, "worktree": str(worker.worktree), "output_base": str(worker.output_base),
                  "start_unix_ns": start_unix, "end_unix_ns": end_unix,
                  "start_monotonic_ns": start, "end_monotonic_ns": end,
                  "wall_seconds": (end - start) / 1e9, "exit_code": code, "timed_out": timed_out,
                  "server_before": server_before, "server": server_sample(worker.output_base),
                  "fixture_expected_state": dataclasses.asdict(expected), "validation": validation,
                  "fixture_input_snapshot": input_snapshot,
                  "evidence_nonempty": {name: (worker_dir / name).is_file() and (worker_dir / name).stat().st_size > 0
                                        for name in ("bep.jsonl", "profile.json.gz")},
                  "artifacts": {name: str(worker_dir / name) for name in ("command.log", "command.json", "bep.jsonl", "profile.json.gz")}}
        write_json(worker_dir / "result.json", record)
        return record

    def cohort(self, workers, case, repetition, expected):
        name = f"{workers[0].name.rsplit('-w', 1)[0]}-{case}-r{repetition}"
        cohort_dir = self.results / name
        cohort_dir.mkdir()
        jobs = max(1, 4 // len(workers))
        barrier = threading.Barrier(len(workers) + 1)
        sampler = Sampler(workers, cohort_dir / "server-rss.jsonl")
        for worker in workers:
            self.active_workers[worker.name] = worker
        sampler.thread.start()
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(workers)) as executor:
                futures = [executor.submit(self.run_one, w, state, cohort_dir, jobs, barrier)
                           for w, state in zip(workers, expected)]
                release = time.monotonic_ns()
                barrier.wait(timeout=30)
                records = [future.result() for future in futures]
        finally:
            memory = sampler.finish()
        intervals = sorted([(r["start_monotonic_ns"], 1) for r in records]
                           + [(r["end_monotonic_ns"], -1) for r in records])
        overlap, peak = 0, 0
        for _, delta in intervals:
            overlap += delta
            peak = max(peak, overlap)
        pids = [r["server"]["pid"] for r in records if r["server"]]
        valid = cohort_is_valid(records, len(workers), peak)
        summary = {"name": name, "case": case, "repetition": repetition, "concurrency": len(workers),
                   "jobs_per_command": jobs, "launch_barrier_release_monotonic_ns": release,
                   "launch_spread_ms": (max(r["start_monotonic_ns"] for r in records) - min(r["start_monotonic_ns"] for r in records)) / 1e6,
                   "peak_overlapping_command_intervals": peak, "distinct_server_pids": pids,
                   "valid": valid, "memory": memory, "commands": records}
        self.records.append(summary)
        write_json(cohort_dir / "cohort.json", summary)
        print(json.dumps({"cohort": name, "valid": valid, "seconds": [round(r["wall_seconds"], 3) for r in records],
                          "overlap": peak, "server_pids": pids}), flush=True)
        if not valid:
            raise RuntimeError(f"Cohort failed correctness or isolation checks: {name}")

    def shutdown(self, workers):
        for worker in workers:
            if worker.name not in self.active_workers:
                continue
            log_path = self.results / f"shutdown-{worker.name}.log"
            before = server_sample(worker.output_base)
            daemon_identity = owned_daemon_identity(worker.output_base)
            try:
                with log_path.open("wb") as log:
                    process = subprocess.Popen(self.startup(worker) + ["shutdown"], cwd=worker.worktree,
                                               stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
                    code, reaping = wait_for_shutdown_client(process, daemon_identity, 45)
                record = {"worker": worker.name, "exit_code": code, "before": before,
                          "daemon_identity": daemon_identity, "reaping": reaping,
                          "after": process_identity(daemon_identity["pid"]) if daemon_identity else owned_daemon_identity(worker.output_base),
                          "log": str(log_path)}
            except subprocess.TimeoutExpired as error:
                record = {"worker": worker.name, "error": "shutdown timed out", "before": before,
                          "daemon_identity": daemon_identity, "reaping": error.output,
                          "after": process_identity(daemon_identity["pid"]) if daemon_identity else owned_daemon_identity(worker.output_base),
                          "log": str(log_path)}
            self.shutdown_records.append(record)
            self.active_workers.pop(worker.name, None)

    def run(self):
        failure = None
        try:
            if not self.subreaper["enabled"]:
                raise RuntimeError("Child subreaper unavailable: " + str(self.subreaper["error"]))
            self.setup()
            prime = self.create_workers("prime", 1)
            self.cohort(prime, "initial_cold", 0, [ExpectedState()])
            self.shutdown(prime)
            for count in self.arguments.concurrency:
                workers = self.create_workers(f"n{count}", count)
                try:
                    self.cohort(workers, "coldgraph_hotdisk", 0, [ExpectedState()] * count)
                    for case in self.arguments.cases:
                        expected = [expected_state(case, i) for i in range(count)]
                        if case != "warm_identical":
                            for worker, state in zip(workers, expected):
                                set_fixture_state(worker.worktree, state)
                        for repetition in range(self.arguments.repetitions):
                            self.cohort(workers, case, repetition, expected)
                finally:
                    self.shutdown(workers)
        except BaseException as error:
            failure = f"{type(error).__name__}: {error}"
            raise
        finally:
            self.shutdown(list(self.active_workers.values()))
            cleanup_failed = any(shutdown_failed(record) for record in self.shutdown_records)
            write_json(self.results / "summary.json", {"schema_version": 1, "failure": failure,
                       "child_subreaper": self.subreaper,
                       "cleanup_failed": cleanup_failed,
                       "cohorts": self.records, "shutdown": self.shutdown_records,
                       "limitations": ["Tiny Linux build smoke; not representative analysis/memory performance",
                                       "Build mode only; no test runner coverage",
                                       "Synthetic fixture uses undeclared host /bin/sh and /bin/cat; not a hermetic toolchain",
                                       "RSS excludes clients and action children; sampling can miss peaks",
                                       "Shared cache materialization is exercised; cache hits must be verified from BEP/profile"]})
            if cleanup_failed and failure is None:
                raise RuntimeError("Owned-server cleanup failed; inspect summary.json and shutdown logs")


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", required=True)
    parser.add_argument("--state-dir", required=True, help="New empty directory; preserved for inspection")
    parser.add_argument("--results-dir", required=True, help="New empty directory; preserved for inspection")
    parser.add_argument("--concurrency", default="1,2,4")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--heap-mb", type=int, choices=(512, 768), default=512)
    parser.add_argument("--java-trust-store", help="Optional existing Java trust store; TLS verification stays enabled")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--cases", default=",".join(CASES))
    parsed = parser.parse_args(argv)
    try:
        parsed.concurrency = [int(n) for n in parsed.concurrency.split(",")]
        if not parsed.concurrency or len(set(parsed.concurrency)) != len(parsed.concurrency) or any(n not in (1, 2, 4) for n in parsed.concurrency):
            raise ValueError("concurrency must be a nonrepeating subset of 1,2,4")
        parsed.cases = parsed.cases.split(",")
        if not parsed.cases or any(case not in CASES for case in parsed.cases):
            raise ValueError("unknown case")
        if parsed.cases[0] != "warm_identical":
            raise ValueError("cases must start with warm_identical")
        if parsed.repetitions < 1 or parsed.timeout_seconds < 1:
            raise ValueError("repetitions and timeout must be positive")
        if parsed.java_trust_store and not Path(parsed.java_trust_store).is_file():
            raise ValueError("Java trust store must be an existing regular file")
        state, results = Path(parsed.state_dir).resolve(), Path(parsed.results_dir).resolve()
        if state == results or state in results.parents or results in state.parents:
            raise ValueError("state and results directories must be separate, non-nested paths")
    except ValueError as error:
        parser.error(str(error))
    return parsed


if __name__ == "__main__":
    Harness(parse_arguments()).run()
