"""One Linux M0 cohort; run under a fresh systemd user scope.

--prepare creates a private Git repository/worktree fixture. Every measured
invocation needs a new --run-id; evidence files are never reused. An optional
pool retains strictly bound mutable Bazel output state across warm/restart runs.
Cold means new output bases, not an empty caller-supplied repository/disk cache.
Warm and restart perform an unmeasured build first; restart then stops daemons.
"""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlparse

import fixture
import output_bases


def require_fixture_tree(workspace, expected_tree):
    actual_tree = fixture.git(workspace, "rev-parse", "HEAD^{tree}").decode().strip()
    if actual_tree != expected_tree:
        raise RuntimeError("Fixture committed tree changed: " + str(workspace))


def inventory(path, budget=2048):
    """Bounded metadata-only walk; truncated counts/sizes are lower bounds."""
    if path is None:
        return {"enabled": False}
    path = Path(path)
    result = {"enabled": True, "path": str(path), "exists": path.exists(),
              "files_observed": 0, "directories_observed": 0, "file_bytes_observed": 0,
              "symlinks_observed": 0, "truncated": False, "entry_budget": budget}
    if not path.exists():
        return result
    pending = deque([path])
    inspected = 0
    while pending:
        directory = pending.popleft()
        with os.scandir(directory) as children:
            for entry in children:
                if inspected >= budget:
                    result["truncated"] = True
                    return result
                inspected += 1
                if entry.is_symlink():
                    result["symlinks_observed"] += 1
                elif entry.is_dir(follow_symlinks=False):
                    result["directories_observed"] += 1
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    result["files_observed"] += 1
                    result["file_bytes_observed"] += entry.stat(follow_symlinks=False).st_size
    return result


def publication_inventory(path, budget=4096):
    """Inspect only the known publication subtree, never object packs."""
    result = {"path": str(path), "publication_count": 0, "publication_bytes": 0,
              "namespace_directories": 0, "truncated": False, "entry_budget": budget}
    pending = deque([(Path(path), 0)])
    inspected = 0
    while pending:
        directory, level = pending.popleft()
        if not directory.exists():
            continue
        with os.scandir(directory) as children:
            for entry in children:
                if inspected >= budget:
                    result["truncated"] = True
                    result["state"] = "populated" if result["publication_count"] else "unknown"
                    return result
                inspected += 1
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if ((level == 0 and entry.name.startswith("leased-v"))
                            or (level == 1 and entry.name.startswith("publications-v"))
                            or level == 2):
                        pending.append((Path(entry.path), level + 1))
                        if level == 2:
                            result["namespace_directories"] += 1
                elif level == 3 and entry.name.endswith(".manifest"):
                    result["publication_count"] += 1
                    result["publication_bytes"] += entry.stat(follow_symlinks=False).st_size
    result["state"] = "populated" if result["publication_count"] else "empty"
    return result


def cache_inventory(args):
    return {"repository": inventory(args.repository_cache), "disk": inventory(args.disk_cache),
            "skycache": inventory(args.skycache_root) if args.mode == "skycache" else {"enabled": False},
            "skycache_publications": publication_inventory(args.skycache_root) if args.mode == "skycache" else None}


def proc_stat(pid):
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        return {"pid": pid, "ppid": int(fields[1]), "start": int(fields[19]),
                "ticks": int(fields[11]) + int(fields[12])}
    except (OSError, ValueError, IndexError):
        return None


