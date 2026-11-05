"""Materialize pinned Datadog targets and independent source and BUILD edits.

The private sparse workspace keeps the original module, lockfile, package,
rule implementation and configuration, using only committed Git objects.
"""

import io
import zipfile
from functools import lru_cache
from pathlib import Path

from datadog_cases import REVISION, git


TARGET = "//rtloader:constants_gen"
SPAWN_TARGET = "//pkg/ebpf:include_headers_src"
COMPILED_TARGET = "//pkg/ebpf:include_headers"
BUILD_FLAGS = ("--incompatible_autoload_externally=",)
TEMPLATE = "rtloader/three/constants.h.in"
BUILD_FILE = "rtloader/BUILD.bazel"
GO_SOURCE = "pkg/ebpf/include_headers.go"
GO_BUILD = "pkg/ebpf/BUILD.bazel"
VARIANTS = ("base", "source", "build")
SOURCE_ADDITION = b"/* overlay source edit */\n"
PYTHON_PATH = b"{install_dir}/embedded/lib/python3.13"
PROJECTION = (
    ".bazelversion", ".bazelignore", "BUILD.bazel", "REPO.bazel", "MODULE.bazel",
    "MODULE.bazel.lock", "bazel", "deps", "rtloader", "go.work", "go.mod",
    "go.sum", "release.json", "Cargo.lock", "Cargo.toml", "rust-toolchain.toml",
    # bazelify_go_work checks package markers for each module, then Gazelle
    # resolves those modules' manifests and sums during repository mapping.
    ":(glob)**/go.mod", ":(glob)**/go.sum", ":(glob)**/BUILD.bazel",
    GO_SOURCE,
)


@lru_cache(maxsize=1)
def _archive(repo):
    return git(repo, "archive", "--format=zip", REVISION, "--", *PROJECTION)


@lru_cache(maxsize=4)
def _original(repo, path):
    return git(repo, "show", f"{REVISION}:{path}")


def apply_variant(repo: Path, workspace: Path, variant: str, target: str = TARGET):
    """Set either input edit or restore base, including across a warm rebuild."""
    if variant not in VARIANTS:
        raise ValueError(f"Unknown overlay variant {variant}")
    if target == TARGET:
        src_name, build_name = TEMPLATE, BUILD_FILE
        source = _original(repo, src_name)
        if variant == "source":
            source += SOURCE_ADDITION
        before, after = PYTHON_PATH, PYTHON_PATH.replace(b"3.13", b"3.14")
    elif target in (SPAWN_TARGET, COMPILED_TARGET):
        src_name, build_name = GO_SOURCE, GO_BUILD
        source = _original(repo, src_name)
        if variant == "source":
            source = source.replace(b'"vmlinux.h"', b'"vmlinux-source.h"')
        before = b"grep -v '^//go:build ignore' $< > $@"
        after = b"grep -v '^//go:build ignore' $< | sed 's/vmlinux[.]h/vmlinux-build.h/' > $@"
    else:
        raise ValueError(f"Unknown target {target}")
    build = _original(repo, build_name)
    if build.count(before) != 1:
        raise ValueError(f"Unexpected pinned rule in {build_name}")
    if variant == "build":
        build = build.replace(before, after)
    for name, data in ((src_name, source), (build_name, build)):
        path = workspace / name
        if path.read_bytes() != data:
            path.write_bytes(data)


def materialize(repo: Path, workspace: Path, variant: str, target: str = TARGET):
    if workspace.exists() and any(workspace.iterdir()):
        raise ValueError(f"Materialization requires a private empty workspace: {workspace}")
    workspace.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(_archive(repo))) as archive:
        for member in archive.infolist():
            if member.external_attr >> 28 == 0xA:
                raise ValueError("Unexpected symlink in pinned target projection: " + member.filename)
        archive.extractall(workspace)
    apply_variant(repo, workspace, variant, target)


def expected_output(repo: Path, variant: str, target: str = TARGET) -> bytes:
    if variant not in VARIANTS:
        raise ValueError(f"Unknown overlay variant {variant}")
    if target == SPAWN_TARGET:
        data = b"".join(line for line in _original(repo, GO_SOURCE).splitlines(keepends=True)
                        if not line.startswith(b"//go:build ignore"))
        if variant == "base":
            return data
        return data.replace(b"vmlinux.h", f"vmlinux-{variant}.h".encode())
    if target != TARGET:
        raise ValueError(f"No generated-file oracle for {target}")
    version = b"3.14" if variant == "build" else b"3.13"
    data = _original(repo, TEMPLATE).replace(
        b"@Python3_STDLIB@", b"/opt/datadog-agent/embedded/lib/python" + version)
    return data + SOURCE_ADDITION if variant == "source" else data


def artifact(workspace: Path, output_base: Path, target: str = TARGET) -> Path:
    """Resolve output directly when Windows doesn't create bazel-bin."""
    configuration_dir = output_base / "execroot" / "_main" / "bazel-out"
    if target == COMPILED_TARGET:
        relative = "pkg/ebpf/include_headers_/include_headers"
        paths = [p for suffix in ("", ".exe") for p in configuration_dir.glob("*/bin/" + relative + suffix)
                 if p.is_file()]
    else:
        relative = {TARGET: "rtloader/constants.h", SPAWN_TARGET: "pkg/ebpf/include_headers_gen.go"}[target]
        paths = [p for p in configuration_dir.glob("*/bin/" + relative) if p.is_file()]
    if len(paths) != 1:
        raise ValueError(f"Expected one {relative} in {configuration_dir}, found {paths}")
    return paths[0]
