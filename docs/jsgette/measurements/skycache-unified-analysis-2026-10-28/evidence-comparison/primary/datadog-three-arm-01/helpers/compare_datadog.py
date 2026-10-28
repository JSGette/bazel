#!/usr/bin/env python3
"""Native, three-arm Datadog comparison; preserves failed commands as evidence.

Every command is a fresh batch JVM/output base. The first three cold commands and
two restored-cache smoke commands are excluded from the optional 18-command
balanced matrix. Ordinary remote action-cache reads are identical in every arm;
uploads and network Skycache are disabled. No fixture source is written.

Example (the provenance and archived inputs are local, unpublished prerequisites):
  python3 compare_datadog.py --provenance /path/three-arm-baseline-provenance.json \
    --run-dir /path/new-run --rounds 0
Use --rounds 6 for all six permutations after the same smoke gate succeeds.
"""

import argparse
import hashlib
import itertools
import json
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import url2pathname

import paired_baseline as paired

REPOS = Path("/home/jgett/REPOS")
PRIOR = REPOS / ".work/skycache-retained-chunks-2026-10-25/agent-matrix"
PRELIMINARY = REPOS / ".work/skycache-preliminary-2026-10-26"
WORKSPACE = REPOS / ".work/skycache-large-remote-2026-10-25/agent/worktree"
JDK = REPOS / ".tools/skycache-benchmark-jdk25"
ARMS = ("vanilla", "fork-off", "fork-on")
TARGETS = tuple(f"//cmd/{name}:{name}" for name in
                ("agent", "cluster-agent", "process-agent", "trace-agent",
                 "security-agent", "system-probe"))
PRIMARY_OUTPUTS = {f"cmd/{name}/{name}_/{name}" for name in
                   (target.rsplit(":", 1)[1] for target in TARGETS)}
DIAGNOSTICS = ("local-skycache.json", "local-skycache-validation.json",
               "local-skycache-ownership.json", "local-skycache-phases.json")
# These options are supplied explicitly by this experiment, or refer to old
# output bases/endpoints. All remaining expanded canonical options are shared.
CONTROL_OPTIONS = {
    "config", "jobs", "local_resources", "lockfile_mode", "color", "curses",
    "disk_cache", "remote_cache", "remote_executor", "remote_instance_name",
    "remote_timeout", "remote_retries", "remote_local_fallback",
    "remote_upload_local_results", "remote_accept_cached", "bes_backend",
    "bes_results_url", "symlink_prefix", "build_event_json_file", "profile",
    "remote_download_outputs", "remote_grpc_log", "repository_cache",
    "repo_contents_cache", "remote_max_connections",
    "remote_max_concurrency_per_connection", "build_event_json_file_path_conversion",
    "experimental_disk_cache_gc_max_size", "disk_cache_gc_max_size",
    "disk_cache_gc_max_age", "skip_incompatible_explicit_targets", "build",
}
SAFE_LITERAL_ENV = {
    "GO_REPOSITORY_USE_HOST_MODCACHE", "GOTOOLCHAIN", "REPIN",
    "BAZEL_TOOLCHAINS_MSVC_ACCEPT_MICROSOFT_VISUAL_STUDIO_BUILDTOOLS_EULA",
    "JAVA_HOME",
}


