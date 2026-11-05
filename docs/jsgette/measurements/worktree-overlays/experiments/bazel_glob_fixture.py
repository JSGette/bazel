"""Pinned Git views and an independent Bazel 9.3.1 oracle for the glob probe."""

import io
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile


RULE = b'filegroup(name = "sources", srcs = glob(["**/*.txt"]))\n'
QUERY = "buildfiles(//app:sources) union labels(srcs, //app:sources)"


def _git(repo, *args, data=None, index=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1",
        GIT_AUTHOR_NAME="Overlay probe", GIT_COMMITTER_NAME="Overlay probe",
        GIT_AUTHOR_EMAIL="probe@example.invalid", GIT_COMMITTER_EMAIL="probe@example.invalid",
        GIT_AUTHOR_DATE="2000-01-01T00:00:00Z", GIT_COMMITTER_DATE="2000-01-01T00:00:00Z",
    )
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index.resolve())
    options = ["-c", "core.autocrlf=false", "-c", "core.filemode=false", "-c", "gc.auto=0",
               "-c", f"core.attributesFile={os.devnull}"]
    return subprocess.run(["git", *options, *args], cwd=repo, env=env, input=data,
                          check=True, stdout=subprocess.PIPE).stdout


def create_fixture(root: Path):
    """Create one private base commit and eight independently edited, uncommitted trees."""
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    repo = root / "source"
    repo.mkdir()
    _git(repo, "init", "-q", "--template=", "--object-format=sha1", "-b", "main")
    files = {
        ".bazelversion": b"9.3.1\n",
        "MODULE.bazel": b'module(name = "overlay_probe")\n',
        "app/BUILD": RULE,
        "app/.hidden.txt": b"hidden basename\n",
        "app/.dotdir/visible.txt": b"visible basename under a hidden directory\n",
        "app/root.txt": b"root\n",
        "app/docs/plain.txt": b"document\n",
        "app/nested/source.txt": b"source\n",
        "app/nested/deeper/more.txt": b"more\n",
        "app/excluded/BUILD": b"# Separate package\n",
        "app/excluded/hidden.txt": b"hidden\n",
    }
    for i in range(128):
        extension = "txt" if i % 32 == 0 else "bin"
        files[f"app/wide/item{i:03d}.{extension}"] = b"wide fixture\n"
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    _git(repo, "add", "-f", "--", ".")
    _git(repo, "commit", "-qm", "base fixture")
    head = _git(repo, "rev-parse", "HEAD")
    real_index = (repo / ".git/index").read_bytes()
    base = _git(repo, "rev-parse", "HEAD^{tree}").decode().strip()
    views = {"base": base}
    edits = {
        "priority_build": {"app/BUILD.bazel": RULE},
        "added_source": {"app/wide/added.txt": b"added\n"},
        "removed_source": {"app/root.txt": None},
        "added_boundary": {"app/nested/BUILD.bazel": b"# New subpackage\n"},
        "removed_boundary": {"app/excluded/BUILD": None},
        "content_only": {"app/root.txt": b"new contents, same glob member\n"},
        "excluded_subtree": {"app/excluded/extra.txt": b"also hidden\n"},
        "outside_package": {"docs/notes.txt": b"unrelated\n"},
    }
    # Re-seed a private staging area for each edit set. The ref pins the resulting
    # tree for the lifetime of this private repo without manufacturing a commit.
    index = root / "draft.index"
    for name, changes in edits.items():
        _git(repo, "read-tree", base, index=index)
        for path, content in changes.items():
            if content is None:
                _git(repo, "update-index", "--force-remove", "--", path, index=index)
            else:
                blob = _git(repo, "hash-object", "-w", "--stdin", "--no-filters",
                            data=content).decode().strip()
                _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}",
                     index=index)
        tree = _git(repo, "write-tree", index=index).decode().strip()
        _git(repo, "update-ref", f"refs/overlay-probe/{name}", tree)
        views[name] = tree
    if (len(set(views.values())) != len(views) or head != _git(repo, "rev-parse", "HEAD")
            or real_index != (repo / ".git/index").read_bytes()):
        raise AssertionError("Source publication changed HEAD or staging, or collapsed distinct views")
    return repo, views


def oracle(repo: Path, tree: str, scratch: Path, bazel: str):
    """Query a fresh materialization and fresh Bazel output base for this tree.

    The output user root is shared for Bazel's installation and repository caches.
    Each --batch JVM gets a separate output base and has repository downloads disabled.
    Oracle materialization deliberately reads the entire small trusted fixture.
    The candidate evaluator is measured separately.
    """
    scratch = scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="q", dir=scratch) as directory:
        trial = Path(directory)
        workspace = trial / "w"
        workspace.mkdir()
        archived = _git(repo, "archive", "--format=zip", tree)
        with zipfile.ZipFile(io.BytesIO(archived)) as archive:
            archive.extractall(workspace)  # Only paths authored by create_fixture.
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith(("BAZEL", "GIT_")) and k.upper() not in
               {"USE_BAZEL_VERSION", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"}}
        command = [bazel, "--batch", "--ignore_all_rc_files",
                   f"--output_user_root={scratch / 'install-cache'}",
                   f"--output_base={trial / 'out'}", "query", "--noshow_progress",
                   "--repository_disable_download", "--incompatible_autoload_externally=",
                   "--output=label", QUERY]
        result = subprocess.run(command, cwd=workspace, env=env, capture_output=True,
                                encoding="utf-8", errors="replace", timeout=120)
        if result.returncode:
            raise RuntimeError(f"Bazel query for {tree} exited {result.returncode}:\n{result.stderr}")
        labels = result.stdout.splitlines()
    if any(not label.startswith("//app:") for label in labels):
        raise AssertionError(f"Unexpected oracle label: {labels!r}")
    paths = [label[len("//app:"):] for label in labels]
    selected = [path for path in paths if path in {"BUILD", "BUILD.bazel"}]
    matches = sorted(path for path in paths if path.endswith(".txt"))
    if len(selected) != 1 or len(paths) != 1 + len(matches):
        raise AssertionError(f"Unexpected BUILD or srcs from Bazel: {paths!r}")
    return {"build_file": selected[0], "matches": matches}
