"""One fresh, guarded M1 private/native arm. No coordinator, repair or pool reuse.

Future Linux entry points (not qualification evidence):
  python3 -B native_comparison.py --manifest /task/reference.json --reference-only
  python3 -B native_comparison.py --manifest /task/private-or-native.json
Both commands must be launched by run_guarded.ps1 after host-budget preflight.
Reference mode is private only and NEVER performance eligible. Its successful
report is mandatory input to a comparison arm; reference creation is explicit.

Manifest schema 1: task_root, run_root (new direct child of task_root), hosting
(private/native), clients (1/2/10), state (restart/warm), scenario, shared_name,
source_repository, binary {path,sha256,source_commit,source_tree_sha256},
repository_seed {root,files:{relative_name:sha256}}, oracle_report {path,sha256,
guard_result:{path,sha256}}
(comparison only), guard_evidence, guard_command_sha256, launch_argv,
host_preflight {path,sha256?}, sample_interval_seconds, timeout_seconds,
history {pool_run:1,preparations_per_engine:1,identity_calls_per_preparation:2}.
All filesystem paths are explicit absolute paths. Writable state is created
below run_root; external sources/seed/candidate/oracle remain read-only.
The guard evidence is policy.json + scope-identity.json from the existing guard.
Runtime preflight path must be guard_evidence.parent/host-budget-preflight.json;
its observed byte digest is recorded, with an optional predeclared digest check.
Final host guard success/absence remains a coordinator requirement; this runner
can only mark its internal result ready for that final check.

The sampler's engine bindings during restart are provisional until successful
post-window per-engine info checks. One native PID is not proof of N engines.
Raw counters and observer cost are preserved, never silently subtracted.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlparse

import baseline
import fixture
import m1_resources
from m1_resources import EngineMapping, ResourceSampler, read_process_identity


HISTORY = {"pool_run": 1, "preparations_per_engine": 1,
           "identity_calls_per_preparation": 2}
POLICY = {
    "jobs": "2", "loading_phase_threads": "2", "local_test_jobs": "2",
    "spawn_strategy": "sandboxed,local", "genrule_strategy": "sandboxed,local",
    "reuse_sandbox_directories": "false", "remote_executor": "", "remote_cache": "",
    "remote_accept_cached": "true", "remote_upload_local_results": "true",
    "remote_cache_async": "false", "remote_download_outputs": "all",
    "guard_against_concurrent_changes": "full", "cache_computed_file_digests": "0",
    "experimental_non_deterministic_memory_optimizations": "false",
    "generate_json_trace_profile": "no", "local_resources": ["cpu=2", "memory=1920"],
    "lockfile_mode": "off", "experimental_convenience_symlinks": "ignore",
    "bes_backend": "", "bes_results_url": "",
}
RESET_OPTIONS = {"strategy", "strategy_regexp", "allowed_strategies_by_exec_platform",
                 "profile", "memory_profile", "starlark_cpu_profile",
                 "experimental_remote_downloader", "experimental_remote_output_service"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def checked_document(record):
    path = absolute(record["path"])
    if fixture.sha256(path) != record["sha256"]:
        raise ValueError("Document digest mismatch: " + str(path))
    return load_json(path)


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Path must be absolute, normalized and non-redirected: " + str(path))
    return path


def validate_launch(pinned, original, script, script_arguments):
    if not original:
        raise ValueError("Original interpreter argv unavailable")
    prefix = original[:2] if original[1:2] == ["-B"] else original[:1]
    expected = [*prefix, str(script), *script_arguments]
    if pinned != original or original != expected:
        raise ValueError("Invocation differs from pinned literal argv or uses unsupported interpreter flags")


def validate_manifest(config, reference_only=False):
    if config.get("schema_version") != 1 or config.get("history") != HISTORY:
        raise ValueError("Unsupported schema or preparation/pool history")
    if config["hosting"] not in ("private", "native") or config["clients"] not in (1, 2, 10):
        raise ValueError("Unsupported hosting/client count")
    if config["state"] not in ("restart", "warm") or config["scenario"] not in fixture.SCENARIOS:
        raise ValueError("Unsupported state/scenario")
    if reference_only and (config["hosting"] != "private" or config.get("oracle_report")):
        raise ValueError("Reference-only requires private hosting and no input oracle")
    if not reference_only and not config.get("oracle_report"):
        raise ValueError("Comparison requires an independent same-binary private reference")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,48}", config["shared_name"]):
        raise ValueError("Invalid shared name")
    if not 0.01 <= config["sample_interval_seconds"] <= 60 or config["timeout_seconds"] <= 0:
        raise ValueError("Invalid sampling cadence/timeout")
    root, task = absolute(config["run_root"]), absolute(config["task_root"])
    if root.parent != task or root.exists():
        raise ValueError("Run root must be a new direct child of the task root")
    for path in (config["binary"]["path"], config["source_repository"],
                 config["repository_seed"]["root"], config["guard_evidence"]):
        if absolute(path).is_relative_to(root):
            raise ValueError("External inputs/evidence must not be inside new run state")
    for key in ("sha256", "source_tree_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", config["binary"][key]):
            raise ValueError("Candidate digest missing")
    if not re.fullmatch(r"[0-9a-f]{40,64}", config["binary"]["source_commit"]):
        raise ValueError("Candidate source revision missing")
    return root


def layout(config, rows):
    root = Path(config["run_root"])
    user = root / "state/user"
    control = user / "shared_servers/v3" / config["shared_name"] / "control"
    bases = {str(row["index"]): (control.parent / "engines" /
             hashlib.md5(row["workspace"].encode(), usedforsecurity=False).hexdigest()
             if config["hosting"] == "native" else root / "state" / ("o%02d" % row["index"]))
             for row in rows}
    return {"user": user, "control": control, "bases": bases,
            "disk": control / "action-cache" if config["hosting"] == "native" else root / "state/action-cache",
            "repository": root / "state/repository-cache", "tmp": root / "temp/tmp",
            "java": root / "temp/java", "home": root / "home"}


def launch_environment(paths):
    return {"HOME": str(paths["home"]), "PATH": "/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TMP": str(paths["tmp"]),
            "TMPDIR": str(paths["tmp"]), "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def startup(config, paths, index):
    heap = 768 * (config["clients"] if config["hosting"] == "native" else 1)
    args = [config["binary"]["path"], "--ignore_all_rc_files", "--noidle_server_tasks",
            "--max_idle_secs=300", "--output_user_root=" + str(paths["user"]),
            "--host_jvm_args=-Xmx%dm" % heap, "--host_jvm_args=-XX:ActiveProcessorCount=2",
            "--host_jvm_args=-Djava.io.tmpdir=" + str(paths["java"])]
    args += (["--experimental_shared_server=" + config["shared_name"]]
             if config["hosting"] == "native" else ["--output_base=" + str(paths["bases"][str(index)])])
    return args


def effective_policy(paths, row):
    return {**POLICY, "compilation_mode": row.get("compilation_mode", "fastbuild"),
            "disk_cache": str(paths["disk"]), "repository_cache": str(paths["repository"]),
            "repo_contents_cache": str(paths["bases"][str(row["index"])] / "repo-contents-cache"),
            "repo_env": ["TMP=" + str(paths["tmp"]), "TMPDIR=" + str(paths["tmp"])],
            "action_env": ["TMP=" + str(paths["tmp"]), "TMPDIR=" + str(paths["tmp"])]}


def build_command(config, paths, row, directory):
    flags = []
    for name, values in effective_policy(paths, row).items():
        for value in values if isinstance(values, list) else [values]:
            flags.append("--%s=%s" % (name, value))
    return startup(config, paths, row["index"]) + ["build", fixture.TARGET,
        *row.get("extra_targets", []), *fixture.FLAGS, *flags, "--noshow_progress", "--color=no",
        "--curses=no", "--nobuild_event_json_file_path_conversion",
        "--build_event_json_file=" + str(directory / "bep.jsonl")]


def audit_policy(events, expected):
    canonical = [e["structuredCommandLine"] for e in events
                 if e.get("structuredCommandLine", {}).get("commandLineLabel") == "canonical"]
    if len(canonical) != 1:
        raise ValueError("Exactly one canonical BEP command line is required")
    observed = {}
    for section in canonical[0].get("sections", []):
        if section.get("sectionLabel") != "command options":
            continue
        for option in section.get("optionList", {}).get("option", []):
            name, value = option["optionName"], option.get("optionValue", "")
            if name in expected and expected[name] in ("true", "false"):
                if value.lower() in ("true", "1", "yes"):
                    value = "true"
                elif value.lower() in ("false", "0", "no"):
                    value = "false"
                elif not value:
                    value = {"--" + name: "true", "--no" + name: "false"}.get(option.get("combinedForm"), "")
            observed.setdefault(name, []).append(value)
    for name, value in expected.items():
        wanted = value if isinstance(value, list) else [value]
        if sorted(observed.get(name, [])) != sorted(wanted):
            raise ValueError("Canonical effective policy mismatch: " + name)
    if RESET_OPTIONS.intersection(observed):
        raise ValueError("Unexpected strategy/profile/downloader/output-service override")
    return {name: observed[name] for name in sorted(expected)}


def validate_guard(policy, observed, live, membership, limits, launch_argv):
    expected = policy["scope"]
    for row in (observed, live):
        if (row.get("Id") != expected["unit"] or row.get("Description") != expected["description"]
                or not row.get("InvocationID") or row.get("ActiveState") != "active"
                or not row.get("ControlGroup", "").endswith("/" + expected["unit"])):
            raise ValueError("Task scope ownership attestation failed")
    if any(observed[key] != live[key] for key in ("Id", "Description", "InvocationID", "ControlGroup")):
        raise ValueError("Scope identity changed")
    if membership != live["ControlGroup"]:
        raise ValueError("Runner is not in the exact owned measurement scope")
    if (policy.get("memory_max_mib") != 10240 or policy.get("cpu_quota_percent") != 600
            or policy.get("minimum_free_physical_bytes", 0) < 3072 * 1024**2
            or policy.get("minimum_free_commit_bytes", 0) < 4096 * 1024**2
            or policy.get("sample_interval_ms") != 500
            or policy.get("argv", [])[-len(launch_argv):] != launch_argv):
        raise ValueError("Guard launch or fixed resource policy mismatch")
    quota, period = map(int, limits["cpu.max"].split())
    if (limits["memory.max"] != str(10240 * 1024**2) or limits["memory.swap.max"] != "0"
            or period <= 0 or quota != 6 * period):
        raise ValueError("Live cgroup resource limits differ")


def read_host_preflight(config, *, require_fresh=True, now=None):
    path = absolute(config["host_preflight"]["path"])
    if path != absolute(config["guard_evidence"]).parent / "host-budget-preflight.json":
        raise ValueError("Host preflight must be the generated sibling of this guard evidence")
    raw = path.read_bytes()  # Hash exactly the bytes validated, without a second read.
    observed_digest = hashlib.sha256(raw).hexdigest()
    expected_digest = config["host_preflight"].get("sha256")
    if expected_digest is not None and expected_digest != observed_digest:
        raise ValueError("Host preflight digest mismatch")
    preflight = json.loads(raw.decode("utf-8-sig"))
    if (preflight.get("status") != "ready" or preflight.get("consecutive_ready_samples", 0) < 2
            or preflight["policy"]["minimum_physical_bytes"] < 13824 * 1024**2
            or preflight["policy"]["minimum_commit_bytes"] < 14848 * 1024**2
            or preflight["policy"]["sample_interval_ms"] != 2000):
        raise ValueError("Required two-sample host-budget preflight missing")
    recent = preflight.get("samples", [])[-2:]
    if len(recent) != 2 or any(r["available_physical_bytes"] < 13824 * 1024**2
                              or r["available_commit_bytes"] < 14848 * 1024**2 for r in recent):
        raise ValueError("Host preflight samples do not prove the required headroom")
    times = [datetime.fromisoformat(r["utc"].replace("Z", "+00:00")) for r in recent]
    if (times[1] - times[0]).total_seconds() < 1.9:
        raise ValueError("Host preflight samples are not two seconds apart")
    if require_fresh and not 0 <= ((now or datetime.now(timezone.utc)) - times[1]).total_seconds() <= 60:
        raise ValueError("Host preflight is stale or future-dated")
    return {"host_preflight_path": str(path), "host_preflight_sha256": observed_digest,
            "host_preflight_last_sample_utc": recent[-1]["utc"]}


def attest_scope(config, *, require_fresh_preflight=True):
    evidence = Path(config["guard_evidence"])
    policy, observed = load_json(evidence / "policy.json"), load_json(evidence / "scope-identity.json")
    if policy["command_sha256"] != config["guard_command_sha256"]:
        raise ValueError("Guard command digest mismatch")
    result = subprocess.run(["systemctl", "--user", "show", "--no-pager",
        "--property=Id,Description,InvocationID,ControlGroup,ActiveState", policy["scope"]["unit"]],
        check=True, capture_output=True, text=True, timeout=5)
    live = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    membership = m1_resources._membership(Path("/proc/self/cgroup").read_text())
    group = Path("/sys/fs/cgroup") / membership.lstrip("/")
    limits = {name: (group / name).read_text().strip()
              for name in ("cpu.max", "memory.max", "memory.swap.max")}
    validate_guard(policy, observed, live, membership, limits, config["launch_argv"])
    preflight = read_host_preflight(config, require_fresh=require_fresh_preflight)
    return {"membership": membership, "identity": live, "limits": limits,
            **preflight,
            "guard_policy_sha256": fixture.sha256(evidence / "policy.json"),
            "scope_identity_sha256": fixture.sha256(evidence / "scope-identity.json")}


def seed_repository(seed, destination):
    source = absolute(seed["root"])
    destination.mkdir(parents=True)
    for name, expected in sorted(seed["files"].items()):
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts or "\\" in name or str(relative) != name:
            raise ValueError("Unsafe repository seed path")
        origin = source.joinpath(*relative.parts)
        if origin.resolve() != origin or not origin.is_file() or fixture.sha256(origin) != expected:
            raise ValueError("Repository seed mismatch: " + name)
        target = destination.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
        if fixture.sha256(target) != expected:
            raise ValueError("Repository seed copy mismatch")


def validate_reference(reference, manifest, config, policy_sha256):
    if (not reference.get("passed") or not reference.get("reference_only")
            or reference.get("performance_eligible") is not False or reference.get("hosting") != "private"
            or reference.get("binary_sha256") != config["binary"]["sha256"]
            or reference.get("comparison_policy_sha256") != policy_sha256):
        raise ValueError("Reference must be successful independent private same-binary/same-policy evidence")
    if Path(reference["run_root"]) == Path(config["run_root"]):
        raise ValueError("Oracle must use an independent run root")
    for key in ("source_revision", "projection_archive_sha256", "private_tree", "target"):
        if reference["fixture"].get(key) != manifest.get(key):
            raise ValueError("Reference fixture mismatch: " + key)
    inputs, outputs = {}, {}
    for row in reference["fixture"]["worktrees"]:
        key, value = fixture.oracle_key(row), fixture.oracle_inputs(row)
        if inputs.setdefault(key, value) != value:
            raise ValueError("Reference mixes variant inputs")
    for row in reference["measured"]["results"]:
        if row["exit_code"] or not row.get("behavior", {}).get("passed") or not row.get("policy_verified"):
            raise ValueError("Reference contains an unverified result")
        key, value = fixture.oracle_key(row), row["output"]["sha256"]
        if outputs.setdefault(key, value) != value:
            raise ValueError("Reference mixes variant outputs")
    for row in manifest["worktrees"]:
        key = fixture.oracle_key(row)
        if key not in outputs or inputs.get(key) != fixture.oracle_inputs(row):
            raise ValueError("Reference is missing matching variant/configuration inputs: " + key)
    return outputs


def validate_reference_guard(guard, reference):
    if (guard.get("status") != "completed" or guard.get("exit_code") != 0
            or guard.get("scope", {}).get("unit") != reference["scope"]["identity"]["Id"]
            or guard.get("scope", {}).get("invocation_id") != reference["scope"]["identity"]["InvocationID"]
            or guard.get("final_scope_cleanup", {}).get("verified") is not True
            or guard.get("final_scope_cleanup", {}).get("signalled") is not False):
        raise ValueError("Reference's final external host guard did not complete successfully")


def bep_identity(events, row, mapping):
    starts = [event["started"] for event in events if "started" in event]
    finishes = [event["finished"] for event in events if "finished" in event]
    if len(starts) != 1 or len(finishes) != 1:
        raise ValueError("Missing or duplicate BEP start/finish")
    start, finish = starts[0], finishes[0]
    build_id = start.get("uuid")
    if (start.get("command") != "build" or finish.get("overallSuccess") is not True
            or finish.get("exitCode", {}).get("name") != "SUCCESS"
            or finish.get("exitCode", {}).get("code", 0) != 0):
        raise ValueError("BEP start/finish does not describe a successful build")
    if not isinstance(build_id, str) or not re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", build_id):
        raise ValueError("Missing or invalid BEP build UUID")
    if start.get("workspaceDirectory") != row["workspace"]:
        raise ValueError("BEP workspace differs from expected engine")
    values = [start.get("serverPid"), start.get("startTimeMillis"), finish.get("finishTimeMillis")]
    if not all(type(value) is int or isinstance(value, str) and re.fullmatch(r"[0-9]+", value) for value in values):
        raise ValueError("Invalid BEP PID/timestamps")
    pid, begin, end = map(int, values)
    if mapping is None or pid != mapping.identity.pid:
        raise ValueError("BEP PID differs from confirmed engine mapping")
    if not 0 < begin < end:
        raise ValueError("BEP command lacks a positive interval")
    return {"index": row["index"], "build_uuid": build_id.lower(), "workspace": row["workspace"],
            "server_pid": pid, "start_time_millis": begin, "finish_time_millis": end}


def cohort_bep_proof(results, rows):
    intervals = [result["bep_identity"] for result in results]
    if sorted(interval["index"] for interval in intervals) != sorted(row["index"] for row in rows):
        raise ValueError("BEP cohort does not cover exactly the expected engines")
    ids = {interval["build_uuid"] for interval in intervals}
    if len(ids) != len(rows):
        raise ValueError("Duplicate BEP build UUIDs across engines")
    latest_start = max(interval["start_time_millis"] for interval in intervals)
    earliest_finish = min(interval["finish_time_millis"] for interval in intervals)
    if len(rows) > 1 and latest_start >= earliest_finish:
        raise ValueError("Measured BEP commands lack simultaneous all-client overlap")
    return {"verified": True, "intervals": intervals,
            "all_clients_overlap_seconds": (earliest_finish - latest_start) / 1000,
            "interpretation": "Concurrent in-flight commands, not proof of simultaneous CPU-running threads"}


def verify_build(row, result, directory, paths, oracles, reference_only, mapping):
    if result["exit_code"] or result["timed_out"]:
        raise RuntimeError("Bazel build did not succeed")
    events = [json.loads(line) for line in (directory / "bep.jsonl").read_text().splitlines()]
    result["bep_identity"] = bep_identity(events, row, mapping)
    result["effective_policy"] = audit_policy(events, effective_policy(paths, row))
    metrics = baseline.bep_metrics(directory / "bep.jsonl")
    if not metrics["finished"] or metrics.get("exit_code", {}).get("code", 0) != 0:
        raise ValueError("BEP completion missing or unsuccessful")
    result.update(index=row["index"], variant=row["variant"],
                  compilation_mode=row.get("compilation_mode", "fastbuild"), policy_verified=True,
                  bep={key: value for key, value in metrics.items() if key != "files"})
    files = set()
    for entry in metrics["files"]:
        uri = urlparse(entry.get("uri", ""))
        if uri.scheme == "file" and not uri.netloc:
            path = Path(unquote(uri.path)).resolve()
            if path.name in ("normalize_cmd", "sources.manifest"):
                if not path.is_relative_to(paths["bases"][str(row["index"])].resolve()) or not path.is_file():
                    raise ValueError("Output is missing or outside its engine")
                files.add(path)
    executables = [path for path in files if path.name == "normalize_cmd"]
    if len(executables) != 1:
        raise ValueError("Expected exactly one normalizer output")
    executable = executables[0]
    result["output"] = {"path": str(executable), "sha256": fixture.sha256(executable),
                        "bytes": executable.stat().st_size}
    result["behavior"] = fixture.verify_program(executable, row.get("behavior_variant", "base"), directory / "behavior")
    if not reference_only and result["output"]["sha256"] != oracles[fixture.oracle_key(row)]:
        raise ValueError("Output digest differs from independent reference")
    if row.get("expected_glob_sources"):
        glob = [path for path in files if path.name == "sources.manifest"]
        expected = ("\n".join(row["expected_glob_sources"]) + "\n").encode()
        if len(glob) != 1 or glob[0].read_bytes() != expected:
            raise ValueError("Glob membership differs from independent expected source list")
        result["glob_oracle"] = {"passed": True, "sha256": fixture.sha256(glob[0])}
    result["digest_oracle"] = {"required": not reference_only, "matches": True if not reference_only else None}
    fixture.save_new(directory / "verified-result.json", result)
    return result


def execute(argv, workspace, env, directory, timeout):
    directory.mkdir(parents=True)
    fixture.save_new(directory / "command.json", {"argv": argv, "cwd": str(workspace)})
    started = time.monotonic_ns()
    with (directory / "stdout.log").open("xb") as stdout, (directory / "stderr.log").open("xb") as stderr:
        process = subprocess.Popen(argv, cwd=workspace, env=env, stdout=stdout, stderr=stderr)
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            code = process.returncode
    result = {"exit_code": code, "timed_out": timed_out,
              "started_ns": started, "exited_ns": time.monotonic_ns()}
    fixture.save_new(directory / "process-result.json", result)
    return result


def cpu_delta(initial, final, state):
    def counters(sample):
        if not sample["eligible"]:
            raise ValueError("Incomplete process endpoint")
        return {(p["pid"], p["start_ticks"]): p["cpu_ticks"] for p in sample["processes"]}
    end = counters(final)
    if state == "restart":
        # Caller separately proves all owned daemons absent before launching and
        # checks birth ticks against the boundary; never subtract changing sets.
        return sum(end.values()) / final["clock_ticks_per_second"]
    begin = counters(initial)
    if begin.keys() != end.keys() or any(end[key] < begin[key] for key in begin):
        raise ValueError("Warm process identity/counter continuity failed")
    return sum(end[key] - begin[key] for key in begin) / final["clock_ticks_per_second"]


def scope_delta(initial, final):
    if (not initial["eligible"] or not final["eligible"]
            or initial["directory_identity"] != final["directory_identity"]):
        raise ValueError("Scope endpoints are incomplete or changed")
    start, end = initial["cpu.stat"]["usage_usec"], final["cpu.stat"]["usage_usec"]
    if end < start:
        raise ValueError("Scope CPU counter reset")
    for key in ("oom", "oom_kill"):
        if final["memory.events"][key] != initial["memory.events"][key]:
            raise ValueError("OOM event or counter reset in measurement window")
    return (end - start) / 1e6


class Arm:
    def __init__(self, config, manifest, scope, reference_only=False):
        self.config, self.manifest, self.scope = config, manifest, scope
        self.rows, self.root = manifest["worktrees"], Path(config["run_root"])
        self.paths = layout(config, self.rows)
        self.env = launch_environment(self.paths)
        self.reference_only = reference_only
        self.verified, self.observed, self.history, self.admitted = {}, {}, [], set()
        self.discovery_errors = {}
        self.command = execute

    def pid_file(self, row):
        base = self.paths["control"] if self.config["hosting"] == "native" else self.paths["bases"][str(row["index"])]
        return base / "server/server.pid.txt"

    def discover(self, rows):
        mappings, cached = {}, {}
        self.discovery_errors = {}
        for row in rows:
            key, pid_file = str(row["index"]), self.pid_file(row)
            if pid_file not in cached:
                identity, error = None, None
                try:
                    pid = int(pid_file.read_text().strip())
                    identity = read_process_identity(pid)
                    if identity is None and (Path("/proc") / str(pid)).exists():
                        error = "ProcessIdentityUnavailable"
                except FileNotFoundError:
                    pass
                except (OSError, ValueError) as failure:
                    error = type(failure).__name__
                cached[pid_file] = identity, error
            identity, error = cached[pid_file]
            if error:
                self.discovery_errors[key] = error
            if identity:
                mappings[key] = EngineMapping(str(self.paths["bases"][key]), pid_file, identity)
        return mappings

    def identity(self, row, phase):
        values = {}
        for key in ("server_pid", "output_base"):
            directory = self.root / phase / ("c%02d-%s" % (row["index"], key))
            result = self.command(startup(self.config, self.paths, row["index"]) + ["info", key],
                                  row["workspace"], self.env, directory, 90)
            self.history.append({"phase": phase, "index": row["index"], "command": "info " + key})
            if result["exit_code"] or result["timed_out"]:
                raise ValueError("Engine identity command failed")
            values[key] = (directory / "stdout.log").read_text().strip()
        key = str(row["index"])
        mapping = self.discover([row]).get(key)
        if (mapping is None or values["output_base"] != str(self.paths["bases"][key])
                or values["server_pid"] != str(mapping.identity.pid)):
            raise ValueError("Engine info/PID file/derived output mapping mismatch")
        self.verified[key] = mapping
        self.admitted.add(key)
        return mapping

    def require_absent(self, rows):
        if self.discover(rows) or self.discovery_errors:
            raise ValueError("Owned daemon still alive; restart/cleanup not complete")

    def shutdown(self, rows, phase):
        results = []
        for row in rows:
            key = str(row["index"])
            known = self.verified.get(key) or self.observed.get(key)
            current = self.discover([row]).get(key)
            if current is None:
                continue  # Never launch shutdown against a stopped daemon.
            if key not in self.admitted:
                continue  # A control PID does not prove this engine was admitted.
            if known is None or current.identity != known.identity:
                raise ValueError("Refusing shutdown without matching owned process identity")
            member = m1_resources._membership((Path("/proc") / str(current.identity.pid) / "cgroup").read_text())
            if member != self.scope["membership"] and not member.startswith(self.scope["membership"] + "/"):
                raise ValueError("Refusing shutdown outside owned scope")
            directory = self.root / phase / ("c%02d" % row["index"])
            result = self.command(startup(self.config, self.paths, row["index"]) + ["shutdown"],
                                  row["workspace"], self.env, directory, 90)
            results.append(result)
            self.history.append({"phase": phase, "index": row["index"], "command": "shutdown"})
            if result["exit_code"] or result["timed_out"]:
                raise ValueError("Owned shutdown failed")
            self.verified.pop(key, None)
            self.observed.pop(key, None)
            self.admitted.discard(key)
        deadline = time.monotonic() + 10
        while (self.discover(rows) or self.discovery_errors) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.require_absent(rows)
        return results

    def prepare(self, oracles):
        results = []
        for row in self.rows:
            directory = self.root / "preparation" / ("c%02d" % row["index"])
            result = self.command(build_command(self.config, self.paths, row, directory), row["workspace"],
                                  self.env, directory, self.config["timeout_seconds"])
            self.history.append({"phase": "preparation", "index": row["index"], "command": "build"})
            self.observed.update(self.discover([row]))
            if result["exit_code"] == 0 and not result["timed_out"]:
                self.admitted.add(str(row["index"]))
            if result["exit_code"] or result["timed_out"]:
                raise ValueError("Preparation build failed; do not warm/register an engine through info")
            mapping = self.identity(row, "preparation-identity")
            results.append(verify_build(row, result, directory, self.paths, oracles, self.reference_only, mapping))
            if self.config["state"] == "restart":
                self.shutdown([row], "preparation-shutdown")
        self.check_process_count(self.verified, require_all=self.config["state"] == "warm")
        return results

    def check_process_count(self, mappings, require_all=True):
        if require_all and set(mappings) != {str(row["index"]) for row in self.rows}:
            raise ValueError("Not every engine is mapped")
        if not mappings and not require_all:
            return
        count = len({m.identity for m in mappings.values()})
        expected = 1 if self.config["hosting"] == "native" else len(mappings)
        if count != expected:
            raise ValueError("Unexpected distinct daemon count")

    def measured(self, oracles):
        sampler = ResourceSampler({key: str(base) for key, base in self.paths["bases"].items()},
                                  expected_cgroup=self.scope["membership"])
        samples, reads, processes, results = [], [], [], []
        directory = self.root / "measured"
        directory.mkdir()
        # All command/log allocation is outside the build-only counters.
        for row in self.rows:
            member = directory / ("c%02d" % row["index"])
            member.mkdir()
            argv = build_command(self.config, self.paths, row, member)
            fixture.save_new(member / "command.json", {"argv": argv, "cwd": row["workspace"]})
            processes.append({"row": row, "directory": member, "argv": argv})
        stream = (directory / "samples.jsonl").open("x", encoding="utf-8")

        def scope_read(label, *, independent=False):
            before, cpu = time.monotonic_ns(), time.thread_time_ns()
            value = (m1_resources.read_cgroup_snapshot(expected_cgroup=self.scope["membership"],
                     expected_identity=scope_start["directory_identity"]) if independent
                     else sampler.cgroup_snapshot())
            reads.append({"label": label, "thread_cpu_ns": time.thread_time_ns() - cpu,
                          "duration_ns": time.monotonic_ns() - before, "snapshot": value})
            return value

        def sample(label):
            cpu = time.thread_time_ns()
            mappings = self.discover(self.rows)
            for key, mapping in mappings.items():
                self.observed.setdefault(key, mapping)
            value = sampler.sample(mappings)
            value["discovery_errors"] = dict(self.discovery_errors)
            value["discovery_and_sample_thread_cpu_ns"] = time.thread_time_ns() - cpu
            value["label"] = label
            value["engine_mapping_status"] = "provisional_until_post_window_info"
            samples.append(value)
            stream.write(json.dumps(value) + "\n")
            stream.flush()
            return value

        build_end, executor = None, None
        completed, completion_lock, frozen = threading.Event(), threading.Lock(), {}
        try:
            initial = sample("initial")
            if self.config["state"] == "restart":
                self.require_absent(self.rows)
            else:
                current = self.discover(self.rows)
                if not initial["eligible"] or current != self.verified:
                    raise ValueError("Warm initial process endpoint differs from prepared engines")
                self.check_process_count(current)
            scope_start = scope_read("build-start")
            if not scope_start["eligible"]:
                raise ValueError("Build-start scope counters unavailable")
            start = time.monotonic_ns()
            boot_start = time.clock_gettime(time.CLOCK_BOOTTIME)
            deadline = time.monotonic() + self.config["timeout_seconds"]
            cadence, next_sample = self.config["sample_interval_seconds"], time.monotonic()
            for member in processes:
                member["stdout"] = (member["directory"] / "stdout.log").open("xb")
                member["stderr"] = (member["directory"] / "stderr.log").open("xb")
                member["started_ns"] = time.monotonic_ns()
                member["process"] = subprocess.Popen(member["argv"], cwd=member["row"]["workspace"],
                    env=self.env, stdout=member["stdout"], stderr=member["stderr"])
                self.history.append({"phase": "measured", "index": member["row"]["index"], "command": "build"})

            def wait_for_client(member):
                # Blocking wait is independent of PSS I/O. The main deadline and
                # finally block own bounded termination, not a polling wait timeout.
                code = member["process"].wait()
                result = {"exit_code": code, "timed_out": False,
                          "started_ns": member["started_ns"], "exited_ns": time.monotonic_ns()}
                with completion_lock:
                    member["result"] = result
                    if code == 0:
                        self.admitted.add(str(member["row"]["index"]))
                    if all("result" in item for item in processes):
                        frozen["clients_exited"] = max(item["result"]["exited_ns"] for item in processes)
                        frozen["scope_end"] = scope_read("build-end", independent=True)
                        frozen["boundary_ns"] = time.monotonic_ns()
                        fixture.save_new(directory / "build-boundary.json", {
                            "started_ns": start, "clients_exited_observed_ns": frozen["clients_exited"],
                            "counters_frozen_ns": frozen["boundary_ns"], "scope_start": scope_start,
                            "scope_end": frozen["scope_end"]})
                        completed.set()
                return result

            executor = ThreadPoolExecutor(max_workers=len(processes), thread_name_prefix="m1-client-wait")
            waiters = [executor.submit(wait_for_client, member) for member in processes]
            while not completed.is_set():
                for future in waiters:
                    if future.done():
                        future.result()  # Propagate waiter/read failures before postprocessing.
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError("Measured clients exceeded the fixed timeout")
                if now >= next_sample and not completed.is_set():
                    sample("periodic")
                    next_sample += (int((time.monotonic() - next_sample) // cadence) + 1) * cadence
                time.sleep(min(0.01, max(0.001, next_sample - time.monotonic())))
            for future in waiters:
                future.result()
            clients_exited, build_end = frozen["clients_exited"], frozen["scope_end"]
            final = sample("final-after-build-boundary")
            stream.close()
            for member in processes:
                member["stdout"].close()
                member["stderr"].close()
                fixture.save_new(member["directory"] / "process-result.json", member["result"])
            if any(member["result"]["exit_code"] for member in processes):
                raise ValueError("Measured build failed; do not issue identity calls for failed engines")
            for row in self.rows:
                old = self.observed.get(str(row["index"]))
                confirmed = self.identity(row, "measured-identity")
                if old is None or old.identity != confirmed.identity:
                    raise ValueError("Post-window info changed the observed daemon identity")
            self.check_process_count(self.verified)
            if not final["eligible"] or any(not s["identity_continuity_valid"] or s["discovery_errors"]
                    or not s["membership_continuity_valid"] or not s["cgroup"]["eligible"] for s in samples):
                raise ValueError("Measured process accounting is incomplete")
            seen_complete = self.config["state"] == "warm"
            for observation in samples[1:-1]:
                if not observation["eligible"]:
                    # Only absent not-yet-started mappings may be pending at startup.
                    errors = [error for e in observation["engines"].values() for error in e["errors"].values()]
                    if seen_complete or self.config["state"] != "restart" or any(e != "MissingEngine" for e in errors):
                        raise ValueError("Resource sample read/mapping failure")
                else:
                    seen_complete = True
            if self.config["state"] == "restart":
                if any(p["start_ticks"] < int(boot_start * final["clock_ticks_per_second"])
                       for p in final["processes"]):
                    raise ValueError("Restarted JVM predates the build boundary")
            daemon_cpu = cpu_delta(initial, final, self.config["state"])
            build_cpu = scope_delta(scope_start, build_end)
            for member in processes:
                results.append(verify_build(member["row"], member["result"], member["directory"],
                                            self.paths, oracles, self.reference_only,
                                            self.verified[str(member["row"]["index"])]))
            concurrency = cohort_bep_proof(results, self.rows)
            end_to_end = scope_read("end-to-end")
            return {"passed": True, "results": results, "scope_reads": reads,
                    "build_scope_cpu_seconds": build_cpu,
                    "end_to_end_scope_cpu_seconds": scope_delta(scope_start, end_to_end),
                    "bep_concurrency": concurrency,
                    "daemon_cpu_seconds": daemon_cpu,
                    "daemon_cpu_endpoint_note": "Process endpoints bracket scope endpoints; final PSS/CPU sampling follows build-only scope freeze. Raw sample timing bounds this skew.",
                    "exit_observation_note": "Independent blocking waiters observe exits and freeze scope counters; timestamps are user-space observations, not kernel exit instants.",
                    "build_clients_wall_seconds": (clients_exited - start) / 1e9,
                    "end_to_end_wall_seconds": (time.monotonic_ns() - start) / 1e9,
                    "sampled_peak_daemon_pss_bytes": max(s["pss_bytes"] for s in samples if s["pss_bytes"] is not None),
                    "observer_sample_thread_cpu_ns": sum(s["discovery_and_sample_thread_cpu_ns"] for s in samples),
                    "observer_scope_thread_cpu_ns": sum(r["thread_cpu_ns"] for r in reads),
                    "samples_path": str(directory / "samples.jsonl"), "samples": len(samples),
                    "initial": initial, "final": final, "engine_mappings_verified_after_window": True}
        finally:
            for member in processes:
                process = member.get("process")
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                for name in ("stdout", "stderr"):
                    if name in member:
                        member[name].close()
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            stream.close()
            fixture.save_new(directory / "partial-accounting.json", {
                "scope_reads": reads, "build_endpoint_captured": build_end is not None,
                "client_results": [p.get("result") for p in processes]})


def run(config, reference_only=False):
    root = validate_manifest(config, reference_only)
    scope = attest_scope(config)
    if fixture.sha256(config["binary"]["path"]) != config["binary"]["sha256"]:
        raise ValueError("Pinned candidate binary changed")
    root.mkdir()  # No reuse, deletion, reset or automatic repair after a partial run.
    fixture.save_new(root / "manifest.json", config)
    report = {"schema_version": 1, "run_root": str(root), "hosting": config["hosting"],
              "clients": config["clients"], "state": config["state"], "scenario": config["scenario"],
              "sample_interval_seconds": config["sample_interval_seconds"],
              "binary_manifest": config["binary"],
              "reference_only": reference_only, "performance_eligible": False, "passed": False,
              "requires_successful_final_host_guard": True, "scope": scope,
              "metric_notes": ["Scope memory.peak includes fixture creation and serial preparation.",
                  "Sampled daemon PSS includes initial/final reads and is not retained heap or whole-scope memory.",
                  "Build-only scope CPU freezes before final PSS sampling, info, BEP parsing, hashes and behavior checks.",
                  "End-to-end includes those checks; final shutdown/source checks are outside both windows.",
                  "Sample read CPU is diagnostic; JSON/log writing remains in raw scope CPU. No observer subtraction.",
                  "Native is the current implementation bundle including action coalescing and different modules/interner policy."],
              "manifest_sha256": fixture.sha256(root / "manifest.json"),
              "candidate_manifest_sha256": digest(config["binary"]),
              "binary_sha256": config["binary"]["sha256"], "history_contract": HISTORY,
              "comparison_policy_sha256": digest({"policy": POLICY, "fixture_flags": fixture.FLAGS,
                  "source_seed": config["repository_seed"]["files"], "history": HISTORY,
                  "temporary_policy": "per-arm-shared-tmp-and-java-v1", "heap_per_engine_mib": 768}),
              "harness_sha256": {Path(module.__file__).name: fixture.sha256(module.__file__)
                  for module in (sys.modules[__name__], baseline, fixture, m1_resources)}}
    arm = None
    lifecycle_start = None
    previous_temp = {name: os.environ.get(name) for name in ("TMP", "TMPDIR")}
    try:
        lifecycle_start = m1_resources.read_cgroup_snapshot(expected_cgroup=scope["membership"])
        report["lifecycle_scope_start"] = lifecycle_start
        if (not lifecycle_start["eligible"]
                or any(lifecycle_start["memory.events"][key] for key in ("oom", "oom_kill"))):
            raise ValueError("Fresh scope accounting unavailable or already contains OOM events")
        temporary = root / "temp/tmp"
        temporary.mkdir(parents=True)
        os.environ.update(TMP=str(temporary), TMPDIR=str(temporary))
        manifest = fixture.prepare(config["source_repository"], root, config["clients"], config["scenario"])
        report["fixture"] = manifest
        arm = Arm(config, manifest, scope, reference_only)
        for name in ("user", "tmp", "java", "home", "disk"):
            arm.paths[name].mkdir(parents=True, exist_ok=True)
        seed_repository(config["repository_seed"], arm.paths["repository"])
        report["environment_sha256"] = digest(arm.env)
        report["paths"] = {name: {k: str(v) for k, v in value.items()} if isinstance(value, dict)
                           else str(value) for name, value in arm.paths.items()}
        oracles = {}
        if not reference_only:
            reference = checked_document(config["oracle_report"])
            validate_reference_guard(checked_document(config["oracle_report"]["guard_result"]), reference)
            oracles = validate_reference(reference, manifest, config, report["comparison_policy_sha256"])
        arm.require_absent(arm.rows)
        report["preparation"] = arm.prepare(oracles)
        # Preparation includes one successful build and two identity calls per engine.
        report["measured"] = arm.measured(oracles)
        report["passed"] = report["measured"]["passed"]
    except Exception as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    finally:
        if arm is not None:
            try:
                report["shutdown"] = arm.shutdown(arm.rows, "final-shutdown")
                for row in arm.rows:
                    baseline.require_fixture_tree(row["workspace"], arm.manifest["private_tree"])
                    if fixture.source_state(row["workspace"]) != row["source_state"]:
                        raise ValueError("Fixture source state changed")
                if fixture.sha256(config["binary"]["path"]) != config["binary"]["sha256"]:
                    raise ValueError("Candidate binary changed during run")
                report["final_scope_attestation"] = attest_scope(config, require_fresh_preflight=False)
                for name in ("guard_policy_sha256", "scope_identity_sha256", "host_preflight_sha256"):
                    if report["final_scope_attestation"][name] != scope[name]:
                        raise ValueError("Runtime guard evidence changed during the arm: " + name)
                report["owned_daemons_absent"] = True
                report["source_and_binary_unchanged"] = True
            except Exception as error:
                report["cleanup_error"] = type(error).__name__ + ": " + str(error)
                report["passed"] = False
            report["history"] = arm.history
        if lifecycle_start is not None:
            try:
                final_scope = m1_resources.read_cgroup_snapshot(expected_cgroup=scope["membership"],
                    expected_identity=lifecycle_start["directory_identity"])
                report["lifecycle_scope_end"] = final_scope
                report["lifecycle_scope_cpu_seconds"] = scope_delta(lifecycle_start, final_scope)
            except Exception as error:
                report["lifecycle_accounting_error"] = type(error).__name__ + ": " + str(error)
                report["passed"] = False
        # External guard final absence/host-safety checks are not yet known here.
        report["internally_ready_for_performance_review"] = report["passed"] and not reference_only
        fixture.save_new(root / "report.json", report)
        for name, value in previous_temp.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--reference-only", action="store_true")
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Live comparison is Linux-only; offline unit tests require no Linux runtime")
    config = load_json(absolute(args.manifest))
    try:
        validate_launch(config["launch_argv"], sys.orig_argv, Path(__file__).resolve(), sys.argv[1:])
    except ValueError as error:
        parser.error(str(error))
    report = run(config, args.reference_only)
    print(json.dumps({"report": str(Path(config["run_root"]) / "report.json"), "passed": report["passed"],
                      "performance_eligible": False, "requires_successful_final_host_guard": True}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
