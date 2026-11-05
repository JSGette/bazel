"""Validate trusted, JSON-serializable discovery receipts for the fixed Bazel fixture.

A receipt has the same semantics in a local database or a shipped file. Its checksum
detects accidental damage, not a dishonest producer. It retains the entire answer.
This experiment covers regular UTF-8 files and directories, one fixed glob, normal
BUILD precedence and nested packages. Workspace-wide settings are fixed by the fixture.
"""

from collections import defaultdict
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

if __package__:
    from . import bazel_glob_cursor as cursor
else:
    import bazel_glob_cursor as cursor


SCHEMA = 1
OPERATION = "//app:sources;" + cursor.QUERY
OID = re.compile(r"(?:[a-f0-9]{40}|[a-f0-9]{64})\Z")
SHA256 = re.compile(r"[a-f0-9]{64}\Z")
BUILD_FILES = ("BUILD.bazel", "BUILD")
REGULAR_MODES = (b"100644", b"100755")


class Miss(Exception):
    """The candidate cannot serve this request. The caller should discover afresh."""


def _checksum(payload):
    digest = hashlib.sha256()
    size = 0
    for text in json.JSONEncoder(sort_keys=True, separators=(",", ":")).iterencode(payload):
        chunk = text.encode("utf-8")
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _observe(digest, kind, name):
    # A Git tree component contains no NUL. D and F disambiguate directories and files.
    digest.update(kind + name + b"\0")


def make_record(state: Path):
    result, trace = cursor.answer(state), cursor.read_trace(state)
    directory_oids = trace["directories"]
    boundaries = sorted(trace["boundaries"])
    members = defaultdict(list)
    for path in [*directory_oids, *boundaries]:
        if path != "app":
            parent, name = path.rsplit("/", 1)
            members[parent].append((b"D", name.encode("utf-8")))
    for relative in result["matches"]:
        parent, name = ("app/" + relative).rsplit("/", 1)
        members[parent].append((b"F", name.encode("utf-8")))
    directories = {}
    for path, oid in directory_oids.items():
        digest = hashlib.sha256()
        # Reproduce Git tree ordering, where a directory name compares with a trailing '/'.
        for kind, name in sorted(members[path], key=lambda e: e[1] + (b"/" if e[0] == b"D" else b"")):
            _observe(digest, kind, name)
        directories[path] = {"oid": oid, "shape": digest.hexdigest()}
    if trace["build_file"] != result["build_file"]:
        raise ValueError("inconsistent package lookup evidence")
    payload = {"schema": SCHEMA, "operation": OPERATION, "answer": result,
               "build_oid": trace["build_oid"], "directories": directories, "boundaries": boundaries}
    checksum, _ = _checksum(payload)
    return {**payload, "checksum": checksum}


@contextmanager
def _entries(repo, oid, paths, stats):
    # Stream one non-recursive tree from Git. No child name or caller-supplied path
    # is interpreted as a revision. The only pathspecs used are literal fixture names.
    if not isinstance(oid, str) or not OID.fullmatch(oid):
        raise Miss("invalid_tree_id")
    cmd = ["git", "-C", str(repo), "--literal-pathspecs", "ls-tree", "-z", oid, "--", *paths]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")
    stats["git_queries"] += 1
    with subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        def rows():
            tail = b""
            while block := proc.stdout.read1(8192):
                stats["git_payload_bytes_read"] += len(block)
                *records, tail = (tail + block).split(b"\0")
                for record in records:
                    meta, name = record.split(b"\t", 1)
                    mode, typ, raw_oid = meta.split()
                    stats["entries_examined"] += 1
                    if mode == b"040000" and typ == b"tree":
                        kind = b"D"
                    elif mode in REGULAR_MODES and typ == b"blob":
                        kind = b"F"
                    else:
                        raise Miss("unsupported_entry_type")
                    yield kind, name, raw_oid.decode("ascii")
            if tail or proc.wait() != 0:
                raise Miss("git_tree_unavailable")

        try:
            yield rows()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stdout.close()


