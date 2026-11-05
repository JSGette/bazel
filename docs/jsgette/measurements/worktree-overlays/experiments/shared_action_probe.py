"""Exercise a bounded action owner using independent client and producer processes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time


SCRIPT = Path(__file__).resolve()
TIMEOUT = 45


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


def until(predicate, detail):
    deadline = time.monotonic() + TIMEOUT
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError(detail)
        time.sleep(.01)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def output_bytes(payload):
    return b"producer result\n" + payload


def worker(case, stage, attempt):
    # The gate and start markers instrument the test. Only result.bin is published.
    data = (case / "input").read_bytes()
    (case / f"started-{attempt}").write_text(str(os.getpid()))
    until(lambda: (case / f"release-{attempt}").exists(), f"producer gate {case.name}")
    if (case / f"fail-{attempt}").exists():
        (stage / "result.bin").write_bytes(b"incomplete")
        return 7
    (stage / "result.bin").write_bytes(output_bytes(data))
    return 0


def request(connection_path, key, workspace):
    from shared_action import open_request

    connection = json.loads(connection_path.read_text(encoding="utf-8"))
    with open_request(tuple(connection["address"]), connection["token"], key) as sub:
        reply = sub.wait()
    result = {"status": reply["status"]}
    if reply["status"] == "ok":
        published = Path(reply["path"])
        private = workspace / "result.bin"
        shutil.copyfile(published / "result.bin", private)
        result["sha256"] = digest(private.read_bytes())
    elif reply["status"] == "error":
        result["error"] = reply["message"]
    print(json.dumps(result))


class Probe:
    def __init__(self, root):
        self.root = root
        self.specs = {}
        self.starts = {}
        self.lock = threading.Lock()
        self.live = self.peak_live = 0
        self.clients = []
        self.server = None

    def connect(self, server):
        self.server = server
        self.connection = self.root / "connection.json"
        temp = self.connection.with_suffix(".tmp")
        temp.write_text(json.dumps({"address": server.address, "token": server.token}))
        temp.replace(self.connection)

    def register(self, name):
        data = f"immutable input for {name}\n".encode()
        key = digest(b"fixture-v1\0" + data)
        case = self.root / name
        case.mkdir()
        (case / "input").write_bytes(data)
        self.specs[key] = case
        return key

    def produce(self, key, stage):
        with self.lock:
            attempt = self.starts.get(key, 0) + 1
            self.starts[key] = attempt
            self.live += 1
            self.peak_live = max(self.peak_live, self.live)
        try:
            subprocess.run([sys.executable, "-B", str(SCRIPT), "--worker", str(self.specs[key]),
                            str(stage), str(attempt)], check=True, timeout=TIMEOUT + 5)
        finally:
            with self.lock:
                self.live -= 1

    def start(self, key):
        workspace = self.root / f"client-{len(self.clients)}"
        workspace.mkdir()
        (workspace / "draft.txt").write_text(workspace.name)
        proc = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "--request",
                                 str(self.connection), key, str(workspace)], cwd=workspace,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        client = proc, workspace, key
        self.clients.append(client)
        return client

    def join(self, key, count, attempt=1):
        def joined():
            active = self.server.snapshot()["active"]
            return active.get(key) == count and (self.specs[key] / f"started-{attempt}").exists()
        until(joined, f"{self.specs[key].name}: expected {count} attached subscribers, attempt {attempt}")

    def release(self, key, attempt=1):
        (self.specs[key] / f"release-{attempt}").touch()

    def finish(self, client, expected="ok"):
        proc, workspace, key = client
        stdout, stderr = proc.communicate(timeout=TIMEOUT)
        check(proc.returncode == 0, (proc.returncode, stderr, stdout))
        reply = json.loads(stdout)
        check(reply["status"] == expected, reply)
        path = workspace / "result.bin"
        if expected == "ok":
            data = output_bytes((self.specs[key] / "input").read_bytes())
            check(path.read_bytes() == data and reply["sha256"] == digest(data), reply)
        else:
            check(not path.exists(), f"{expected} client materialized {path}")
        return reply

    def disconnect(self, client):
        proc, workspace, _ = client
        check(proc.poll() is None, "client completed before disconnect")
        proc.kill()
        proc.communicate(timeout=TIMEOUT)
        check(not (workspace / "result.bin").exists(), "disconnected client returned output")
        # The producing action must outlive this workspace too.
        workspace.rename(workspace.with_name(workspace.name + "-retired"))

    def stop_clients(self):
        for proc, _, _ in self.clients:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=TIMEOUT)
        for case in self.specs.values():
            (case / "release-1").touch()
            (case / "release-2").touch()

    def delta(self, before):
        now = self.server.snapshot()
        return {k: now[k] - before[k] for k in ("starts", "joins", "hits", "busy")}

    def cold_group(self, size):
        before = self.server.snapshot()
        key = self.register(f"fanout-{size}")
        clients = [self.start(key) for _ in range(size)]
        self.join(key, size)
        self.release(key)
        for c in clients:
            self.finish(c)
        # A caller can modify its delivery. The next caller should get pristine bytes.
        (clients[0][1] / "result.bin").write_bytes(b"caller's subsequent edit")
        self.finish(self.start(key))
        counts = self.delta(before)
        check(counts == {"starts": 1, "joins": size - 1, "hits": 1, "busy": 0}, counts)
        check(self.starts[key] == 1, "multiple process executions")
        return {"clients_at_gate": size, "physical_starts": self.starts[key],
                "verified_outputs": size + 1, "late_result_after_mutating_delivery": True, **counts}

    def cancel_one(self, first):
        before = self.server.snapshot()
        key = self.register("cancel-first" if first else "cancel-waiter")
        clients = [self.start(key)]
        self.join(key, 1)
        clients.extend(self.start(key) for _ in range(7))
        self.join(key, 8)
        self.disconnect(clients.pop(0 if first else -1))
        self.join(key, 7)
        self.release(key)
        for c in clients:
            self.finish(c)
        self.finish(self.start(key))
        counts = self.delta(before)
        check(counts == {"starts": 1, "joins": 7, "hits": 1, "busy": 0}, counts)
        check(self.starts[key] == 1, "cancel restarted producer")
        return {"cancelled": "first" if first else "waiter", "subscribers_observed": [1, 8, 7],
                "survivors": 7, "verified_outputs": 8, "physical_starts": self.starts[key], **counts}

    def abandon(self, reattach):
        before = self.server.snapshot()
        key = self.register("reattach" if reattach else "no-subscribers")
        clients = [self.start(key) for _ in range(3)]
        self.join(key, 3)
        for c in clients:
            self.disconnect(c)
        self.join(key, 0)
        survivor = self.start(key) if reattach else None
        if survivor:
            self.join(key, 1)
        self.release(key)
        until(lambda: key not in self.server.snapshot()["active"], "abandoned action did not publish")
        if survivor:
            self.finish(survivor)
        self.finish(self.start(key))
        counts = self.delta(before)
        check(counts == {"starts": 1, "joins": 2 + int(reattach), "hits": 1, "busy": 0}, counts)
        check(self.starts[key] == 1, "abandonment duplicated execution")
        return {"zero_live_subscribers_observed": True, "new_subscriber_before_publication": reattach,
                "verified_outputs": 1 + int(reattach), "physical_starts": self.starts[key], **counts}

    def transient_failure(self):
        before = self.server.snapshot()
        key = self.register("transient-failure")
        (self.specs[key] / "fail-1").touch()
        first = [self.start(key) for _ in range(4)]
        self.join(key, 4)
        self.release(key)
        errors = [self.finish(c, "error")["error"] for c in first]
        check(errors[0] and len(set(errors)) == 1, "failure was not consistently delivered")
        retry = [self.start(key) for _ in range(4)]
        self.join(key, 4, attempt=2)
        self.release(key, attempt=2)
        for c in retry:
            self.finish(c)
        self.finish(self.start(key))
        counts = self.delta(before)
        check(counts == {"starts": 2, "joins": 6, "hits": 1, "busy": 0}, counts)
        check(self.starts[key] == 2, "wrong transient-failure attempt count")
        return {"failed_consumers": 4, "verified_outputs": 5, "physical_starts": 2, **counts}

    def capacity(self):
        before = self.server.snapshot()
        keys = [self.register(f"window-{i}") for i in range(8)]
        producers = [self.start(key) for key in keys[:2]]
        for key in keys[:2]:
            self.join(key, 1)
        denied = [self.start(key) for key in keys[2:]]
        for c in denied:
            self.finish(c, "busy")
        duplicate = self.start(keys[0])
        self.join(keys[0], 2)
        active = self.server.snapshot()["active"]
        check(active == {keys[0]: 2, keys[1]: 1}, active)
        for key in keys[:2]:
            self.release(key)
        for c in [*producers, duplicate]:
            self.finish(c)
        for i in range(2, len(keys), 2):
            pair = keys[i:i+2]
            clients = [self.start(key) for key in pair]
            for key in pair:
                self.join(key, 1)
            for key in pair:
                self.release(key)
            for c in clients:
                self.finish(c)
        counts = self.delta(before)
        check(counts == {"starts": 8, "joins": 1, "hits": 0, "busy": 6}, counts)
        check([self.starts[key] for key in keys] == [1] * 8, "retried key ran twice")
        check(self.peak_live == self.server.snapshot()["peak_active"] == 2, "producer bound")
        return {"distinct_keys": 8, "same_key_joined_at_capacity": True, "verified_outputs": 9,
                "peak_simultaneous_producer_callbacks": self.peak_live, **counts}


def contend(store):
    from shared_action import SharedActionServer

    try:
        with SharedActionServer(store, 1, lambda *_: None):
            raise AssertionError("second process obtained live owner's store")
    except sqlite3.OperationalError as exc:
        check(exc.sqlite_errorcode in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED), str(exc))
        print(json.dumps({"owner_refused": True, "sqlite_result": exc.sqlite_errorname}))


def fresh_owner(root, drain_key):
    from shared_action import SharedActionServer

    probe = Probe(root)
    probe.specs[drain_key] = root / "shutdown-drain"
    with SharedActionServer(root / "published", 2, probe.produce) as server:
        probe.connect(server)
        (root / "restart-ready").write_text(str(os.getpid()))
        until(lambda: server.snapshot()["hits"] == 1, "no durable cache hit on restart")
        snapshot = server.snapshot()
        check(snapshot["starts"] == 0 and snapshot["active"] == {}, snapshot)
        (root / "restart-hit").write_text(json.dumps(snapshot))
        probe.join(drain_key, 2)
        # Return from the context while two clients still await a gated producer.
        (root / "begin-drain").touch()


def restart_and_drain(probe, saved_key):
    key = probe.register("shutdown-drain")
    child = subprocess.Popen([sys.executable, "-B", str(SCRIPT), "--fresh-owner",
                              str(probe.root), key], stderr=subprocess.PIPE, text=True)
    try:
        until(lambda: (probe.root / "restart-ready").is_file(), "second process startup")
        check(child.pid != os.getpid(), "owner did not move to a fresh process")
        probe.finish(probe.start(saved_key))
        until(lambda: (probe.root / "restart-hit").exists(), "persisted result on fresh owner")
        clients = [probe.start(key), probe.start(key)]
        until(lambda: (probe.root / "begin-drain").exists(), "new owner did not begin shutdown")
        address = tuple(json.loads(probe.connection.read_text())["address"])

        def listener_closed():
            try:
                with socket.create_connection(address, timeout=4):
                    return False
            except ConnectionRefusedError:
                return True
            except TimeoutError:
                return False

        until(listener_closed, "server still admitting connections during shutdown")
        check(child.poll() is None, "server stopped before its gated producer finished")
        probe.release(key)
        for client in clients:
            probe.finish(client)
        _, errors = child.communicate(timeout=TIMEOUT)
        check(child.returncode == 0, errors)
        return {"durable_cache": json.loads((probe.root / "restart-hit").read_text()),
                "distinct_owner_processes": True, "subscribers_during_shutdown": 2,
                "listener_closed_before_release": True, "verified_outputs": 3}
    finally:
        probe.stop_clients()
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=TIMEOUT)


def run(root):
    from shared_action import SharedActionServer

    probe = Probe(root)
    store = root / "published"
    with SharedActionServer(store, 2, probe.produce) as server:
        probe.connect(server)
        try:
            report = {"platform": platform.platform(), "producer_limit": 2,
                      "fanout": [probe.cold_group(n) for n in (1, 2, 4, 8, 16)],
                      "disconnect": [probe.cancel_one(first) for first in (True, False)],
                      "abandon": [probe.abandon(reattach) for reattach in (False, True)],
                      "transient_failure": probe.transient_failure(), "capacity": probe.capacity()}
            saved_key = next(iter(probe.specs))
            report["server_before_restart"] = server.snapshot()
            result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--contend", str(store)],
                                    capture_output=True, text=True, check=True, timeout=TIMEOUT)
            report["second_owner_of_live_store"] = json.loads(result.stdout)
        finally:
            probe.stop_clients()
    report["restart_and_shutdown"] = restart_and_drain(probe, saved_key)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--worker", nargs=3, help=argparse.SUPPRESS)
    parser.add_argument("--request", nargs=3, help=argparse.SUPPRESS)
    parser.add_argument("--contend", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--fresh-owner", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        case, stage, attempt = args.worker
        return worker(Path(case), Path(stage), int(attempt))
    if args.request:
        connection, key, workspace = args.request
        request(Path(connection), key, Path(workspace))
        return 0
    if args.contend:
        contend(args.contend)
        return 0
    if args.fresh_owner:
        root, key = args.fresh_owner
        fresh_owner(Path(root), key)
        return 0
    with tempfile.TemporaryDirectory(prefix="shared-action-probe-") as tmp:
        report = run(Path(tmp))
    data = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8")
    else:
        print(data, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
