"""One guarded N1 diagnostic arm; never a performance-gate runner.

Run off/on separately, only after M0 completes. The outer host guard owns scope
termination; this script owns only a new m0-diagnostics-v1/{off,on} directory.
No installation, existing-pool reuse, cache deletion or global setting changes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from urllib.parse import unquote, urlparse

import baseline
import fixture
import m1_resources as resources


RUNTIME = Path("/mnt/shared-evaluation-20261107")
BINARY_SHA256 = "11547ce20ac9edcaa14c48206ac14618012d898ef24d4bbf6e0ac229b44b1e25"
TEMPLATE_SHA256 = "caa43c2067b0c4e7dda07386b9ed2ff78fc744017f3a62cd5fe60bb67fce4cc6"
COMMAND_SHA256 = "26c4a86c53af7eea177037e1c575f7ee21ccdb2769dd6ee6efb531e2056fc105"
REPORT_SHA256 = "a66df4219e9081e86a4556a9f9e22837a74a4dd1146df4e5da5becbc8fa91364"
HELPER_HASHES = {
    "baseline.py": "f418157ada895e181f75c43cd4c7c05e69077598013b9097420f650fa6c6f906",
    "fixture.py": "2de503bc8d4b5e7e1817b5a73e9bb78c1d7bacc56f5ae19847b8f686ebaff78a",
}


def pinned_json(data, digest):
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Pinned evidence hash differs")
    return json.loads(data)


def load_template(archive):
    if fixture.sha256(archive) != TEMPLATE_SHA256:
        raise ValueError("Expected the archived v7b i1-off-r template")
    with tarfile.open(archive) as stream:
        command = pinned_json(stream.extractfile("measured/c00/command.json").read(), COMMAND_SHA256)
        report = pinned_json(stream.extractfile("report.json").read(), REPORT_SHA256)
    return command, report


def require_new_root(path, arm):
    expected = RUNTIME / "m0-diagnostics-v1" / arm
    if path.absolute() != expected or path.resolve() != expected:
        raise ValueError("Run root must be the exact task-owned m0-diagnostics-v1/" + arm)
    if path.exists():
        raise FileExistsError("Diagnostic run roots are never reused")


def check_sources(manifest):
    for row in manifest["worktrees"]:
        workspace = Path(row["workspace"])
        baseline.require_fixture_tree(workspace, manifest["private_tree"])
        if fixture.source_state(workspace) != row["source_state"]:
            raise ValueError("Fixture source-state changed")
        for name, field in (("root.go", "source_sha256"), ("BUILD.bazel", "build_sha256")):
            if fixture.sha256(workspace / fixture.COMMAND / name) != row[field]:
                raise ValueError("Fixture source input changed")


def check_oracle(oracle, manifest):
    if (oracle.get("passed") is not True or oracle.get("mode") != "vanilla"
            or oracle.get("analysis_only") is not False
            or oracle.get("extra_bazel_arguments") != []):
        raise ValueError("Expected a successful execution-enabled vanilla oracle")
    other = oracle["fixture"]
    for key in ("source_revision", "projection_archive_sha256", "private_tree", "target", "clients", "scenario"):
        if other.get(key) != manifest.get(key):
            raise ValueError("Oracle fixture differs: " + key)
    row, reference = manifest["worktrees"][0], other["worktrees"][0]
    results = oracle["measured"]["results"]
    if (len(results) != 1 or fixture.oracle_inputs(row) != fixture.oracle_inputs(reference)
            or fixture.oracle_key(results[0]) != fixture.oracle_key(row)
            or results[0]["exit_code"] != 0 or not results[0].get("behavior", {}).get("passed")):
        raise ValueError("Oracle result/input mismatch")
    return results[0]["output"]["sha256"]


def command_argv(template, binary, root, arm, phase, profile):
    directory = root / phase
    replacements = {
        "--output_base=": str(root / "output"),
        "--host_jvm_args=-Djava.io.tmpdir=": str(root / "java"),
        "--build_event_json_file=": str(directory / "bep.jsonl"),
        "--profile=": str(directory / "profile.json.gz"),
        "--experimental_local_skycache=": str(root / "skycache") if arm == "on" else "",
        "--repo_env=TMP=": str(root / "tmp"),
        "--repo_env=TMPDIR=": str(root / "tmp"),
    }
    argv = [str(binary)]
    for item in template["argv"][1:]:
        prefix = next((key for key in replacements if item.startswith(key)), None)
        argv.append(prefix + replacements[prefix] if prefix else item)
    if arm == "on":
        argv.append("--experimental_analysis_cache_idle_retention=3600s")
    if profile:
        argv.append("--experimental_command_profile=cpu")
    return argv


def startup(argv):
    return argv[:argv.index("build")]


def scope_contract():
    group = baseline.cgroup_path()
    if group is None or not group.name.startswith("shared-eval-guard-") or not group.name.endswith(".scope"):
        raise ValueError("Run under a fresh existing host-guard scope")
    limits = {name: (group / name).read_text().strip()
              for name in ("cpu.max", "memory.max", "memory.swap.max")}
    if limits != {"cpu.max": "600000 100000", "memory.max": str(10240 * 2**20), "memory.swap.max": "0"}:
        raise ValueError("Diagnostic scope must retain the frozen six-CPU/10GiB/no-swap limits")
    return group, limits


def scope_snapshot(group, before=None):
    return resources.read_cgroup_snapshot(
        expected_cgroup="/" + str(group.relative_to("/sys/fs/cgroup")),
        expected_identity=before["directory_identity"] if before else None)


class Window:
    """N1 observer; endpoints and every discovery/sample read are CPU-timed."""

    def __init__(self, base, group, interval=0.1):
        self.base, self.group, self.interval = base, group, interval
        self.pid_file = base / "server/server.pid.txt"
        self.hz = os.sysconf("SC_CLK_TCK")
        self.birth_boundary = int(time.clock_gettime(time.CLOCK_BOOTTIME) * self.hz)
        self.reader = resources.ResourceSampler(
            {"c0": str(base)},
            expected_cgroup="/" + str(group.relative_to("/sys/fs/cgroup")),
            clock_ticks_per_second=self.hz)
        self.rows, self.errors = [], []
        self.stop = threading.Event()
        self.sample()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def sample(self):
        started, cpu = time.monotonic_ns(), time.thread_time_ns()
        try:
            identity = resources.read_process_identity(int(self.pid_file.read_text().strip()))
        except (OSError, ValueError):
            identity = None
        mappings = ({"c0": resources.EngineMapping(str(self.base), self.pid_file, identity)}
                    if identity is not None else {})
        row = self.reader.sample(mappings)
        row["observer_thread_cpu_ns"] = time.thread_time_ns() - cpu
        row["observer_duration_ns"] = time.monotonic_ns() - started
        self.rows.append(row)

    def loop(self):
        while not self.stop.wait(self.interval):
            try:
                self.sample()
            except Exception as error:
                self.errors.append(type(error).__name__ + ": " + str(error))
                return

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop.set()
        self.thread.join()
        self.sample()
        first, last = self.rows[0], self.rows[-1]
        complete = [r for r in self.rows if r["eligible"]]
        started = next((i for i, row in enumerate(self.rows) if row["eligible"]), len(self.rows))
        valid = bool(complete and last["eligible"] and not self.errors
                     and all(row["eligible"] for row in self.rows[started:])
                     and all(not row["processes"] and row["cgroup"]["eligible"]
                             for row in self.rows[:started]))
        engine_cpu = None
        if valid:
            identity = last["processes"][0]
            if first["eligible"]:
                valid = all(first["processes"][0][key] == identity[key] for key in ("pid", "start_ticks"))
                ticks = last["cpu_ticks"] - first["cpu_ticks"]
            else:
                valid = identity["start_ticks"] >= self.birth_boundary
                ticks = last["cpu_ticks"]
            valid = valid and ticks >= 0
            if valid:
                engine_cpu = ticks / self.hz
        before, after = first["cgroup"], last["cgroup"]
        scope_cpu = None
        if before["eligible"] and after["eligible"]:
            delta = after["cpu.stat"]["usage_usec"] - before["cpu.stat"]["usage_usec"]
            if delta >= 0:
                scope_cpu = delta / 1e6
        return {"accounting_passed": valid and scope_cpu is not None,
                "engine_cpu_seconds": engine_cpu, "scope_cpu_seconds": scope_cpu,
                "engine_identity": ({key: last["processes"][0][key] for key in ("pid", "start_ticks")}
                                    if valid else None),
                "engine_sampled_peak_pss_bytes": max((r["pss_bytes"] for r in complete), default=None),
                "observer_thread_cpu_seconds": sum(r["observer_thread_cpu_ns"] for r in self.rows) / 1e9,
                "sample_interval_after_reads_seconds": self.interval,
                "new_process_birth_boundary_ticks": self.birth_boundary,
                "complete_sample_count": len(complete), "errors": self.errors, "samples": self.rows,
                "notes": ["Build-only diagnostic window; correctness verification follows separately.",
                          "Raw cgroup snapshots include preparation residency and cumulative reclaim/PSI/throttling counters.",
                          "Observer CPU includes mapping discovery and initial/final reads; do not sum nested helper timings."]}


def run_command(argv, workspace, env, directory, timeout):
    import resource

    directory.mkdir()
    fixture.save_new(directory / "command.json", {"argv": argv, "cwd": str(workspace)})
    before = resource.getrusage(resource.RUSAGE_CHILDREN)
    begin, cpu = time.monotonic(), time.process_time()
    with (directory / "stdout.log").open("xb") as out, (directory / "stderr.log").open("xb") as err:
        process = subprocess.Popen(argv, cwd=workspace, env=env, stdout=out, stderr=err)
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            code = process.returncode
    after = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = {"exit_code": code, "timed_out": timed_out, "client_pid": process.pid,
              "wall_seconds": time.monotonic() - begin, "harness_process_cpu_seconds": time.process_time() - cpu,
              "waited_children_user_cpu_seconds": after.ru_utime - before.ru_utime,
              "waited_children_system_cpu_seconds": after.ru_stime - before.ru_stime,
              "notes": ["Waited-child rusage includes descendants reaped by that child; not an additive role partition."]}
    fixture.save_new(directory / "command-result.json", result)
    return result


def verify_output(directory, expected_digest):
    metrics = baseline.bep_metrics(directory / "bep.jsonl")
    if not metrics["finished"] or metrics.get("exit_code", {}).get("name") != "SUCCESS":
        raise RuntimeError("BEP build did not finish successfully")
    paths = set()
    for entry in metrics["files"]:
        uri = urlparse(entry.get("uri", ""))
        if uri.scheme == "file":
            path = Path(unquote(uri.path))
            if path.name == "normalize_cmd" and path.is_file():
                paths.add(path)
    if len(paths) != 1:
        raise ValueError("Expected one normalizer output")
    executable = paths.pop()
    digest = fixture.sha256(executable)
    if digest != expected_digest:
        raise ValueError("Output digest differs from the independent vanilla oracle")
    behavior = fixture.verify_program(executable, "base", directory / "behavior")
    return {"output": {"path": str(executable), "sha256": digest}, "behavior": behavior,
            "bep": {k: v for k, v in metrics.items() if k != "files"}}


def require_stopped(identity):
    if identity is None:
        return
    current = resources.read_process_identity(identity["pid"])
    if current is not None and current.start_ticks == identity["start_ticks"]:
        raise RuntimeError("Diagnostic daemon remains alive after shutdown")


def require_lifecycle(phases, result):
    identity = result["resources"]["engine_identity"]
    if result["phase"] == "restart" and identity == phases[0]["resources"]["engine_identity"]:
        raise RuntimeError("Restarted measurement reused the preparation JVM")
    if result["phase"] == "warm" and identity != phases[-1]["resources"]["engine_identity"]:
        raise RuntimeError("Warm measurement did not retain the restarted JVM")


def file_token(path):
    try:
        stat = path.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
    except FileNotFoundError:
        return None


def copy_jfr(source, destination, before):
    after = file_token(source)
    if after is None or after[2] == 0 or after == before:
        return {"available": False, "gap": "Profiler output absent, empty or unchanged"}
    with source.open("rb") as inp, destination.open("xb") as out:
        shutil.copyfileobj(inp, out)
    return {"available": True, "path": str(destination), "bytes": after[2],
            "sha256": fixture.sha256(destination), "source_token_before": before,
            "source_token_after": after}


def build_phase(args, template, manifest, phase, group, env, digest):
    root, base = args.run_root, args.run_root / "output"
    profile = phase != "preparation"
    argv = command_argv(template, args.binary, root, args.arm, phase, profile)
    before_jfr = file_token(base / "cpu.jfr")
    window = Window(base, group)
    window.start()
    result = {"phase": phase, "passed": False}
    try:
        result["command"] = run_command(argv, template["cwd"], env, root / phase, args.timeout)
    finally:
        result["resources"] = window.finish()
        fixture.save_new(root / (phase + "-resources.json"), result["resources"])
        result["resources"] = {key: value for key, value in result["resources"].items() if key != "samples"}
        result["resources_path"] = str(root / (phase + "-resources.json"))
    try:
        if result["command"]["exit_code"] or result["command"]["timed_out"]:
            raise RuntimeError("Build command failed: " + phase)
        if not result["resources"]["accounting_passed"]:
            raise RuntimeError("Build accounting failed: " + phase)
        if profile:
            result["cpu_profile"] = copy_jfr(base / "cpu.jfr", root / phase / "cpu.jfr", before_jfr)
        for sidecar in base.glob("*skycache*.json"):
            with sidecar.open("rb") as inp, (root / phase / sidecar.name).open("xb") as out:
                shutil.copyfileobj(inp, out)
        result["correctness"] = verify_output(root / phase, digest)
        if int(result["correctness"]["bep"]["started"]["serverPid"]) != result["resources"]["engine_identity"]["pid"]:
            raise RuntimeError("BEP server PID differs from the observed engine")
        check_sources(manifest)
        result["passed"] = True
    except Exception as error:
        result["error"] = type(error).__name__ + ": " + str(error)
        raise
    finally:
        fixture.save_new(root / phase / "result.json", result)
    return result


def decode_profiles(args, phases, env, workspace):
    tool = args.jfr_tool
    if tool is None:
        try:
            pid = int((args.run_root / "output/server/server.pid.txt").read_text())
            tool = Path(f"/proc/{pid}/exe").resolve().with_name("jfr")
        except (OSError, ValueError):
            tool = None
    if tool is None or not tool.is_file():
        return {"available": False, "gap": "No existing JFR decoder"}
    result = {"tool": str(tool), "tool_sha256": fixture.sha256(tool), "phases": {}}
    decoder_env = dict(env, JAVA_TOOL_OPTIONS="-Xmx256m -XX:ActiveProcessorCount=2 -Djava.io.tmpdir="
                       + str(args.run_root / "java"))
    for phase in phases:
        profile = phase.get("cpu_profile", {})
        if not profile.get("available"):
            continue
        name = phase["phase"]
        summary = run_command([str(tool), "summary", profile["path"]], workspace, decoder_env,
                              args.run_root / (name + "-jfr-summary"), 60)
        decoded = run_command([str(tool), "print", "--json", "--events",
                               "jdk.ExecutionSample,jdk.NativeMethodSample", profile["path"]],
                              workspace, decoder_env, args.run_root / (name + "-jfr-decoded"), 60)
        usable = False
        if summary["exit_code"] == decoded["exit_code"] == 0:
            try:
                rows = json.loads((args.run_root / (name + "-jfr-decoded/stdout.log")).read_text())
                usable = bool(rows["recording"]["events"])
            except (OSError, ValueError, KeyError):
                pass
        result["phases"][name] = {"summary": summary, "decode": decoded, "cpu_samples_available": usable}
    result["available"] = all(result["phases"].get(name, {}).get("cpu_samples_available")
                              for name in ("restart", "warm"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("run-root", "template-archive", "fixture-manifest", "oracle-report", "binary"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--arm", choices=("off", "on"), required=True)
    parser.add_argument("--jfr-tool", type=Path)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    if sys.platform != "linux" or args.timeout <= 0:
        parser.error("Linux and a positive timeout are required")
    require_new_root(args.run_root, args.arm)
    template, reference = load_template(args.template_archive)
    args.binary = args.binary.resolve()
    if fixture.sha256(args.binary) != BINARY_SHA256:
        raise ValueError("Pinned safe-subset binary hash differs")
    for module in (baseline, fixture):
        path = Path(module.__file__)
        if fixture.sha256(path) != HELPER_HASHES[path.name]:
            raise ValueError("Frozen helper hash differs: " + path.name)
    manifest = json.loads(args.fixture_manifest.read_text())
    if manifest != reference["fixture"] or template["cwd"] != manifest["worktrees"][0]["workspace"]:
        raise ValueError("Expected the exact archived N1 identical fixture")
    if fixture.sha256(args.oracle_report) != reference["oracle_report"]["sha256"]:
        raise ValueError("Pinned vanilla oracle report hash differs")
    oracle = check_oracle(json.loads(args.oracle_report.read_text()), manifest)
    check_sources(manifest)
    group, limits = scope_contract()
    prepared_paths = {}
    for prefix in ("--output_user_root=", "--repository_cache=", "--disk_cache="):
        path = Path(next(a[len(prefix):] for a in template["argv"] if a.startswith(prefix)))
        if not path.resolve().is_relative_to(RUNTIME) or not path.is_dir():
            raise ValueError("Prepared installation/caches must exist on task storage")
        prepared_paths[prefix[2:-1]] = str(path)
    args.run_root.mkdir(parents=True)
    for name in ("tmp", "java", "skycache"):
        (args.run_root / name).mkdir()
    env = fixture.clean_env()
    env.update(TMP=str(args.run_root / "tmp"), TMPDIR=str(args.run_root / "tmp"), USE_BAZEL_VERSION="9.1.0")
    # verify_program uses clean_env() itself; keep its child temp policy task-local too.
    os.environ.update(TMP=env["TMP"], TMPDIR=env["TMPDIR"])
    start = startup(command_argv(template, args.binary, args.run_root, args.arm, "unused", False))
    report = {"schema_version": 1, "diagnostic_only": True, "arm": args.arm, "passed": False,
              "binary_sha256": BINARY_SHA256, "template_sha256": TEMPLATE_SHA256,
              "fixture_manifest_sha256": fixture.sha256(args.fixture_manifest),
              "oracle_report_sha256": fixture.sha256(args.oracle_report), "fixture": manifest,
              "scope": str(group), "scope_limits": limits, "phases": [], "diagnostic_gaps": [],
              "cache_policy": {**prepared_paths, "skycache": str(args.run_root / "skycache"),
                               "skycache_initially_empty": True},
              "temporary_policy": {"tmp": env["TMP"], "java": str(args.run_root / "java")},
              "cache_before": {key: baseline.inventory(Path(value)) for key, value in prepared_paths.items()
                               if key != "output_user_root"},
              "harness_sha256": {Path(m.__file__).name: fixture.sha256(Path(m.__file__))
                                 for m in (baseline, fixture, resources)},
              "script_sha256": fixture.sha256(Path(__file__)),
              "environment_digest": hashlib.sha256(json.dumps(sorted(env.items())).encode()).hexdigest(),
              "environment_values_recorded": False}
    fixture.save_new(args.run_root / "provenance.json", report)
    try:
        for phase in ("preparation", "restart", "warm"):
            result = build_phase(args, template, manifest, phase, group, env, oracle)
            require_lifecycle(report["phases"], result)
            report["phases"].append(result)
            if phase == "preparation":
                stopped = run_command(start + ["shutdown"], template["cwd"], env,
                                      args.run_root / "restart-shutdown", 90)
                report["restart_shutdown"] = stopped
                if stopped["exit_code"] or stopped["timed_out"]:
                    raise RuntimeError("Preparation shutdown failed")
                require_stopped(result["resources"]["engine_identity"])
        try:
            report["decoder"] = decode_profiles(args, report["phases"], env, template["cwd"])
        except Exception as error:
            report["decoder"] = {"available": False, "gap": type(error).__name__ + ": " + str(error)}
        if not report["decoder"]["available"]:
            report["diagnostic_gaps"].append("CPU profiling/decode incomplete; no CPU-attribution claim")
        for name, command in (("heap-after-gc", ["info", "used-heap-size-after-gc"]),
                              ("skyvalue-memory", ["dump", "--memory=full,summary"])):
            before = scope_snapshot(group)
            result = run_command(start + command, template["cwd"], env, args.run_root / name, args.timeout)
            result["scope_before"], result["scope_after"] = before, scope_snapshot(group, before)
            report[name] = result
            if result["exit_code"] or result["timed_out"]:
                report["diagnostic_gaps"].append(name + " unavailable")
        report["passed"] = True
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        try:
            try:
                pid = int((args.run_root / "output/server/server.pid.txt").read_text().strip())
                final_identity = resources.read_process_identity(pid)
            except (OSError, ValueError):
                final_identity = None
            report["final_shutdown"] = run_command(start + ["shutdown"], template["cwd"], env,
                                                   args.run_root / "final-shutdown", 90)
            if report["final_shutdown"]["exit_code"] or report["final_shutdown"]["timed_out"]:
                report["passed"] = False
            if final_identity is not None:
                require_stopped({"pid": final_identity.pid, "start_ticks": final_identity.start_ticks})
            check_sources(manifest)
            report["source_state_unchanged"] = True
            report["skycache_publications_after"] = baseline.publication_inventory(args.run_root / "skycache")
        except Exception as error:
            report["cleanup_error"] = type(error).__name__ + ": " + str(error)
            report["passed"] = False
        fixture.save_new(args.run_root / "report.json", report)
    print(json.dumps({"report": str(args.run_root / "report.json"), "passed": report["passed"],
                      "diagnostic_gaps": report["diagnostic_gaps"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