def digest(path):
    return paired.digest(path)


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def events(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def canonical_flags(bep):
    """Reuse expanded build configuration without importing an ambient bazelrc."""
    canonical = [e["structuredCommandLine"] for e in events(bep)
                 if e.get("structuredCommandLine", {}).get("commandLineLabel") == "canonical"]
    if len(canonical) != 1:
        raise ValueError("expected exactly one archived canonical command line")
    result = []
    for section in canonical[0].get("sections", []):
        if section.get("sectionLabel") != "command options":
            continue
        for option in section.get("optionList", {}).get("option", []):
            flag = option.get("combinedForm", "")
            name = option.get("optionName") or flag.removeprefix("--").split("=", 1)[0]
            if (name in CONTROL_OPTIONS or name.startswith(("credential_", "experimental_local_skycache",
                    "experimental_remote_skycache", "experimental_remote_analysis_cache",
                    "experimental_analysis_cache", "experimental_active_directories"))):
                continue
            if name == "override_repository":
                if option.get("optionValue", "").startswith("bazel_tools="):
                    continue
                raise ValueError("unexpected historical repository override")
            if any(word in name.lower() for word in ("header", "auth", "token", "password")):
                raise ValueError("unexpected credential-bearing option; review privately")
            if name in ("repo_env", "action_env", "test_env"):
                value = option.get("optionValue", flag.split("=", 1)[-1])
                key, separator, _ = value.partition("=")
                if separator and key not in SAFE_LITERAL_ENV:
                    raise ValueError(f"unreviewed literal environment option: {key}")
            if not flag.startswith("--"):
                raise ValueError("unsupported archived option form")
            result.append(flag)
    if "--//:release=true" not in result:
        raise ValueError("archived command lacks the expected expanded release setting")
    return result


def environment(jfr, bep, root):
    """Restore only declared inherited inputs; never persist their secret values."""
    captured = subprocess.run([str(JDK / "bin/jfr"), "print", "--json", "--events",
                               "jdk.InitialEnvironmentVariable", str(jfr)],
                              check=True, capture_output=True, text=True)
    values = {e["values"]["key"]: e["values"]["value"]
              for e in json.loads(captured.stdout)["recording"]["events"]}
    inherited = {"PATH", "BZLMOD_ALLOW_YANKED_VERSIONS"}
    for event in events(bep):
        for section in event.get("structuredCommandLine", {}).get("sections", []):
            for option in section.get("optionList", {}).get("option", []):
                value = option.get("optionValue", "")
                if option.get("optionName") in ("repo_env", "action_env", "test_env"):
                    if value and "=" not in value:
                        inherited.add(value)
    # HOME and locale are ordinary process context. Everything else is an
    # explicit declared repository/action input restored from the prior run.
    env = {name: os.environ[name] for name in ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL")
           if name in os.environ}
    env.update({name: values[name] for name in inherited if name in values})
    if not env.get("PATH"):
        raise ValueError("the archived environment lacks PATH")
    env.update(TMPDIR=str(root / "tmp"), DD_BAZEL_REMOTE_CACHE="off")
    # tools/bazel supplied these defaults in the prior benchmark. Invoke the
    # native binaries directly here, with the same explicit repository inputs.
    env.setdefault("GOCACHE", str(Path(env["HOME"]) / ".cache/go-build"))
    env.setdefault("GOMODCACHE", str(Path(env["HOME"]) / "go/pkg/mod"))
    env.pop("XDG_CACHE_HOME", None)
    return env, {"restored_names": sorted(inherited),
                 "present_names": sorted(env), "secret_values_recorded": False,
                 "path_sha256": hashlib.sha256(env["PATH"].encode()).hexdigest()}


def workspace_state(workspace):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(workspace), *args])
    untracked = {}
    for raw in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if raw:
            name = os.fsdecode(raw)
            path = workspace / name
            untracked[name] = ({"symlink": os.readlink(path)} if path.is_symlink()
                               else {"sha256": digest(path)})
    return {"head": git("rev-parse", "HEAD").decode().strip(),
            "status": git("status", "--porcelain=v1").decode(),
            "tracked_diff_sha256": hashlib.sha256(git("diff", "--binary", "HEAD")).hexdigest(),
            "index_sha256": hashlib.sha256(git("ls-files", "--stage", "-z")).hexdigest(),
            "module_lock_sha256": digest(workspace / "MODULE.bazel.lock"),
            "untracked": untracked}


