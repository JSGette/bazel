#!/usr/bin/env python3
"""Exercise native same-JVM execution, sharing, and failure isolation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time


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

  def __init__(self, binary, root, repository_cache):
    self.root = root.resolve()
    self.root.mkdir()
    root = self.root
    self.binary_sha256 = hashlib.file_digest(
        binary.open("rb"), "sha256"
    ).hexdigest()
    self.env = {
        "HOME": str(root / "home"),
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
    }
    (root / "home").mkdir()
    self.startup = [
        str(binary.resolve()),
        "--nosystem_rc",
        "--nohome_rc",
        "--noworkspace_rc",
        "--output_user_root=" + str(root / "state"),
        "--experimental_shared_server=native-acceptance",
        "--host_jvm_args=-Xmx3g",
        "--host_jvm_args=-XX:ActiveProcessorCount=4",
        "--max_idle_secs=600",
    ]
    self.common = [
        "--incompatible_autoload_externally=",
        "--repository_cache=" + str(repository_cache),
        "--color=no",
        "--curses=no",
        "--noshow_progress",
    ]
    self.processes = []
    self.checks = []
    for name in ("a", "b", "c"):
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
    log = open(directory / (label + ".log"), "wb")
    (directory / (label + "-command.json")).write_text(
        json.dumps(argv, indent=2)
    )
    proc = subprocess.Popen(
        argv,
        cwd=self.root / name,
        env=self.env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    self.processes.append((proc, log))
    return proc

  def finish(self, proc, expected=0):
    result = proc.wait(timeout=120)
    if expected is None:
      assert result != 0, result
    else:
      assert result == expected, (proc.args, result)
    return result

  def check(self, name):
    self.checks.append(name)
    print("PASS", name, flush=True)

  def case(self, name, values, test=False, fail=False, action_env=False):
    directory = self.root / name
    directory.mkdir()
    for workspace, value in zip(("a", "b"), values):
      source = self.root / workspace
      (source / "input.txt").write_text(value + "\n")
      script = (
          "value=$(cat input.txt); " if not test else "value=" + value + "; "
      )
      if action_env:
        script = "value=$PROOF_VARIANT; "
      script += "echo $value >> " + str(directory / "starts") + "; "
      if fail:
        script += '[ "$value" != fail ] || exit 9; '
      script += (
          "touch "
          + str(directory)
          + "/ready-$value; while [ ! -f "
          + str(directory / "release")
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

  def run(self):
    directory = self.root / "identity"
    directory.mkdir()
    pids = []
    for name in ("a", "b"):
      self.finish(self.start(name, "info", ["server_pid"], directory))
      pids.append(
          int(
              next(
                  line
                  for line in (
                      (directory / (name + ".log")).read_text().splitlines()
                  )
                  if line.isdecimal()
              )
          )
      )
    assert pids[0] == pids[1]
    self.pid = pids[0]
    self.check("two workspaces report one server PID")
    self.finish(self.start("c", "info", ["server_pid"], directory), 36)
    self.check("third engine rejected by admission bound")

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
    self.check("identical action digest executes once and materializes twice")
    for name in ("a", "b", "a"):
      self.finish(
          self.build(name, d, label=name + "-warm-" + str(len(self.processes)))
      )
    self.check("warm A-B-A retains private incremental state")

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
    active = self.build("b", d)
    self.ready(d, ("b",), [active])
    self.finish(self.start("a", "shutdown", [], d))
    assert active.poll() is None
    self.release(d)
    self.finish(active)
    self.finish(
        self.start("b", "info", ["server_pid"], d, label="surviving-pid")
    )
    assert str(self.pid) in (d / "surviving-pid.log").read_text().splitlines()
    self.check("engine shutdown leaves another engine running in same PID")
    self.finish(self.start("b", "shutdown", [], d, label="last-shutdown"))
    deadline = time.monotonic() + 20
    while (
        Path(f"/proc/{self.pid}/cmdline").exists()
        and Path(f"/proc/{self.pid}/cmdline").read_bytes()
    ):
      assert time.monotonic() < deadline
      time.sleep(0.05)
    self.check("last engine shutdown terminates daemon")

  def close(self):
    for proc, log in self.processes:
      if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
      try:
        proc.wait(timeout=15)
      except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
      log.close()
    for name in ("a", "b"):
      subprocess.run(
          self.startup + ["shutdown"],
          cwd=self.root / name,
          env=self.env,
          stdout=subprocess.DEVNULL,
          stderr=subprocess.DEVNULL,
          timeout=30,
      )
    (self.root / "results.json").write_text(
        json.dumps(
            {
                "checks": self.checks,
                "success": len(self.checks) == 14,
                "server_pid": getattr(self, "pid", None),
                "binary_sha256": self.binary_sha256,
            },
            indent=2,
        )
    )


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument("--bazel", type=Path, required=True)
  parser.add_argument("--root", type=Path, required=True)
  parser.add_argument("--repository-cache", type=Path, required=True)
  args = parser.parse_args()
  harness = Harness(args.bazel, args.root, args.repository_cache)
  try:
    harness.run()
  finally:
    harness.close()


if __name__ == "__main__":
  main()
