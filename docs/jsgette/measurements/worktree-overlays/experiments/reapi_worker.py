"""Trusted loopback REAPI experiment. The worker executes uploaded command trees.

This single-platform worker supports regular files and directories, SHA-256,
plain ByteStream transfers and the local instance. Run only on a trusted host.
"""

import argparse
from contextlib import closing, contextmanager
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid

import grpc
import psutil
from build.bazel.remote.execution.v2 import remote_execution_pb2 as api
from build.bazel.remote.execution.v2 import remote_execution_pb2_grpc as rpc
from google.bytestream import bytestream_pb2 as stream
from google.bytestream import bytestream_pb2_grpc as bs
from google.longrunning import operations_pb2
from google.rpc import code_pb2
from google.rpc.status_pb2 import Status

from reapi_worker_store import Store, atomic, relative


RESOURCE = re.compile(r"local/(?:uploads/[^/]+/)?blobs/([0-9a-f]{64})/(\d+)")
MAX_TIMEOUT = 600


def execution_timeout(action):
    if not action.HasField("timeout"):
        return MAX_TIMEOUT
    seconds, nanos = action.timeout.seconds, action.timeout.nanos
    if (seconds < 0 or not 0 <= nanos < 1_000_000_000 or seconds > MAX_TIMEOUT
            or seconds == MAX_TIMEOUT and nanos):
        raise ValueError(f"Action timeout must be a duration between zero and {MAX_TIMEOUT}s")
    return seconds + nanos / 1_000_000_000


def stop_expired(process):
    """Terminate and wait for this command and its observed local descendants."""
    seen = set()
    try:
        try:
            parent = psutil.Process(process.pid)
            pending = [*parent.children(recursive=True), parent]
        except psutil.NoSuchProcess:
            pending = []
        while pending:
            child = pending.pop()
            if child in seen:
                continue
            seen.add(child)
            try:
                pending.extend(child.children())
                child.kill()
            except psutil.NoSuchProcess:
                pass
    finally:
        process.kill()
        process.wait()
    alive = [child for child in seen if child.pid != process.pid]
    while alive:
        _, remaining = psutil.wait_procs(alive, timeout=.1)
        alive = []
        for child in remaining:
            try:
                if child.status() != psutil.STATUS_ZOMBIE:
                    alive.append(child)
            except psutil.NoSuchProcess:
                pass


def resource(name, ctx):
    match = RESOURCE.fullmatch(name)
    if not match:
        ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, "Unsupported ByteStream resource")
    return api.Digest(hash=match[1], size_bytes=int(match[2]))


def instance(request, ctx):
    if request.instance_name != "local":
        ctx.abort(grpc.StatusCode.NOT_FOUND, "Use instance local")
    if getattr(request, "digest_function", 0) not in (0, api.DigestFunction.SHA256):
        ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, "Only SHA-256 is supported")


def metadata(ctx):
    for name, raw in ctx.invocation_metadata():
        if name == "build.bazel.remote.execution.v2.requestmetadata-bin":
            return api.RequestMetadata.FromString(raw)
    return api.RequestMetadata()


