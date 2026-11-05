"""Stress the native two-engine fork using pinned Datadog header bytes on Linux."""

import argparse
import hashlib
import io
import json
import os
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from bazel_stress_metrics import read_metrics

sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "tools/multiworktree"))
sys.dont_write_bytecode = True
from native_integration_test import Harness, read_spawns

REV = "85ad968bca856fa3d4e07b4d8e11961dabe6866f"
PKG = "pkg/security/ebpf/c/include"
FORK_SHA = "c5d3867ac6a1d0c22a9c43c51edc0192c8cb670ead0f51c789227275f4d2b643"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def manifest(files):
    return "".join(f"{sha(data)}  {path}\n" for path, data in sorted(files.items())).encode()


def read_headers(repo):
    data = subprocess.check_output(["git", "-C", str(repo), "archive", "--format=zip", REV, PKG])
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {p: archive.read(p) for p in archive.namelist() if p.endswith(".h")}


def wait_for(predicate, processes, logs):
    deadline = time.monotonic() + 90
    while not predicate():
        for proc, log in zip(processes, logs):
            if proc.poll() is not None:
                raise RuntimeError(f"Process exited {proc.returncode}: {log.read_text()[-3000:]}")
        if time.monotonic() >= deadline:
            raise TimeoutError("Execution barrier did not open")
        time.sleep(0.05)


def result(proc, log, cancelled=False):
    rc = proc.wait(timeout=90)
    if rc != (8 if cancelled else 0):
        raise RuntimeError(f"Unexpected exit {rc}: {log.read_text()[-3000:]}")
    return rc


def bep(path):
    metrics = read_metrics(path)
    return {k: metrics.get(k) for k in ("exit_code", "server_pid", "actions_executed",
                                         "actions_created", "runner_counts", "packages_loaded",
                                         "targets_configured", "post_build_skyframe_nodes")}


def await_follower(pid, directory, procs, logs):
    """Require the follower's Java stack inside the fork's wait loop before release."""
    jcmd = Path(f"/proc/{pid}/exe").resolve(strict=True).with_name("jcmd")
    dump = directory / "threads.txt"
    found = []

    def probe():
        subprocess.run([jcmd, str(pid), "Thread.dump_to_file", "-overwrite", str(dump)],
                       check=True, capture_output=True, timeout=10)
        for thread in dump.read_text().split("\n\n"):
            if ("SharedActionExecutionRegistry.acquire(" in thread
                    and "Object.wait" in thread and "RemoteSpawnCache." in thread):
                found.extend(line.strip() for line in thread.splitlines()
                             if any(s in line for s in ("Object.wait", "SharedActionExecutionRegistry.",
                                                        "RemoteSpawnCache.")))
                return True
        return False

    wait_for(probe, procs, logs)
    return found


