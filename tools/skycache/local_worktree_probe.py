#!/usr/bin/env python3
"""Exercise a local SkyCache with two real Git worktrees and independent Bazel servers.

Usage: python3 local_worktree_probe.py --bazel /absolute/bazel --root /new/run/directory
The run directory is preserved with command logs, BEP data and a JSON protocol.
This is a correctness probe; its small generated workload is not a speed benchmark.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import threading
import time
from storage_preflight import check_storage


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--lock-file", type=Path)
    args = parser.parse_args()
    binary = args.bazel.resolve(strict=True)
    root = args.root.resolve()
    initial_storage = check_storage([root])
    root.mkdir(parents=True, exist_ok=False)
    lock = None
    if args.lock_file:
        import fcntl
        lock = args.lock_file.open("a")
        fcntl.flock(lock, fcntl.LOCK_EX)
    environment = dict(os.environ)
    seed = root / "seed"
    seed.mkdir()
    command_log = []

    def git(*arguments):
        subprocess.run(["git", "-C", str(seed), *arguments], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    git("init", "--quiet")
    (seed / "MODULE.bazel").write_text('module(name="skycache_worktrees_probe")\n')
    (seed / "defs.bzl").write_text(
        'SUFFIX = ""\n'
        'def copy_rule(name, src):\n'
        '    native.genrule(name=name, srcs=[src], outs=[name+".txt"], '
        'cmd="cat $(SRCS) > $@; printf \'" + SUFFIX + "\' >> $@", '
        'visibility=["//visibility:public"])\n'
    )
    (seed / "BUILD").write_text('exports_files(["defs.bzl"])\n')
    count = 16
    for i in range(count):
        package = seed / f"lib{i:02d}"
        package.mkdir()
        (package / "input").write_text(f"base:{i:02d}\n")
        (package / "BUILD").write_text(
            'load("//:defs.bzl", "copy_rule")\n'
            'copy_rule(name="one", src="input")\n'
            'copy_rule(name="two", src=":one")\n'
            'copy_rule(name="three", src=":two")\n'
        )
    (seed / "app").mkdir()
    sources = ",".join(f'"//lib{i:02d}:three"' for i in range(count))
    (seed / "app/BUILD").write_text(
        f'genrule(name="all", srcs=[{sources}], outs=["all.txt"], '
        'cmd="cat $(SRCS) > $@")\n'
    )
    git("add", ".")
    git("-c", "user.name=SkyCache Probe", "-c", "user.email=skycache-probe@example.invalid",
        "commit", "--quiet", "-m", "SkyCache worktree correctness fixture")
    for name in ("a", "b"):
        git("worktree", "add", "--detach", str(root / name), "HEAD")

    def startup(name):
        return [str(binary), "--nosystem_rc", "--nohome_rc", "--noworkspace_rc",
                "--host_jvm_args=-Xmx2g", "--output_base=" + str(root / ("out-" + name))]

    def shutdown(name):
        subprocess.run(startup(name) + ["shutdown"], cwd=root / name, env=environment,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)

    def expected(name):
        workspace = root / name
        suffix = re.search(r'^SUFFIX = "(.*)"$', (workspace / "defs.bzl").read_text(),
                          re.MULTILINE).group(1)
        suffix = suffix.encode().decode("unicode_escape").encode()
        return b"".join((workspace / f"lib{i:02d}/input").read_bytes() + suffix * 3
                        for i in range(count))

    def build(name, phase):
        prefix = root / (phase + "-" + name)
        arguments = startup(name) + [
            "build", "//app:all", "--jobs=2", "--local_resources=memory=2048",
            "--experimental_local_skycache=" + str(root / "cache"),
            "--experimental_active_directories=app", "--lockfile_mode=off",
            "--experimental_strict_repo_env",
            "--color=no", "--curses=no",
            "--build_event_json_file=" + str(prefix) + ".bep.json",
        ]
        command_log.append({"phase": phase, "worktree": name, "argv": arguments})
        started = time.monotonic()
        client_environment = dict(
            environment, PWD=str(root / name), OLDPWD=str(seed),
            SKYCACHE_UNRELATED_SESSION=f"{name}-{phase}",
        )
        with Path(str(prefix) + ".log").open("w") as log:
            result = subprocess.run(arguments, cwd=root / name, env=client_environment,
                                    stdout=log, stderr=subprocess.STDOUT)
        log_text = Path(str(prefix) + ".log").read_text()
        assert result.returncode == 0, f"{phase}/{name} failed: see {prefix}.log"
        assert "No such file or directory" not in log_text, f"missing input in {prefix}.log"
        output = (root / name / "bazel-bin/app/all.txt").read_bytes()
        assert output == expected(name), f"incorrect output in {phase}/{name}"
        stats = json.loads((root / ("out-" + name) / "local-skycache.json").read_text())
        hits = re.findall(r"CONFIGURED_TARGET: (\d+)/(\d+)", log_text)
        return {"worktree": name, "exit_code": result.returncode,
                "seconds": time.monotonic() - started,
                "output_sha256": hashlib.sha256(output).hexdigest(),
                "analysis_hits": sum(int(hit) for hit, _ in hits), "skycache": stats}

    protocol = {
        "binary": str(binary),
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "purpose": "correctness; two concurrent servers, not a speed benchmark",
        "environment": "strict repository environment; natural per-worktree PWD and distinct session markers",
        "initial_storage": initial_storage,
        "phases": [],
    }
    protocol_path = root / "results.json"

    def pair(phase, require_hits=False):
        storage = check_storage([root])
        finished = threading.Event()
        peak_rss = [0]

        def sample():
            while not finished.wait(0.2):
                total = 0
                for name in ("a", "b"):
                    try:
                        pid = (root / ("out-" + name) / "server/server.pid.txt").read_text().strip()
                        status = Path(f"/proc/{pid}/status").read_text()
                        total += int(re.search(r"^VmRSS:\s+(\d+)", status, re.MULTILINE).group(1))
                    except (FileNotFoundError, AttributeError, ProcessLookupError):
                        pass
                peak_rss[0] = max(peak_rss[0], total)

        sampler = threading.Thread(target=sample, daemon=True)
        sampler.start()
        started = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(build, name, phase) for name in ("a", "b")]
                results = [future.result() for future in futures]
        finally:
            finished.set()
            sampler.join()
        record = {"phase": phase, "seconds": time.monotonic() - started,
                  "storage_preflight": storage,
                  "peak_combined_server_rss_kib": peak_rss[0], "builds": results}
        entries = list((root / "cache/objects").rglob("*.entry"))
        record["cache_entry_files"] = len(entries)
        record["cache_entry_bytes"] = sum(path.stat().st_size for path in entries)
        packs = list((root / "cache/objects").rglob("data.pack"))
        indexes = list((root / "cache/objects").rglob("index"))
        record["cache_pack_files"] = len(packs)
        record["cache_index_files"] = len(indexes)
        record["cache_packed_bytes"] = sum(path.stat().st_size for path in packs + indexes)
        record["cache_record_count"] = len(entries)
        for path in indexes:
            with path.open("rb") as stream:
                magic, pack_count = struct.unpack(">ii", stream.read(8))
            assert magic == 0x534B4901
            record["cache_record_count"] += pack_count
        protocol["phases"].append(record)
        protocol_path.write_text(json.dumps(protocol, indent=2) + "\n")
        print(json.dumps(record), flush=True)
        if require_hits:
            assert all(row["analysis_hits"] > 0 for row in results), results

    try:
        pair("concurrent-empty-cache")
        assert all(row["skycache"]["published"] for row in protocol["phases"][-1]["builds"])
        pair("warm")
        for name in ("a", "b"):
            (root / name / "lib00/input").write_text("feature-" + name + "\n")
        pair("independent-source-edits")
        for name in ("a", "b"):
            (root / name / "defs.bzl").write_text(
                (root / name / "defs.bzl").read_text().replace('SUFFIX = ""', f'SUFFIX = "rule-{name}"')
            )
        pair("independent-starlark-edits")
        for name in ("a", "b"):
            subprocess.run(["git", "-C", str(root / name), "restore", "lib00/input", "defs.bzl"],
                           check=True)
            shutdown(name)
        pair("reverted-restarted-readers", require_hits=True)
        for name in ("a", "b"):
            shutdown(name)
        damaged = 0
        preserved = 0
        for path in (root / "cache/objects").rglob("*.entry"):
            data = path.read_bytes()
            if len(data) < 44:
                continue
            _, key_length, _ = struct.unpack_from(">iii", data)
            value = data[44 + key_length:]
            if value and value[0] == 4:
                preserved += 1
            else:
                path.write_bytes(b"deliberately corrupt shared chunk")
                damaged += 1
        for path in (root / "cache/objects").rglob("data.pack"):
            index = (path.parent / "index").read_bytes()
            magic, pack_count, data_size = struct.unpack_from(">iiq", index)
            assert magic == 0x534B4901 and len(index) == 16 + pack_count * 48 + 32
            assert path.stat().st_size == data_size
            with path.open("r+b") as stream:
                for number in range(pack_count):
                    offset, length = struct.unpack_from(">qq", index, 16 + number * 48 + 32)
                    assert 0 <= offset <= data_size - length
                    stream.seek(offset)
                    header = stream.read(44)
                    magic, key_length, value_length = struct.unpack_from(">iii", header)
                    assert magic == 0x534B5001 and length == 44 + key_length + value_length
                    stream.seek(offset + 44 + key_length)
                    first_value_byte = stream.read(1) if value_length else b""
                    if first_value_byte == b"\x04":
                        preserved += 1
                    else:
                        # Preserve the index and record lengths so roots are found and nested
                        # chunk checksums fail during deserialization, exercising fallback.
                        stream.seek(offset + 12)
                        stream.write(bytes([header[12] ^ 128]))
                        damaged += 1
                stream.flush()
                os.fsync(stream.fileno())
        protocol["corruption"] = {"damaged_chunks": damaged, "preserved_possible_roots": preserved}
        assert damaged and preserved
        pair("corrupt-chunks-fallback")
        protocol["passed"] = True
    finally:
        for name in ("a", "b"):
            shutdown(name)
        protocol["commands"] = command_log
        protocol_path.write_text(json.dumps(protocol, indent=2) + "\n")
        if lock:
            lock.close()


if __name__ == "__main__":
    main()
