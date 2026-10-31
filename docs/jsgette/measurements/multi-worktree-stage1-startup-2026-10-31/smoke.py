#!/usr/bin/env python3
"""Run the copied candidate in an isolated named control namespace on Linux."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--bazel", type=Path, required=True)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--result", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=False)
workspace = args.root / "workspace"
workspace.mkdir()
(workspace / "MODULE.bazel").touch()
libc = ctypes.CDLL(None, use_errno=True)
assert libc.prctl(36, 1, 0, 0, 0) == 0  # Adopt/reap the test daemon.
startup = [str(args.bazel), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
           "--output_user_root=" + str(args.root / "outputs"),
           "--experimental_shared_server=smoke", "--host_jvm_args=-Xmx512m"]
results = []
def run(command, expected=0):
    argv = startup + command
    begin = time.monotonic()
    proc = subprocess.run(argv, cwd=workspace, capture_output=True, text=True, timeout=60)
    results.append(dict(argv=argv, returncode=proc.returncode, stdout=proc.stdout,
                        stderr=proc.stderr, seconds=time.monotonic()-begin))
    assert proc.returncode == expected, results[-1]
    return proc.stdout.strip()
pid = int(run(["info", "server_pid"]))
ticks = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
assert run(["info", "workspace"]) == str(workspace)
output = run(["info", "output_base"])
assert "/engines/" in output
assert not (Path(output) / "server/server.pid.txt").exists()
run(["--host_jvm_args=-Dstartup.mismatch=true", "info", "server_pid"], 36)
assert int(run(["info", "server_pid"])) == pid
assert Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19] == ticks
run(["shutdown"])
reaped, status = os.waitpid(pid, 0)
assert reaped == pid and status == 0, (reaped, status)
args.result.write_text(json.dumps(dict(
    binary_sha256=hashlib.sha256(args.bazel.read_bytes()).hexdigest(),
    pid=pid, start_ticks=ticks, engine_output=output, reaped_status=status,
    commands=results), indent=2) + "\n")
print(f"Verified {len(results)} commands, stable PID/start ticks, and reaped shutdown")
