"""Check forced-refresh publication, timeout cleanup and read-only AC policy."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading

import grpc
import psutil
from build.bazel.remote.execution.v2 import remote_execution_pb2 as api
from build.bazel.remote.execution.v2 import remote_execution_pb2_grpc as rpc
from google.protobuf.duration_pb2 import Duration
from google.rpc import code_pb2

from reapi_execution_probe import Gate, call, completed, digest, prepare, running_worker
from reapi_worker import Cache, Executor
from reapi_worker_store import Store


def publication_race(root):
    cache = Cache(Store(root / "store"))
    executor = Executor(cache, "shared", 2, root / "events", root)
    visible, release = threading.Event(), threading.Event()
    save = cache.save
    starts = []

    def execute(flight, action):
        starts.append(flight.id)
        return api.ActionResult(stdout_raw=flight.id.encode())

    def publish_first(key, result):
        save(key, result)
        if len(starts) == 1:
            visible.set()
            if not release.wait(10):
                raise TimeoutError("publication gate expired")

    executor.execute_command = execute
    cache.save = publish_first
    key = cache.store.put(api.Action().SerializeToString())
    request = api.ExecuteRequest(instance_name="local", action_digest=key, skip_cache_lookup=True)
    class Client:
        def is_active(self):
            return True

    ctx = Client()
    meta = api.RequestMetadata()
    try:
        first = executor.admit(request, ctx, meta)
        if not visible.wait(10) or cache.cached(key).stdout_raw != first.id.encode():
            raise AssertionError("first result was not visible in the AC")
        # The first record is now visible. Admission must still be locked
        # until that flight ceases to be joinable.
        if executor.lock.acquire(blocking=False):
            executor.lock.release()
            raise AssertionError("publication was outside admission lock")
        with ThreadPoolExecutor(max_workers=1) as calls:
            next_request = calls.submit(executor.admit, request, ctx, meta)
            release.set()
            second = next_request.result(10)
        if first.id == second.id or second.result.result(10).result.stdout_raw != second.id.encode():
            raise AssertionError("forced request received an already-visible execution")
        return {"execution_callbacks": len(starts), "fresh_operation": first.id != second.id}
    finally:
        release.set()
        executor.pool.shutdown()


def expect_rpc(code, operation):
    try:
        operation()
    except grpc.RpcError as exc:
        if exc.code() == code:
            return code.name
        raise
    raise AssertionError(f"Expected {code}")


def upload(cas, *data):
    req = api.BatchUpdateBlobsRequest(instance_name="local")
    for value in data:
        req.requests.add(digest=digest(value), data=value)
    result = cas.BatchUpdateBlobs(req, timeout=10)
    if any(row.status.code for row in result.responses):
        raise AssertionError("fixture CAS upload failed")
    return digest(data[-1])


def response(stream):
    result = api.ExecuteResponse()
    last = list(stream)[-1]
    if not last.done or not last.response.Unpack(result):
        raise AssertionError("Missing final ExecuteResponse")
    return result


def timed_actions(service, ac, cas):
    # The local witness gets both process IDs before their action expires.
    with socket.create_server(("127.0.0.1", 0)) as listener:
        listener.settimeout(12)
        port = listener.getsockname()[1]
        program = ("import os,subprocess,sys,socket,time; "
                   "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(40)']); "
                   f"s=socket.create_connection(('127.0.0.1',{port})); "
                   "s.sendall(('%d %d'%(os.getpid(),p.pid)).encode()); s.close(); time.sleep(40)")
        command = api.Command(arguments=[sys.executable, "-I", "-c", program])
        if os.name == "nt":
            command.environment_variables.add(name="SYSTEMROOT", value=os.environ["SystemRoot"])
        action = api.Action(command_digest=upload(cas, command.SerializeToString()),
                            input_root_digest=upload(cas, api.Directory().SerializeToString()))
        results = {}
        for name, seconds, nanos in (("oversize",600,1), ("negative",-1,0), ("nanos",0,1_000_000_000)):
            action.timeout.CopyFrom(Duration(seconds=seconds, nanos=nanos))
            key = upload(cas, action.SerializeToString())
            results[name] = expect_rpc(grpc.StatusCode.INVALID_ARGUMENT,
                                       lambda: list(call(service, key, name)))
        action.timeout.CopyFrom(Duration())
        key = upload(cas, action.SerializeToString())
        zero = response(call(service, key, "zero"))
        if zero.status.code != code_pb2.DEADLINE_EXCEEDED:
            raise AssertionError(zero)
        action.timeout.CopyFrom(Duration(seconds=3))
        key = upload(cas, action.SerializeToString())
        invocation = call(service, key, "expired")
        next(invocation)
        conn, _ = listener.accept()
        with conn, conn.makefile("rb") as witness:
            pids = list(map(int,witness.read().split()))
        timed = response(invocation)
        if timed.status.code != code_pb2.DEADLINE_EXCEEDED or len(pids) != 2:
            raise AssertionError(timed)
        for pid in pids:
            if psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE:
                raise AssertionError(f"Timed-out process still running: {pid}")
        results["cache_after_expiry"] = expect_rpc(grpc.StatusCode.NOT_FOUND, lambda: ac.GetActionResult(
            api.GetActionResultRequest(instance_name="local", action_digest=key), timeout=5))
        return {**results, "expired": timed.status.code, "zero": zero.status.code,
                "observed_processes_terminated": len(pids)}


def run(root):
    worker = Path(__file__).with_name("reapi_worker.py")
    report = {"worker_sha256": hashlib.sha256(worker.read_bytes()).hexdigest(),
              "publication": publication_race(root / "unit")}
    with running_worker(root, "policy") as address, grpc.insecure_channel(address) as channel:
        cas, service = rpc.ContentAddressableStorageStub(channel), rpc.ExecutionStub(channel)
        ac = rpc.ActionCacheStub(channel)
        cap = rpc.CapabilitiesStub(channel).GetCapabilities(api.GetCapabilitiesRequest(instance_name="local"))
        if cap.cache_capabilities.action_cache_update_capabilities.update_enabled:
            raise AssertionError("Worker advertised external AC writes")
        gate = Gate()
        try:
            key = prepare(cas, gate)
            seed = call(service, key, "seed")
            next(seed)
            gate.accept()
            gate.release()
            completed(seed, cas)
            req = api.UpdateActionResultRequest(instance_name="local", action_digest=key,
                                                action_result=api.ActionResult(exit_code=99))
            report["public_AC_update"] = expect_rpc(grpc.StatusCode.PERMISSION_DENIED,
                                                   lambda: ac.UpdateActionResult(req, timeout=5))
            if not completed(call(service, key, "still_cached"), cas)["cached"]:
                raise AssertionError("External update changed the cached result")
            owner = call(service, key, "refresh", skip=True)
            first = next(owner)
            gate.accept()
            follower = call(service, key, "join_refresh", skip=True)
            second = next(follower)
            gate.release()
            completed(owner, cas)
            completed(follower, cas)
            if first.name != second.name or gate.starts != 2:
                raise AssertionError("Concurrent forced requests failed to join an unpublished refresh")
            report["prior_AC_then_forced_pair"] = {"physical_starts_including_seed": gate.starts,
                                                    "same_refresh_operation": True}
        finally:
            gate.close()
        report["timeouts"] = timed_actions(service, ac, cas)
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    with tempfile.TemporaryDirectory(prefix="re-policy-") as temp:
        result = run(Path(temp))
    args.output.write_text(json.dumps(result, indent=2)+"\n", encoding="utf-8")