def command(binary, arm, root, run, flags, cache, repository_cache=None):
    argv = [str(binary), "--batch", "--ignore_all_rc_files",
            f"--output_user_root={root / 'bazel-user'}",
            f"--output_base={run / 'output-base'}", "--host_jvm_args=-Xmx6g",
            f"--host_jvm_args=-Xlog:gc*:file={run / 'gc.log'}:time,uptime,level,tags",
            "build", *flags, "--jobs=6", "--local_resources=memory=6144",
            "--lockfile_mode=off", "--color=no", "--curses=no", "--symlink_prefix=/",
            "--noskip_incompatible_explicit_targets", "--remote_executor=", "--disk_cache=",
            "--remote_cache=grpc://10.55.0.1:8080", "--remote_instance_name=",
            "--remote_timeout=600", "--remote_retries=1", "--noremote_local_fallback",
            "--remote_upload_local_results=false", "--remote_accept_cached",
            "--remote_download_outputs=toplevel", "--remote_max_connections=1",
            "--remote_max_concurrency_per_connection=16", "--bes_backend=", "--bes_results_url=",
            "--experimental_remote_analysis_cache_mode=off",
            f"--repository_cache={repository_cache or root / 'repository-cache'}",
            f"--repo_contents_cache={root / 'repository-contents'}",
            "--build_event_json_file_path_conversion=false",
            f"--build_event_json_file={run / 'build.bep.json'}", f"--profile={run / 'build.profile.gz'}"]
    if arm != "vanilla":
        argv += [f"--experimental_local_skycache={cache if cache else ''}",
                 "--experimental_remote_skycache=", "--experimental_analysis_cache_idle_retention=0",
                 "--experimental_local_skycache_max_publications=64",
                 "--experimental_local_skycache_max_metadata_mb=256",
                 "--experimental_local_skycache_gc_max_size=0",
                 "--experimental_local_skycache_gc_max_age=0"]
    return argv + ["--", *TARGETS]


def output_manifest(bep, output_base):
    """Hash the complete default outputs of exactly six successful target builds."""
    stream = events(bep)
    terminal = paired.validate_bep_terminal(stream)
    completed, sets, placeholders = {}, {}, []
    for event in stream:
        identity = event.get("id", {})
        if "namedSet" in identity:
            sets[identity["namedSet"]["id"]] = event.get("namedSetOfFiles", {})
        target = identity.get("targetCompleted", {})
        if target.get("label") not in TARGETS:
            continue
        configuration = target.get("configuration", {}).get("id")
        if configuration == "none" and event.get("aborted") == {} and "completed" not in event:
            placeholders.append(target)
            continue
        if (not configuration or configuration == "none" or "aborted" in event
                or event.get("completed", {}).get("success") is not True):
            raise ValueError("requested target did not successfully build")
        if target["label"] in completed:
            raise ValueError("duplicate configured target completion")
        completed[target["label"]] = event["completed"]
    if set(completed) != set(TARGETS):
        raise ValueError("BEP does not prove execution completed for all six targets")
    pending = [entry["id"] for target in completed.values()
               for group in target.get("outputGroup", []) if group.get("name") == "default"
               for entry in group.get("fileSets", [])]
    visited, result = set(), {}
    while pending:
        key = pending.pop()
        if key in visited:
            continue
        visited.add(key)
        if key not in sets:
            raise ValueError("missing referenced default output set")
        value = sets[key]
        pending.extend(entry["id"] for entry in value.get("fileSets", []))
        for item in value.get("files", []):
            uri = urlsplit(item.get("uri", ""))
            if uri.scheme != "file" or uri.netloc not in ("", "localhost"):
                raise ValueError("top-level output was not downloaded as a local file")
            path = paired.inside(Path(url2pathname(uri.path)), output_base)
            if not path.is_file():
                raise ValueError("default output is not a regular file")
            name = item.get("name", "")
            actual = {"sha256": digest(path), "bytes": path.stat().st_size}
            if item.get("digest") and item["digest"] != actual["sha256"]:
                raise ValueError("BEP digest disagrees with downloaded bytes")
            if name in result and result[name] != actual:
                raise ValueError("ambiguous default output name")
            result[name] = actual
    if set(result) != PRIMARY_OUTPUTS:
        raise ValueError("default output set differs from the six requested executable outputs")
    return {"outputs": dict(sorted(result.items())), "targets": sorted(completed),
            "terminal": terminal, "ignored_unconfigured_placeholders": placeholders}