def pss(pid):
    try:
        for line in Path(f"/proc/{pid}/smaps_rollup").read_text().splitlines():
            if line.startswith("Pss:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    return None


def cgroup_path():
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            return Path("/sys/fs/cgroup") / line[3:].lstrip("/")
    return None


def read_number(path):
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def cgroup_cpu(path):
    if path is None:
        return None
    try:
        values = dict(line.split() for line in (path / "cpu.stat").read_text().splitlines())
        return int(values["usage_usec"])
    except (OSError, KeyError, ValueError):
        return None


def cgroup_memory_stat(path):
    if path is None:
        return None
    try:
        values = dict(line.split() for line in (path / "memory.stat").read_text().splitlines())
        return {key: int(values[key]) for key in ("anon", "file", "shmem", "kernel", "sock")
                if key in values}
    except (OSError, ValueError):
        return None


class Sampler:
    """Phase CPU and sampled PSS; memory.peak is explicitly scope-lifetime."""

    def __init__(self, bases, interval, require_cgroup):
        self.bases, self.interval = bases, interval
        self.group = cgroup_path()
        self.valid_group = bool(self.group and self.group.name.endswith(".scope")
                                and cgroup_cpu(self.group) is not None
                                and (self.group / "memory.current").is_file())
        if require_cgroup and not self.valid_group:
            raise RuntimeError("A dedicated cgroup-v2 .scope with CPU/memory accounting is required")
        self.stop_event = threading.Event()
        self.rows = []
        self.last = {}
        self.engine_ticks = 0
        self.engine_memberships = set()
        self.observed_bases = set()
        self.started = time.monotonic()
        self.cpu_before = cgroup_cpu(self.group) if self.valid_group else None
        self.sample(initial=True)
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def engine_pids(self):
        found = []
        for base in self.bases:
            try:
                found.append((base, int((base / "server/server.pid.txt").read_text().strip())))
            except (OSError, ValueError):
                continue
        return found

    def sample(self, initial=False):
        total = 0
        count = 0
        for base, pid in self.engine_pids():
            stat = proc_stat(pid)
            if stat is None:
                continue
            self.observed_bases.add(str(base))
            key = (pid, stat["start"])
            before = self.last.get(key, stat["ticks"] if initial else 0)
            self.engine_ticks += max(0, stat["ticks"] - before)
            self.last[key] = stat["ticks"]
            memory = pss(pid)
            if memory is not None:
                total += memory
                count += 1
            if self.valid_group:
                try:
                    self.engine_memberships.add((pid, Path(f"/proc/{pid}/cgroup").read_text().strip()))
                except OSError:
                    pass
        current = read_number(self.group / "memory.current") if self.valid_group else None
        self.rows.append({"elapsed_s": time.monotonic() - self.started,
                          "engine_pss_bytes": total if count else None,
                          "engine_pids_sampled": count, "scope_memory_current_bytes": current,
                          "scope_charged_memory_stat": cgroup_memory_stat(self.group) if self.valid_group else None})

    def loop(self):
        while not self.stop_event.wait(self.interval):
            self.sample()

    def start(self):
        self.thread.start()

    def finish(self):
        self.stop_event.set()
        self.thread.join()
        self.sample()
        cpu_after = cgroup_cpu(self.group) if self.valid_group else None
        expected = "0::/" + str(self.group.relative_to("/sys/fs/cgroup")) if self.valid_group else None
        final_pids = self.engine_pids()
        observed_pids = {pid for pid, _ in self.engine_memberships}
        memberships_valid = (len(final_pids) == len(self.bases)
                             and all(pid in observed_pids and proc_stat(pid) is not None
                                     for _, pid in final_pids)
                             and all(row == expected or row.startswith(expected + "/")
                                 for _, row in self.engine_memberships)
                             if self.valid_group and self.engine_memberships else None)
        return {
            "cgroup_accounted": self.valid_group, "cgroup": str(self.group),
            "engine_membership_verified": memberships_valid,
            "engine_memberships": sorted(self.engine_memberships),
            "engine_observed_output_bases": sorted(self.observed_bases),
            "engine_expected_count": len(self.bases),
            "scope_cpu_seconds": (cpu_after - self.cpu_before) / 1e6 if cpu_after is not None else None,
            "scope_lifetime_peak_bytes": read_number(self.group / "memory.peak") if self.valid_group else None,
            "scope_phase_sampled_peak_bytes": max((r["scope_memory_current_bytes"] or 0 for r in self.rows), default=0) if self.valid_group else None,
            "scope_charged_memory_stat_peak": {
                key: max((r["scope_charged_memory_stat"] or {}).get(key, 0) for r in self.rows)
                for key in ("anon", "file", "shmem", "kernel", "sock")
            } if self.valid_group else None,
            "engine_sampled_peak_pss_bytes": max((r["engine_pss_bytes"] or 0 for r in self.rows), default=0),
            "engine_observed_cpu_seconds": self.engine_ticks / os.sysconf("SC_CLK_TCK"),
            "sample_interval_s": self.interval, "samples": self.rows,
            "notes": ["Scope CPU includes harness, clients, daemons, and compiler descendants.",
                      "memory.peak covers the whole scope lifetime, including preparation.",
                      "Scope memory is charged bytes, not RSS; shared page-cache first-touch ownership can bias arms.",
                      "memory.stat shmem is a subset of file; component peaks occur at different times and must not be summed.",
                      "Engine PSS excludes clients/compiler workers; sampling can miss short peaks.",
                      "Engine CPU is observed daemon ticks and can miss exited daemons' final ticks."],
        }


def run_logged(argv, workspace, env, directory, timeout):
    fixture.save_new(directory / "command.json", {"argv": argv, "cwd": str(workspace)})
    start = time.monotonic()
    with (directory / "console.log").open("xb") as output:
        process = subprocess.Popen(argv, cwd=workspace, env=env, stdout=output, stderr=subprocess.STDOUT)
        try:
            code = process.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            code, timed_out = process.returncode, True
    return {"exit_code": code, "timed_out": timed_out, "wall_seconds": time.monotonic() - start,
            "command": str(directory / "command.json"), "console": str(directory / "console.log")}


def bep_metrics(path):
    result = {"finished": False, "build_metrics": None, "files": []}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if "buildMetrics" in event:
            result["build_metrics"] = event["buildMetrics"]
        if "finished" in event:
            result.update(finished=True, exit_code=event["finished"].get("exitCode"))
        if "started" in event:
            result["started"] = event["started"]
        result["files"].extend(event.get("namedSetOfFiles", {}).get("files", []))
    return result


def output_base(args, run, index):
    return (getattr(args, "output_base_root", None) or run) / ("o%02d" % index)


def startup(args, run, index):
    return [str(args.binary), "--ignore_all_rc_files", f"--output_user_root={args.install_root}",
            f"--output_base={output_base(args, run, index)}", f"--host_jvm_args=-Xmx{args.heap_mb}m",
            f"--host_jvm_args=-XX:ActiveProcessorCount={args.jobs}", "--max_idle_secs=300"]


def build(args, run, row, phase, env, oracles):
    index = row["index"]
    directory = run / phase / ("c%02d" % index)
    directory.mkdir(parents=True)
    base = output_base(args, run, index)
    bep, profile = directory / "bep.jsonl", directory / "profile.json.gz"
    argv = startup(args, run, index) + ["build", fixture.TARGET, *row.get("extra_targets", []), *fixture.FLAGS,
        "--compilation_mode=" + row.get("compilation_mode", "fastbuild"),
        f"--jobs={args.jobs}", f"--loading_phase_threads={args.jobs}",
        f"--local_resources=cpu={args.jobs}", f"--local_resources=memory={args.local_memory_mb}",
        "--worker_max_instances=1", f"--repository_cache={args.repository_cache}",
        "--remote_cache=", "--remote_executor=", "--bes_backend=", "--bes_results_url=",
        f"--disk_cache={args.disk_cache or ''}", "--lockfile_mode=off",
        "--experimental_convenience_symlinks=ignore", "--experimental_record_skyframe_metrics",
        "--noshow_progress", "--color=no", "--curses=no", f"--build_event_json_file={bep}",
        f"--profile={profile}", "--nobuild_event_json_file_path_conversion"]
    if args.mode != "vanilla":
        argv += ["--experimental_remote_skycache=",
                 "--experimental_local_skycache=" + (str(args.skycache_root) if args.mode == "skycache" else "")]
        if args.active_directories:
            argv += ["--experimental_active_directories=" + args.active_directories]
        if args.mode == "skycache":
            argv += ["--experimental_analysis_cache_idle_retention=3600s"]
    if args.analysis_only:
        argv += ["--nobuild"]
    argv += args.bazel_arg
    result = {"index": index, "variant": row["variant"], "workspace": row["workspace"],
              "compilation_mode": row.get("compilation_mode", "fastbuild"),
              "oracle_key": fixture.oracle_key(row),
              "output_base": str(base), "phase": phase}
    result.update(run_logged(argv, row["workspace"], env, directory, args.timeout))
    metrics = bep_metrics(bep)
    result["bep"] = {k: v for k, v in metrics.items() if k != "files"}
    result["skycache"] = {}
    for source in sorted(base.glob("*skycache*.json")):
        destination = directory / source.name
        destination.write_bytes(source.read_bytes())
        result["skycache"][source.name] = json.loads(destination.read_text())
    if not result["exit_code"] and not args.analysis_only:
        artifacts, glob_artifacts = [], []
        for entry in metrics["files"]:
            uri = urlparse(entry.get("uri", ""))
            if uri.scheme != "file":
                continue
            path = Path(unquote(uri.path))
            if path.name == "normalize_cmd" and path.is_file():
                artifacts.append(path)
            if path.name == "sources.manifest" and path.is_file():
                glob_artifacts.append(path)
        artifacts = sorted(set(artifacts))
        if len(artifacts) != 1:
            raise RuntimeError("Expected one normalizer output, found " + repr(artifacts))
        executable = artifacts[0]
        result["output"] = {"path": str(executable), "bytes": executable.stat().st_size,
                            "sha256": fixture.sha256(executable)}
        result["behavior"] = fixture.verify_program(executable, row.get("behavior_variant", row["variant"]), directory / "behavior")
        if row.get("expected_glob_sources"):
            glob_artifacts = sorted(set(glob_artifacts))
            if len(glob_artifacts) != 1:
                raise RuntimeError("Expected exactly one generated glob manifest")
            manifest = glob_artifacts[0]
            expected_bytes = ("\n".join(row["expected_glob_sources"]) + "\n").encode()
            if manifest.read_bytes() != expected_bytes:
                raise AssertionError("Generated glob manifest differs from independent expected file list")
            result["glob_oracle"] = {"passed": True, "path": str(manifest),
                                     "sha256": fixture.sha256(manifest),
                                     "expected_sources": row["expected_glob_sources"]}
        expected = oracles.get(fixture.oracle_key(row))
        result["digest_oracle"] = {"available": expected is not None,
                                   "matches": result["output"]["sha256"] == expected if expected else None}
        if expected and not result["digest_oracle"]["matches"]:
            result["correctness_failure"] = "Output differs from the independent oracle"
    fixture.save_new(directory / "result.json", result)
    return result


def shutdown(args, run, rows, env, phase):
    results = []
    for row in rows:
        directory = run / phase / ("c%02d" % row["index"])
        directory.mkdir(parents=True)
        results.append(run_logged(startup(args, run, row["index"]) + ["shutdown"],
                                  row["workspace"], env, directory, 90))
    return results


def cohort(args, run, rows, phase, env, oracles, resource_name=None):
    sampler = Sampler([output_base(args, run, r["index"]) for r in rows], args.sample_interval,
                      args.require_cgroup)
    sampler.start()
    start = time.monotonic()
    results = []
    try:
        with ThreadPoolExecutor(max_workers=args.clients) as pool:
            futures = [pool.submit(build, args, run, row, phase, env, oracles) for row in rows]
            for future in futures:
                results.append(future.result())
    finally:
        resources = sampler.finish()
        fixture.save_new(run / ((resource_name or phase) + "-resources.json"), resources)
    accounting_passed = (not args.require_cgroup or resources["engine_membership_verified"] is True)
    return {"wall_seconds": time.monotonic() - start, "results": results,
            "resources": {k: v for k, v in resources.items() if k != "samples"},
            "accounting_passed": accounting_passed,
            "passed": accounting_passed and all(not r["exit_code"] and not r.get("correctness_failure")
                          and r["bep"]["finished"] for r in results)}


def serial_restart_preparation(args, run, rows, env, oracles):
    """Prepare one output base at a time, stopping its daemon before the next build."""
    start = time.monotonic()
    group = cgroup_path()
    cpu_before = cgroup_cpu(group) if group else None
    result = {"protocol": "serial-build-then-shutdown", "client_cohorts": [],
              "results": [], "shutdown": [], "passed": False}
    try:
        for row in rows:
            try:
                member = cohort(args, run, [row], "preparation", env, oracles,
                                resource_name="preparation-c%02d" % row["index"])
                result["client_cohorts"].append(member)
                result["results"].extend(member["results"])
            finally:
                stopped = shutdown(args, run, [row], env, "restart-shutdown")
                result["shutdown"].extend(stopped)
            if not member["passed"] or any(r["exit_code"] for r in stopped):
                break
        result["accounting_passed"] = all(
            member["accounting_passed"] for member in result["client_cohorts"])
        result["passed"] = (len(result["client_cohorts"]) == len(rows)
                            and all(member["passed"] for member in result["client_cohorts"])
                            and all(not r["exit_code"] for r in result["shutdown"]))
    except Exception as error:
        result["error"] = type(error).__name__ + ": " + str(error)
    finally:
        cpu_after = cgroup_cpu(group) if group else None
        result["wall_seconds"] = time.monotonic() - start
        result["resources"] = {
            "scope_cpu_seconds": ((cpu_after - cpu_before) / 1e6
                                  if cpu_before is not None and cpu_after is not None else None),
            "scope_lifetime_peak_bytes": read_number(group / "memory.peak") if group else None,
            "notes": ["Preparation wall time and scope CPU include each immediate shutdown.",
                      "Per-client sampled memory and engine CPU remain in client_cohorts; peaks are not summed.",
                      "Scope lifetime peak includes preparation; measured cohort remains fully concurrent."]}
        fixture.save_new(run / "serial-preparation.json", result)
    return result


def output_base_binding(args, manifest, report):
    group = cgroup_path()
    limits = {}
    for name in ("cpu.max", "memory.max", "memory.swap.max"):
        try:
            limits[name] = (group / name).read_text().strip() if group else None
        except OSError:
            limits[name] = None
    return {"schema_version": 1, "binary_sha256": report["binary_sha256"], "mode": args.mode,
            "fixture": manifest, "resource_flags": {"jobs": args.jobs, "heap_mb": args.heap_mb,
                "local_memory_mb": args.local_memory_mb, "scope_limits": limits},
            "cache_paths": {"repository": str(args.repository_cache),
                "disk": str(args.disk_cache) if args.disk_cache else None,
                "skycache": str(args.skycache_root) if args.mode == "skycache" else None},
            "install_root": str(args.install_root), "analysis_only": args.analysis_only,
            "active_directories": args.active_directories, "extra_bazel_arguments": args.bazel_arg,
            "harness_sha256": report["harness_sha256"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--clients", type=int, choices=(1, 10), default=1)
    parser.add_argument("--scenario", choices=fixture.SCENARIOS, default="identical")
    parser.add_argument("--mode", choices=("vanilla", "candidate-off", "skycache"))
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--repository-cache", type=Path)
    parser.add_argument("--disk-cache", type=Path)
    parser.add_argument("--skycache-root", type=Path,
                        help="Shared local store; defaults to root/skycache; never copied or deleted")
    parser.add_argument("--fresh-caches", action="store_true",
                        help="Allocate empty repository/disk/SkyCache directories under this new run")
    parser.add_argument("--install-root", type=Path,
                        help="Shared output_user_root for extracted installations; defaults to root.parent/install")
    parser.add_argument("--cache-state", choices=("cold", "warm", "restart"), default="cold")
    parser.add_argument("--serial-restart-preparation", action="store_true",
                        help="For restart only: prepare and stop each daemon before preparing the next")
    parser.add_argument("--output-base-root", type=Path,
                        help="Strictly bound, exclusively leased output bases for warm/restart trials")
    parser.add_argument("--run-id")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--heap-mb", type=int, default=768)
    parser.add_argument("--local-memory-mb", type=int, default=2048)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--sample-interval", type=float, default=0.1)
    parser.add_argument("--require-cgroup", action="store_true")
    parser.add_argument("--analysis-only", action="store_true")
    parser.add_argument("--active-directories", default="",
                        help="Optional legacy flag; may be deprecated/no-op in the selected binary")
    parser.add_argument("--oracle-report", type=Path)
    parser.add_argument("--bazel-arg", action="append", default=[])
    args = parser.parse_args()
    args.root = args.root.resolve()
    if args.prepare:
        if not args.repo:
            parser.error("--prepare requires --repo")
        print(json.dumps(fixture.prepare(args.repo, args.root, args.clients, args.scenario), indent=2))
        return 0
    if sys.platform != "linux":
        parser.error("Resource measurement requires Linux")
    if not all((args.binary, args.mode, args.run_id)) or not (args.repository_cache or args.fresh_caches):
        parser.error("Measurement requires --binary --mode --run-id and --repository-cache or --fresh-caches")
    if args.fresh_caches and any((args.repository_cache, args.disk_cache, args.skycache_root)):
        parser.error("--fresh-caches cannot be combined with explicit cache paths")
    if args.serial_restart_preparation and args.cache_state != "restart":
        parser.error("--serial-restart-preparation requires --cache-state=restart")
    if args.output_base_root and (args.cache_state == "cold" or args.fresh_caches):
        parser.error("--output-base-root requires warm/restart and cannot use --fresh-caches")
    if any(value.startswith("--compilation_mode") or value.startswith("-c") and not value.startswith("--")
           for value in args.bazel_arg):
        parser.error("Compilation mode is fixture-controlled; use the config scenario")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,100}", args.run_id):
        parser.error("Invalid --run-id")
    if min(args.jobs, args.heap_mb, args.local_memory_mb, args.timeout, args.sample_interval) <= 0:
        parser.error("Resource limits and timeouts must be positive")
    args.binary = args.binary.resolve()
    if args.repository_cache:
        args.repository_cache = args.repository_cache.resolve()
    args.skycache_root = (args.skycache_root or (args.root / "skycache")).resolve()
    args.install_root = (args.install_root or (args.root.parent / "install")).resolve()
    if args.disk_cache:
        args.disk_cache = args.disk_cache.resolve()
    if args.output_base_root:
        args.output_base_root = args.output_base_root.resolve()
    manifest = json.loads((args.root / "fixture/manifest.json").read_text())
    args.fixture_tree = manifest["private_tree"]
    if manifest["clients"] != args.clients or manifest["scenario"] != args.scenario:
        parser.error("Fixture clients/scenario differ from this invocation")
    for row in manifest["worktrees"]:
        workspace = Path(row["workspace"])
        if args.output_base_root and args.output_base_root.is_relative_to(workspace.resolve()):
            parser.error("--output-base-root must be outside every fixture worktree")
        require_fixture_tree(workspace, args.fixture_tree)
        if "source_state" not in row or fixture.source_state(workspace) != row["source_state"]:
            raise RuntimeError("Fixture source-state guard missing or changed: " + str(workspace))
        for name, field in (("root.go", "source_sha256"), ("BUILD.bazel", "build_sha256")):
            if fixture.sha256(workspace / fixture.COMMAND / name) != row[field]:
                raise RuntimeError("Fixture changed: " + str(workspace))
    run = args.root / "runs" / args.run_id
    run.mkdir(parents=True)  # Existing runs are never reused, even after failure.
    if args.fresh_caches:
        args.repository_cache, args.disk_cache, args.skycache_root = (
            run / "fresh-caches" / name for name in ("repository", "disk", "skycache"))
        for path in (args.repository_cache, args.disk_cache, args.skycache_root):
            path.mkdir(parents=True)
    env = fixture.clean_env()
    env["USE_BAZEL_VERSION"] = "9.1.0"
    oracles = {}
    oracle_input_records = {}
    if args.oracle_report:
        oracle = json.loads(args.oracle_report.read_text())
        if not oracle["passed"] or oracle["mode"] != "vanilla" or oracle["analysis_only"]:
            raise ValueError("Oracle must be a successful execution-enabled vanilla report")
        if oracle["fixture"]["source_revision"] != fixture.REVISION:
            raise ValueError("Oracle source revision differs")
        if (oracle["fixture"]["projection_archive_sha256"] != manifest["projection_archive_sha256"]
                or oracle["fixture"]["target"] != manifest["target"]):
            raise ValueError("Oracle projection or target differs")
        if not manifest.get("private_tree") or oracle["fixture"].get("private_tree") != manifest["private_tree"]:
            raise ValueError("Oracle private Git tree missing or different, including auxiliary rules")
        if oracle.get("extra_bazel_arguments", []) != args.bazel_arg:
            raise ValueError("Oracle extra Bazel flags differ")
        for row in oracle["fixture"]["worktrees"]:
            key, inputs = fixture.oracle_key(row), fixture.oracle_inputs(row)
            if oracle_input_records.setdefault(key, inputs) != inputs:
                raise ValueError("Oracle mixes inputs under one variant/configuration key")
        for row in oracle["measured"]["results"]:
            if row["exit_code"] or not row.get("behavior", {}).get("passed"):
                raise ValueError("Oracle contains failed builds")
            key = fixture.oracle_key(row)
            previous = oracles.setdefault(key, row["output"]["sha256"])
            if previous != row["output"]["sha256"]:
                raise ValueError("Oracle contains inconsistent variant outputs")
        missing = {fixture.oracle_key(row) for row in manifest["worktrees"]} - oracles.keys()
        if missing:
            raise ValueError("Oracle is missing required variants: " + repr(sorted(missing)))
        if any(oracle_input_records.get(fixture.oracle_key(row)) != fixture.oracle_inputs(row)
               for row in manifest["worktrees"]):
            raise ValueError("Oracle source inputs or extra targets differ")
    report = {"schema_version": 1, "mode": args.mode, "cache_state": args.cache_state,
              "engine_state": {"cold": "cold-engine", "warm": "warm-engine",
                               "restart": "restarted-engine"}[args.cache_state],
              "fresh_caches_requested": args.fresh_caches,
              "output_base_root": str(args.output_base_root) if args.output_base_root else None,
              "preparation_protocol": ("serial-build-then-shutdown" if args.serial_restart_preparation
                                       else "concurrent-builds-then-shutdown" if args.cache_state == "restart"
                                       else "concurrent-builds" if args.cache_state == "warm" else "none"),
              "cache_before": cache_inventory(args),
              "binary": str(args.binary), "binary_sha256": fixture.sha256(args.binary),
              "fixture": manifest, "analysis_only": args.analysis_only,
              "extra_bazel_arguments": args.bazel_arg,
              "oracle_report": {"path": str(args.oracle_report.resolve()),
                                "sha256": fixture.sha256(args.oracle_report),
                                "variant_configuration_digests": oracles,
                                "variant_configuration_inputs": oracle_input_records} if args.oracle_report else None,
              "installation": {"shared_output_user_root": str(args.install_root),
                               "existing_install_directories": sorted(p.name for p in (args.install_root / "install").glob("*") if p.is_dir()),
                               "note": "Prewarm both binaries outside measured scopes; harness records but does not assume prewarming."},
              "harness_sha256": {p.name: fixture.sha256(p) for p in
                                 (Path(__file__), Path(fixture.__file__), Path(output_bases.__file__))},
              "environment_digest": hashlib.sha256(json.dumps(sorted(env.items())).encode()).hexdigest(),
              "environment_values_recorded": False, "passed": False,
              "notes": ["Caller-supplied repository/disk caches may be warm.",
                        "Disjoint edits preserve runtime behavior; binary digest equality is measured, not presumed.",
                        "A fresh systemd scope is required for complete aggregate accounting."]}
    binding = output_base_binding(args, manifest, report) if args.output_base_root else None
    report["output_base_pool_binding_sha256"] = (
        hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest() if binding else None)
    lease = (output_bases.Pool(args.output_base_root, binding, run / "report.json")
             if args.output_base_root else nullcontext())
    with lease:
        if args.output_base_root:
            fixture.save_new(run / "output-base-binding.json", binding)
            report["output_base_pool"] = {"run_number": lease.run_number,
                                        "previous_report_path": lease.previous_report_path,
                                        "binding_path": str(args.output_base_root / "binding.json")}
            report["notes"].append(
                "Pool binding excludes the full environment digest because launcher variables vary. "
                "Environment changes may invalidate Bazel state; performance comparisons must inspect per-run provenance.")
        fixture.save_new(run / "provenance.json", report)
        try:
            if args.cache_state != "cold":
                if args.serial_restart_preparation:
                    report["preparation"] = serial_restart_preparation(
                        args, run, manifest["worktrees"], env, oracles)
                    report["restart_shutdown"] = report["preparation"]["shutdown"]
                else:
                    report["preparation"] = cohort(args, run, manifest["worktrees"], "preparation", env, oracles)
                if not report["preparation"]["passed"]:
                    raise RuntimeError("Preparation build failed")
                if args.cache_state == "restart" and not args.serial_restart_preparation:
                    report["restart_shutdown"] = shutdown(args, run, manifest["worktrees"], env, "restart-shutdown")
                    if any(r["exit_code"] for r in report["restart_shutdown"]):
                        raise RuntimeError("Preparation daemon shutdown failed")
            report["cache_before_measured"] = cache_inventory(args)
            report["measured"] = cohort(args, run, manifest["worktrees"], "measured", env, oracles)
            report["passed"] = report["measured"]["passed"]
        except Exception as error:
            report["error"] = type(error).__name__ + ": " + str(error)
        finally:
            try:
                report["final_shutdown"] = shutdown(args, run, manifest["worktrees"], env, "final-shutdown")
                if any(r["exit_code"] for r in report["final_shutdown"]):
                    report["passed"] = False
                report["source_state_unchanged"] = all(
                    fixture.source_state(row["workspace"]) == row["source_state"]
                    for row in manifest["worktrees"])
                if not report["source_state_unchanged"]:
                    report["passed"] = False
                report["source_tree_unchanged"] = False
                for row in manifest["worktrees"]:
                    require_fixture_tree(row["workspace"], args.fixture_tree)
                report["source_tree_unchanged"] = True
                report["cache_after"] = cache_inventory(args)
            except Exception as error:
                report["cleanup_error"] = type(error).__name__ + ": " + str(error)
                report["passed"] = False
            fixture.save_new(run / "report.json", report)
    print(json.dumps({"report": str(run / "report.json"), "passed": report["passed"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
