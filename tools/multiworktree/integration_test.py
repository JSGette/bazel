#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at https://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Real copied-binary acceptance, including a build/test execution barrier.

Run explicitly, outside a Bazel sandbox. Creates only under a new --root.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

import bazel_multiworktree as mw


def wait_until(predicate, message, timeout=120):
  deadline = time.monotonic() + timeout
  while not predicate():
    if time.monotonic() >= deadline:
      raise AssertionError(message)
    time.sleep(0.05)


class Acceptance:

  def __init__(self, options):
    self.options = options
    self.root = options.root.resolve()
    self.root.mkdir(parents=True, exist_ok=False)
    self.script = Path(__file__).with_name("bazel_multiworktree.py").resolve()
    self.state = self.root / "state"
    self.base = [sys.executable, str(self.script), "--state", str(self.state)]
    self.server_count = 0
    self.events = []
    self.commands = []
    self.pending = []
    self.release = self.root / "release"
    self.server = None
    self.environments = {}
    self.workspaces = {}
    for name in ("a", "b", "café"):
      self.workspace(name)

  def workspace(self, name):
    def quote(value):
      return json.dumps(value, ensure_ascii=False)

    path = self.root / name
    path.mkdir()
    (path / "MODULE.bazel").touch()
    (path / "input.txt").write_text(name + "\n")
    # A private local dependency differs across the workspaces too.
    dependency = path / "dep"
    dependency.mkdir()
    (dependency / "MODULE.bazel").write_text('module(name="private_dep")\n')
    (dependency / "BUILD.bazel").write_text('exports_files(["value.txt"])\n')
    (dependency / "value.txt").write_text("dep-" + name + "\n")
    (path / "MODULE.bazel").write_text(
        'bazel_dep(name="private_dep", version="1.0")\n'
        'local_path_override(module_name="private_dep", path="dep")\n'
    )
    ready = self.root / (name + ".ready")
    gate = (
        "touch "
        + shlex.quote(str(ready))
        + "; while [ ! -f "
        + shlex.quote(str(self.release))
        + " ]; do sleep 0.1; done; "
    )
    (path / "checks.bzl").write_text(
        "def _impl(ctx):\n    out = ctx.actions.declare_file(ctx.label.name +"
        ' ".sh")\n    ctx.actions.write(out, "#!/bin/sh\\n" + ctx.attr.body,'
        " is_executable=True)\n    return"
        " [DefaultInfo(executable=out)]\ncheck_test ="
        ' rule(implementation=_impl, test=True, attrs={"body":'
        " attr.string()})\n"
    )
    command = (
        "cat $(location input.txt) $(location @private_dep//:value.txt) >$@;"
        " echo $(FLAVOR) >>$@; echo $$WORKTREE_ENV >>$@"
    )
    (path / "BUILD.bazel").write_text(
        'load(":checks.bzl", "check_test")\ngenrule(name="value",'
        ' srcs=["input.txt", "@private_dep//:value.txt"], outs=["value.txt"],'
        " cmd="
        + quote(command)
        + ')\ngenrule(name="gate", srcs=["input.txt"], outs=["gate.txt"],'
        " local=True, cmd="
        + quote(gate + "cat $(location input.txt) >$@")
        + ')\ncheck_test(name="gate_test", tags=["local"], body='
        + quote(gate + 'test "$WORKTREE_ENV" = ' + shlex.quote(name) + "\n")
        + ')\ncheck_test(name="pass_test", body='
        + quote('test "$WORKTREE_ENV" = ' + shlex.quote(name) + "\n")
        + ')\ncheck_test(name="fail_test", body="exit 1\\n")\n'
    )
    self.workspaces[name] = path
    self.environments[name] = dict(os.environ, WORKTREE_ENV=name)
    return path

  def start(self):
    if self.server:
      self.server_log.close()
    self.server_count += 1
    log_path = self.root / ("supervisor-%d.log" % self.server_count)
    self.server_log = log_path.open("wb")
    journal = self.state / "session.json"
    previous = (
        json.loads(journal.read_text())["session"] if journal.exists() else None
    )
    self.server = subprocess.Popen(
        self.base
        + [
            "--bazel",
            str(self.options.bazel),
            "--max-engines=2",
            "--cpus=4",
            "--memory-mb=4096",
            "--heap-mb=512",
            "serve",
        ],
        stdout=self.server_log,
        stderr=self.server_log,
    )
    wait_until(
        lambda: (
            journal.exists()
            and json.loads(journal.read_text())["session"] != previous
        )
        or self.server.poll() is not None,
        "Supervisor did not start",
    )
    assert self.server.poll() is None, log_path.read_text()

  def launch(self, name, arguments, no_wait=False):
    argv = self.base + (["--no-wait"] if no_wait else []) + arguments
    index = len(self.commands)
    log = self.root / ("command-%02d" % index)
    stdout = log.with_suffix(".out").open("wb")
    stderr = log.with_suffix(".err").open("wb")
    record = dict(
        argv=argv,
        workspace=str(self.workspaces[name]),
        stdout_path=stdout.name,
        stderr_path=stderr.name,
        started=time.monotonic(),
    )
    self.commands.append(record)
    proc = subprocess.Popen(
        argv,
        cwd=self.workspaces[name],
        env=self.environments[name],
        stdout=stdout,
        stderr=stderr,
    )
    self.pending.append(proc)
    return proc, record, stdout, stderr

  def finish(self, task, code=0):
    proc, record, stdout, stderr = task
    actual = proc.wait(timeout=120)
    stdout.close()
    stderr.close()
    self.pending.remove(proc)
    record.update(
        returncode=actual, seconds=time.monotonic() - record["started"]
    )
    record["stdout"] = Path(record["stdout_path"]).read_text(errors="replace")
    record["stderr"] = Path(record["stderr_path"]).read_text(errors="replace")
    assert actual == code, record
    return record["stdout"].strip()

  def run(self, name, arguments, code=0, no_wait=False):
    return self.finish(self.launch(name, arguments, no_wait), code)

  def build_args(self, name, command="build", target="//:value"):
    return [
        command,
        "--repository_cache=" + str(self.options.repository_cache),
        "--define=FLAVOR=flag-" + name,
        "--action_env=WORKTREE_ENV",
        "--test_env=WORKTREE_ENV",
        "--color=no",
        "--curses=no",
        target,
    ]

  def status(self):
    # Status polling isn't part of the performance cohort or a workspace command.
    proc = subprocess.run(
        self.base + ["status"], capture_output=True, text=True, timeout=10
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)

  def pid(self, name):
    return int(self.run(name, ["info", "server_pid"]))

  def check_value(self, name, environment=None):
    actual = (
        (self.workspaces[name] / "bazel-bin/value.txt").read_text().splitlines()
    )
    assert actual == [
        name,
        "dep-" + name,
        "flag-" + name,
        environment or name,
    ], actual

  def record(self, name, **details):
    self.events.append(dict(check=name, **details))
    print("PASS " + name, flush=True)

  def test(self):
    self.start()
    # A -> B -> A: distinct dependency/config/environment values, retained warm JVMs.
    pid_a, pid_b = self.pid("a"), self.pid("b")
    assert pid_a != pid_b
    for name in ("a", "b"):
      self.run(name, self.build_args(name))
      self.check_value(name)
    output = self.workspaces["a"] / "bazel-bin/value.txt"
    mtime = output.stat().st_mtime_ns
    self.run("a", self.build_args("a"))
    assert output.stat().st_mtime_ns == mtime
    assert (
        "0 packages loaded, 0 targets configured" in self.commands[-1]["stderr"]
    ), self.commands[-1]
    assert (
        "1 process: 1 internal" in self.commands[-1]["stderr"]
    ), self.commands[-1]
    assert self.pid("a") == pid_a and self.pid("b") == pid_b
    self.record(
        "warm_A_B_A_private_sources_dependencies_flags_environment",
        pids=[pid_a, pid_b],
    )

    # One build and one test must both execute before either barrier is released.
    a = self.launch("a", self.build_args("a", target="//:gate"))
    b = self.launch("b", self.build_args("b", "test", "//:gate_test"))
    wait_until(
        lambda: (self.root / "a.ready").exists()
        and (self.root / "b.ready").exists(),
        "Both build and test did not reach the execution barrier",
    )
    assert (
        a[0].poll() is None
        and b[0].poll() is None
        and not self.release.exists()
    )
    snapshot = self.status()
    assert sum(e["busy"] for e in snapshot["engines"]) == 2
    self.record("build_and_test_overlap_at_barrier", status=snapshot)
    self.run("a", ["info", "server_pid"], code=9, no_wait=True)
    self.run("a", ["stop"], code=9)
    queued = self.launch("a", ["info", "server_pid"])
    wait_until(
        lambda: self.status()["queued"] == 1,
        "Same-engine request did not queue",
    )
    queued[0].send_signal(signal.SIGINT)
    self.finish(queued, 8)
    assert (
        self.status()["queued"] == 0
        and a[0].poll() is None
        and b[0].poll() is None
    )
    self.record("same_engine_serialization_nonblocking_and_queued_cancel")
    # Complete both overlapping commands successfully before the cancellation cohort.
    self.release.touch()
    self.finish(a)
    self.finish(b)
    assert (self.workspaces["a"] / "bazel-bin/gate.txt").read_text() == "a\n"
    assert self.pid("a") == pid_a and self.pid("b") == pid_b
    self.record("overlapping_build_and_test_both_complete_successfully")
    self.release.unlink()
    (self.root / "a.ready").unlink()
    (self.root / "b.ready").unlink()
    a = self.launch(
        "a",
        self.build_args("a", target="//:gate") + ["--action_env=GATE_EPOCH=2"],
    )
    b = self.launch(
        "b",
        self.build_args("b", "test", "//:gate_test")
        + ["--nocache_test_results"],
    )
    wait_until(
        lambda: (self.root / "a.ready").exists()
        and (self.root / "b.ready").exists(),
        "Cancellation cohort did not reach both barriers",
    )
    # Failure and shutdown of A cannot terminate B's active test.
    # Cancel A while B stays at the barrier, then shut down and recreate A.
    a[0].send_signal(signal.SIGINT)
    self.finish(a, 8)
    assert b[0].poll() is None and mw.process_ticks(pid_b)
    self.run("a", ["shutdown"])
    assert not mw.process_ticks(pid_a) and b[0].poll() is None
    self.run("a", self.build_args("a", target="//:missing"), code=1)
    assert b[0].poll() is None and mw.process_ticks(pid_b)
    self.record(
        "running_cancel_failure_and_workspace_shutdown_isolate_other_test"
    )
    self.release.touch()
    self.finish(b)
    assert self.pid("b") == pid_b
    self.run("a", self.build_args("a"))
    self.check_value("a")

    # Resource policy survives direct overrides; inspect canonical BEP command options.
    bep = self.root / "policy.json"
    arguments = self.build_args("a") + [
        "--jobs=999",
        "--loading_phase_threads=999",
        "--local_resources=cpu=999",
        "--local_resources=memory=999999",
        "--local_test_jobs=999",
        "--build_event_json_file=" + str(bep),
    ]
    self.run("a", arguments)
    options = {}
    for line in bep.read_text().splitlines():
      command_line = json.loads(line).get("structuredCommandLine", {})
      if command_line.get("commandLineLabel") == "canonical":
        for section in command_line.get("sections", []):
          for option in section.get("optionList", {}).get("option", []):
            options.setdefault(option["optionName"], []).append(
                option["optionValue"]
            )
    assert options["jobs"] == ["2"] and options["loading_phase_threads"] == [
        "2"
    ], options
    assert set(options["local_resources"]) == {"cpu=2", "memory=1408"}, options
    assert options["local_test_jobs"] == ["2"], options
    self.record(
        "aggregate_resource_policy_cannot_be_overridden",
        enforced={
            key: options[key]
            for key in (
                "jobs",
                "loading_phase_threads",
                "local_resources",
                "local_test_jobs",
            )
        },
    )

    # Environment changes retire only the requesting engine, preserving normal env semantics.
    previous = self.pid("a")
    self.environments["a"]["WORKTREE_ENV"] = "changed"
    self.run("a", self.build_args("a"))
    self.check_value("a", "changed")
    assert self.pid("a") != previous and not mw.process_ticks(previous)
    assert self.pid("b") == pid_b
    self.record("environment_change_drains_only_own_engine")

    # Alias resolves to the same warm engine, and a third workspace evicts only an idle LRU.
    alias = self.root / "alias-a"
    alias.symlink_to(self.workspaces["a"])
    original = self.workspaces["a"]
    self.workspaces["a"] = alias
    current_a = self.pid("a")
    self.workspaces["a"] = original
    assert self.pid("a") == current_a
    self.run("café", self.build_args("café"))
    self.check_value("café")
    snapshot = self.status()
    assert len(snapshot["engines"]) == 2 and not mw.process_ticks(pid_b)
    assert self.pid("a") == current_a
    self.record(
        "canonical_alias_non_ascii_and_bounded_idle_eviction", status=snapshot
    )

    # A disconnected client cancels its action; another engine remains available.
    self.release.unlink()
    (self.root / "a.ready").unlink()
    disconnected = self.launch("a", self.build_args("a", target="//:gate"))
    wait_until(
        lambda: (self.root / "a.ready").exists(),
        "Disconnect action did not start",
    )
    disconnected[0].kill()
    self.finish(disconnected, -signal.SIGKILL)
    wait_until(
        lambda: not any(e["busy"] for e in self.status()["engines"]),
        "Disconnected action was not cancelled",
    )
    assert self.pid("a") == current_a
    self.run("café", self.build_args("café", "test", "//:pass_test"))
    self.run("café", self.build_args("café", "test", "//:fail_test"), code=3)
    assert self.pid("a") == current_a
    self.record("disconnect_cancellation_and_test_status_isolation")

    # A workspace replacement is fenced; restoring it allows only shutdown.
    saved = original.with_name("a.saved")
    original.rename(saved)
    original.mkdir()
    (original / "MODULE.bazel").touch()
    (original / "sentinel").write_text("untouched")
    self.run("a", ["info", "server_pid"], code=36)
    assert (original / "sentinel").read_text() == "untouched"
    replacement = original.with_name("a.replacement")
    original.rename(replacement)
    saved.rename(original)
    self.run("a", ["info", "server_pid"], code=36)
    self.run("a", ["shutdown"])
    self.record("directory_replacement_fenced_and_restored_shutdown")

    remaining = [e["pid"] for e in self.status()["engines"]]
    self.run("a", ["stop"])
    assert self.server.wait(timeout=30) == 0
    assert all(not mw.process_ticks(pid) for pid in remaining)
    assert json.loads((self.root / "state/session.json").read_text())["closed"]
    self.record("clean_stop_drains_and_reaps_workers")

  def test_faults(self):
    # Clean restart gets a fresh generation and supports graceful signal cancellation.
    self.start()
    self.release.unlink(missing_ok=True)
    (self.root / "a.ready").unlink(missing_ok=True)
    active = self.launch("a", self.build_args("a", target="//:gate"))
    wait_until(
        lambda: (self.root / "a.ready").exists(),
        "Signal-drain action did not start",
    )
    pid = self.status()["engines"][0]["pid"]
    self.server.terminate()
    self.finish(active, 8)
    assert self.server.wait(timeout=60) == 0
    assert not mw.process_ticks(pid)
    assert json.loads((self.state / "session.json").read_text())["closed"]
    self.record("supervisor_signal_cancels_drains_and_reaps")

    # A worker crash fences its slot without replacing outputs or stopping its peer.
    self.start()
    pid_a, pid_b = self.pid("a"), self.pid("b")
    os.kill(pid_a, signal.SIGKILL)
    wait_until(lambda: not mw.process_ticks(pid_a), "Worker did not exit")
    self.run("a", ["info", "server_pid"], code=36)
    assert self.pid("b") == pid_b
    snapshot = self.status()
    assert any(
        e["pid"] == pid_a and e["quarantined"] for e in snapshot["engines"]
    )
    self.run("a", ["stop"], code=36)
    self.run("b", ["shutdown"])
    self.server.terminate()
    assert self.server.wait(timeout=60) == 36
    reaped, code = os.waitpid(pid_a, 0)
    assert reaped == pid_a and os.WTERMSIG(code) == signal.SIGKILL
    assert not json.loads((self.state / "session.json").read_text())["closed"]
    self.record(
        "worker_crash_quarantines_without_output_reuse_or_peer_shutdown",
        status=snapshot,
    )

    # Supervisor crash leaves the worker's native lifetime reservations intact.
    self.state = self.root / "crash-state"
    self.base = [sys.executable, str(self.script), "--state", str(self.state)]
    self.start()
    pid_a = self.pid("a")
    snapshot = self.status()
    engine = snapshot["engines"][0]
    metadata = (
        Path(snapshot["output_user_root"])
        / "shared_servers/v2"
        / engine["registration"]
        / "control/server/startup_profile"
    )
    original_metadata = metadata.read_bytes()
    self.server.kill()
    assert self.server.wait(timeout=30) == -signal.SIGKILL
    assert mw.process_ticks(pid_a)
    restart = subprocess.run(
        self.base + ["--bazel", str(self.options.bazel), "serve"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (
        restart.returncode == 36
        and "Unclean supervisor state" in restart.stderr
    ), restart
    assert metadata.read_bytes() == original_metadata and mw.process_ticks(
        pid_a
    )
    # Explicit test cleanup uses the original worker profile/environment, never a new owner.
    budget = mw.Budget(2, 4, 4096, 512)
    argv = [
        str(self.options.bazel),
        "--nosystem_rc",
        "--nohome_rc",
        "--noworkspace_rc",
        "--output_user_root=" + snapshot["output_user_root"],
        "--experimental_shared_server=" + engine["registration"],
        "--host_jvm_args=-Xmx512m",
        "--host_jvm_args=-XX:ActiveProcessorCount=2",
        "--max_idle_secs=0",
        "--invocation_policy=" + budget.policy(),
        "shutdown",
    ]
    cleanup = subprocess.run(
        argv,
        cwd=self.workspaces["a"],
        env=self.environments["a"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert cleanup.returncode == 0, cleanup
    assert os.waitpid(pid_a, 0) == (pid_a, 0)
    self.record(
        "supervisor_crash_refuses_restart_and_preserves_worker_ownership",
        rejected_restart=restart.stderr,
        original_worker_reaped=True,
    )

  def cleanup(self):
    self.release.touch()
    for proc in self.pending:
      if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
    for proc in self.pending:
      try:
        proc.wait(timeout=30)
      except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    if self.server and self.server.poll() is None:
      self.server.terminate()
      try:
        self.server.wait(timeout=90)
      except subprocess.TimeoutExpired:
        self.server.kill()
        self.server.wait()
    if self.server:
      self.server_log.close()
    self.options.result.write_text(
        json.dumps(
            dict(
                binary_sha256=hashlib.file_digest(
                    self.options.bazel.open("rb"), "sha256"
                ).hexdigest(),
                checks=self.events,
                commands=self.commands,
            ),
            indent=2,
        )
        + "\n"
    )


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--bazel", type=Path, required=True)
  parser.add_argument("--repository-cache", type=Path, required=True)
  parser.add_argument("--root", type=Path, required=True)
  parser.add_argument("--result", type=Path, required=True)
  options = parser.parse_args()
  options.bazel = options.bazel.resolve(strict=True)
  options.repository_cache = options.repository_cache.resolve(strict=True)
  assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
  acceptance = Acceptance(options)
  try:
    acceptance.test()
    acceptance.test_faults()
  finally:
    acceptance.cleanup()


if __name__ == "__main__":
  main()
