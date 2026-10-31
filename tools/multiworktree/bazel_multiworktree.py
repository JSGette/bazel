#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# https://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Linux concurrent-worktree MVP using the plan's private-process fallback.

One foreground supervisor, bounded retained engines, per-engine serialization.
Requires this checkout's Bazel binary with v2 shared-server ownership support.
See README.md for the supported contract and deliberate limitations.
"""

import argparse
import base64
import ctypes
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
import uuid

MAX_FRAME = 8 * 1024 * 1024
MAX_QUEUE = 64
COMMANDS = ("build", "test", "info", "shutdown", "status", "stop")


class Rejected(Exception):

  def __init__(self, message, code=36):
    super().__init__(message)
    self.code = code


def encode_frame(value):
  data = json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()
  if len(data) > MAX_FRAME:
    raise Rejected("Request exceeds the 8 MiB protocol limit", 2)
  return struct.pack("!I", len(data)) + data


def read_exact(conn, size, deadline=None):
  data = bytearray()
  while len(data) < size:
    if deadline is not None and time.monotonic() >= deadline:
      raise Rejected("Request timed out", 2)
    try:
      block = conn.recv(size - len(data))
    except socket.timeout:
      continue
    if not block:
      raise EOFError()
    data.extend(block)
  return bytes(data)


def read_frame(conn, deadline=None):
  size = struct.unpack("!I", read_exact(conn, 4, deadline))[0]
  if size > MAX_FRAME:
    raise Rejected("Request exceeds the 8 MiB protocol limit", 2)
  try:
    result = json.loads(read_exact(conn, size, deadline))
  except (ValueError, UnicodeError) as error:
    raise Rejected("Invalid protocol JSON", 2) from error
  if not isinstance(result, dict):
    raise Rejected("Expected a protocol object", 2)
  return result


def endpoint(root):
  digest = hashlib.sha256(os.fsencode(root)).hexdigest()
  return "\0bazel-multiworktree-v1:%s:%s" % (os.getuid(), digest)


class DirectoryPin:
  """Admission guard; this does not snapshot files while actions are running."""

  def __init__(self, path):
    self.path = Path(path)
    self.fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    self.identity = self.key(os.fstat(self.fd))
    self.fenced = False
    try:
      self.check()
    except BaseException:
      os.close(self.fd)
      raise

  @staticmethod
  def key(value):
    return value.st_dev, value.st_ino

  def check(self, allow_restored=False):
    try:
      valid = self.key(self.path.lstat()) == self.identity
    except OSError:
      valid = False
    self.fenced |= not valid
    if self.fenced and not (valid and allow_restored):
      raise Rejected("Directory identity changed: " + str(self.path))

  def close(self):
    os.close(self.fd)


@dataclasses.dataclass(frozen=True)
class Budget:
  engines: int
  cpus: int
  memory_mb: int
  heap_mb: int

  def __post_init__(self):
    if not 1 <= self.engines <= 8:
      raise Rejected("--max-engines must be between 1 and 8", 2)
    if self.cpus < self.engines:
      raise Rejected(
          "The CPU budget must provide at least one CPU per engine", 2
      )
    if self.heap_mb < 256 or self.action_memory < 256:
      raise Rejected(
          "Memory budget needs >=256 MiB heap and action memory per engine,"
          " plus 256 MiB supervisor headroom",
          2,
      )

  @property
  def jobs(self):
    return self.cpus // self.engines

  @property
  def action_memory(self):
    return (self.memory_mb - 256) // self.engines - self.heap_mb

  def policy(self):
    # Enforced after all flag/config expansion, not an overrideable --jobs suffix.
    values = {
        "jobs": [str(self.jobs)],
        "loading_phase_threads": [str(self.jobs)],
        "local_test_jobs": [str(self.jobs)],
        "local_resources": [
            "cpu=" + str(self.jobs),
            "memory=" + str(self.action_memory),
        ],
    }
    return " ".join(
        'flag_policies { flag_name: "'
        + key
        + '" commands: "build" set_value { '
        + " ".join('flag_value: "' + value + '"' for value in values[key])
        + " behavior: FINAL_VALUE_IGNORE_OVERRIDES } }"
        for key in values
    )


@dataclasses.dataclass
class Engine:
  workspace: str
  environment: dict
  name: str
  pin: DirectoryPin
  pid: int = 0
  ticks: str = ""
  quarantined: bool = False

  def public(self):
    return {
        "workspace": self.workspace,
        "registration": self.name,
        "pid": self.pid,
        "start_ticks": self.ticks,
        "quarantined": self.quarantined,
    }


@dataclasses.dataclass
class Slot:
  engine: object = None
  busy: bool = False
  target: str = ""
  touched: float = 0


class Scheduler:
  """FIFO among eligible requests, allowing independent engines to progress."""

  def __init__(self, count):
    self.condition = threading.Condition(threading.RLock())
    self.slots = [Slot() for _ in range(count)]
    self.queue = []
    self.stopping = False

  def candidate(self, workspace, earlier, shutdown):
    for slot in self.slots:
      if slot.target == workspace or (
          slot.engine and slot.engine.workspace == workspace
      ):
        if slot.engine and slot.engine.quarantined and not shutdown:
          raise Rejected(
              "Engine is quarantined; inspect status and shut down its original"
              " worker"
          )
        return None if slot.busy or workspace in earlier else slot
    eligible = [
        s
        for s in self.slots
        if not s.busy
        and (
            s.engine is None
            or (not s.engine.quarantined and s.engine.workspace not in earlier)
        )
    ]
    return (
        min(eligible, key=lambda s: (s.engine is not None, s.touched))
        if eligible
        else None
    )

  def acquire(self, workspace, cancelled, no_wait=False, shutdown=False):
    ticket = (object(), workspace)
    with self.condition:
      if len(self.queue) >= MAX_QUEUE:
        raise Rejected("Admission queue is full", 9)
      self.queue.append(ticket)
      try:
        while True:
          if self.stopping or cancelled.is_set():
            raise Rejected("Command cancelled before admission", 8)
          earlier_tickets = self.queue[: self.queue.index(ticket)]
          earlier = {key for _, key in earlier_tickets}
          slot = self.candidate(workspace, earlier, shutdown)
          # Let the first eligible waiter claim capacity before later requests.
          preceding = set()
          for _, key in earlier_tickets:
            try:
              if self.candidate(key, preceding, shutdown=True) is not None:
                slot = None
                break
            except Rejected:
              pass
            preceding.add(key)
          if slot is not None:
            slot.busy = True
            slot.target = workspace
            return slot
          if no_wait:
            raise Rejected(
                "Engine or supervisor capacity is busy (--no-wait)", 9
            )
          if all(
              s.engine and s.engine.quarantined and not s.busy
              for s in self.slots
          ):
            raise Rejected("All engine slots are quarantined")
          self.condition.wait(0.1)
      finally:
        self.queue.remove(ticket)
        self.condition.notify_all()

  def release(self, slot):
    with self.condition:
      slot.busy = False
      slot.target = ""
      slot.touched = time.monotonic()
      self.condition.notify_all()


def process_ticks(pid):
  try:
    fields = Path("/proc/%s/stat" % pid).read_text().rsplit(")", 1)[1].split()
    return "" if fields[0] == "Z" else fields[19]
  except (OSError, IndexError):
    return ""


def workspace_for(cwd):
  path = Path(cwd).resolve(strict=True)
  if not path.is_dir():
    raise Rejected("Client working directory is not a directory", 2)
  for candidate in (path, *path.parents):
    if any(
        (candidate / name).is_file()
        for name in (
            "MODULE.bazel",
            "REPO.bazel",
            "WORKSPACE.bazel",
            "WORKSPACE",
        )
    ):
      return str(candidate)
  raise Rejected("Run this command inside a Bazel workspace", 2)


def validate_request(request):
  if request.get("version") != 1 or request.get("command") not in COMMANDS:
    raise Rejected("Unsupported protocol version or command", 2)
  if request["command"] in ("status", "stop"):
    if request.get("args", []) or request.get("no_wait", False):
      raise Rejected(
          "status and stop accept no command arguments or --no-wait", 2
      )
    return
  for name in ("cwd",):
    if not isinstance(request.get(name), str) or "\0" in request[name]:
      raise Rejected("Invalid " + name, 2)
  args = request.get("args")
  env = request.get("environment")
  if not isinstance(args, list) or not all(
      isinstance(a, str) and "\0" not in a for a in args
  ):
    raise Rejected("Invalid command arguments", 2)
  if not isinstance(env, dict) or not all(
      isinstance(k, str)
      and k
      and "=" not in k
      and "\0" not in k
      and isinstance(v, str)
      and "\0" not in v
      for k, v in env.items()
  ):
    raise Rejected("Invalid environment", 2)
  if type(request.get("no_wait", False)) is not bool:
    raise Rejected("Invalid no_wait", 2)
  if request["command"] == "shutdown" and args:
    raise Rejected("Workspace shutdown does not accept command arguments", 2)


class Connection:

  def __init__(self, conn):
    self.conn = conn
    self.cancelled = threading.Event()
    self.dead = False

  def send(self, value):
    if not self.dead:
      try:
        self.conn.sendall(encode_frame(value))
      except OSError:
        self.dead = True
        self.cancelled.set()
        self.conn.close()

  def output(self, stream, data):
    self.send({stream: base64.b64encode(data).decode()})

  def monitor(self):
    try:
      while True:
        message = read_frame(self.conn)
        if message != {"cancel": True}:
          raise Rejected("Invalid control message", 2)
        self.cancelled.set()
    except (OSError, EOFError, Rejected):
      self.cancelled.set()


class Supervisor:

  def __init__(self, root, bazel, budget):
    self.root = root
    self.bazel = bazel
    self.budget = budget
    self.scheduler = Scheduler(budget.engines)
    self.done = threading.Event()
    self.threads = set()
    self.connections = set()
    self.session = uuid.uuid4().hex
    self.output_root = root / "runs" / self.session
    self.root_pin = DirectoryPin(root)
    self.binary_stat = bazel.stat()
    with bazel.open("rb") as binary:
      self.binary_sha256 = hashlib.file_digest(binary, "sha256").hexdigest()
    self.listener = socket.socket(socket.AF_UNIX)
    self.object_owner = socket.socket(socket.AF_UNIX)
    try:
      self.listener.bind(endpoint(root))
      self.object_owner.bind(
          "\0bazel-multiworktree-object-v1:%s:%s:%s"
          % (os.getuid(), *self.root_pin.identity)
      )
      journal = root / "session.json"
      if journal.exists() and not json.loads(journal.read_text()).get(
          "closed", False
      ):
        raise Rejected(
            "Unclean supervisor state: retained workers may still own it."
            " Inspect session.json; this MVP refuses automatic recovery. Use a"
            " fresh state directory only after accounting for those workers."
        )
      self.output_root.mkdir(parents=True)
      self.run_pin = DirectoryPin(self.output_root)
      self.persist()
      self.listener.listen(64)
      self.listener.settimeout(0.2)
    except BaseException:
      self.listener.close()
      self.object_owner.close()
      self.root_pin.close()
      raise

  def check(self):
    self.root_pin.check()
    self.run_pin.check()
    current = self.bazel.stat()
    if (
        current.st_dev,
        current.st_ino,
        current.st_size,
        current.st_mtime_ns,
    ) != (
        self.binary_stat.st_dev,
        self.binary_stat.st_ino,
        self.binary_stat.st_size,
        self.binary_stat.st_mtime_ns,
    ):
      raise Rejected(
          "Bazel binary changed; stop this supervisor with its original binary"
      )

  def status(self):
    with self.scheduler.condition:
      return {
          "version": 1,
          "mode": "private-process-fallback",
          "session": self.session,
          "supervisor_pid": os.getpid(),
          "bazel": str(self.bazel),
          "binary_sha256": self.binary_sha256,
          "output_user_root": str(self.output_root),
          "budget": dataclasses.asdict(self.budget),
          "action_memory_per_engine_mb": self.budget.action_memory,
          "jobs_per_engine": self.budget.jobs,
          "queued": len(self.scheduler.queue),
          "engines": [
              dict(s.engine.public(), busy=s.busy, target=s.target)
              for s in self.scheduler.slots
              if s.engine
          ],
      }

  def persist(self, closed=False):
    # No environment or command arguments are written to the journal.
    with self.scheduler.condition:
      self.root_pin.check()
      data = dict(self.status(), closed=closed)
      temporary = ".session-" + uuid.uuid4().hex
      fd = os.open(
          temporary,
          os.O_WRONLY | os.O_CREAT | os.O_EXCL,
          0o600,
          dir_fd=self.root_pin.fd,
      )
      with os.fdopen(fd, "w") as output:
        json.dump(data, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
      os.replace(
          temporary,
          "session.json",
          src_dir_fd=self.root_pin.fd,
          dst_dir_fd=self.root_pin.fd,
      )
      os.fsync(self.root_pin.fd)

  def startup(self, engine):
    return [
        str(self.bazel),
        "--nosystem_rc",
        "--nohome_rc",
        "--noworkspace_rc",
        "--output_user_root=" + str(self.output_root),
        "--experimental_shared_server=" + engine.name,
        "--host_jvm_args=-Xmx%sm" % self.budget.heap_mb,
        "--host_jvm_args=-XX:ActiveProcessorCount=%s" % self.budget.jobs,
        "--max_idle_secs=0",
        "--invocation_policy=" + self.budget.policy(),
    ]

  def pump(
      self,
      engine,
      command,
      args,
      cwd,
      connection,
      capture=False,
      cancellable=True,
  ):
    self.check()
    engine.pin.check(allow_restored=command == "shutdown")
    if cancellable and connection.cancelled.is_set():
      raise Rejected("Command cancelled", 8)
    proc = subprocess.Popen(
        self.startup(engine) + [command] + args,
        cwd=cwd,
        env=engine.environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    collected = bytearray()
    interrupted = False
    with selectors.DefaultSelector() as selector:
      selector.register(proc.stdout, selectors.EVENT_READ, "stdout")
      selector.register(proc.stderr, selectors.EVENT_READ, "stderr")
      try:
        while selector.get_map():
          if cancellable and connection.cancelled.is_set() and not interrupted:
            try:
              proc.send_signal(signal.SIGINT)
            except ProcessLookupError:
              pass
            interrupted = True
          for key, _ in selector.select(0.1):
            block = os.read(key.fileobj.fileno(), 16384)
            if not block:
              selector.unregister(key.fileobj)
            elif capture and key.data == "stdout":
              if len(collected) + len(block) > MAX_FRAME:
                raise Rejected("Unexpected oversized engine startup output")
              collected.extend(block)
            else:
              connection.output(key.data, block)
        result = proc.wait()
      finally:
        if proc.poll() is None:
          proc.send_signal(signal.SIGINT)
          proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    return (result if result >= 0 else 128 - result), bytes(collected)

  def verify_engine(self, engine, shutdown=False):
    engine.pin.check(allow_restored=shutdown)
    if not engine.pid or process_ticks(engine.pid) != engine.ticks:
      engine.quarantined = True
      self.persist()
      raise Rejected(
          "Worker exited unexpectedly; its slot is quarantined (no automatic"
          " output reuse)"
      )

  def shutdown_engine(self, slot, connection):
    engine = slot.engine
    if engine is None:
      return
    self.verify_engine(engine, shutdown=True)
    code, _ = self.pump(
        engine, "shutdown", [], engine.workspace, connection, cancellable=False
    )
    if code or process_ticks(engine.pid):
      engine.quarantined = True
      self.persist()
      raise Rejected(
          "Worker shutdown failed; engine ownership retained", code or 36
      )
    try:
      os.waitpid(engine.pid, 0)
    except ChildProcessError:
      pass
    engine.pin.close()
    with self.scheduler.condition:
      slot.engine = None
      self.persist()

  def prepare(self, slot, workspace, environment, connection):
    engine = slot.engine
    if engine and (
        engine.workspace != workspace or engine.environment != environment
    ):
      connection.output(
          "stderr",
          b"multiworktree: retiring idle engine for workspace/environment"
          b" change\n",
      )
      self.shutdown_engine(slot, connection)
      engine = None
    if engine is None:
      engine = Engine(
          workspace,
          environment,
          "e" + uuid.uuid4().hex,
          DirectoryPin(workspace),
      )
      with self.scheduler.condition:
        slot.engine = engine
        self.persist()  # Record the private namespace before any JVM can be started.
      # Startup is drained even if the requester disconnects, so the worker remains tracked.
      code, output = self.pump(
          engine,
          "info",
          ["server_pid"],
          workspace,
          connection,
          capture=True,
          cancellable=False,
      )
      try:
        engine.pid = int(output.strip()) if code == 0 else 0
        engine.ticks = process_ticks(engine.pid) if engine.pid else ""
        if not engine.ticks:
          raise ValueError("Missing worker identity")
      except ValueError as error:
        engine.quarantined = True
        self.persist()
        raise Rejected(
            "Engine startup failed; slot quarantined", code or 36
        ) from error
      self.persist()
    self.verify_engine(engine)
    return engine

  def execute(self, request, connection):
    self.check()
    command = request["command"]
    if command == "status":
      connection.send({"status": self.status()})
      return 0
    if command == "stop":
      return self.stop(connection)
    workspace = workspace_for(request["cwd"])
    if command == "shutdown":
      with self.scheduler.condition:
        if not any(
            s.target == workspace
            or (s.engine and s.engine.workspace == workspace)
            for s in self.scheduler.slots
        ):
          return 0
    slot = self.scheduler.acquire(
        workspace,
        connection.cancelled,
        request.get("no_wait", False),
        command == "shutdown",
    )
    try:
      if command == "shutdown":
        # The engine may have been evicted while this request waited.
        if slot.engine and slot.engine.workspace == workspace:
          self.shutdown_engine(slot, connection)
        return 0
      engine = self.prepare(slot, workspace, request["environment"], connection)
      code, _ = self.pump(
          engine, command, request["args"], request["cwd"], connection
      )
      self.verify_engine(engine)
      return code
    except (OSError, Rejected) as error:
      # Ordinary command failures are returned above; only lifecycle failures fence.
      if slot.engine and (not isinstance(error, Rejected) or error.code != 8):
        slot.engine.quarantined = True
      raise
    finally:
      with self.scheduler.condition:
        self.scheduler.release(slot)
        self.persist()

  def stop(self, connection):
    with self.scheduler.condition:
      if any(s.busy for s in self.scheduler.slots) or self.scheduler.queue:
        raise Rejected(
            "Builds are active or queued; cancel/wait before stopping the"
            " supervisor",
            9,
        )
      if self.scheduler.stopping:
        raise Rejected("Supervisor is already stopping", 9)
      self.scheduler.stopping = True
    try:
      for slot in self.scheduler.slots:
        self.shutdown_engine(slot, connection)
      self.persist(closed=True)
      self.done.set()
      return 0
    except BaseException:
      with self.scheduler.condition:
        self.scheduler.stopping = False
        self.scheduler.condition.notify_all()
      raise

  def handle(self, conn):
    connection = Connection(conn)
    monitor = None
    with self.scheduler.condition:
      self.connections.add(connection)
    try:
      _, uid, _ = struct.unpack(
          "3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
      )
      if uid != os.getuid():
        raise Rejected("Supervisor accepts only clients owned by its user")
      request = read_frame(conn, time.monotonic() + 10)
      validate_request(request)
      monitor = threading.Thread(target=connection.monitor, daemon=True)
      monitor.start()
      code = self.execute(request, connection)
      connection.send({"exit_code": code})
    except Rejected as error:
      connection.output(
          "stderr", ("multiworktree: " + str(error) + "\n").encode()
      )
      connection.send({"exit_code": error.code})
    except (OSError, EOFError, ValueError) as error:
      connection.output(
          "stderr", ("multiworktree: " + str(error) + "\n").encode()
      )
      connection.send({"exit_code": 36})
    finally:
      try:
        conn.shutdown(socket.SHUT_RDWR)
      except OSError:
        pass
      conn.close()
      if monitor:
        monitor.join()
      with self.scheduler.condition:
        self.connections.discard(connection)
        self.threads.discard(threading.current_thread())

  def serve(self):
    # Reap the daemonized worker JVMs after their normal shutdown.
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
      raise Rejected("Cannot establish Linux child subreaper ownership")
    print(json.dumps(self.status()), flush=True)
    interrupted = threading.Event()

    def interrupt(_signum, _frame):
      interrupted.set()

    old_int = signal.signal(signal.SIGINT, interrupt)
    old_term = signal.signal(signal.SIGTERM, interrupt)
    try:
      while not self.done.is_set() and not interrupted.is_set():
        try:
          conn, _ = self.listener.accept()
        except socket.timeout:
          continue
        conn.settimeout(1)
        with self.scheduler.condition:
          if len(self.threads) >= MAX_QUEUE + self.budget.engines + 4:
            conn.close()
            continue
          thread = threading.Thread(target=self.handle, args=(conn,))
          self.threads.add(thread)
          thread.start()
      if interrupted.is_set() and not self.done.is_set():
        with self.scheduler.condition:
          self.scheduler.stopping = True
          for connection in self.connections:
            connection.cancelled.set()
          self.scheduler.condition.notify_all()
        for thread in list(self.threads):
          thread.join()
        self.scheduler.stopping = False
        self.stop(LocalConnection())
    finally:
      signal.signal(signal.SIGINT, old_int)
      signal.signal(signal.SIGTERM, old_term)
      self.listener.close()
      for thread in list(self.threads):
        thread.join()
      self.object_owner.close()
      self.run_pin.close()
      self.root_pin.close()


class LocalConnection:
  """Output sink used while draining the supervisor after a termination signal."""

  def __init__(self):
    self.cancelled = threading.Event()

  def output(self, _stream, data):
    sys.stderr.buffer.write(data)
    sys.stderr.buffer.flush()


def client(root, command, args, no_wait=False):
  request = {
      "version": 1,
      "command": command,
      "args": args,
      "cwd": os.getcwd(),
      "environment": dict(os.environ),
      "no_wait": no_wait,
  }
  with socket.socket(socket.AF_UNIX) as conn:
    conn.settimeout(1)
    conn.connect(endpoint(root))
    conn.sendall(encode_frame(request))

    def cancel(_signum, _frame):
      try:
        conn.sendall(encode_frame({"cancel": True}))
      except OSError:
        pass

    old_int = signal.signal(signal.SIGINT, cancel)
    old_term = signal.signal(signal.SIGTERM, cancel)
    try:
      while True:
        response = read_frame(conn)
        for key, stream in (
            ("stdout", sys.stdout.buffer),
            ("stderr", sys.stderr.buffer),
        ):
          if key in response:
            stream.write(base64.b64decode(response[key], validate=True))
            stream.flush()
        if "status" in response:
          print(json.dumps(response["status"], indent=2), flush=True)
        if "exit_code" in response:
          return response["exit_code"]
    finally:
      signal.signal(signal.SIGINT, old_int)
      signal.signal(signal.SIGTERM, old_term)


def main(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "--state",
      type=Path,
      required=True,
      help="Private supervisor state directory",
  )
  parser.add_argument(
      "--no-wait", action="store_true", help="Reject busy admission with exit 9"
  )
  parser.add_argument(
      "--bazel", type=Path, help="Copied Bazel candidate (serve only)"
  )
  parser.add_argument(
      "--max-engines", type=int, help="Retained engine limit (default: 2)"
  )
  parser.add_argument(
      "--cpus", type=int, help="Aggregate scheduler CPU budget (default: 4)"
  )
  parser.add_argument(
      "--memory-mb",
      type=int,
      help="Aggregate estimated memory budget (default: 4096)",
  )
  parser.add_argument(
      "--heap-mb", type=int, help="JVM heap per engine (default: 768)"
  )
  parser.add_argument("command", choices=("serve", *COMMANDS))
  parser.add_argument("args", nargs=argparse.REMAINDER)
  options = parser.parse_args(argv)
  try:
    if not sys.platform.startswith("linux"):
      raise Rejected(
          "The concurrent-worktree MVP currently supports Linux only", 2
      )
    if options.command == "serve":
      if not options.bazel or options.args or options.no_wait:
        raise Rejected(
            "serve requires --bazel and accepts no command arguments or"
            " --no-wait",
            2,
        )
      options.state.mkdir(mode=0o700, parents=True, exist_ok=True)
      root = options.state.resolve(strict=True)
      mode = root.stat()
      if mode.st_uid != os.getuid() or stat.S_IMODE(mode.st_mode) & 0o077:
        raise Rejected(
            "Supervisor state must be owned by you with mode 0700", 2
        )
      budget = Budget(*(
          default if value is None else value
          for value, default in (
              (options.max_engines, 2),
              (options.cpus, 4),
              (options.memory_mb, 4096),
              (options.heap_mb, 768),
          )
      ))
      supervisor = Supervisor(root, options.bazel.resolve(strict=True), budget)
      # A crash or failed drain leaves the journal unclean and blocks automatic reuse.
      supervisor.serve()
      return 0
    if options.bazel or any(
        value is not None
        for value in (
            options.max_engines,
            options.cpus,
            options.memory_mb,
            options.heap_mb,
        )
    ):
      raise Rejected("--bazel and resource budgets apply only to serve", 2)
    return client(
        options.state.resolve(strict=True),
        options.command,
        options.args,
        options.no_wait,
    )
  except (Rejected, OSError, EOFError, KeyboardInterrupt) as error:
    print(
        "multiworktree: " + (str(error) or "Supervisor connection closed"),
        file=sys.stderr,
    )
    return error.code if isinstance(error, Rejected) else 36


if __name__ == "__main__":
  sys.exit(main())
