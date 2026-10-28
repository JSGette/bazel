#!/usr/bin/env python3
"""Small, stdlib-only paired local-Skycache experiment. See README.md."""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname

HERE = Path(__file__).resolve().parent
ARMS = ("cache-off", "current-cache")
SCHEMA_VERSION = 1
SETTINGS = {"local_max_publications": 64, "local_max_metadata_mb": 256,
            "local_gc_max_size": "5G", "local_gc_max_age": "7d",
            "local_gc_idle_delay": "30s", "analysis_idle_retention": "0",
            "remote_chunk_cache_size": "5G", "remote_skycache": "disabled",
            "ordinary_remote_and_disk_action_cache": "disabled", "jvm_mode": "batch"}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"path escapes owned directory: {path}")
    return path


def inventory(root):
    """Only ordinary files: fixture symlinks could escape the copied input boundary."""
    result = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_symlink():
            raise ValueError(f"input symlink is unsupported: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path)
    return result


def balanced_order(pairs):
    if pairs < 2 or pairs % 2:
        raise ValueError("pairs must be a positive even count of at least 2 (AB/BA)")
    return [(pair, arm) for pair in range(pairs)
            for arm in (ARMS if pair % 2 == 0 else tuple(reversed(ARMS)))]


def host_description():
    result = {"platform": platform.platform(), "python": sys.version,
              "cpu_count": os.cpu_count(), "cpu_model": platform.processor() or None}
    cpuinfo, meminfo = Path("/proc/cpuinfo"), Path("/proc/meminfo")
    if cpuinfo.exists():
        result["cpu_model"] = next((line.split(":", 1)[1].strip()
                                    for line in cpuinfo.read_text().splitlines()
                                    if line.startswith("model name")), None)
    if meminfo.exists():
        result["linux_memtotal_bytes"] = next(int(line.split()[1]) * 1024
                                              for line in meminfo.read_text().splitlines()
                                              if line.startswith("MemTotal:"))
    if hasattr(os, "sched_getaffinity"):
        result["cpu_affinity"] = sorted(os.sched_getaffinity(0))
    return result


def copy_diagnostics(run):
    output_base = run / "output-base"
    paths = sorted(set(output_base.glob("local-skycache*.json")) |
                   set(output_base.glob("remote-skycache.json")))
    if not paths:
        return {}
    directory = run / "diagnostics"
    directory.mkdir()
    result = {}
    for path in paths:
        inside(path, output_base)
        target = directory / path.name
        shutil.copyfile(path, target)
        result[path.name] = digest(target)
    return result


def source_provenance(repo, expected_commit):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args])
    commit = git("rev-parse", "HEAD").decode().strip()
    if commit != expected_commit:
        raise ValueError(f"source commit differs: expected {expected_commit}, found {commit}")
    patch = git("diff", "--binary", "HEAD")
    untracked = {}
    for raw in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if raw:
            name = os.fsdecode(raw)
            path = Path(repo) / name
            if path.is_symlink():
                untracked[name] = {"symlink": os.readlink(path)}
            else:
                untracked[name] = {"sha256": digest(path)}
    status = git("status", "--porcelain=v1").decode()
    return {
        "repository": str(repo), "commit": commit,
        "tree": git("rev-parse", "HEAD^{tree}").decode().strip(),
        "tracked_diff_sha256": hashlib.sha256(patch).hexdigest(),
        "status": status, "source_state": "dirty" if status else "clean",
        "untracked": untracked,
        "binary_source_binding": "operator-declared; not verified by this harness",
    }, patch