def stress(h, base, rounds):
    def build(w, d, label):
        return h.build(w, d, label=label)

    def consume(w, d, label, proc, expected):
        result(proc, d / (label + ".log"))
        out = (h.root / w / "bazel-bin/out.txt").read_bytes()
        if out != expected:
            raise RuntimeError(f"Wrong output in {label}: {sha(out)} vs {sha(expected)}")
        spawns = read_spawns(d / (label + "-spawns.json"))
        return {"output_sha256": sha(out), "headers": len(out.splitlines()),
                "bep": bep(d / (label + "-bep.jsonl")), "spawns": [
            {"digest": s["digest"]["hash"], "runner": s["runner"], "cache_hit": s["cacheHit"]}
            for s in spawns if s["mnemonic"] == "Genrule"]}

    identities = h.root / "identity"
    identities.mkdir()
    pids = []
    for w in ("a", "b", "c"):
        p = h.start(w, "info", ["server_pid"], identities)
        rc = p.wait(timeout=90)
        log = (identities / (w + ".log")).read_text()
        if w == "c":
            if rc != 36 or "engine" not in log.lower():
                raise RuntimeError(f"Unexpected third-engine result {rc}: {log}")
            denial = log.strip().splitlines()[-1]
        else:
            if rc:
                raise RuntimeError(log)
            pids.append(int(next(x for x in log.splitlines() if x.isdigit())))
    if len(set(pids)) != 1:
        raise RuntimeError(f"Visited different daemons: {pids}")

    for w in ("a", "b"):
        for name, data in base.items():
            path = h.root / w / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    rows = []
    boundary = PKG + "/helpers/"
    boundary_file = h.root / "a" / boundary / "BUILD.bazel"
    edited = PKG + "/events_definition.h"
    for i in range(rounds):
        for mode in ("identical", "package-boundary", "cancel-waiter", "cancel-producer"):
            d = h.root / f"{i}-{mode}"
            d.mkdir()
            marker = shlex.quote(str(d))
            cmd = (
                "LC_ALL=C sha256sum $(SRCS) | LC_ALL=C sort -k2 > $@; "
                "v=$$(sha256sum $@ | cut -d' ' -f1); "
                f"echo $$v >> {marker}/starts; touch {marker}/ready-$$v; "
                f"while [ ! -f {marker}/release ]; do sleep 0.05; done"
            )
            for w in ("a", "b"):
                (h.root / w / "BUILD.bazel").write_text(
                    'genrule(name="proof", srcs=glob(["' + PKG +
                    '/**/*.h"]), outs=["out.txt"], cmd=' + json.dumps(cmd) + ')\n')
            a = base
            if mode == "package-boundary":
                boundary_file.write_text("# Newly introduced package boundary\n")
                a = {p: data for p, data in base.items() if not p.startswith(boundary)}
                a[edited] += f"\n/* round {i} */\n".encode()
                (h.root / "a" / edited).write_bytes(a[edited])
            else:
                boundary_file.unlink(missing_ok=True)
                (h.root / "a" / edited).write_bytes(base[edited])
            expected = {"a": manifest(a), "b": manifest(base)}
            logs = [d / (w + ".log") for w in ("a", "b")]
            procs = [build("a", d, "a")]
            row = {"round": i + 1, "case": mode}
            try:
                wait_for(lambda: (d / ("ready-" + sha(expected["a"]))).is_file(), procs, logs)
                procs.append(build("b", d, "b"))
                if mode == "package-boundary":
                    wait_for(lambda: (d / ("ready-" + sha(expected["b"]))).is_file(), procs, logs)
                else:
                    row["follower_while_owner_held"] = await_follower(pids[0], d, procs, logs)
                if any(p.poll() is not None for p in procs):
                    raise RuntimeError("A client finished before the gate opened")
                cancelled = {"cancel-producer": 0, "cancel-waiter": 1}.get(mode)
                if cancelled is not None:
                    procs[cancelled].send_signal(signal.SIGINT)
                    row["cancelled_client_exit"] = result(procs[cancelled], logs[cancelled], cancelled=True)
                    if cancelled == 0:
                        wait_for(lambda: len((d / "starts").read_text().splitlines()) >= 2,
                                 [procs[1]], [logs[1]])
                (d / "release").touch()
                for n, w in enumerate(("a", "b")):
                    if n != cancelled:
                        row[w] = consume(w, d, w, procs[n], expected[w])
                if cancelled is not None:
                    w = ("a", "b")[cancelled]
                    p = build(w, d, "late")
                    row["late"] = consume(w, d, "late", p, expected[w])
                    if len(row["late"]["spawns"]) != 1 or not row["late"]["spawns"][0]["cache_hit"]:
                        raise RuntimeError(f"Expected late requester to materialize cached output: {row}")
                starts = (d / "starts").read_text().splitlines()
                planned = [sha(expected["a"])]
                if mode in ("package-boundary", "cancel-producer"):
                    planned.append(sha(expected["b"]))
                if starts != planned:
                    raise RuntimeError(f"Inconsistent execution starts: {starts} vs {planned}")
                if cancelled is None:
                    a_spawn, b_spawn = (row[w]["spawns"][0] for w in ("a", "b"))
                    same_key = a_spawn["digest"] == b_spawn["digest"]
                    if same_key != (mode == "identical") or b_spawn["cache_hit"] != same_key or a_spawn["cache_hit"]:
                        raise RuntimeError(f"Unexpected action reuse: {row}")
                row["action_body_starts"] = len(starts)
                rows.append(row)
                print(f"pass {i+1} {mode} ({len(starts)} starts)", flush=True)
            finally:
                (d / "release").touch()
    return {"server_pids": pids, "third_engine_exit": 36, "third_engine_diagnostic": denial, "rows": rows}


