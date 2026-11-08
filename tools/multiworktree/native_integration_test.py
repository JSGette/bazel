#!/usr/bin/env python3
"""Exercise native same-JVM execution, sharing, and failure isolation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time


REQUIRED_CHECKS = frozenset({
    "admitted workspaces report one server PID",
    "engine beyond configured admission bound rejected",
    "admitted engines have distinct owned output bases",
    "all admitted engines overlap at execution barrier and preserve outputs",
    "divergent builds overlap at execution barrier and preserve outputs",
    "identical action digest executes once and materializes twice",
    "warm A-B-A retains private incremental state",
    "shared listings preserve private glob additions, entry types, and restoration",
    "environment differences remain private and execute independently",
    "configuration differences remain private and execute independently",
    "two test commands overlap at execution barrier",
    "ordinary failure does not cancel another engine",
    "same-engine command lock rejects nonblocking competitor",
    "cancelled waiter leaves other consumer able to complete",
    "cancelled producer leaves other consumer able to complete",
    "engine shutdown leaves another engine running in same PID",
    "closed retained engine cannot reopen before daemon restart",
    "closed retained slot does not admit a replacement",
    "last engine shutdown terminates daemon",
    "fresh daemon admits previously closed workspace",
    "restarted daemon shutdown terminates daemon",
})


def process_identity(pid):
  """PID plus Linux start ticks; zombies count as exited, reused PIDs differ."""
  try:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
  except FileNotFoundError:
    return None
  if fields[0] == "Z":
    return None
  return (pid, int(fields[19]))


def owned_child(root, path):
  path = Path(path)
  if not path.is_absolute() or path.resolve() != path or not path.is_relative_to(root) or path == root:
    raise ValueError("Writable path must be a non-aliased child of the fresh root: " + str(path))
  return path


def read_spawns(path):
  remaining = path.read_text()
  result = []
  decoder = json.JSONDecoder()
  while remaining.strip():
    value, end = decoder.raw_decode(remaining.lstrip())
    result.append(value)
    remaining = remaining.lstrip()[end:]
  return result


class Harness:

  def __init__(self, binary, root, repository_cache=None, *, heap_mib=None,
               processors=2, tmp_root=None, expected_engine_cap=2):
    if not 2 <= expected_engine_cap <= 10 or processors < 1:
      raise ValueError("Expected cap must be 2..10 and processors must be positive")
    heap_mib = 768 * expected_engine_cap if heap_mib is None else heap_mib
    if heap_mib < 1:
      raise ValueError("Heap must be positive")
    if not root.is_absolute() or root.resolve() != root or root.exists():
      raise ValueError("Acceptance root must be new, absolute and non-aliased")
    self.root = root
    self.tmp_root = owned_child(root, tmp_root or root / "tmp")
    self.repository_cache = owned_child(root, repository_cache or root / "repository-cache")
    if self.tmp_root == self.repository_cache or self.tmp_root.is_relative_to(self.repository_cache) or self.repository_cache.is_relative_to(self.tmp_root):
      raise ValueError("Temporary and repository-cache directories must be separate")
    self.expected_engine_cap = expected_engine_cap
    self.heap_mib, self.processors = heap_mib, processors
    self.workspaces = ["a", "b"] + ["engine-%02d" % i for i in range(2, expected_engine_cap)]
    self.overflow_workspace = "overflow"
    self.daemon_identity = None
    self.daemon_history = []
    self.open_engines = set()
    self.started_engines = set()
    self.closed_engines = set()
    self.output_bases = {}
    self.run_completed = False
    self.cleanup_errors = []
    self.root.mkdir()
    root = self.root
    self.tmp_root.mkdir(parents=True)
    self.repository_cache.mkdir(parents=True)
    with binary.open("rb") as stream:
      self.binary_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    self.env = {
        "HOME": str(root / "home"),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TMP": str(self.tmp_root),
        "TMPDIR": str(self.tmp_root),
        "XDG_CACHE_HOME": str(root / "cache"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    (root / "home").mkdir()
    (root / "cache").mkdir()
    self.startup = [
        str(binary.resolve()),
        "--ignore_all_rc_files",
        "--output_user_root=" + str(root / "state"),
        "--experimental_shared_server=native-acceptance",
        "--host_jvm_args=-Xmx" + str(heap_mib) + "m",
        "--host_jvm_args=-XX:ActiveProcessorCount=" + str(processors),
        "--host_jvm_args=-Djava.io.tmpdir=" + str(self.tmp_root),
        "--noidle_server_tasks",
        "--max_idle_secs=600",
    ]
    self.common = [
        "--incompatible_autoload_externally=",
        "--repository_cache=" + str(self.repository_cache),
        "--repo_env=TMP=" + str(self.tmp_root),
        "--repo_env=TMPDIR=" + str(self.tmp_root),
        "--color=no",
        "--curses=no",
        "--noshow_progress",
    ]
    self.processes = []
    self.checks = []
    self.control_pid_file = root / "state/shared_servers/v3/native-acceptance/control/server/server.pid.txt"
    for name in self.workspaces + [self.overflow_workspace]:
      workspace = root / name
      workspace.mkdir()
      (workspace / "MODULE.bazel").write_text('module(name="proof")\n')
      (workspace / "proof.bzl").write_text(
          "def _impl(ctx):\n    out = ctx.actions.declare_file(ctx.label.name +"
          ' ".sh")\n    ctx.actions.write(out, ctx.attr.script,'
          " is_executable=True)\n    return"
          " [DefaultInfo(executable=out)]\nproof_test ="
          ' rule(implementation=_impl, test=True, attrs={"script":'
          " attr.string()})\n"
      )

  def start(self, name, command, args, directory, label=None, startup_extra=()):
    label = label or name
    argv = self.startup + list(startup_extra) + [command] + list(args)
    if command != "shutdown":
      argv += self.common
    if command in ("build", "test"):
      for kind in ("action_env", "test_env"):
        for variable in ("TMP", "TMPDIR"):
          argv.append("--" + kind + "=" + variable + "=" + str(self.tmp_root))
    log = open(directory / (label + ".log"), "wb")
    (directory / (label + "-command.json")).write_text(
        json.dumps(argv, indent=2)
    )
    try:
      proc = subprocess.Popen(
          argv, cwd=self.root / name, env=self.env,
          stdout=log, stderr=subprocess.STDOUT,
      )
    except BaseException:
      log.close()
      raise
    self.processes.append((proc, log))
    if command != "shutdown" and name in self.workspaces:
      self.started_engines.add(name)
    return proc

  def finish(self, proc, expected=0):
    result = proc.wait(timeout=120)
    if expected is None:
      assert result != 0, result
    else:
      assert result == expected, (proc.args, result)
    return result

  def check(self, name):
    assert name in REQUIRED_CHECKS and name not in self.checks, name
    self.checks.append(name)
    print("PASS", name, flush=True)

  def check_identical_materialization(self):
    for name in ("a", "b"):
      assert (self.root / name / "bazel-bin/out.txt").read_text() == "same\n", name
    self.check("identical action digest executes once and materializes twice")

  def case(self, name, values, test=False, fail=False, action_env=False, workspaces=("a", "b")):
    assert len(workspaces) == len(values), "Each workspace needs one distinct case value"
    directory = self.root / name
    directory.mkdir()
    for workspace, value in zip(workspaces, values):
      source = self.root / workspace
      (source / "input.txt").write_text(value + "\n")
      script = (
          "value=$(cat input.txt); " if not test else "value=" + value + "; "
      )
      if action_env:
        script = "value=$PROOF_VARIANT; "
      script += "echo $value >> " + shlex.quote(str(directory / "starts")) + "; "
      if fail:
        script += '[ "$value" != fail ] || exit 9; '
      script += (
          "touch "
          + shlex.quote(str(directory))
          + '/ready-"$value"; while [ ! -f '
          + shlex.quote(str(directory / "release"))
      )
      script += " ]; do sleep 0.1; done; "
      if test:
        build = (
            'load(":proof.bzl", "proof_test")\nproof_test(name="proof_test",'
            " script="
            + json.dumps("#!/bin/sh\n" + script + "exit 0\n")
            + ")\n"
        )
      else:
        script = script.replace("input.txt", "$(location input.txt)").replace(
            "$", "$$"
        )
        script = script.replace(
            "$$(location input.txt)", "$(location input.txt)"
        )
        script += (
            'printf "%s\\n" "$$PROOF_VARIANT" > $@'
            if action_env
            else "cat $(location input.txt) > $@"
        )
        build = (
            'genrule(name="proof", srcs=["input.txt"], outs=["out.txt"], cmd='
            + json.dumps(script)
            + ")\n"
        )
      (source / "BUILD.bazel").write_text(build)
    return directory

  def build(
      self,
      workspace,
      directory,
      test=False,
      label=None,
      extra=(),
      startup_extra=(),
  ):
    label = label or workspace
    return self.start(
        workspace,
        "test" if test else "build",
        [
            "//:proof_test" if test else "//:proof",
            "--sandbox_writable_path=" + str(directory),
            "--execution_log_json_file="
            + str(directory / (label + "-spawns.json")),
            "--build_event_json_file="
            + str(directory / (label + "-bep.jsonl")),
            *extra,
        ],
        directory,
        label,
        startup_extra,
    )

  def ready(self, directory, values, processes):
    deadline = time.monotonic() + 60
    while not all(
        (directory / ("ready-" + value)).exists() for value in values
    ):
      assert all(
          p.poll() is None for p in processes
      ), "Command exited before barrier: " + str(directory)
      assert time.monotonic() < deadline, "Barrier timeout: " + str(directory)
      time.sleep(0.05)

  def release(self, directory):
    (directory / "release").touch()

  def daemon_live(self):
    if self.daemon_identity is None:
      return False
    return process_identity(self.daemon_identity[0]) == self.daemon_identity

  def identify(self, name, directory, label=None):
    label = label or name
    self.finish(self.start(name, "info", ["server_pid"], directory, label=label))
    pids = [int(line) for line in (directory / (label + ".log")).read_text().splitlines()
            if line.isdecimal()]
    assert len(pids) == 1, pids
    identity = process_identity(pids[0])
    assert identity is not None, "Reported daemon already exited"
    assert int(self.control_pid_file.read_text().strip()) == pids[0]
    if self.daemon_identity is None:
      self.daemon_identity = identity
      self.daemon_history.append(identity)
    else:
      assert self.daemon_identity == identity, "Daemon identity changed"
    self.open_engines.add(name)
    return pids[0]

  def reject_admission(self, directory, label):
    self.finish(self.start(self.overflow_workspace, "info", ["server_pid"],
                           directory, label=label), 36)
    message = (directory / (label + ".log")).read_text()
    bounds = [str(self.expected_engine_cap)]
    if self.expected_engine_cap == 2:
      bounds.append("two")
    assert any("supports at most " + bound + " retained workspace engines" in message
               for bound in bounds), message
    assert self.daemon_live(), "Admission rejection terminated the daemon"

  def shutdown_engine(self, name, directory, label):
    assert self.daemon_live(), "Refusing shutdown that could start a replacement daemon"
    assert int(self.control_pid_file.read_text().strip()) == self.daemon_identity[0]
    self.finish(self.start(name, "shutdown", [], directory, label=label))
    self.open_engines.discard(name)
    self.closed_engines.add(name)

  def wait_daemon_exit(self):
    deadline = time.monotonic() + 20
    while self.daemon_live():
      assert time.monotonic() < deadline, "Daemon did not exit after final engine shutdown"
      time.sleep(0.05)

  def run(self):
    directory = self.root / "identity"
    directory.mkdir()
    pids = [self.identify(name, directory) for name in self.workspaces]
    assert len(set(pids)) == 1
    self.pid = pids[0]
    self.check("admitted workspaces report one server PID")
    engine_parent = self.root / "state/shared_servers/v3/native-acceptance/engines"
    for name in self.workspaces:
      label = name + "-output-base"
      self.finish(self.start(name, "info", ["output_base"], directory, label=label))
      outputs = [Path(line) for line in (directory / (label + ".log")).read_text().splitlines()
                 if line.startswith(str(engine_parent) + "/")]
      assert len(outputs) == 1 and outputs[0].parent == engine_parent, outputs
      assert outputs[0].resolve() == outputs[0] and outputs[0].is_dir()
      assert not (outputs[0] / "server/server.pid.txt").exists()
      self.output_bases[name] = str(outputs[0])
    assert len(set(self.output_bases.values())) == self.expected_engine_cap
    self.check("admitted engines have distinct owned output bases")
    self.reject_admission(directory, "overflow")
    self.check("engine beyond configured admission bound rejected")

    d = self.case("cap-wide-divergent", self.workspaces, workspaces=self.workspaces)
    cohort = [self.build(name, d) for name in self.workspaces]
    self.ready(d, self.workspaces, cohort)
    assert set((d / "starts").read_text().splitlines()) == set(self.workspaces)
    self.release(d)
    for proc in cohort:
      self.finish(proc)
    for name in self.workspaces:
      assert (self.root / name / "bazel-bin/out.txt").read_text() == name + "\n"
    self.check("all admitted engines overlap at execution barrier and preserve outputs")

    d = self.case("divergent", ("a", "b"))
    pair = [self.build(n, d) for n in ("a", "b")]
    self.ready(d, ("a", "b"), pair)
    self.release(d)
    for proc in pair:
      self.finish(proc)
    assert (self.root / "a/bazel-bin/out.txt").read_text() == "a\n"
    assert (self.root / "b/bazel-bin/out.txt").read_text() == "b\n"
    self.check(
        "divergent builds overlap at execution barrier and preserve outputs"
    )

    d = self.case("identical", ("same", "same"))
    pair = [self.build(n, d) for n in ("a", "b")]
    self.ready(d, ("same",), pair)
    time.sleep(1)
    assert (d / "starts").read_text().splitlines() == ["same"]
    self.release(d)
    for proc in pair:
      self.finish(proc)
    spawns = [read_spawns(d / (n + "-spawns.json"))[0] for n in ("a", "b")]
    assert spawns[0]["digest"] == spawns[1]["digest"]
    assert sum(not s["cacheHit"] for s in spawns) == 1
    assert (d / "starts").read_text().splitlines() == ["same"]
    self.check_identical_materialization()
    for name in ("a", "b", "a"):
      self.finish(
          self.build(name, d, label=name + "-warm-" + str(len(self.processes)))
      )
    self.check("warm A-B-A retains private incremental state")

    d = self.root / "glob-isolation"
    d.mkdir()
    for name in ("a", "b"):
      package = self.root / name / "globcase"
      package.mkdir()
      (package / "BUILD.bazel").write_text(
          'genrule(name="listing", srcs=glob(["*.txt"]), outs=["listing.out"], '
          'cmd="cat $(SRCS) > $@")\n'
      )
      (package / "common.txt").write_text("common\n")
      (package / "item.txt").write_text("item\n")
    edited = self.root / "a/globcase"
    for step in ("same", "different", "restored"):
      if step == "different":
        (edited / "item.txt").unlink()
        (edited / "item.txt").mkdir()
        (edited / "extra.txt").write_text("a-only\n")
      elif step == "restored":
        (edited / "extra.txt").unlink()
        (edited / "item.txt").rmdir()
        (edited / "item.txt").write_text("item\n")
      pair = [
          self.start(
              n, "build", ["//globcase:listing"], d, label=n + "-" + step
          )
          for n in ("a", "b")
      ]
      for proc in pair:
        self.finish(proc)
      for name in ("a", "b"):
        expected = (
            "common\na-only\n"
            if name == "a" and step == "different"
            else "common\nitem\n"
        )
        assert (
            self.root / name / "bazel-bin/globcase/listing.out"
        ).read_text() == expected
    self.check(
        "shared listings preserve private glob additions, entry types, and"
        " restoration"
    )

    for kind in ("environment", "configuration"):
      d = self.case(kind, ("same", "same"), action_env=kind == "environment")
      pair = [
          self.build(
              n,
              d,
              extra=(
                  "--action_env=PROOF_VARIANT=" + n
                  if kind == "environment"
                  else "--compilation_mode=" + mode,
              ),
          )
          for n, mode in zip(("a", "b"), ("dbg", "opt"))
      ]
      self.ready(d, ("a", "b") if kind == "environment" else ("same",), pair)
      deadline = time.monotonic() + 30
      while len((d / "starts").read_text().splitlines()) < 2:
        assert all(p.poll() is None for p in pair)
        assert time.monotonic() < deadline, kind + " actions incorrectly shared"
        time.sleep(0.05)
      self.release(d)
      for proc in pair:
        self.finish(proc)
      spawns = [read_spawns(d / (n + "-spawns.json"))[0] for n in ("a", "b")]
      assert spawns[0]["digest"] != spawns[1]["digest"]
      assert all(not s["cacheHit"] for s in spawns)
      if kind == "environment":
        for n in ("a", "b"):
          assert (self.root / n / "bazel-bin/out.txt").read_text() == n + "\n"
      self.check(kind + " differences remain private and execute independently")

    d = self.case("test-overlap", ("a", "b"), test=True)
    pair = [self.build(n, d, test=True) for n in ("a", "b")]
    self.ready(d, ("a", "b"), pair)
    self.release(d)
    for proc in pair:
      self.finish(proc)
    self.check("two test commands overlap at execution barrier")

    d = self.case("failure-isolation", ("fail", "b"), fail=True)
    failing, healthy = [self.build(n, d) for n in ("a", "b")]
    self.finish(failing, 1)
    self.ready(d, ("b",), [healthy])
    self.release(d)
    self.finish(healthy)
    self.check("ordinary failure does not cancel another engine")

    d = self.case("same-engine-lock", ("a", "b"))
    active = self.build("a", d)
    self.ready(d, ("a",), [active])
    blocked = self.build(
        "a", d, label="blocked", startup_extra=("--noblock_for_lock",)
    )
    self.finish(blocked, 9)
    self.release(d)
    self.finish(active)
    self.check("same-engine command lock rejects nonblocking competitor")

    for cancel in ("waiter", "producer"):
      d = self.case("cancel-" + cancel, ("same", "same"))
      producer = self.build("a", d)
      self.ready(d, ("same",), [producer])
      waiter = self.build("b", d)
      time.sleep(1)
      cancelled, survivor = (
          (waiter, producer) if cancel == "waiter" else (producer, waiter)
      )
      cancelled.send_signal(signal.SIGINT)
      self.finish(cancelled, None)
      if cancel == "producer":
        deadline = time.monotonic() + 30
        while len((d / "starts").read_text().splitlines()) < 2:
          assert survivor.poll() is None
          assert time.monotonic() < deadline
          time.sleep(0.05)
      assert survivor.poll() is None
      self.release(d)
      self.finish(survivor)
      self.check(
          "cancelled " + cancel + " leaves other consumer able to complete"
      )

    d = self.case("shutdown-isolation", ("a", "b"))
    for name in self.workspaces[2:]:
      self.shutdown_engine(name, d, "close-" + name)
    active = self.build("b", d)
    self.ready(d, ("b",), [active])
    self.shutdown_engine("a", d, "close-a")
    assert active.poll() is None
    self.release(d)
    self.finish(active)
    self.finish(
        self.start("b", "info", ["server_pid"], d, label="surviving-pid")
    )
    assert str(self.pid) in (d / "surviving-pid.log").read_text().splitlines()
    self.check("engine shutdown leaves another engine running in same PID")
    self.finish(self.start("a", "info", ["server_pid"], d, label="closed-reopen"), 36)
    assert "Workspace engine has shut down; restart the daemon to reopen it" in (
        d / "closed-reopen.log").read_text()
    assert self.daemon_live()
    self.check("closed retained engine cannot reopen before daemon restart")
    self.reject_admission(d, "replacement-rejected")
    self.check("closed retained slot does not admit a replacement")
    self.shutdown_engine("b", d, "last-shutdown")
    self.wait_daemon_exit()
    self.check("last engine shutdown terminates daemon")
    previous_identity = self.daemon_identity
    self.daemon_identity = None
    self.started_engines.clear()
    self.closed_engines.clear()
    self.identify("a", d, "fresh-daemon")
    assert self.daemon_identity != previous_identity
    self.check("fresh daemon admits previously closed workspace")
    self.shutdown_engine("a", d, "fresh-last-shutdown")
    self.wait_daemon_exit()
    self.check("restarted daemon shutdown terminates daemon")
    self.run_completed = True

  def close(self):
    for proc, log in self.processes:
      try:
        if proc.poll() is None:
          proc.send_signal(signal.SIGINT)
        try:
          proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
          proc.kill()
          proc.wait(timeout=15)
      except Exception as error:
        self.cleanup_errors.append(type(error).__name__ + ": " + str(error))
      finally:
        log.close()
    try:
      # A failed first info command may have started the owned daemon before
      # identity capture. Recover only from this fresh root's control PID file,
      # confirming command-line ownership and stable process start ticks.
      if self.daemon_identity is None and self.control_pid_file.exists():
        pid = int(self.control_pid_file.read_text().strip())
        identity = process_identity(pid)
        if identity is not None:
          command_line = Path(f"/proc/{pid}/cmdline").read_bytes()
          assert str(self.root / "state").encode() in command_line
          assert process_identity(pid) == identity
          self.daemon_identity = identity
          self.daemon_history.append(identity)
          self.open_engines.update(self.started_engines)
      if self.daemon_live():
        directory = self.root / "cleanup"
        directory.mkdir()
        candidates = (self.open_engines | self.started_engines) - self.closed_engines
        for name in sorted(candidates):
          if not self.daemon_live():
            break
          try:
            self.shutdown_engine(name, directory, name)
          except Exception as error:
            self.cleanup_errors.append(type(error).__name__ + ": " + str(error))
        self.wait_daemon_exit()
      assert all(process_identity(identity[0]) != identity for identity in self.daemon_history), "Owned daemon remains alive"
    except Exception as error:
      self.cleanup_errors.append(type(error).__name__ + ": " + str(error))
    finally:
      for _, log in self.processes:
        log.close()
    success = (self.run_completed and set(self.checks) == REQUIRED_CHECKS
               and len(self.checks) == len(REQUIRED_CHECKS) and not self.cleanup_errors)
    (self.root / "results.json").write_text(
        json.dumps(
            {
                "checks": self.checks,
                "required_checks": sorted(REQUIRED_CHECKS),
                "missing_checks": sorted(REQUIRED_CHECKS - set(self.checks)),
                "run_completed": self.run_completed,
                "success": success,
                "server_pid": getattr(self, "pid", None),
                "daemon_identities": self.daemon_history,
                "output_bases": self.output_bases,
                "expected_engine_cap": self.expected_engine_cap,
                "heap_mib": self.heap_mib,
                "processors": self.processors,
                "temporary_root": str(self.tmp_root),
                "repository_cache": str(self.repository_cache),
                "startup": self.startup,
                "environment_sha256": hashlib.sha256(json.dumps(self.env, sort_keys=True).encode()).hexdigest(),
                "cleanup_errors": self.cleanup_errors,
                "run_error": getattr(self, "run_error", None),
                "binary_sha256": self.binary_sha256,
                "harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            indent=2,
        )
    )
    if self.cleanup_errors:
      raise RuntimeError("Acceptance cleanup failed; see results.json")
    return success


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--bazel", type=Path, required=True)
  parser.add_argument("--root", type=Path, required=True)
  parser.add_argument("--repository-cache", type=Path,
                      help="Fresh child of --root; defaults to ROOT/repository-cache")
  parser.add_argument("--tmp-root", type=Path, help="Child of --root; defaults to ROOT/tmp")
  parser.add_argument("--heap-mib", type=int, help="Aggregate native heap; default 768 times cap")
  parser.add_argument("--processors", type=int, default=2)
  parser.add_argument("--expected-engine-cap", type=int, default=2)
  args = parser.parse_args()
  harness = Harness(args.bazel, args.root, args.repository_cache,
                    heap_mib=args.heap_mib, processors=args.processors,
                    tmp_root=args.tmp_root, expected_engine_cap=args.expected_engine_cap)
  try:
    harness.run()
  except BaseException as error:
    harness.run_error = type(error).__name__ + ": " + str(error)
    raise
  finally:
    success = harness.close()
  if not success:
    raise RuntimeError("Acceptance protocol incomplete; see results.json")


if __name__ == "__main__":
  main()
