"""Verify remote execution ownership and cancellation with actual REAPI streams."""

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import grpc
from build.bazel.remote.execution.v2 import remote_execution_pb2 as api
from build.bazel.remote.execution.v2 import remote_execution_pb2_grpc as rpc


SCRIPT = Path(__file__).resolve()
INSTANCE = "local"
PAYLOAD = b"bytes supplied through the REAPI input root\n"
GATE_SCRIPT = b"""import pathlib, socket, sys
with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=45) as gate:
    if gate.recv(1) != b'1':
        raise SystemExit('gate closed')
pathlib.Path('out.txt').write_bytes(pathlib.Path('input.txt').read_bytes().upper())
"""


class Gate:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(15)
        self.port = self.listener.getsockname()[1]
        self.connections = []
        self.starts = 0

    def accept(self):
        conn, _ = self.listener.accept()
        self.connections.append(conn)
        self.starts += 1

    def release(self):
        for conn in self.connections:
            with conn:
                conn.sendall(b"1")
        self.connections.clear()

    def close(self):
        self.release()
        self.listener.close()


def digest(data):
    return api.Digest(hash=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


def prepare(cas, gate, uncacheable=False):
    command = api.Command(arguments=[sys.executable, "-I", "-B", "gate.py", str(gate.port)],
                          output_paths=["out.txt"])
    if os.name == "nt":
        command.environment_variables.add(name="SYSTEMROOT", value=os.environ["SystemRoot"])
    root = api.Directory()
    for name, payload in (("gate.py", GATE_SCRIPT), ("input.txt", PAYLOAD)):
        root.files.add(name=name, digest=digest(payload))
    cmd_blob, dir_blob = command.SerializeToString(), root.SerializeToString()
    action = api.Action(command_digest=digest(cmd_blob), input_root_digest=digest(dir_blob),
                        do_not_cache=uncacheable)
    action.timeout.seconds = 60
    action_blob = action.SerializeToString()
    req = api.BatchUpdateBlobsRequest(instance_name=INSTANCE)
    for blob in (GATE_SCRIPT, PAYLOAD, cmd_blob, dir_blob, action_blob):
        req.requests.add(digest=digest(blob), data=blob)
    result = cas.BatchUpdateBlobs(req, timeout=15)
    if len(result.responses) != len(req.requests) or any(r.status.code for r in result.responses):
        raise ValueError(f"worker rejected input blobs: {result}")
    return digest(action_blob)


def call(service, key, name, skip=False):
    metadata = api.RequestMetadata(tool_invocation_id=name, action_id=name,
                                   action_mnemonic="OwnershipProbe")
    return service.Execute(api.ExecuteRequest(instance_name=INSTANCE, action_digest=key,
                           skip_cache_lookup=skip), timeout=75,
                           metadata=(("build.bazel.remote.execution.v2.requestmetadata-bin",
                                      metadata.SerializeToString()),))


def wait_until(predicate, what, delay=15):
    deadline = time.monotonic() + delay
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError(what)
        time.sleep(.025)


def completed(stream, cas):
    operations = list(stream)
    if not operations or not operations[-1].done:
        raise ValueError("Stream closed without a completed operation")
    response = api.ExecuteResponse()
    if not operations[-1].response.Unpack(response) or response.status.code or response.result.exit_code:
        raise ValueError(f"Unsuccessful operation: {operations[-1]}")
    artifact, = response.result.output_files
    if artifact.path != "out.txt" or artifact.digest != digest(PAYLOAD.upper()):
        raise ValueError(f"Output digest did not match expected bytes: {artifact}")
    req = api.BatchReadBlobsRequest(instance_name=INSTANCE, digests=[artifact.digest])
    downloaded, = cas.BatchReadBlobs(req, timeout=10).responses
    if downloaded.status.code or downloaded.data != PAYLOAD.upper():
        raise ValueError("Published CAS output bytes did not match")
    return dict(operation=operations[-1].name, cached=response.cached_result, sha256=artifact.digest.hash)


def launch_args(root, name, reuse):
    return [sys.executable, "-B", str(SCRIPT.with_name("reapi_worker.py")), "--root", str(root / "store"),
            "--mode", "shared", "--jobs", "2", "--ready-file", str(root / f"{name}.json"),
            "--events", str(root / f"{name}.jsonl"), *(["--reuse-store"] if reuse else [])]


@contextmanager
def running_worker(root, name, reuse=False):
    ready, log = root / f"{name}.json", root / f"{name}.log"
    with log.open("wb") as stderr:
        worker = subprocess.Popen(launch_args(root, name, reuse), stdin=subprocess.PIPE,
                                  stdout=stderr, stderr=subprocess.STDOUT)
    try:
        wait_until(lambda: ready.exists() or worker.poll() is not None, "worker startup")
        if worker.poll() is not None:
            raise RuntimeError(log.read_text(errors="replace")[-2000:])
        yield json.loads(ready.read_text())["endpoint"].removeprefix("grpc://")
    finally:
        if worker.poll() is None:
            try:
                worker.communicate(b"stop\n", timeout=20)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
                raise
        if worker.returncode:
            raise RuntimeError(log.read_text(errors="replace")[-2000:])


def exercise(address, events):
    gate, special = Gate(), Gate()
    try:
        with grpc.insecure_channel(address) as channel:
            service = rpc.ExecutionStub(channel)
            cas = rpc.ContentAddressableStorageStub(channel)
            key = prepare(cas, gate)
            streams = [call(service, key, "origin")]
            first = next(streams[0])
            gate.accept()
            for i in range(3):
                stream = call(service, key, f"follower-{i}")
                if next(stream).done:
                    raise AssertionError("follower completed before physical action was released")
                streams.append(stream)
            def joined():
                rows = [json.loads(line) for line in events.read_text().splitlines() if line]
                return sum(r.get("event") == "join" for r in rows) >= 3
            wait_until(joined, "three followers did not join")
            if first.done or not streams[0].cancel():
                raise AssertionError("first execution stream could not be cancelled at gate")
            def origin_detached():
                rows = [json.loads(line) for line in events.read_text().splitlines() if line]
                return any(r["event"] == "detach" and r["invocation"] == "origin"
                           and r["running"] and r["subscribers"] == 3 for r in rows)
            wait_until(origin_detached, "origin did not detach while the three followers were waiting")
            waiter = service.WaitExecution(api.WaitExecutionRequest(name=first.name), timeout=60)
            if next(waiter).done:
                raise AssertionError("WaitExecution did not attach to the live operation")
            gate.release()
            survivors = [completed(stream, cas) for stream in streams[1:]]
            resumed = completed(waiter, cas)
            late = completed(call(service, key, "late"), cas)
            if gate.starts != 1 or not late["cached"]:
                raise AssertionError("cancelled origin restarted the worker or late request missed")
            # Force a fresh execution after a published hit, then submit a pair
            # that forbids caching. These requests must never consume the old answer.
            forced = call(service, key, "skip", skip=True)
            gate.accept()
            gate.release()
            bypass = completed(forced, cas)
            if gate.starts != 2 or bypass["cached"]:
                raise AssertionError("skip_cache_lookup consumed the published result")
            special_key = prepare(cas, special, uncacheable=True)
            pair = [call(service, special_key, f"uncacheable-{i}") for i in range(2)]
            for stream in pair:
                next(stream)
            special.accept()
            special.accept()
            special.release()
            for stream in pair:
                if completed(stream, cas)["cached"]:
                    raise AssertionError("uncacheable result reported as a hit")
            return dict(action_digest=key.hash, action_size=key.size_bytes, origin=first.name, survivors=survivors,
                        resumed=resumed, late=late, skip_cache_lookup=bypass,
                        live_subscribers_after_origin_cancel=3,
                        starts_before_forced_retry=1, starts_after_forced_retry=gate.starts,
                        uncacheable_starts=special.starts, passed=True)
    finally:
        gate.close()
        special.close()


def run(root):
    with running_worker(root, "first") as address:
        result = exercise(address, root / "first.jsonl")
    with running_worker(root, "restarted", reuse=True) as address, grpc.insecure_channel(address) as channel:
        second_owner = subprocess.run(launch_args(root, "conflicting", True), input=b"stop\n",
                                      capture_output=True, timeout=10)
        if not second_owner.returncode or b"locked" not in second_owner.stderr.lower():
            raise AssertionError("The live owner did not exclude a second writer")
        cas, service = rpc.ContentAddressableStorageStub(channel), rpc.ExecutionStub(channel)
        result["reopened_operation"] = completed(service.WaitExecution(
            api.WaitExecutionRequest(name=result["origin"]), timeout=10), cas)
        key = api.Digest(hash=result["action_digest"], size_bytes=result["action_size"])
        result["reopened_cache"] = completed(call(service, key, "reopened"), cas)
        events = [json.loads(line) for line in (root / "restarted.jsonl").read_text().splitlines()]
        result["reopened_starts"] = sum(e["event"] == "start" for e in events)
        if result["reopened_starts"] or not result["reopened_cache"]["cached"]:
            raise AssertionError("Reopened store re-executed a completed action")
    result["second_owner_rejected"] = True
    result["worker_sha256"] = hashlib.sha256(SCRIPT.with_name("reapi_worker.py").read_bytes()).hexdigest()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="reapi-proof-") as scratch:
        result = run(Path(scratch))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
