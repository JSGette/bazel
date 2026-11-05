"""Exercise one coordinator-owned GoStdlib action through stock Bazel's HTTP cache."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import platform
import select
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import psutil

from bazel_stress_metrics import govern_current_process, hash_target_outputs, peak_job_commit_bytes, read_metrics
from datadog_action_cache import Cache, verify_result
from datadog_build_stress import logged_spawns
from datadog_cases import clean_env
from datadog_compiled_probe import verify_program
from datadog_stress_target import BUILD_FLAGS, COMPILED_TARGET, REVISION, materialize
from shared_action import SharedActionServer, open_request


KEY = "80a0c564683f8138fbea0013d87890f54f36d53523126916c80c24db1d7d1865"
VARIANTS = ("base", "base", "source", "build")


def wait_for(predicate, timeout, description):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = predicate()
        if found:
            return found
        time.sleep(.05)
    raise TimeoutError(description)


def kill_tree(proc):
    if proc.poll() is not None:
        return []
    parent = psutil.Process(proc.pid)
    processes = [parent, *parent.children(recursive=True)]
    for child in reversed(processes):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(processes[1:], timeout=10)
    proc.wait(timeout=10)
    if alive:
        raise RuntimeError(f"Client descendants still alive: {[p.pid for p in alive]}")
    return [p.pid for p in processes]


class Trial:
    def __init__(self, args, root, report):
        self.args, self.root, self.report = args, root, report
        self.env = clean_env()
        self.env["USE_BAZEL_VERSION"] = "9.1.0"
        if os.name == "nt":
            bash = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
            if bash.is_file():
                self.env["BAZEL_SH"] = str(bash)
        self.clients = {}
        self.joined = []
        self.active = set()
        self.detached = []
        self.lock = threading.Lock()
        self.producer = None
        self.producer_attempts = 0
        self.epoch = time.monotonic()
        self.coordinator = None

    def seconds(self):
        return round(time.monotonic() - self.epoch, 4)

    def setup(self):
        version = subprocess.check_output([self.args.bazel, "--version"], env=self.env, text=True).strip()
        if version != "bazel 9.1.0":
            raise ValueError(f"The pinned Datadog experiment requires Bazel 9.1.0, got {version}")
        self.report["bazel"] = version
        n = 5 if self.args.mode == "cancel" else 4
        for i in range(n):
            # The fifth request recreates the cancelled variant after publication.
            variant = VARIANTS[i] if i < 4 else "base"
            materialize(self.args.repo, self.root / f"w{i}", variant, COMPILED_TARGET)
        if self.args.mode != "control":
            ws = self.root / "producer"
            materialize(self.args.repo, ws, "base", COMPILED_TARGET)
            package = ws / "ownership_probe"
            package.mkdir()
            # The pinned rules_go target exposes its tree via a provider rather than DefaultInfo.
            # Forward that very artifact so no unrelated Agent actions run in the private job.
            (package / "stdlib.bzl").write_text(
                'load("@rules_go//go/private:providers.bzl", "GoStdLib")\n'
                'def _impl(ctx):\n    return [DefaultInfo(files=ctx.attr.dep[GoStdLib].libs)]\n'
                'request_stdlib = rule(implementation=_impl, attrs={"dep": attr.label(mandatory=True)})\n',
                encoding="utf-8")
            (package / "BUILD.bazel").write_text(
                'load(":stdlib.bzl", "request_stdlib")\n'
                'request_stdlib(name="stdlib", dep="@rules_go//:stdlib")\n', encoding="utf-8")
        (self.root / "install").mkdir()
        subprocess.run([self.args.bazel, "--batch", "--ignore_all_rc_files",
                        f"--output_user_root={self.root / 'install'}",
                        f"--output_base={self.root / 'launcher'}", "help", "startup_options"],
                       cwd=self.root / "w0", env=self.env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=90, check=True)

    def launch(self, caller, target, output_base=None):
        root = self.root
        base = output_base or root / ("o-" + caller)
        args = [self.args.bazel, "--batch", "--ignore_all_rc_files", "--host_jvm_args=-Xmx768m",
                f"--output_user_root={root / 'install'}", f"--output_base={base}",
                "build", target, *BUILD_FLAGS, "--@rules_go//go/config:pure=true", "--jobs=2",
                "--noshow_progress", "--disk_cache=", f"--remote_cache={self.cache.url(caller)}",
                "--remote_timeout=240s", "--remote_max_connections=8", "--remote_download_outputs=all",
                "--noremote_cache_async", "--remote_verify_downloads",
                f"--build_event_json_file={root / (caller + '.bep')}",
                f"--execution_log_json_file={root / (caller + '.spawns')}"]
        if self.args.repository_cache:
            args.append(f"--repository_cache={self.args.repository_cache}")
        with (root / (caller + ".log")).open("wb") as log:
            return subprocess.Popen(args, cwd=root / caller, env=self.env, stdout=log, stderr=subprocess.STDOUT)

    def produce(self, key, output):
        if key != KEY:
            raise ValueError("Unknown private action recipe")
        if self.args.mode == "cancel":
            wait_for(lambda: self.coordinator.snapshot()["active"].get(KEY) == 4, 140,
                     "Four distinct subscriptions did not reach the gate")
        start = self.seconds()
        self.producer_attempts += 1
        base = self.root / f"o-producer-{self.producer_attempts}"
        self.producer = self.launch("producer", "//ownership_probe:stdlib", base)
        try:
            rc = self.producer.wait(timeout=180)
        except subprocess.TimeoutExpired:
            kill_tree(self.producer)
            raise
        log = self.root / "producer.spawns"
        rows = logged_spawns(log) if log.is_file() else []
        self.report["producer"] = {"exit": rc, "start_s": start, "finish_s": self.seconds(), "spawns": rows}
        owned = [s for s in rows if s["mnemonic"] == "GoStdlib"]
        if rc or len(owned) != 1 or owned[0]["digest"] != key or owned[0]["runner"] != "local":
            diagnostic = (self.root / "producer.log").read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"Private producer did not execute the requested digest: {owned}, {diagnostic}")
        result = (self.cache.root / "ac" / key).read_bytes()
        checked = verify_result(result, self.cache.root / "cas")
        self.report["producer"].update(checked, action_result_sha256=hashlib.sha256(result).hexdigest())
        (output / "action_result").write_bytes(result)

    def lookup(self, caller, key, connection):
        # A subscriber belongs to the transport request. Its disappearance must wake the broker.
        # The broker worker continues in a private workspace outside this request handler.
        with self.lock:
            if caller in self.active:
                raise ValueError(f"Unexpected simultaneous duplicate HTTP lookup from {caller}")
            sub = open_request(self.coordinator.address, self.coordinator.token, key)
            if self.args.mode == "cancel" and not self.joined:
                # Other HTTP handlers are held outside this lock until admission is observable.
                wait_for(lambda: self.coordinator.snapshot()["active"].get(key) == 1, 5,
                         "First subscriber was not admitted")
                self.report["first_admission"] = {"caller": caller, "snapshot": self.coordinator.snapshot()}
            self.active.add(caller)
            self.joined.append(caller)
        stopped = threading.Event()

        def watch_disconnect():
            while not stopped.wait(.05):
                if select.select([connection], [], [], 0)[0]:
                    try:
                        gone = not connection.recv(1, socket.MSG_PEEK)
                    except OSError:
                        gone = True
                    if gone:
                        with self.lock:
                            self.detached.append(caller)
                        sub.close()
                        return
        watcher = threading.Thread(target=watch_disconnect, daemon=True)
        watcher.start()
        try:
            try:
                with sub:
                    reply = sub.wait()
            except OSError:
                with self.lock:
                    if caller in self.detached:
                        raise ConnectionAbortedError("Bazel HTTP subscriber disconnected")
                raise
            if reply["status"] != "ok":
                raise RuntimeError(f"Coordinator returned {reply}")
            return Path(reply["path"]) / "action_result"
        finally:
            stopped.set()
            watcher.join()
            with self.lock:
                self.active.discard(caller)

    def build(self, index, variant, gate=None):
        caller = f"w{index}"
        if gate:
            gate.wait(timeout=20)
        start = self.seconds()
        proc = self.launch(caller, COMPILED_TARGET)
        self.clients[caller] = proc
        try:
            rc = proc.wait(timeout=320)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            raise
        row = {"caller": caller, "variant": variant, "exit": rc,
               "start_s": start, "finish_s": self.seconds()}
        log = (self.root / f"{caller}.log").read_text(encoding="utf-8", errors="replace")
        row["transient_build_retry"] = "retrying the build" in log
        spawnlog = self.root / f"{caller}.spawns"
        row["spawns"] = logged_spawns(spawnlog) if spawnlog.is_file() else []
        if rc:
            row["diagnostic"] = log[-2200:]
            return row
        if row["transient_build_retry"]:
            row["diagnostic"] = log[-5000:]
        bep = self.root / f"{caller}.bep"
        metrics = read_metrics(bep)
        row.update({key: metrics[key] for key in ("packages_loaded", "targets_configured", "exit_code", "runner_counts")})
        target_files = hash_target_outputs(bep, COMPILED_TARGET)
        name, fingerprint = next(iter(target_files.items()))
        binary, = (self.root / f"o-{caller}" / "execroot/_main/bazel-out").glob("*/bin/" + name)
        row.update(fingerprint, program_output=verify_program(binary, variant, self.root / f"test-{caller}"))
        row["matches_reference"] = row["sha256"] == self.report["reference_sha256"][variant]
        return row

    def cancel_origin(self):
        wait_for(lambda: self.coordinator.snapshot()["active"].get(KEY) == 4, 155, "Four live clients")
        with self.lock:
            if len(self.active) != 4 or len(self.joined) != 4:
                raise RuntimeError(f"Expected four distinct simultaneous clients, saw {self.joined}")
            origin = self.joined[0]

        def compiling():
            if not self.producer or self.producer.poll() is not None:
                return None
            try:
                for child in psutil.Process(self.producer.pid).children(recursive=True):
                    if "builder" in child.name().lower() and "stdlib" in child.cmdline():
                        return child.pid
            except psutil.NoSuchProcess:
                pass
            return None

        worker = wait_for(compiling, 120, "An executing GoStdlib compiler under the independent producer")
        before = self.coordinator.snapshot()
        when = self.seconds()
        terminated = kill_tree(self.clients[origin])
        wait_for(lambda: self.coordinator.snapshot()["active"].get(KEY) == 3, 10,
                 "Bazel disconnect did not withdraw its subscription")
        self.report["cancellation"] = {"caller": origin, "at_s": when,
            "method": "abrupt termination of the original Bazel client process tree",
            "compiler_pid": worker, "killed_pids": terminated, "producer_alive_after_detach": self.producer.poll() is None,
            "before": before, "after": self.coordinator.snapshot()}
        return origin

    def run(self):
        self.setup()
        coordinated = self.args.mode != "control"
        with Cache(self.root / "http", KEY, self.lookup if coordinated else None) as cache:
            self.cache = cache
            thread = threading.Thread(target=cache.serve_forever, daemon=True)
            thread.start()
            try:
                server = SharedActionServer(self.root / "store", 1, self.produce) if coordinated else nullcontext()
                with server as self.coordinator:
                    gate = threading.Barrier(4)
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        futures = [pool.submit(self.build, i, v, gate) for i, v in enumerate(VARIANTS)]
                        origin = self.cancel_origin() if self.args.mode == "cancel" else None
                        rows = [f.result() for f in futures]
                    self.report["builds"] = rows
                    self.report["batch_s"] = round(max(r["finish_s"] for r in rows) - min(r["start_s"] for r in rows), 4)
                    if origin:
                        from datadog_stress_target import apply_variant
                        variant = VARIANTS[int(origin[1:])]
                        apply_variant(self.args.repo, self.root / "w4", variant, COMPILED_TARGET)
                        self.report["late"] = self.build(4, variant)
                    if coordinated:
                        self.report["coordinator"] = self.coordinator.snapshot()
                    self.report.update(join_order=self.joined, transport_disconnects=self.detached)
            finally:
                cache.shutdown()
                thread.join()
                self.report["http_errors"] = cache.errors
                self.report["http_get_counts"] = cache.get_counts
                self.report["http_cas_misses"] = cache.cas_misses
        outputs = [*rows, *([self.report["late"]] if origin else [])]
        for row in outputs:
            if origin and row["caller"] == origin:
                if row["exit"] == 0:
                    raise RuntimeError("Cancelled client unexpectedly succeeded")
            else:
                if row["exit"] or row["transient_build_retry"] or not row["matches_reference"]:
                    raise RuntimeError(f"Output comparison failed for {row['caller']}")
                matching = [s for s in row["spawns"] if s["mnemonic"] == "GoStdlib" and s["digest"] == KEY]
                if len(matching) != 1 or (coordinated and matching[0]["runner"] != "remote cache hit"):
                    raise RuntimeError(f"Unexpected GoStdlib execution for {row['caller']}: {matching}")
        if self.report["http_errors"]:
            raise RuntimeError(f"HTTP cache errors: {self.report['http_errors']}")
        if coordinated and self.report["coordinator"]["starts"] != 1:
            raise RuntimeError("Expected exactly one independent GoStdlib producer")
        if origin and (self.detached != [origin] or not self.report["cancellation"]["producer_alive_after_detach"]):
            raise RuntimeError("Subscriber departure did not preserve an active producer")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--bazel", default="bazel")
    p.add_argument("--repository-cache", type=Path)
    p.add_argument("--references", type=Path, required=True)
    p.add_argument("--mode", choices=("cancel", "natural", "control"), required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    if os.name != "nt":
        p.error("This fixture pins a Windows Bazel action digest and Windows executable references")
    args.repo = args.repo.resolve()
    args.bazel = shutil.which(args.bazel)
    if not args.bazel:
        p.error("Bazel executable unavailable")
    reference = json.loads(args.references.read_text(encoding="utf-8"))
    if not reference["passed"] or reference["revision"] != REVISION or reference["target"] != COMPILED_TARGET:
        p.error("Pinned successful compiled references are required")
    governor = govern_current_process(8, 8192)
    report = {"mode": args.mode, "target": COMPILED_TARGET, "revision": REVISION, "action_digest": KEY,
              "coordinator_source_sha256": hashlib.sha256(Path(__file__).with_name("shared_action.py").read_bytes()).hexdigest(),
              "host": platform.platform(), "governor": governor, "passed": False,
              "transport": {"http_connections_per_bazel": 8, "listen_backlog": Cache.request_queue_size,
                            "download_outputs": "all", "eligible_action": "GoStdlib",
                            "cache_initial_state": "private empty CAS and action cache per run"},
              "reference_sha256": {r["variant"]: r["sha256"] for r in reference["builds"]}}
    try:
        with tempfile.TemporaryDirectory(prefix="dd-own-") as root:
            Trial(args, Path(root), report).run()
        report["passed"] = True
    except BaseException as exc:
        report["error"] = repr(exc)
        raise
    finally:
        report["peak_job_commit_bytes"] = peak_job_commit_bytes()
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
