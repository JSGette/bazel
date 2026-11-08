"""Private Git worktrees of a pinned, real Datadog normalizer target.

Reads the source repository through git archive only. Every writable checkout,
index, ref and worktree administration file belongs to the supplied root.
"""

import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

REVISION = "85ad968bca856fa3d4e07b4d8e11961dabe6866f"
PACKAGE = "pkg/networkdevice/profile/profiledefinition"
COMMAND = PACKAGE + "/normalize_cmd/cmd"
TARGET = "//" + PACKAGE + "/normalize_cmd:normalize_cmd"
GLOB_PACKAGE = "shared_eval_glob"
GLOB_TARGET = "//" + GLOB_PACKAGE + ":manifest"
SCENARIOS = ("identical", "source", "build", "disjoint", "config", "glob")
FLAGS = ("--incompatible_autoload_externally=", "--@rules_go//go/config:pure=true")
PROJECTION = (
    ".bazelversion", ".bazelignore", "BUILD.bazel", "REPO.bazel", "MODULE.bazel",
    "MODULE.bazel.lock", "bazel", "deps", "rtloader", "go.work", "go.mod",
    "go.sum", "release.json", "Cargo.lock", "Cargo.toml", "rust-toolchain.toml",
    ":(glob)**/go.mod", ":(glob)**/go.sum", ":(glob)**/BUILD.bazel",
    "pkg/ebpf/include_headers.go", PACKAGE,
)


def clean_env():
    env = {
        k: v for k, v in os.environ.items()
        if not k.upper().startswith(("GIT_", "BAZEL", "USE_BAZEL"))
        and k.upper() not in (
            "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS", "WSL_INTEROP",
            "WSLENV", "SHLVL", "OLDPWD", "_",
        )
    }
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    return env


def git(repo, *arguments):
    return subprocess.check_output(["git", "-C", str(repo), *arguments], env=clean_env(),
                                   stderr=subprocess.PIPE)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_state(workspace):
    workspace = Path(workspace)
    untracked = []
    # With no exclude options, git also enumerates ignored untracked files.
    for name in git(workspace, "ls-files", "--others", "-z").decode().split("\0"):
        if not name:
            continue
        path = workspace / name
        item = {"path": name, "mode": path.lstat().st_mode}
        if path.is_symlink():
            item["symlink"] = os.readlink(path)
        elif path.is_file():
            item["sha256"] = sha256(path)
        else:
            raise ValueError("Unsupported untracked fixture input: " + name)
        untracked.append(item)
    return {"tracked_diff_sha256": hashlib.sha256(git(workspace, "diff", "--binary", "HEAD")).hexdigest(),
            "git_status": git(workspace, "status", "--porcelain=v1", "--untracked-files=all").decode(),
            "untracked_files": sorted(untracked, key=lambda item: item["path"])}


def oracle_key(row):
    return row["variant"] + "|" + row.get("compilation_mode", "fastbuild")


def oracle_inputs(row):
    return {"variant": row["variant"], "compilation_mode": row.get("compilation_mode", "fastbuild"),
            "source_sha256": row["source_sha256"], "build_sha256": row["build_sha256"],
            "changed_file": row.get("changed_file"), "changed_sha256": row.get("changed_sha256"),
            "extra_targets": row.get("extra_targets", []),
            "expected_glob_sources": row.get("expected_glob_sources", [])}


def compiled_go_sources(repository):
    """Read the explicit srcs of this pinned go_library, rejecting unfamiliar syntax."""
    build = (repository / PACKAGE / "BUILD.bazel").read_text(encoding="utf-8")
    match = re.search(r'go_library\(\s*name\s*=\s*"profiledefinition",\s*srcs\s*=\s*\[(.*?)\]',
                      build, flags=re.DOTALL)
    if not match:
        raise ValueError("Pinned profiledefinition srcs declaration changed")
    names = re.findall(r'"([A-Za-z_][A-Za-z_0-9]*\.go)"', match.group(1))
    residue = re.sub(r'"[A-Za-z_][A-Za-z_0-9]*\.go"|\s|,', "", match.group(1))
    if residue or not names or len(set(names)) != len(names):
        raise ValueError("Expected a literal list of distinct Go source filenames")
    if any(not (repository / PACKAGE / name).is_file() for name in names):
        raise ValueError("A declared Go source is missing from the projection")
    return names


