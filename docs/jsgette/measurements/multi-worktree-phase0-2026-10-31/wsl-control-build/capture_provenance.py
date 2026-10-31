"""Retain provenance of this local pinned-source stock build after success."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

root = Path(__file__).resolve().parent.parent
repo = root / "bazel"
control = root / "control"
base = "9a2ababbd5cf113c31450fbe9214b501be8c58a2"
evidence = repo / "docs/jsgette/measurements/multi-worktree-phase0-2026-10-31/wsl-control-build"
evidence.mkdir(exist_ok=True)


def capture(argv):
    return subprocess.check_output(argv, cwd=repo, text=True, stderr=subprocess.STDOUT).strip()


def file_record(path):
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size}


# Documentation/harness additions do not change the control's product sources.
assert capture(["git", "diff", base, "--", ".", ":(exclude)docs/jsgette"]) == ""
assert "Build completed successfully" in (control / "build.log").read_text()
shutil.copy2(repo / "bazel-bin/src/bazel-dev", control / "bazel-control")
shutil.copy2(control / "build.log", evidence / "build.log")
shutil.copy2(repo / ".bazelrc", evidence / "source.bazelrc")
shutil.copy2(repo / "MODULE.bazel.lock", evidence / "MODULE.bazel.lock")
shutil.copy2(Path(__file__), evidence / "capture_provenance.py")
server_pid = int((control / "output-base/server/server.pid.txt").read_text())
server_java = Path(f"/proc/{server_pid}/exe").resolve()
compiler_jdk = control / "output-base/external/rules_java++toolchains+remotejdk25_linux"
build_argv = [str(root / "local-tools/bazel-9.3.1"), "--nosystem_rc", "--nohome_rc",
              f"--output_base={control / 'output-base'}", "--host_jvm_args=-Xmx4g",
              "build", "//src:bazel-dev", "--jobs=6", "--local_resources=memory=8192",
              f"--repository_cache={control / 'repository-cache'}"]
record = {
    "schema_version": 1,
    "source_base": base,
    "checkout_commit": capture(["git", "rev-parse", "HEAD"]),
    "product_diff_from_base": "empty (docs/jsgette excluded)",
    "build_exit_code": 0,
    "cwd": str(repo),
    "build_argv": build_argv,
    "bootstrap": file_record(root / "local-tools/bazel-9.3.1"),
    "control": file_record(control / "bazel-control"),
    "build_server_java": {**file_record(server_java), "version": capture([str(server_java), "-version"])},
    "compiler_jdk_release": (compiler_jdk / "release").read_text(),
    "compiler_javac": file_record(compiler_jdk / "bin/javac"),
    "gcc": capture(["gcc", "--version"]),
    "g++": capture(["g++", "--version"]),
    "ld": capture(["ld", "--version"]),
    "system": platform.uname()._asdict(),
    "cpu_affinity": sorted(os.sched_getaffinity(0)),
    "memory": {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
               for line in Path("/proc/meminfo").read_text().splitlines()
               if line.split(":", 1)[0] in ("MemTotal", "SwapTotal")},
    "filesystem": capture(["stat", "-f", "-c", "%T", str(repo)]),
    "environment_allowlist": {key: os.environ[key] for key in ("LANG", "LC_ALL", "TZ", "JAVA_HOME") if key in os.environ},
    "source_bazelrc": file_record(repo / ".bazelrc"),
    "source_module_lock": file_record(repo / "MODULE.bazel.lock"),
    "user_bazelrc_present": (repo / "user.bazelrc").exists(),
    "limitations": ["Stock source control; no shared daemon implementation",
                    "Build overlapped correctness smoke; neither provides performance evidence"],
}
(evidence / "provenance.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps({"control": record["control"], "provenance": str(evidence / "provenance.json")}, indent=2))
