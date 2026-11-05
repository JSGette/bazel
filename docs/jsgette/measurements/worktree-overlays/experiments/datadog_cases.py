"""Independent Bazel oracle for the seekable-cursor benchmark on Datadog Agent.

All Git reads use a pinned commit. The query workspace and Bazel state are
private temporary directories. Only the security header filegroup is an
upstream target. The other queries exercise actual Git subtrees using a
controlled filegroup, preserving all nested package markers.
"""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import zipfile


REVISION = "85ad968bca856fa3d4e07b4d8e11961dabe6866f"
CASES = (
    dict(name="headers", package="pkg/security/ebpf/c/include", suffix=".h", recursive=True,
         label="//pkg/security/ebpf/c/include:security_ebpf_headers_files", kind="upstream_filegroup"),
    dict(name="dyninst", package="pkg/dyninst", suffix=".json", recursive=True,
         label="//pkg/dyninst:cursor_probe", kind="projected_filegroup"),
    dict(name="dca_notes", package="releasenotes-dca/notes", suffix=".yaml", recursive=True,
         label="//releasenotes-dca/notes:cursor_probe", kind="projected_filegroup"),
    dict(name="agent_notes", package="releasenotes/notes", suffix=".yaml", recursive=True,
         label="//releasenotes/notes:cursor_probe", kind="projected_filegroup"),
)


def clean_env():
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("GIT_", "BAZEL", "USE_BAZEL"))
           and k.upper() not in ("JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0",
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    return env


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], env=clean_env())


def match_digest(paths):
    data = b"".join(p.encode("utf-8") + b"\0" for p in sorted(paths))
    return hashlib.sha256(data).hexdigest()


def query(ws, user_root, output_base, bazel, version, cases):
    expr = " union ".join(f"labels(srcs, {case['label']})" for case in cases)
    opts = ["--batch", "--ignore_all_rc_files", f"--output_user_root={user_root}",
            f"--output_base={output_base}", "query", "--noshow_progress",
            "--incompatible_autoload_externally=", "--output=label"]
    env = clean_env()
    env["USE_BAZEL_VERSION"] = version
    actual_version = subprocess.check_output([bazel, "--version"], cwd=ws, env=env, text=True).strip()
    if actual_version != f"bazel {version}":
        raise ValueError(f"Datadog pins Bazel {version}; found {actual_version}. Use Bazelisk or matching Bazel.")
    run = subprocess.run([bazel, *opts, expr], cwd=ws, env=env, capture_output=True,
                         encoding="utf-8", errors="replace", timeout=300)
    if run.returncode:
        raise RuntimeError(f"Bazel exited {run.returncode}: {run.stderr[-2500:]}")
    labels = run.stdout.splitlines()
    validated = {}
    for case in cases:
        prefix = "//" + case["package"] + ":"
        paths = sorted(p[len(prefix):] for p in labels if p.startswith(prefix))
        if not paths or len(set(paths)) != len(paths) or any(not p.endswith(case["suffix"]) for p in paths):
            raise ValueError(f"unexpected source labels for {case['label']}")
        validated[case["name"]] = {**case, "match_count": len(paths), "match_digest": match_digest(paths)}
    if sum(c["match_count"] for c in validated.values()) != len(labels):
        raise ValueError("Bazel returned an unexpected package")
    return validated


def original_module_query(repo, rev, scratch, bazel, version):
    """Check the upstream filegroup in a complete disposable detached checkout."""
    env = clean_env()
    ws = scratch / "upstream"
    # --shared only gives the private repo a read dependency on source Git
    # objects. Its index, refs, checkout and any lockfile updates are private.
    subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(repo), str(ws)],
                   check=True, env=env)
    git(ws, "-c", "core.autocrlf=false", "-c", "core.longpaths=true", "checkout", "--detach", "--quiet", rev)
    return query(ws, scratch / "install", scratch / "original-out", bazel, version, CASES[:1])["headers"]


def run(repo, rev, bazel, try_original):
    repo = repo.resolve()
    rev = git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    version = git(repo, "show", rev + ":.bazelversion").decode().strip()
    topology = git(repo, "ls-tree", "-rz", "-t", rev).split(b"\0")[:-1]
    directories = sum(x.startswith(b"040000 ") for x in topology)
    packages = len({p.split(b"\t", 1)[1].rpartition(b"/")[0] for p in topology
                    if p.split(b"\t", 1)[1].rsplit(b"/", 1)[-1] in {b"BUILD", b"BUILD.bazel"}})
    result = dict(revision=rev, source_tree=git(repo, "rev-parse", rev+"^{tree}").decode().strip(),
                  repo="https://github.com/DataDog/datadog-agent", bazel=version,
                  host=platform.system(), tree_entries=len(topology)-directories,
                  descendant_directories=directories, bazel_packages=packages,
                  digest_format="SHA-256 of sorted UTF-8 package-relative paths, each followed by one NUL")
    with tempfile.TemporaryDirectory(prefix="dd-oracle-") as name:
        scratch = Path(name)
        ws = scratch / "projection"
        ws.mkdir()
        paths = [c["package"] for c in CASES] + [".bazelversion", ".bazelignore", "REPO.bazel"]
        archived = git(repo, "archive", "--format=zip", rev, "--", *paths)
        with zipfile.ZipFile(io.BytesIO(archived)) as archive:
            # Trusted pinned Datadog paths. Do not silently reify symlinks as
            # normal files if selecting different workloads in the future.
            for member in archive.infolist():
                if member.external_attr >> 28 == 0xA:
                    raise ValueError("symlink in oracle projection: " + member.filename)
            archive.extractall(ws)
        (ws / "MODULE.bazel").write_text(
            'module(name="cursor_oracle")\nbazel_dep(name="rules_cc",version="0.2.17")\n', encoding="utf-8")
        for case in CASES:
            if case["kind"] == "projected_filegroup":
                build = ws / case["package"] / "BUILD.bazel"
                build.write_text(f'filegroup(name="cursor_probe", srcs=glob(["**/*{case["suffix"]}"]))\n',
                                 encoding="utf-8")
        result["cases"] = query(ws, scratch / "install", scratch / "projection-out", bazel, version, CASES)
        for case in CASES:
            result["cases"][case["name"]]["source_subtree"] = git(
                repo, "rev-parse", rev + ":" + case["package"]).decode().strip()
        result["native_module_check"] = {"status": "not_requested"}
        if try_original:
            try:
                native = original_module_query(repo, rev, scratch, bazel, version)
            except (RuntimeError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                # Limit stored diagnostics to a concise, reproducible reason.
                result["native_module_check"] = {"status": "unavailable", "diagnostic": str(exc)[-1600:]}
            else:
                assert native["match_digest"] == result["cases"]["headers"]["match_digest"]
                result["native_module_check"] = {"status": "passed", **native}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--revision", default=REVISION)
    parser.add_argument("--bazel", default="bazel", help="Bazelisk or the repository's pinned Bazel")
    parser.add_argument("--original-module", action="store_true",
                        help="Also try the unchanged upstream module in a fresh complete private checkout")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bazel = shutil.which(args.bazel)
    if not bazel:
        parser.error("Bazelisk or matching Bazel binary required")
    data = json.dumps(run(args.repo, args.revision, bazel, args.original_module), indent=2) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8")
    else:
        print(data, end="")


if __name__ == "__main__":
    main()