def worker(argv, workspace, env, run, timeout):
    """Separate accounting process; never write inherited environment values."""
    import resource
    clock = lambda: time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
    start = clock()
    result = {"clock": "CLOCK_MONOTONIC_RAW", "timed_out": False}
    try:
        with (run / "stdout.log").open("wb") as stdout, (run / "stderr.log").open("wb") as stderr:
            proc = subprocess.Popen(argv, cwd=workspace, env=env, stdout=stdout,
                                    stderr=stderr, start_new_session=True)
            try:
                result["exit_code"] = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                os.killpg(proc.pid, signal.SIGKILL)
                result["exit_code"] = proc.wait()
    except OSError as error:
        result["exit_code"] = None
        result["launch_error"] = str(error)
    result["elapsed_seconds"] = clock() - start
    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    result["resources"] = {"user_cpu_seconds": usage.ru_utime,
                           "system_cpu_seconds": usage.ru_stime,
                           "max_child_rss_kib": usage.ru_maxrss,
                           "scope": "batch JVM/launcher and subprocess accounting; maximum single-child RSS, not process-tree sum or retained heap"}
    save(run / "metrics.json", result)


def validate_skycache(stats, restored, log):
    if stats.get("failed") is not False:
        raise ValueError("Skycache diagnostics are missing or report failure")
    if not restored:
        if stats.get("published") is not True:
            raise ValueError("Skycache producer did not finish a successful publication")
        return None
    hits = sum(int(value) for value in re.findall(r"CONFIGURED_TARGET: (\d+)/", log))
    if hits <= 0 or int(stats.get("reads", 0)) <= 0:
        raise ValueError("seeded consumer did not prove an analysis-cache hit")
    # A fully restored consumer may correctly publish nothing (writes=0).
    return hits