def add_glob_package(repository):
    package = repository / GLOB_PACKAGE
    package.mkdir()
    command = "printf '%s\\n' $(SRCS) | LC_ALL=C sort > $@"
    (package / "BUILD.bazel").write_text(
        'genrule(\n    name = "manifest",\n    srcs = glob(["*.txt"]),\n'
        '    outs = ["sources.manifest"],\n    cmd = ' + json.dumps(command) + ',\n)\n',
        encoding="utf-8")
    (package / "base.txt").write_text("committed base input\n", encoding="utf-8")


def save_new(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def extract_archive(data, destination):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for member in archive.infolist():
            parts = PurePosixPath(member.filename)
            if parts.is_absolute() or ".." in parts.parts or "\\" in member.filename:
                raise ValueError("Unsafe archive path: " + member.filename)
            if member.external_attr >> 28 == 0xA:
                raise ValueError("Unexpected symlink in fixture: " + member.filename)
        archive.extractall(destination)
        for member in archive.infolist():
            path = destination / member.filename
            if path.is_file():
                path.chmod(0o755 if member.external_attr >> 16 & 0o111 else 0o644)


def edit(workspace, variant):
    if variant == "base":
        return
    if variant == "source":
        path = workspace / COMMAND / "root.go"
        before = b'filename = name + ".json"'
        data = path.read_bytes()
        if data.count(before) != 1:
            raise ValueError("Pinned source replacement is not unique")
        path.write_bytes(data.replace(before, b'filename = name + ".source.json"'))
    elif variant == "build":
        path = workspace / COMMAND / "BUILD.bazel"
        data = path.read_text(encoding="utf-8")
        before = 'srcs = ["root.go"],'
        if data.count(before) != 1:
            raise ValueError("Pinned BUILD replacement is not unique")
        command = '''sed 's/name + "[.]json"/name + ".build.json"/' $(location root.go) > $@'''
        data = data.replace(before, 'srcs = [":overlay_root"],')
        data += ('\ngenrule(name = "overlay_root", srcs = ["root.go"], '
                 'outs = ["overlay_root.go"], cmd = ' + json.dumps(command) + ')\n')
        path.write_text(data, encoding="utf-8")
    else:
        raise ValueError("Unsupported fixture variant: " + variant)


def prepare(source_repo, root, clients, scenario):
    """Create a fresh fixture; never alter the user's source Git repository."""
    if scenario not in SCENARIOS:
        raise ValueError("Unsupported scenario")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    fixture_root = root / "fixture"
    fixture_root.mkdir()  # Deliberately fails if an earlier fixture exists.
    repository = fixture_root / "repository"
    repository.mkdir()
    revision = git(source_repo, "rev-parse", "--verify", REVISION + "^{commit}").decode().strip()
    if revision != REVISION:
        raise ValueError("Unexpected source revision")
    data = git(source_repo, "archive", "--format=zip", REVISION, "--", *PROJECTION)
    extract_archive(data, repository)
    go_sources = compiled_go_sources(repository)
    if scenario == "disjoint" and clients > len(go_sources):
        raise ValueError("Insufficient actual Go sources for distinct client edits")
    if scenario == "glob":
        add_glob_package(repository)
    git(repository, "init", "--quiet")
    git(repository, "-c", "core.autocrlf=false", "add", "--all")
    git(repository, "-c", "user.name=Shared evaluation fixture",
        "-c", "user.email=fixture@invalid", "-c", "commit.gpgsign=false",
        "commit", "--quiet", "-m", "Pinned Datadog normalizer projection")
    commit = git(repository, "rev-parse", "HEAD").decode().strip()
    tree = git(repository, "rev-parse", "HEAD^{tree}").decode().strip()
    rows = []
    for index in range(clients):
        workspace = fixture_root / ("w%02d" % index)
        git(repository, "worktree", "add", "--detach", "--quiet", str(workspace), commit)
        metadata = {"compilation_mode": "fastbuild", "behavior_variant": "base",
                    "extra_targets": []}
        if scenario == "disjoint":
            variant = "disjoint-%02d" % index
            changed = workspace / PACKAGE / go_sources[index]
            changed.write_bytes(changed.read_bytes() +
                                ("\n// Shared evaluation disjoint input %02d.\n" % index).encode())
            metadata.update(changed_file=changed.relative_to(workspace).as_posix(),
                            changed_sha256=sha256(changed),
                            edit_kind="comment_only_runtime_behavior_unchanged_digest_not_assumed")
        elif scenario == "config":
            variant = "base"
            metadata["compilation_mode"] = "opt" if clients == 1 or index % 2 else "fastbuild"
        elif scenario == "glob":
            variant = "glob-%02d" % index
            name = "added-%02d.txt" % index
            changed = workspace / GLOB_PACKAGE / name
            changed.write_text("untracked input %02d\n" % index, encoding="utf-8")
            metadata.update(changed_file=changed.relative_to(workspace).as_posix(),
                            changed_sha256=sha256(changed), extra_targets=[GLOB_TARGET],
                            expected_glob_sources=sorted([GLOB_PACKAGE + "/base.txt",
                                                          GLOB_PACKAGE + "/" + name]))
        else:
            variant = "base" if scenario == "identical" or (clients > 1 and index == 0) else scenario
            edit(workspace, variant)
            metadata["behavior_variant"] = variant
        rows.append({"index": index, "workspace": str(workspace), "variant": variant, **metadata,
                     "source_sha256": sha256(workspace / COMMAND / "root.go"),
                     "build_sha256": sha256(workspace / COMMAND / "BUILD.bazel"),
                     "source_state": source_state(workspace)})
    manifest = {"schema_version": 2, "source_revision": revision, "target": TARGET,
                "projection_archive_sha256": hashlib.sha256(data).hexdigest(),
                "private_commit": commit, "private_tree": tree, "private_repository": str(repository),
                "scenario": scenario, "clients": clients, "worktrees": rows,
                "compiled_go_sources": go_sources,
                "variant_note": "source/build repeat one edit after a base; disjoint edits distinct declared sources; config varies per client; glob adds unique untracked inputs"}
    save_new(fixture_root / "manifest.json", manifest)
    return manifest


def verify_program(executable, variant, scratch):
    scratch.mkdir()
    output = scratch / "out"
    output.mkdir()
    profile = scratch / "switch.yaml"
    profile.write_text("name: test-switch\ndescription: Switch profile\nsysobjectid: 1.3.6.1.4.1.9\n",
                       encoding="utf-8")
    subprocess.run([str(executable), "--json", "--outdir", str(output), str(profile)],
                   cwd=scratch, check=True, capture_output=True, timeout=30, env=clean_env())
    name = {"base": "switch.json", "source": "switch.source.json", "build": "switch.build.json"}[variant]
    if sorted(p.name for p in output.iterdir()) != [name]:
        raise AssertionError("Unexpected output filename for " + variant)
    data = json.loads((output / name).read_text(encoding="utf-8"))
    expected = {"name": "test-switch", "description": "Switch profile", "sysobjectid": ["1.3.6.1.4.1.9"]}
    if any(data.get(key) != value for key, value in expected.items()):
        raise AssertionError("Unexpected normalized profile")
    return {"passed": True, "filename": name, "profile": data}