class Cache(rpc.ContentAddressableStorageServicer):
    def __init__(self, store):
        self.store = store
        self.ac_lock = threading.Lock()

    def GetCapabilities(self, req, ctx):
        instance(req, ctx)
        result = api.ServerCapabilities()
        result.low_api_version.major = 2
        result.high_api_version.major = 2
        result.high_api_version.minor = 2
        result.cache_capabilities.digest_functions.append(api.DigestFunction.SHA256)
        result.cache_capabilities.action_cache_update_capabilities.update_enabled = False
        result.cache_capabilities.max_batch_total_size_bytes = 3 * 1024**2
        result.execution_capabilities.digest_function = api.DigestFunction.SHA256
        result.execution_capabilities.exec_enabled = True
        return result

    def FindMissingBlobs(self, req, ctx):
        instance(req, ctx)
        return api.FindMissingBlobsResponse(missing_blob_digests=[
            k for k in req.blob_digests if not self.store.has(k)])

    def BatchUpdateBlobs(self, req, ctx):
        instance(req, ctx)
        result = api.BatchUpdateBlobsResponse()
        for row in req.requests:
            slot = result.responses.add(digest=row.digest)
            try:
                if row.compressor:
                    raise ValueError("Only identity compression is supported")
                self.store.put(row.data, row.digest)
            except ValueError as err:
                slot.status.CopyFrom(Status(code=code_pb2.INVALID_ARGUMENT, message=str(err)))
        return result

    def BatchReadBlobs(self, req, ctx):
        instance(req, ctx)
        result = api.BatchReadBlobsResponse()
        for key in req.digests:
            slot = result.responses.add(digest=key)
            try:
                slot.data = self.store.read(key)
            except FileNotFoundError:
                slot.status.code = code_pb2.NOT_FOUND
        return result

    def GetTree(self, req, ctx):
        instance(req, ctx)
        pending, visited = [req.root_digest], set()
        while pending:
            key = pending.pop()
            if key.hash not in visited:
                try:
                    directory = api.Directory.FromString(self.store.read(key))
                except FileNotFoundError:
                    ctx.abort(grpc.StatusCode.NOT_FOUND, key.hash)
                visited.add(key.hash)
                pending.extend(c.digest for c in directory.directories)
                yield api.GetTreeResponse(directories=[directory])

    def Read(self, req, ctx):
        key = resource(req.resource_name, ctx)
        if not self.store.has(key):
            ctx.abort(grpc.StatusCode.NOT_FOUND, key.hash)
        if req.read_offset < 0 or req.read_offset > key.size_bytes or req.read_limit < 0:
            ctx.abort(grpc.StatusCode.OUT_OF_RANGE, "Bad read offset/limit")
        remaining = key.size_bytes - req.read_offset
        if req.read_limit:
            remaining = min(remaining, req.read_limit)
        with open(self.store.path(key), "rb") as f:
            f.seek(req.read_offset)
            while remaining:
                buf = f.read(min(1024**2, remaining))
                if not buf:
                    ctx.abort(grpc.StatusCode.DATA_LOSS, "Truncated CAS blob")
                remaining -= len(buf)
                yield stream.ReadResponse(data=buf)

    def Write(self, requests, ctx):
        seen, count, finish, key = hashlib.sha256(), 0, False, None
        with tempfile.NamedTemporaryFile(dir=self.store.incoming, delete=False) as f:
            scratch = f.name
            try:
                for row in requests:
                    if key is None:
                        key = resource(row.resource_name, ctx)
                    elif row.resource_name and resource(row.resource_name, ctx) != key:
                        ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, "Write changed blob")
                    if row.write_offset != count or finish:
                        ctx.abort(grpc.StatusCode.OUT_OF_RANGE, "Non-contiguous write")
                    seen.update(row.data)
                    count += len(row.data)
                    if count > key.size_bytes:
                        ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, "Excess bytes")
                    f.write(row.data)
                    finish = row.finish_write
                if key is None or not finish or count != key.size_bytes or seen.hexdigest() != key.hash:
                    ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, "Incomplete or invalid blob")
                f.close()
                self.store.install(scratch, key)
                return stream.WriteResponse(committed_size=count)
            finally:
                f.close()
                os.unlink(scratch)

    def QueryWriteStatus(self, req, ctx):
        key = resource(req.resource_name, ctx)
        if not self.store.has(key):
            ctx.abort(grpc.StatusCode.NOT_FOUND, "Upload must restart")
        return stream.QueryWriteStatusResponse(committed_size=key.size_bytes, complete=True)

    def cached(self, key):
        self.store.path(key)  # validate the hash before deriving a filesystem path
        with self.ac_lock:
            try:
                with open(os.path.join(self.store.ac, key.hash), "rb") as f:
                    return api.ActionResult.FromString(f.read())
            except FileNotFoundError:
                return None

    def save(self, key, result):
        self.store.path(key)
        with self.ac_lock:
            atomic(os.path.join(self.store.ac, key.hash), result.SerializeToString())

    def inline(self, result, req):
        # Decorate a copy, so one subscriber's inline preferences cannot affect another.
        answer = api.ActionResult()
        answer.CopyFrom(result)
        for output in answer.output_files:
            if output.path in req.inline_output_files:
                output.contents = self.store.read(output.digest)
        for channel in ("stdout", "stderr"):
            field = channel + "_digest"
            if getattr(req, "inline_" + channel, False) and answer.HasField(field):
                setattr(answer, channel + "_raw", self.store.read(getattr(answer, field)))
                answer.ClearField(field)
        return answer

    def GetActionResult(self, req, ctx):
        instance(req, ctx)
        result = self.cached(req.action_digest)
        if result is None:
            ctx.abort(grpc.StatusCode.NOT_FOUND, "AC miss")
        self.on_hit(req.action_digest, metadata(ctx))
        return self.inline(result, req)

    def UpdateActionResult(self, req, ctx):
        instance(req, ctx)
        ctx.abort(grpc.StatusCode.PERMISSION_DENIED, "Only worker-produced results are published")