def experiment(args):
    if not sys.platform.startswith("linux") or args.rounds not in (0, 6):
        raise ValueError("Linux is required; rounds must be 0 (smoke) or 6 (all permutations)")
    root = args.run_dir.resolve()
    workspace = args.workspace.resolve(strict=True)
    if root == workspace or root.is_relative_to(workspace):
        raise ValueError("run directory must be outside the source fixture")
    root.mkdir(parents=True, exist_ok=False)
    (root / "tmp").mkdir()
    shutil.copyfile(__file__, root / "harness.py")
    shutil.copyfile(paired.__file__, root / "paired_baseline.py")
    repository_cache = root / "repository-cache"
    if args.shared_repository_cache:
        repository_cache = args.shared_repository_cache.resolve(strict=True)
        if not repository_cache.is_dir():
            raise ValueError("shared repository cache must be an existing directory")
    if args.repository_cache_seed:
        source_cache = args.repository_cache_seed.resolve(strict=True)
        destination_cache = root / "repository-cache"
        destination_cache.mkdir()
        # Reflinks or ordinary copies: never hardlink the mutable cache index.
        subprocess.run(["cp", "-a", "--reflink=auto", str(source_cache) + "/.",
                        str(destination_cache)], check=True)
    provenance = json.loads(args.provenance.read_text())
    binaries = {"vanilla": Path(provenance["vanilla"]["binary"]),
                "fork-off": Path(provenance["candidate"]["binary"]),
                "fork-on": Path(provenance["candidate"]["binary"])}
    for arm, binary in binaries.items():
        expected = provenance["vanilla" if arm == "vanilla" else "candidate"]["sha256"]
        if digest(binary) != expected:
            raise ValueError("binary checksum does not match the reviewed provenance")
    flags = canonical_flags(args.prior_bep)
    env, env_description = environment(args.environment_jfr, args.environment_bep, root)
    before = workspace_state(workspace)
    result = {"schema_version": 1, "status": "running", "workspace": str(workspace),
              "workspace_before": before, "provenance": provenance,
              "provenance_sha256": digest(args.provenance), "harness_sha256": digest(__file__),
              "paired_helper_sha256": digest(paired.__file__),
              "repository_cache_seed": str(args.repository_cache_seed) if args.repository_cache_seed else None,
              "repository_cache": str(repository_cache),
              "repository_cache_policy": ("existing shared content-addressed downloader cache; ordinary Bazel reads and missing-blob insertions permitted; no harness deletion or content reset; warmed by prior work"
                                          if args.shared_repository_cache else "new owned cache shared across all commands"),
              "canonical_flags_source": str(args.prior_bep),
              "canonical_flags_source_sha256": digest(args.prior_bep),
              "environment_jfr_sha256": digest(args.environment_jfr),
              "environment_bep_sha256": digest(args.environment_bep),
              "environment": env_description, "explicit_shared_fixture_flags": flags,
              "host": paired.host_description(),
              "targets": TARGETS, "rounds": args.rounds, "runs": [],
              "method": "Fresh batch JVM/output base; 6GiB heap and jobs=6; identical embedded JDK; shared repository/downloader caches, warm ordinary remote action-cache reads only; local persistent Skycache seeded by this native binary. Full command startup, execution and publication included. Source/page caches are not cold. First three preparation commands are order-confounded and are not an amortized comparison. No remote Skycache network cost is measured."}
    save(root / "results.json", result)
    oracle = None
    seed = None

    def verify_integrity():
        if digest(__file__) != result["harness_sha256"] or digest(paired.__file__) != result["paired_helper_sha256"]:
            raise ValueError("measurement helper changed during the experiment")
        for arm in ("vanilla", "fork-on"):
            binary = binaries[arm]
            expected = provenance["vanilla" if arm == "vanilla" else "candidate"]["sha256"]
            if digest(binary) != expected:
                raise ValueError("native binary changed during the experiment")

    def run(name, arm, preparation=False, restore=False):
        nonlocal oracle, seed
        if workspace_state(workspace) != before:
            raise ValueError("source fixture changed before the next command")
        verify_integrity()
        directory = root / name
        directory.mkdir()
        cache = directory / "cache" if arm == "fork-on" else None
        if restore:
            shutil.copytree(seed, cache)
        elif cache:
            cache.mkdir()
        argv = command(binaries[arm], arm, root, directory, flags, cache, repository_cache)
        row = {"name": name, "arm": arm, "preparation": preparation,
               "seeded": restore, "argv": argv, "status": "running"}
        result["runs"].append(row)
        save(directory / "command.json", {"argv": argv, "cwd": str(workspace),
                                           "timeout_seconds": args.timeout,
                                           "environment": env_description})
        save(root / "results.json", result)
        process = multiprocessing.get_context("fork").Process(
            target=worker, args=(argv, workspace, env, directory, args.timeout))
        process.start()
        process.join(args.timeout + 45)
        if process.is_alive():
            # Do not terminate an unidentified descendant server; batch work is
            # owned by the worker's process group and its watchdog above.
            raise RuntimeError("accounting worker did not exit after its watchdog")
        if not (directory / "metrics.json").exists():
            row["status"] = "failed"
            row["failure"] = f"accounting worker exited {process.exitcode} without metrics"
            raise RuntimeError(row["failure"])
        row["metrics"] = json.loads((directory / "metrics.json").read_text())
        for filename in DIAGNOSTICS:
            path = directory / "output-base" / filename
            if path.exists():
                shutil.copyfile(path, directory / filename)
                row[filename] = json.loads(path.read_text())
        stream_path = directory / "build.bep.json"
        if stream_path.exists():
            try:
                row["build_metrics"] = next((e["buildMetrics"] for e in events(stream_path)
                                              if "buildMetrics" in e), {})
                row["action_runner_counts"] = row["build_metrics"].get("actionSummary", {}).get("runnerCount")
            except (ValueError, KeyError) as error:
                row["bep_parse_error"] = str(error)
        try:
            verify_integrity()
            if row["metrics"]["exit_code"] != 0 or row["metrics"]["timed_out"]:
                raise ValueError("native command failed or timed out")
            validation = output_manifest(stream_path, directory / "output-base")
            save(directory / "outputs.json", validation)
            if oracle is None:
                if arm != "vanilla":
                    raise ValueError("only the initial vanilla build may define the output oracle")
                oracle = validation["outputs"]
                save(root / "expected-outputs.json", oracle)
            elif validation["outputs"] != oracle:
                raise ValueError("full default-output bytes differ from the vanilla oracle")
            row["outputs_match"] = True
            if workspace_state(workspace) != before:
                raise ValueError("source fixture changed during this command")
            if arm == "fork-on":
                stats = row.get("local-skycache.json", {})
                log = (directory / "stdout.log").read_text() + (directory / "stderr.log").read_text()
                row["configured_target_cache_hits"] = validate_skycache(stats, restore, log)
                if not restore:
                    seed = cache
            row["status"] = "passed"
        except (ValueError, OSError, KeyError) as error:
            row["status"] = "failed"
            row["failure"] = str(error)
            raise
        finally:
            row["checksums"] = {path.name: digest(path) for path in sorted(directory.iterdir())
                                if path.is_file()}
            save(root / "results.json", result)
            print(json.dumps({key: row[key] for key in ("name", "arm", "status", "metrics")}), flush=True)

    try:
        for arm in ARMS:
            run("smoke-" + arm, arm, preparation=True)
        for index in range(2):
            run(f"smoke-restore-{index + 1}", "fork-on", preparation=True, restore=True)
        result["smoke_gate"] = "passed"
        for index, order in enumerate(itertools.permutations(ARMS) if args.rounds else ()):
            for arm in order:
                run(f"r{index + 1}-{arm}", arm, restore=arm == "fork-on")
        result["status"] = "passed"
    except Exception as error:
        result["status"] = "failed"
        result["failure"] = str(error)
        raise
    finally:
        result["workspace_after"] = workspace_state(workspace)
        result["source_unchanged"] = result["workspace_after"] == before
        if not result["source_unchanged"]:
            result["status"] = "failed"
            result["source_failure"] = "fixture changed during experiment; no restoration attempted"
        save(root / "results.json", result)
    if result["status"] != "passed":
        raise ValueError("comparison did not pass all gates")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provenance", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--workspace", type=Path, default=WORKSPACE)
    parser.add_argument("--prior-bep", type=Path, default=PRIOR / "r1-minimal-vanilla.bep.json")
    parser.add_argument("--environment-jfr", type=Path, default=PRELIMINARY / "diagnostic-remote.jfr")
    parser.add_argument("--environment-bep", type=Path, default=PRELIMINARY / "r1-remote.bep.json")
    parser.add_argument("--rounds", type=int, choices=(0, 6), default=0)
    parser.add_argument("--timeout", type=int, default=600)
    cache_group = parser.add_mutually_exclusive_group()
    cache_group.add_argument("--repository-cache-seed", type=Path,
                             help="Copy/reflink a bounded downloader cache into the owned shared cache before timing")
    cache_group.add_argument("--shared-repository-cache", type=Path,
                             help="Use an existing content-addressed downloader cache for all arms; missing blobs may be inserted, no deletion/reset")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 600:
        parser.error("timeout must be between 1 and 600 seconds")
    experiment(args)


if __name__ == "__main__":
    main()