def load_fixture(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    if spec.get("schema_version") != SCHEMA_VERSION or not spec.get("outputs"):
        raise ValueError("fixture requires schema_version=1 and nonempty outputs")
    targets = spec.get("targets", [])
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("fixture targets must be nonempty and unique")
    for target in targets:
        if not isinstance(target, str) or not target.startswith("//") or target.startswith("//-"):
            raise ValueError("fixture targets must be explicit workspace labels")
    for name, value in spec["outputs"].items():
        if Path(name).is_absolute() or ".." in Path(name).parts or not name:
            raise ValueError("expected output must be relative to bazel-bin")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("expected output requires lowercase SHA-256")
    return spec


def validate_bep_terminal(events):
    """Require stream completion and successful build status, not just successful targets."""
    terminal = [index for index, event in enumerate(events) if event.get("lastMessage") is True]
    if not events or terminal != [len(events) - 1]:
        raise ValueError("BEP requires exactly one lastMessage on its final event")
    finished = [event for event in events if "buildFinished" in event.get("id", {})]
    if len(finished) != 1:
        raise ValueError("BEP requires exactly one buildFinished event")
    exit_code = finished[0].get("finished", {}).get("exitCode")
    # BuildFinished.exit_code.code == 0 defines success. Proto3 JSON omits the
    # zero default (the real smoke BEP contains only {"name": "SUCCESS"}).
    if not isinstance(exit_code, dict) or exit_code.get("code", 0) != 0 or "aborted" in finished[0]:
        raise ValueError("BEP buildFinished does not prove successful exit")
    identity = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    posted = {identity(event.get("id", {})) for event in events}
    announced = {identity(child) for event in events for child in event.get("children", [])}
    if announced - posted:
        raise ValueError("BEP is missing announced child events")
    return {"last_message": True, "build_exit_code": 0, "announced_children_complete": True}


def validate_outputs(bep_path, output_base, spec):
    """Resolve only requested targets' default output groups, not arbitrary BEP files."""
    events = [json.loads(line) for line in Path(bep_path).read_text().splitlines() if line]
    terminal = validate_bep_terminal(events)
    completed, configured_targets, sets = {}, {}, {}
    placeholders = []
    for event in events:
        identity = event.get("id", {})
        if "namedSet" in identity:
            sets[identity["namedSet"]["id"]] = event.get("namedSetOfFiles", {})
        target_identity = identity.get("targetCompleted", {})
        target = target_identity.get("label")
        if target in spec["targets"]:
            configuration = target_identity.get("configuration", {}).get("id")
            if configuration == "none" and event.get("aborted") == {} and "completed" not in event:
                # Real cache-hit BEPs also emit this unconfigured, empty aborted placeholder.
                # Preserve it as evidence; it cannot satisfy target or output validation.
                placeholders.append({"identity": target_identity, "aborted": event["aborted"]})
                continue
            if not configuration or configuration == "none":
                raise ValueError(f"unsupported unconfigured target completion: {target}")
            key = json.dumps(target_identity, sort_keys=True, separators=(",", ":"))
            if key in completed or target in configured_targets:
                raise ValueError(f"duplicate configured target completion: {target}")
            value = event.get("completed", {})
            if "aborted" in event or value.get("success") is not True:
                raise ValueError(f"configured target did not complete successfully: {target}")
            completed[key] = value
            configured_targets[target] = target_identity
    if set(configured_targets) != set(spec["targets"]):
        raise ValueError("BEP does not prove every requested target completed successfully")
    pending = [item["id"] for target in completed.values()
               for group in target.get("outputGroup", []) if group.get("name") == "default"
               for item in group.get("fileSets", [])]
    files, visited = {}, set()
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        if key not in sets:
            raise ValueError("BEP references a missing named file set")
        pending.extend(item["id"] for item in sets[key].get("fileSets", []))
        for item in sets[key].get("files", []):
            uri = urlsplit(item.get("uri", ""))
            if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
                raise ValueError("fixture output must be a local file")
            path = inside(Path(url2pathname(uri.path)), output_base)
            name = item.get("name", "")
            if name in files and files[name] != path:
                raise ValueError(f"ambiguous output name: {name}")
            files[name] = path
    if set(files) != set(spec["outputs"]):
        raise ValueError(f"default output set differs: {sorted(files)}")
    actual = {name: digest(path) for name, path in files.items()}
    if actual != spec["outputs"]:
        raise ValueError("output digest mismatch")
    return {"targets": sorted(configured_targets), "configured_targets": configured_targets,
            "ignored_unconfigured_placeholders": placeholders,
            "terminal": terminal, "output_sha256": actual, "passed": True}


def worker(command_path):
    """Fresh process makes ru_maxrss per-command, not a cumulative experiment maximum."""
    command_path = Path(command_path).resolve()
    record = json.loads(command_path.read_text())
    root = command_path.parent
    for name in ("stdout.log", "stderr.log", "metrics.json"):
        inside(root / name, root)
    clock_id = getattr(time, "CLOCK_MONOTONIC_RAW", None)
    clock = (lambda: time.clock_gettime(clock_id)) if clock_id is not None else time.monotonic
    started = clock()
    result = {"clock": "CLOCK_MONOTONIC_RAW" if clock_id is not None else "monotonic",
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "timed_out": False}
    with (root / "stdout.log").open("wb") as stdout, (root / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(record["argv"], cwd=record["cwd"], env=record["environment"],
                                   stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            result["exit_code"] = process.wait(timeout=record["timeout_seconds"])
        except subprocess.TimeoutExpired:
            result["timed_out"] = True
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)  # Only the group started above.
            else:
                process.kill()
            result["exit_code"] = process.wait()
    result["elapsed_seconds"] = clock() - started
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
        result["resources"] = {
            "user_cpu_seconds": usage.ru_utime, "system_cpu_seconds": usage.ru_stime,
            "ru_maxrss_raw": usage.ru_maxrss,
            "ru_maxrss_unit": "KiB" if sys.platform.startswith("linux") else "platform-dependent",
            "maxrss_scope": "maximum child-process RSS; not simultaneous process-tree sum",
        }
    except ImportError:
        result["resources"] = {"unavailable": "resource module unavailable"}
    save(root / "metrics.json", result)


def run_command(directory, argv, workspace, environment, timeout, expected_harness_sha=None):
    script = Path(__file__).resolve()
    if expected_harness_sha is None:
        expected_harness_sha = digest(script)

    def verify_harness():
        if digest(script) != expected_harness_sha:
            raise ValueError("harness changed during the experiment")

    directory.mkdir()
    save(directory / "command.json", {
        "argv": argv, "cwd": str(workspace), "environment": environment,
        "timeout_seconds": timeout,
    })
    verify_harness()
    try:
        subprocess.run([sys.executable, str(script), "--_worker",
                        str(directory / "command.json")], check=True)
    finally:
        verify_harness()
    return json.loads((directory / "metrics.json").read_text())


def command(binary, root, run, cache, targets, heap):
    return [str(binary), "--batch", "--ignore_all_rc_files",
            f"--output_user_root={root / 'bazel-user'}", f"--output_base={run / 'output-base'}",
            f"--host_jvm_args=-Xmx{heap}m",
            f"--host_jvm_args=-Xlog:gc*:file={run / 'gc.log'}:time,uptime,level,tags",
            "build", "--color=no", "--curses=no", "--noshow_progress", "--lockfile_mode=off",
            f"--repository_cache={root / 'repository-cache'}",
            f"--symlink_prefix={root / 'links' / (run.name + '-')}",
            f"--build_event_json_file={run / 'build.bep.json'}",
            f"--profile={run / 'build.profile.gz'}",
            f"--experimental_local_skycache={cache if cache is not None else ''}",
            "--experimental_local_skycache_max_publications=64",
            "--experimental_local_skycache_max_metadata_mb=256",
            "--experimental_local_skycache_gc_max_size=5G",
            "--experimental_local_skycache_gc_max_age=7d",
            "--experimental_local_skycache_gc_idle_delay=30s",
            "--experimental_analysis_cache_idle_retention=0",
            "--experimental_remote_skycache_local_cache_size=5G",
            "--experimental_remote_skycache=", "--remote_cache=", "--disk_cache=",
            "--", *targets]


def experiment(args):
    if not sys.platform.startswith("linux"):
        raise ValueError("run the Bazel experiment on Linux/WSL; pure Python tests are portable")
    order = balanced_order(args.pairs)
    binary = args.bazel.resolve(strict=True)
    source = args.source_repo.resolve(strict=True)
    fixture = args.manifest.resolve(strict=True)
    spec = load_fixture(fixture)
    input_hashes = inventory(fixture.parent)
    provenance, patch = source_provenance(source, args.source_commit)
    root = args.run_dir.absolute()
    if root.exists() or root.is_symlink():
        raise ValueError("run directory must not already exist; the harness never deletes runs")
    if root.resolve().is_relative_to(fixture.parent):
        raise ValueError("run directory must be outside the fixture")
    root.mkdir()  # Parent must already exist. Every runtime write is below this new directory.
    root = root.resolve()
    workspace = root / "workspace"
    shutil.copytree(fixture.parent, workspace)
    for name in ("home", "tmp", "links", "runs", "caches"):
        (root / name).mkdir()
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                   "HOME": str(root / "home"), "TMPDIR": str(root / "tmp"),
                   "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"}
    if "JAVA_HOME" in os.environ:
        environment["JAVA_HOME"] = os.environ["JAVA_HOME"]
    manifest = {
        "schema_version": SCHEMA_VERSION, "source": provenance,
        "baseline": "operator-declared source snapshot; cleanliness recorded; not the paper's v8 candidate",
        "binary": {"path": str(binary), "sha256": digest(binary)},
        "harness_sha256": digest(__file__), "fixture_sha256": input_hashes,
        "fixture": spec, "environment": environment,
        "platform": host_description(), "settings": dict(SETTINGS, heap_mb=args.heap_mb),
        "order": [{"pair": pair, "arm": arm} for pair, arm in order],
        "method": "one separately timed producer excluded from paired estimates; independent seed copies; fresh batch JVM/output base per arm; shared warmed repository cache; OS pages uncontrolled",
    }
    save(root / "manifest.json", manifest)
    (root / "source.diff").write_bytes(patch)
    seed = root / "caches" / "seed"
    producer = root / "runs" / "producer"
    summary = {"schema_version": SCHEMA_VERSION, "status": "running", "runs": []}
    save(root / "summary.json", summary)
    try:
        planned = [("producer", None, "current-cache", seed)]
        planned.extend((f"p{pair:02d}-{arm}", pair, arm, root / "caches" / f"p{pair:02d}-{arm}")
                       for pair, arm in order)
        for label, pair, arm, cache in planned:
            if label != "producer" and arm == "current-cache":
                shutil.copytree(seed, cache, symlinks=False)
            run = root / "runs" / label
            argv = command(binary, root, run, cache if arm == "current-cache" else None,
                           spec["targets"], args.heap_mb)
            metrics = run_command(run, argv, workspace, environment, args.timeout_seconds,
                                  manifest["harness_sha256"])
            row = {"label": label, "pair": pair, "arm": arm,
                   "measured": label != "producer", "metrics": metrics,
                   "diagnostics_sha256": copy_diagnostics(run)}
            summary["runs"].append(row)
            save(root / "summary.json", summary)
            if metrics["exit_code"] != 0 or metrics["timed_out"]:
                raise ValueError(f"{label}: command failed or timed out")
            row["validation"] = validate_outputs(run / "build.bep.json", run / "output-base", spec)
            if inventory(workspace) != input_hashes:
                raise ValueError(f"{label}: fixture inputs changed during the experiment")
            if digest(binary) != manifest["binary"]["sha256"]:
                raise ValueError("binary changed during the experiment")
            if label == "producer" and not list(seed.rglob("*.manifest")):
                raise ValueError("producer did not publish a Skycache seed")
            row["raw_artifacts"] = {path.name: digest(path) for path in run.iterdir() if path.is_file()}
            save(root / "summary.json", summary)
        summary["paired_seconds_current_minus_off"] = [
            next(row["metrics"]["elapsed_seconds"] for row in summary["runs"]
                 if row["pair"] == pair and row["arm"] == "current-cache") -
            next(row["metrics"]["elapsed_seconds"] for row in summary["runs"]
                 if row["pair"] == pair and row["arm"] == "cache-off")
            for pair in range(args.pairs)]
        summary["status"] = "passed"
        summary["interpretation"] = "fixture correctness and paired observations only; cache hit effectiveness must be checked in raw metrics; no tail or speedup claim"
    except Exception as error:
        summary["status"] = "failed"
        summary["error"] = str(error)
        raise
    finally:
        save(root / "summary.json", summary)
    return root


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--_worker":
        worker(sys.argv[2])
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", type=Path, required=True, help="standalone Bazel binary, not bazelisk")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--source-commit", required=True, help="full expected source HEAD SHA")
    parser.add_argument("--run-dir", type=Path, required=True, help="new directory; existing parent")
    parser.add_argument("--manifest", type=Path, default=HERE / "fixture" / "manifest.json")
    parser.add_argument("--pairs", type=int, default=2)
    parser.add_argument("--heap-mb", type=int, default=2048)
    parser.add_argument("--timeout-seconds", type=float, default=120)
    args = parser.parse_args()
    if args.heap_mb < 256 or args.timeout_seconds <= 0:
        parser.error("heap must be at least 256 MiB and timeout must be positive")
    print(experiment(args))


if __name__ == "__main__":
    main()
