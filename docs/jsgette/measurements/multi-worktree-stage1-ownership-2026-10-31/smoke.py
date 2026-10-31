#!/usr/bin/env python3
"""Verify Linux process ownership and recovery using a separately copied binary."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--bazel", type=Path, required=True)
parser.add_argument("--previous-bazel", type=Path, required=True)
parser.add_argument("--repository-cache", type=Path, required=True)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--result", type=Path, required=True)
args = parser.parse_args()
args.root.mkdir(parents=True, exist_ok=False)
workspace = args.root / "workspace"
workspace.mkdir()
(workspace / "MODULE.bazel").touch()
action_code = ("import os; [print(os.readlink('/proc/self/fd/'+f)) "
               "for f in os.listdir('/proc/self/fd') "
               "if os.path.exists('/proc/self/fd/'+f)]")
(workspace / "BUILD.bazel").write_text(
    'genrule(name="fds", outs=["fds.txt"], local=True, cmd='
    + json.dumps("/usr/bin/python3 -c " + shlex.quote(action_code) + ' > "$@"') + ')\n')
assert ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) == 0
output_root = args.root / "outputs"
control = output_root / "shared_servers/v2/smoke/control"
startup = [str(args.bazel), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
           "--output_user_root=" + str(output_root), "--experimental_shared_server=smoke",
           "--host_jvm_args=-Xmx512m"]
env = dict(os.environ, SHLVL="1", OLDPWD=str(workspace))
commands = []


def run(command, expected=0, previous=False):
    argv = startup + command
    if previous:
        argv[0] = str(args.previous_bazel)
    start = time.monotonic()
    proc = subprocess.run(argv, cwd=workspace, env=env, capture_output=True,
                          text=True, timeout=90)
    commands.append(dict(argv=argv, returncode=proc.returncode, stdout=proc.stdout,
                         stderr=proc.stderr, seconds=time.monotonic() - start))
    assert proc.returncode == expected, commands[-1]
    return proc.stdout.strip()


def inspect_owner(pid, output):
    prefix = b"--experimental_shared_server_ownership_fds="
    values = [arg[len(prefix):] for arg in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
              if arg.startswith(prefix)]
    assert len(values) == 1
    fds = [int(value) for value in values[0].split(b",")]
    assert len(fds) == len(set(fds)) == 6
    sockets = {}
    for line in Path(f"/proc/{pid}/net/unix").read_text().splitlines()[1:]:
        fields = line.split()
        if len(fields) == 8:
            sockets[fields[6]] = fields[7]
    records = []
    for fd in fds:
        target = os.readlink(f"/proc/{pid}/fd/{fd}")
        info = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/fdinfo/{fd}").read_text().splitlines())
        flags = int(info["flags"].strip(), 8)
        assert flags & os.O_CLOEXEC, (fd, flags)
        records.append(dict(fd=fd, target=target, flags=oct(flags)))
    for offset, path in [(0, control), (3, output)]:
        stat = path.stat()
        keys = ["path:" + hashlib.md5(os.fsencode(path)).hexdigest(),
                f"object:{stat.st_dev}:{stat.st_ino}"]
        assert records[offset + 1]["target"] == str(path)
        for index, key in [(offset, keys[0]), (offset + 2, keys[1])]:
            inode = records[index]["target"].removeprefix("socket:[").removesuffix("]")
            assert sockets[inode] == "@bazel-shared-owner-v1:" + key
    return records


previous_pid = int(run(["info", "server_pid"], previous=True))
previous_ticks = Path(f"/proc/{previous_pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
previous_control = output_root / "shared_servers/v1/smoke/control/server"
previous_metadata = {name: (previous_control / name).read_bytes() for name in
                     ["startup_profile", "server.pid.txt", "request_cookie", "response_cookie", "server_info.rawproto"]}
previous_shutdown = False
current_pid = None
try:
    pid = int(run(["info", "server_pid"]))
    current_pid = pid
    assert pid != previous_pid
    ticks = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    output = Path(run(["info", "output_base"]))
    descriptors = inspect_owner(pid, output)
    run(["build", "--repository_cache=" + str(args.repository_cache), "//:fds"])
    action_descriptors = (workspace / "bazel-bin/fds.txt").read_text().splitlines()
    assert not {d["target"] for d in descriptors}.intersection(action_descriptors)

    saved = control.with_name("control.saved")
    replacement = control.with_name("control.replacement")
    os.kill(pid, signal.SIGSTOP)
    control.rename(saved)
    try:
        run(["info", "server_pid"], 36)
        assert "Shared server ownership is held" in commands[-1]["stderr"]
        run(["shutdown"], 36)
        assert "Shared server ownership is held" in commands[-1]["stderr"]
        replacement_entries = sorted(str(p.relative_to(control)) for p in control.rglob("*"))
        assert "server/startup_profile" not in replacement_entries
        assert "server/server.pid.txt" not in replacement_entries
    finally:
        if control.exists():
            control.rename(replacement)
        saved.rename(control)
        os.kill(pid, signal.SIGCONT)
    assert int(run(["info", "server_pid"])) == pid
    assert Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19] == ticks

    other = output_root / "shared_servers/v2/other"
    (other / "engines").mkdir(parents=True)
    (other / "engines" / output.name).symlink_to(output, target_is_directory=True)
    run(["--experimental_shared_server=other", "info", "server_pid"], 36)
    assert "Shared server ownership is held" in commands[-1]["stderr"]
    assert not (other / "control/server/startup_profile").exists()
    os.kill(pid, signal.SIGKILL)
    reaped, crash_status = os.waitpid(pid, 0)
    current_pid = None
    assert reaped == pid and os.WIFSIGNALED(crash_status)
    assert os.WTERMSIG(crash_status) == signal.SIGKILL
    current_pid = int(run(["info", "server_pid"]))
    assert current_pid != pid
    fresh_pid = current_pid
    fresh_descriptors = inspect_owner(fresh_pid, output)
    run(["shutdown"])
    reaped, shutdown_status = os.waitpid(fresh_pid, 0)
    current_pid = None
    assert reaped == fresh_pid and shutdown_status == 0
    assert int(run(["info", "server_pid"], previous=True)) == previous_pid
    assert Path(f"/proc/{previous_pid}/stat").read_text().rsplit(")", 1)[1].split()[19] == previous_ticks
    assert all((previous_control / name).read_bytes() == value for name, value in previous_metadata.items())
    run(["shutdown"], previous=True)
    reaped, previous_status = os.waitpid(previous_pid, 0)
    previous_shutdown = True
    assert reaped == previous_pid and previous_status == 0
finally:
    if current_pid is not None:
        run(["shutdown"])
        os.waitpid(current_pid, 0)
    if not previous_shutdown:
        run(["shutdown"], previous=True)
        os.waitpid(previous_pid, 0)

args.result.write_text(json.dumps(dict(
    binary_sha256=hashlib.sha256(args.bazel.read_bytes()).hexdigest(),
    previous_binary_sha256=hashlib.sha256(args.previous_bazel.read_bytes()).hexdigest(),
    previous_pid=previous_pid, previous_start_ticks=previous_ticks,
    previous_reaped_status=previous_status, previous_metadata_unchanged=True,
    pid=pid, start_ticks=ticks, descriptors=descriptors,
    action_descriptors=action_descriptors, replacement_entries=replacement_entries,
    crash_status=crash_status, fresh_pid=fresh_pid, fresh_descriptors=fresh_descriptors,
    shutdown_status=shutdown_status, commands=commands), indent=2) + "\n")
print(f"Verified {len(commands)} commands, six ownership descriptors, action exclusion and reaped recovery")