def original_targets(h, repo, modules):
    from datadog_stress_target import SPAWN_TARGET, TARGET, apply_variant, expected_output, materialize

    git_modules = ("rules_go", "rules_rust", "rules_rust_prost", "gcc_toolchain", "rules_pkg")
    for name in git_modules:
        source = (modules / (name + "+")).resolve(strict=True)
        h.common.append("--override_module=" + name + "=" + str(source))
    for w in ("a", "b"):
        ws = h.root / w
        (ws / "proof.bzl").unlink()
        (ws / "MODULE.bazel").unlink()
        materialize(repo, ws, "base")
    h.env["DD_BAZEL_REMOTE_CACHE"] = "off"
    rows = []
    warnings = set()
    for idx, variants in enumerate((("base", "base"), ("source", "build"), ("build", "source"),
                                     ("base", "base"))):
        d = h.root / f"round-{idx}"
        d.mkdir()
        for w, variant in zip(("a", "b"), variants):
            for target in (TARGET, SPAWN_TARGET):
                apply_variant(repo, h.root / w, variant, target)
        procs = [h.start(w, "build", [TARGET, SPAWN_TARGET,
                        "--execution_log_json_file=" + str(d / (w + ".spawns")),
                        "--build_event_json_file=" + str(d / (w + ".bep"))], d) for w in ("a", "b")]
        for w, variant, proc in zip(("a", "b"), variants, procs):
            log = d / (w + ".log")
            result(proc, log)
            warnings.update(s for s in log.read_text().splitlines()
                            if s.startswith("WARNING: For repository"))
            outputs = {}
            for target, path in ((TARGET, "rtloader/constants.h"),
                                 (SPAWN_TARGET, "pkg/ebpf/include_headers_gen.go")):
                data = (h.root / w / "bazel-bin" / path).read_bytes()
                if data != expected_output(repo, variant, target):
                    raise RuntimeError(f"Wrong output for {w} {variant} {target}")
                outputs[target] = sha(data)
            spawns = read_spawns(d / (w + ".spawns"))
            rows.append({"wave": idx + 1, "workspace": w, "variant": variant,
                         "output_sha256": outputs, "bep": bep(d / (w + ".bep")), "spawns": [
                             {"mnemonic": s["mnemonic"], "digest": s["digest"]["hash"],
                              "runner": s["runner"], "cache_hit": s["cacheHit"]} for s in spawns]})
        print(f"pass original wave {idx + 1} {variants}", flush=True)
    if len({r["bep"]["server_pid"] for r in rows}) != 1 or not rows[0]["bep"]["server_pid"]:
        raise RuntimeError("Original builds did not use a common Bazel server")
    return {"workload": "two original Datadog targets built together in sparse pinned workspaces",
            "git_dependency_overrides": list(git_modules), "module_resolution_warnings": sorted(warnings),
            "rows": rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for arg in ("bazel", "repo", "repository-cache", "output"):
        p.add_argument("--" + arg, type=Path, required=True)
    p.add_argument("--install-base", type=Path)
    p.add_argument("--platforms-repo", type=Path,
                   help="Previously extracted @platforms for offline use")
    p.add_argument("--original-git-modules", type=Path,
                   help="Run original Agent targets instead, using these extracted Git overrides from this pin")
    p.add_argument("--rounds", type=int, default=3)
    args = p.parse_args()
    if sys.platform != "linux" or args.rounds < 1 or not __debug__:
        p.error("Linux, a positive round count, and enabled assertions are required")
    with tempfile.TemporaryDirectory(prefix="dd-native-stress-") as temp:
        cache = Path(temp) / "cache"
        cache.mkdir()
        # A private directory tree with immutable archive blobs hardlinked on the same Linux volume.
        shutil.copytree(args.repository_cache.resolve() / "content_addressable",
                        cache / "content_addressable", copy_function=os.link)
        h = Harness(args.bazel, Path(temp) / "run", cache)
        if h.binary_sha256 != FORK_SHA:
            raise RuntimeError(f"Unexpected fork executable: {h.binary_sha256}")
        if args.install_base:
            h.startup.append("--install_base=" + str(args.install_base.resolve()))
        if args.platforms_repo and not args.original_git_modules:
            platforms = Path(temp) / "platforms"
            shutil.copytree(args.platforms_repo.resolve(), platforms)
            for w in ("a", "b", "c"):
                with (h.root / w / "MODULE.bazel").open("a") as mod:
                    mod.write('bazel_dep(name="platforms", version="1.0.0")\n')
            h.common.append("--override_repository=platforms=" + str(platforms))
        try:
            if args.original_git_modules:
                report = original_targets(h, args.repo.resolve(), args.original_git_modules)
            else:
                h.common.append("--repository_disable_download")
                base = read_headers(args.repo)
                report = stress(h, base, args.rounds)
                report.update({"source_package": PKG, "input_headers": len(base),
                               "workload": "projected recursive glob + gated SHA-256 manifest genrule"})
        finally:
            h.close()
    report.update({"platform": platform.platform(), "datadog_revision": REV, "binary_sha256": FORK_SHA})
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