class Flight:
    def __init__(self, digest, meta):
        self.id = uuid.uuid4().hex
        self.digest = digest
        self.meta = meta
        self.result = Future()
        self.subscribers = 0


class Executor:
    def __init__(self, cache, mode, jobs, events, work):
        self.cache, self.store = cache, cache.store
        self.mode, self.jobs, self.events = mode, jobs, events
        self.pool = ThreadPoolExecutor(max_workers=jobs)
        self.lock = threading.Condition()
        self.live, self.by_key = {}, {}
        self.ops = self.store.root / "operations"
        self.work = Path(work)
        self.ops.mkdir(exist_ok=True)
        self.event_lock = threading.Lock()

    def log(self, event, flight, meta, **details):
        self.emit(event, flight.digest, meta, operation="operations/" + flight.id, **details)

    def emit(self, event, digest, meta, **details):
        row = dict(event=event, digest=digest.hash,
                   mnemonic=meta.action_mnemonic, invocation=meta.tool_invocation_id,
                   target=meta.target_id, time_ns=time.time_ns(), **details)
        with self.event_lock, self.events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    @contextmanager
    def workspace(self):
        # The root is new for each worker process. A thread completes its job
        # and cleanup before its staging slot is admitted to another action.
        stage = self.work / str(threading.get_ident())
        stage.mkdir()
        try:
            yield stage
        finally:
            shutil.rmtree(stage)

    def execute_command(self, flight, action):
        cmd = api.Command.FromString(self.store.read(action.command_digest))
        result = api.ActionResult()
        timing = result.execution_metadata
        timing.worker = "loopback-worker"
        timing.worker_start_timestamp.GetCurrentTime()
        timing.input_fetch_start_timestamp.GetCurrentTime()
        with self.workspace() as root:
            execroot = root / "x"
            self.store.load_tree(action.input_root_digest, execroot)
            timing.input_fetch_completed_timestamp.GetCurrentTime()
            cwd = execroot / relative(cmd.working_directory, empty=True)
            cwd.mkdir(parents=True, exist_ok=True)
            outputs = list(cmd.output_paths) or [*cmd.output_files, *cmd.output_directories]
            for name in outputs:
                (cwd / relative(name)).parent.mkdir(parents=True, exist_ok=True)
            if not cmd.arguments:
                raise ValueError("Command has no executable")
            argv = list(cmd.arguments)
            executable = cwd / argv[0]
            if executable.is_file():
                argv[0] = str(executable)
            env = {pair.name: pair.value for pair in cmd.environment_variables}
            scratch = root / "tmp"
            scratch.mkdir()
            # The instance denotes this fixed host toolchain. Supply only
            # platform necessities plus a private temporary directory.
            if os.name == "nt":
                for name in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
                    if name in os.environ:
                        env.setdefault(name, os.environ[name])
                env.setdefault("PATH", os.path.join(os.environ["SYSTEMROOT"], "System32"))
            env.update(TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch))
            timeout = execution_timeout(action)
            if timeout == 0:
                raise subprocess.TimeoutExpired(argv, timeout)
            self.log("start", flight, flight.meta)
            started = time.monotonic()
            timing.execution_start_timestamp.GetCurrentTime()
            with (root/"stdout").open("wb") as out, (root/"stderr").open("wb") as err:
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                           stdout=out, stderr=err)
                try:
                    self.log("running", flight, flight.meta, pid=process.pid)
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    stop_expired(process)
                    self.log("process_timeout", flight, flight.meta, pid=process.pid, timeout=timeout)
                    raise
                except BaseException:
                    stop_expired(process)
                    raise
            timing.execution_completed_timestamp.GetCurrentTime()
            self.log("process_exit", flight, flight.meta, exit=code, elapsed=time.monotonic()-started)
            result.exit_code = code
            timing.output_upload_start_timestamp.GetCurrentTime()
            for channel in ("stdout", "stderr"):
                data = (root/channel).read_bytes()
                if data:
                    getattr(result, channel+"_digest").CopyFrom(self.store.put(data))
            for name in outputs:
                path = cwd / relative(name)
                if path.is_symlink():
                    raise ValueError("Symlink outputs unsupported")
                if path.is_file():
                    result.output_files.add(path=name, digest=self.store.put(path.read_bytes()),
                                            is_executable=os.name != "nt" and os.access(path, os.X_OK))
                elif path.is_dir():
                    tree, tree_root = self.store.output_tree(path)
                    result.output_directories.add(path=name, tree_digest=tree, root_directory_digest=tree_root)
            timing.output_upload_completed_timestamp.GetCurrentTime()
            timing.worker_completed_timestamp.GetCurrentTime()
            return result

    def run(self, flight, action, key):
        try:
            result = self.execute_command(flight, action)
            response = api.ExecuteResponse(result=result)
        except subprocess.TimeoutExpired as exc:
            response = api.ExecuteResponse(status=Status(code=code_pb2.DEADLINE_EXCEEDED, message=str(exc)))
        except Exception as exc:
            self.log("error", flight, flight.meta, error=repr(exc))
            response = api.ExecuteResponse(status=Status(code=code_pb2.INTERNAL, message=repr(exc)))
        cache_result = response.HasField("result") and response.result.exit_code == 0 and not action.do_not_cache
        self.publish(flight, key, response, cache_result=cache_result)

    def publish(self, flight, key, response, *, cache_result=False):
        with self.lock:
            # Once this execution is visible through the AC, a forced request
            # must no longer join it. Admission cannot see an intermediate state.
            if cache_result:
                try:
                    self.cache.save(flight.digest, response.result)
                except Exception as exc:
                    self.log("error", flight, flight.meta, error=repr(exc))
                    response = api.ExecuteResponse(status=Status(code=code_pb2.INTERNAL, message=repr(exc)))
            try:
                atomic(os.path.join(self.ops, flight.id), response.SerializeToString())
                flight.result.set_result(response)
            except Exception as exc:
                flight.result.set_exception(exc)
            finally:
                self.live.pop(flight.id, None)
                if self.by_key.get(key) is flight:
                    del self.by_key[key]
                self.log("finish", flight, flight.meta, subscribers=flight.subscribers)
                self.lock.notify_all()

    def admit(self, req, ctx, meta):
        digest = req.action_digest
        try:
            action = api.Action.FromString(self.store.read(digest))
        except FileNotFoundError:
            ctx.abort(grpc.StatusCode.FAILED_PRECONDITION, "Action unavailable in CAS")
        try:
            execution_timeout(action)
        except ValueError as exc:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, str(exc))
        key = (digest.hash, digest.size_bytes)
        share = self.mode == "shared" and not action.do_not_cache
        with self.lock:
            while ctx.is_active():
                flight = self.by_key.get(key) if share else None
                if flight is not None:
                    flight.subscribers += 1
                    self.log("join", flight, meta, subscribers=flight.subscribers)
                    return flight
                if not req.skip_cache_lookup and not action.do_not_cache:
                    cached = self.cache.cached(digest)
                    if cached is not None:
                        flight = Flight(digest, meta)
                        flight.subscribers = 1
                        response = api.ExecuteResponse(result=cached, cached_result=True)
                        self.log("hit", flight, meta)
                        self.publish(flight, key, response)
                        return flight
                if len(self.live) < self.jobs:
                    flight = Flight(digest, meta)
                    flight.subscribers = 1
                    self.live[flight.id] = flight
                    if share:
                        self.by_key[key] = flight
                    self.log("admit", flight, meta, subscribers=1, active=len(self.live))
                    self.pool.submit(self.run, flight, action, key)
                    return flight
                self.lock.wait(timeout=.1)
        ctx.abort(grpc.StatusCode.CANCELLED, "Client withdrew before admission")

    def watch(self, flight, req, ctx, meta):
        name = "operations/" + flight.id
        detached = False
        def detach():
            nonlocal detached
            with self.lock:
                if not detached:
                    detached = True
                    flight.subscribers -= 1
                    self.log("detach", flight, meta, subscribers=flight.subscribers,
                             running=not flight.result.done())
        if not ctx.add_callback(detach):
            detach()
            return
        try:
            yield operations_pb2.Operation(name=name)
            while ctx.is_active():
                try:
                    response = flight.result.result(timeout=.2)
                    break
                except FutureTimeout:
                    continue
            else:
                return
            reply = api.ExecuteResponse()
            reply.CopyFrom(response)
            if req is not None and reply.HasField("result"):
                reply.result.CopyFrom(self.cache.inline(reply.result, req))
            op = operations_pb2.Operation(name=name, done=True)
            op.response.Pack(reply)
            yield op
        finally:
            detach()

    def Execute(self, req, ctx):
        instance(req, ctx)
        meta = metadata(ctx)
        yield from self.watch(self.admit(req, ctx, meta), req, ctx, meta)

    def WaitExecution(self, req, ctx):
        match = re.fullmatch("operations/([0-9a-f]{32})", req.name)
        if not match:
            ctx.abort(grpc.StatusCode.NOT_FOUND, "Unknown operation")
        with self.lock:
            flight = self.live.get(match[1])
            if flight is not None:
                flight.subscribers += 1
        if flight is not None:
            yield from self.watch(flight, None, ctx, metadata(ctx))
            return
        try:
            with open(os.path.join(self.ops, match[1]), "rb") as f:
                response = api.ExecuteResponse.FromString(f.read())
        except FileNotFoundError:
            ctx.abort(grpc.StatusCode.NOT_FOUND, "Unknown operation")
        op = operations_pb2.Operation(name=req.name, done=True)
        op.response.Pack(response)
        yield op


