"""Pinned Datadog network profile normalizer and independent build references."""

import argparse
from functools import lru_cache
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
import zipfile

from bazel_stress_metrics import hash_target_outputs, read_metrics
from datadog_build_stress import logged_spawns
from datadog_cases import REVISION, clean_env, git
import datadog_stress_target as fixture


PACKAGE = "pkg/networkdevice/profile/profiledefinition"
COMMAND = PACKAGE + "/normalize_cmd/cmd"
TARGET = "//" + PACKAGE + "/normalize_cmd:normalize_cmd"
FLAGS = (*fixture.BUILD_FLAGS, "--@rules_go//go/config:pure=true")
VARIANTS = ("base", "source", "build")


@lru_cache(maxsize=1)
def package_archive(repo):
    return git(repo, "archive", "--format=zip", REVISION, "--", PACKAGE)


def materialize(repo, workspace, variant):
    if variant not in VARIANTS:
        raise ValueError(variant)
    fixture.materialize(repo, workspace, "base")
    with zipfile.ZipFile(io.BytesIO(package_archive(repo))) as archive:
        if any(item.external_attr >> 28 == 0xA for item in archive.infolist()):
            raise ValueError("Unexpected symlink in pinned profile package")
        archive.extractall(workspace)
    if variant == "source":
        source = workspace / COMMAND / "root.go"
        contents = source.read_bytes()
        before = b'filename = name + ".json"'
        if contents.count(before) != 1:
            raise ValueError("Unexpected source at pinned revision")
        source.write_bytes(contents.replace(before, b'filename = name + ".source.json"'))
    if variant == "build":
        build = workspace / COMMAND / "BUILD.bazel"
        contents = build.read_text(encoding="utf-8")
        before = 'srcs = ["root.go"],'
        if contents.count(before) != 1:
            raise ValueError("Unexpected BUILD file at pinned revision")
        command = """sed 's/name + "[.]json"/name + ".build.json"/' $(location root.go) > $@"""
        contents = contents.replace(before, 'srcs = [":overlay_root"],')
        contents += ('\ngenrule(name = "overlay_root", srcs = ["root.go"], '
                     'outs = ["overlay_root.go"], cmd = ' + json.dumps(command) + ')\n')
        build.write_text(contents, encoding="utf-8")


def verify_program(executable, variant, scratch):
    scratch.mkdir(parents=True)
    out = scratch / "out"
    out.mkdir()
    profile = scratch / "switch.yaml"
    profile.write_text("name: test-switch\ndescription: Switch profile\nsysobjectid: 1.3.6.1.4.1.9\n",
                       encoding="utf-8")
    subprocess.run([str(executable), "--json", "--outdir", str(out), str(profile)],
                   cwd=scratch, check=True, capture_output=True, timeout=30)
    name = {"base": "switch.json", "source": "switch.source.json", "build": "switch.build.json"}[variant]
    if [p.name for p in out.iterdir()] != [name]:
        raise AssertionError(f"Unexpected output files for {variant}: {list(out.iterdir())}")
    data = json.loads((out / name).read_bytes())
    expected = {"name": "test-switch", "description": "Switch profile", "sysobjectid": ["1.3.6.1.4.1.9"]}
    if any(data.get(k) != v for k, v in expected.items()):
        raise AssertionError(f"Unexpected normalized profile: {data!r}")
    return {"filename": name, "profile": data}


def build_reference(args, root, variant, ordinal, env):
    workspace = root / f"w{ordinal}"
    base = root / f"o{ordinal}"
    materialize(args.repo, workspace, variant)
    bep, spawns = root / f"b{ordinal}.jsonl", root / f"s{ordinal}.json"
    argv = [args.bazel, "--batch", "--ignore_all_rc_files", "--host_jvm_args=-Xmx768m",
            f"--output_user_root={root/'install'}", f"--output_base={base}", "build", TARGET,
            *FLAGS, "--jobs=4", "--disk_cache=", "--noshow_progress",
            f"--build_event_json_file={bep}", f"--execution_log_json_file={spawns}"]
    if args.repository_cache:
        argv.append(f"--repository_cache={args.repository_cache.resolve()}")
    started = time.monotonic()
    with (root / f"b{ordinal}.log").open("wb") as log:
        run = subprocess.run(argv, cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
                             timeout=args.timeout)
    row = dict(variant=variant, exit=run.returncode, client_s=round(time.monotonic()-started, 3))
    if run.returncode:
        row["diagnostic"] = (root / f"b{ordinal}.log").read_text(encoding="utf-8", errors="replace")[-5000:]
        return row
    metrics = read_metrics(bep)
    row.update({k: metrics[k] for k in ("packages_loaded", "targets_configured", "actions_executed",
                                        "reported_executed_spawns", "runner_counts")})
    outputs = hash_target_outputs(bep, TARGET)
    if len(outputs) != 1:
        raise ValueError(outputs)
    name, artifact = next(iter(outputs.items()))
    exe, = (base / "execroot/_main/bazel-out").glob("*/bin/" + name)
    row.update(output=name, **artifact, program_output=verify_program(exe, variant, root / f"proof{ordinal}"),
               spawns=logged_spawns(spawns))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bazel", default="bazel")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--repository-cache", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args()
    args.repo = args.repo.resolve()
    args.bazel = shutil.which(args.bazel)
    if not args.bazel:
        parser.error("Bazel unavailable")
    env = clean_env()
    env["USE_BAZEL_VERSION"] = "9.1.0"
    if os.name == "nt":
        env["BAZEL_SH"] = str(Path(env.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe")
    version = subprocess.check_output([args.bazel, "--version"], env=env, text=True).strip()
    if version != "bazel 9.1.0":
        raise ValueError(version)
    report = dict(revision=REVISION, target=TARGET, host=platform.platform(), bazel=version,
                  flags=FLAGS, builds=[], passed=False)
    try:
        with tempfile.TemporaryDirectory(prefix="dd-ref-") as directory:
            root = Path(directory)
            for i, variant in enumerate(VARIANTS):
                print(f"Qualifying {variant} in {root}", flush=True)
                row = build_reference(args, root, variant, i, env)
                report["builds"].append(row)
                if row["exit"]:
                    raise RuntimeError(row["diagnostic"])
            if len({b["sha256"] for b in report["builds"]}) != len(VARIANTS):
                raise AssertionError("Overlay binaries did not differ")
            report["passed"] = True
    finally:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
