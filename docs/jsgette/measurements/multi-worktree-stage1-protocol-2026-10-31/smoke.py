#!/usr/bin/env python3
"""Exercise the copied Linux candidate, raw RPC rejection and live cancellation."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--bazel", type=Path, required=True)
parser.add_argument("--probe", type=Path, required=True)
parser.add_argument("--repository-cache", type=Path, required=True)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--result", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=False)
workspace = args.root / "workspace"
workspace.mkdir()
(workspace / "MODULE.bazel").touch()
ready = workspace / "action-ready"
(workspace / "BUILD.bazel").write_text(
    'genrule(name="blocked", outs=["blocked.out"], local=True, cmd='
    + json.dumps("touch '" + str(ready) + "'; exec sleep 60") + ')\n')
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
startup = [str(args.bazel), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
           "--output_user_root=" + str(args.root / "outputs"),
           "--experimental_shared_server=smoke", "--host_jvm_args=-Xmx512m"]
env = dict(os.environ, SHLVL="1", OLDPWD=str(workspace))
results = []


def record(argv, returncode, stdout, stderr, begin):
    results.append(dict(argv=argv, returncode=returncode, stdout=stdout,
                        stderr=stderr, seconds=time.monotonic() - begin))


def run(command, expected=0):
    argv = startup + command
    begin = time.monotonic()
    proc = subprocess.run(argv, cwd=workspace, env=env, capture_output=True,
                          text=True, timeout=90)
    record(argv, proc.returncode, proc.stdout, proc.stderr, begin)
    assert proc.returncode == expected, results[-1]
    return proc.stdout.strip()


pid = int(run(["info", "server_pid"]))
ticks = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
build = None
shutdown = False
try:
    output = Path(run(["info", "output_base"]))
    sentinel = output / "protocol-sentinel"
    sentinel.write_text("preserve-engine-output")
    control = args.root / "outputs/shared_servers/v1/smoke/control/server"
    probe_argv = [str(args.probe), str(control / "server_info.rawproto"),
                  str(workspace), str(output)]
    begin = time.monotonic()
    probe = subprocess.run(probe_argv, cwd=workspace, env=env, capture_output=True,
                           text=True, timeout=90)
    record(probe_argv, probe.returncode, probe.stdout, probe.stderr, begin)
    assert probe.returncode == 0, results[-1]
    assert "8 raw Run requests rejected" in probe.stdout
    assert sentinel.read_text() == "preserve-engine-output"
    assert int(run(["info", "server_pid"])) == pid

    # Wait for actual action execution before interrupting the C++ client. The
    # server must accept its registration-bound cancellation and remain alive.
    argv = startup + ["build", "--repository_cache=" + str(args.repository_cache),
                      "//:blocked"]
    begin = time.monotonic()
    with (args.root / "build.stdout").open("w+") as stdout, \
            (args.root / "build.stderr").open("w+") as stderr:
        build = subprocess.Popen(argv, cwd=workspace, env=env, stdout=stdout,
                                 stderr=stderr, text=True, start_new_session=True)
        deadline = time.monotonic() + 90
        while not ready.exists():
            assert build.poll() is None, "Build exited before reaching action barrier"
            assert time.monotonic() < deadline, "Build never reached action barrier"
            time.sleep(0.05)
        build.send_signal(signal.SIGINT)
        returncode = build.wait(timeout=30)
        stdout.seek(0)
        stderr.seek(0)
        record(argv, returncode, stdout.read(), stderr.read(), begin)
        assert returncode == 8, results[-1]
    assert int(run(["info", "server_pid"])) == pid
    assert Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19] == ticks
    run(["clean", "--expunge"], 2)
    assert sentinel.read_text() == "preserve-engine-output"
    run(["shutdown"])
    shutdown = True
    reaped, status = os.waitpid(pid, 0)
    assert reaped == pid and status == 0, (reaped, status)
finally:
    if build is not None and build.poll() is None:
        build.send_signal(signal.SIGINT)
        build.wait(timeout=30)
    if not shutdown:
        run(["shutdown"])
        os.waitpid(pid, 0)

args.result.write_text(json.dumps(dict(
    binary_sha256=hashlib.sha256(args.bazel.read_bytes()).hexdigest(),
    pid=pid, start_ticks=ticks, reaped_status=status, engine_output=str(output),
    raw_run_rejections=8, action_barrier_reached=True, commands=results), indent=2) + "\n")
print(f"Verified {len(results)} processes, 8 raw rejections, live cancellation and reaped shutdown")