def serve(args, work):
    args.events.parent.mkdir(parents=True, exist_ok=True)
    store = Store(args.root.resolve())
    cache = Cache(store)
    executor = Executor(cache, args.mode, args.jobs, args.events, work)
    cache.on_hit = lambda key, meta: executor.emit("ac_hit", key, meta)
    with ThreadPoolExecutor(max_workers=96) as dispatch:
        # Bound streams at the HTTP/2 transport, where gRPC queues a client's
        # burst. The Python server's RPC limit rejects excess CAS uploads.
        server = grpc.server(dispatch, options=(("grpc.max_concurrent_streams", 16),
                                      ("grpc.max_receive_message_length",16*1024**2),
                                      ("grpc.max_send_message_length",16*1024**2)))
        rpc.add_CapabilitiesServicer_to_server(cache, server)
        rpc.add_ContentAddressableStorageServicer_to_server(cache, server)
        rpc.add_ActionCacheServicer_to_server(cache, server)
        bs.add_ByteStreamServicer_to_server(cache, server)
        rpc.add_ExecutionServicer_to_server(executor, server)
        port = server.add_insecure_port("127.0.0.1:0")
        server.start()
        atomic(args.ready_file, json.dumps({"endpoint":f"grpc://127.0.0.1:{port}","pid":os.getpid()}).encode())
        try:
            for line in sys.stdin:
                if line.strip() == "stop":
                    break
        finally:
            server.stop(2).wait()
            executor.pool.shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--jobs", type=int, required=True)
    parser.add_argument("--mode", choices=("shared","control"), required=True)
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--reuse-store", action="store_true",
                        help="Reopen a private store created by this worker on this host")
    args = parser.parse_args()
    args.root = args.root.resolve()
    marker = args.root / "namespace.json"
    # The earlier store format admitted unverified external AC updates.
    namespace = {"format": 2, "instance": "local", "digest": "SHA256", "os": sys.platform,
                 "machine": platform.machine(), "host": platform.node()}
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.reuse_store:
        if not marker.is_file() or json.loads(marker.read_text(encoding="utf-8")) != namespace:
            parser.error("Cannot reopen an unmarked or incompatible worker store")
    elif args.root.exists() and any(args.root.iterdir()):
        parser.error("A fresh run requires a private empty root")
    args.root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.root / "owner.sqlite", timeout=0)) as owner:
        owner.execute("BEGIN IMMEDIATE")
        if not args.reuse_store:
            atomic(marker, json.dumps(namespace).encode())
        # A surviving child of a crashed worker can only write its old stage.
        with tempfile.TemporaryDirectory(prefix="stage-", dir=args.root) as work:
            serve(args, work)


if __name__ == "__main__":
    main()