def validate(repo: Path, tree: str, record):
    """Return (answer or None, measured validation work). A miss requires fresh lookup.

    Validation materializes the incoming receipt and answer, a boundary set, and up to
    one Git stream per changed directory on the current recursion path. The byte count
    below measures the canonical payload checksummed, not physical disk reads or RSS.
    """
    stats = dict(git_queries=0, git_payload_bytes_read=0, entries_examined=0,
                 record_payload_bytes_checked=0, subtree_fast_hits=0,
                 changed_directories_scanned=0, boundary_probes=0)
    try:
        if not isinstance(record, dict):
            raise Miss("missing_or_malformed_record")
        if type(record.get("schema")) is not int or record["schema"] != SCHEMA:
            raise Miss("schema_mismatch")
        if record.get("operation") != OPERATION:
            raise Miss("operation_mismatch")
        payload = {k: v for k, v in record.items() if k != "checksum"}
        checksum, stats["record_payload_bytes_checked"] = _checksum(payload)
        if checksum != record.get("checksum"):
            raise Miss("checksum_mismatch")
        result = record["answer"]
        if (not isinstance(result, dict) or result["build_file"] not in BUILD_FILES
                or not isinstance(result["matches"], list)
                or not all(isinstance(s, str) for s in result["matches"])):
            raise Miss("malformed_answer")
        dirs = record["directories"]
        if (not isinstance(dirs, dict) or "app" not in dirs
                or not isinstance(record["boundaries"], list)
                or not OID.fullmatch(record["build_oid"])):
            raise Miss("malformed_evidence")
        boundaries = set(record["boundaries"])
        for path, entry in dirs.items():
            if (not isinstance(entry, dict) or not OID.fullmatch(entry["oid"])
                    or not SHA256.fullmatch(entry["shape"])
                    or (path != "app" and path.rpartition("/")[0] not in dirs)):
                raise Miss("malformed_directory")
        if any(p in dirs or p.rpartition("/")[0] not in dirs for p in boundaries):
            raise Miss("malformed_boundary")

        def check_dir(path, oid):
            entry = dirs[path]
            if oid == entry["oid"]:
                stats["subtree_fast_hits"] += 1
                return
            stats["changed_directories_scanned"] += 1
            digest = hashlib.sha256()
            builds = {}
            with _entries(repo, oid, [], stats) as rows:
                for kind, name, child_oid in rows:
                    if kind == b"D":
                        _observe(digest, kind, name)
                        child = path + "/" + name.decode("utf-8")
                        if child in dirs:
                            check_dir(child, child_oid)
                        elif child in boundaries:
                            stats["boundary_probes"] += 1
                            with _entries(repo, child_oid, BUILD_FILES, stats) as markers:
                                if b"F" not in [k for k, _, _ in markers]:
                                    raise Miss("subpackage_removed")
                        else:
                            raise Miss("directory_added")
                    elif name in (b"BUILD.bazel", b"BUILD"):
                        builds[name.decode("ascii")] = child_oid
                    elif cursor.matching_file(name.decode("utf-8")):
                        _observe(digest, kind, name)
            if path == "app":
                selected = next((f for f in BUILD_FILES if f in builds), None)
                if selected != result["build_file"] or builds[selected] != record["build_oid"]:
                    raise Miss("package_definition_changed")
            elif builds:
                raise Miss("subpackage_added")
            if digest.hexdigest() != entry["shape"]:
                raise Miss("glob_membership_changed")

        with _entries(repo, tree, ["app"], stats) as rows:
            roots = list(rows)
        if len(roots) != 1 or roots[0][:2] != (b"D", b"app"):
            raise Miss("package_directory_missing")
        check_dir("app", roots[0][2])
    except Miss as exc:
        return None, {**stats, "reason": str(exc)}
    except (AttributeError, KeyError, TypeError, ValueError, OSError, RecursionError):
        return None, {**stats, "reason": "unreadable_or_malformed_evidence"}
    return result, {**stats, "reason": "validated"}
