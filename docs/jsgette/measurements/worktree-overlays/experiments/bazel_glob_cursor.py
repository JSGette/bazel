"""Resumable package lookup and **/*.txt glob for the controlled regular-file fixture.

Each DFS frame holds an immutable Git directory ID and the next entry ordinal.
Open iterators use bounded chunks. Git's own tree loading and OS buffers are
outside this bound. SQLite holds results and observations separately from frames.
"""

import argparse
from contextlib import closing, suppress
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess


PACKAGE = "app"
QUERY = "v2:app:BUILD.bazel|BUILD:**/*.txt:regular:stop-at-subpackages"
# Only these exact BUILD bytes have an implemented interpretation. Keeping this
# independent of the oracle fixture makes an unrecognized future edit fail closed.
SUPPORTED_RULE = b'filegroup(name = "sources", srcs = glob(["**/*.txt"]))\n'
CHUNK = 4096
GIT_ENV = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
GIT_ENV.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")


def git_cmd(repo, *args):
    return ["git", "-C", str(repo), "--literal-pathspecs", *args]


def parse_entry(raw):
    header, name = raw.split(b"\t", 1)
    mode, _, oid = header.split()
    if mode not in (b"040000", b"100644", b"100755"):
        raise ValueError(f"unsupported fixture entry {name!r} (Git mode {mode!r})")
    return name.decode("utf-8"), mode == b"040000", oid.decode("ascii")


def lookup(repo, tree, *names):
    # Each caller specifies at most two direct names, never an entire directory.
    raw = subprocess.check_output(git_cmd(repo, "ls-tree", "-z", tree, "--", *names), env=GIT_ENV)
    return {name: (is_dir, oid) for name, is_dir, oid in
            (parse_entry(row) for row in raw.split(b"\0") if row)}


def build_entry(repo, tree):
    found = lookup(repo, tree, "BUILD.bazel", "BUILD")
    for name in ("BUILD.bazel", "BUILD"):
        if name in found and not found[name][0]:
            return name, found[name][1]
    return None


def matching_file(name):
    # For a "*.txt" segment Bazel's UnixGlob requires a leading dot to match
    # explicitly. The "**" directory segment may traverse a dot directory.
    return not name.startswith(".") and name.endswith(".txt")


def directory_entries(repo, oid):
    # Stream NUL-delimited names without materializing the directory as a list.
    proc = subprocess.Popen(git_cmd(repo, "ls-tree", "-z", oid), env=GIT_ENV,
                            stdout=subprocess.PIPE, bufsize=0)
    carry = bytearray()
    exhausted = False
    try:
        while block := proc.stdout.read(CHUNK):
            start = 0
            while (end := block.find(b"\0", start)) >= 0:
                carry.extend(block[start:end])
                yield parse_entry(carry)
                carry.clear()
                start = end + 1
            carry.extend(block[start:])
        exhausted = True
        if proc.wait() != 0 or carry:
            raise RuntimeError(f"incomplete git ls-tree stream for {oid}")
    finally:
        proc.stdout.close()
        if not exhausted and proc.poll() is None:
            with suppress(ProcessLookupError):
                proc.terminate()
        proc.wait()


@dataclass
class Frame:
    path: str
    oid: str
    ordinal: int = 0
    entries: object = None


def initialize(db, repo, tree):
    app = lookup(repo, tree, PACKAGE).get(PACKAGE)
    if not app or not app[0]:
        raise ValueError("fixture package directory app is absent")
    selected = build_entry(repo, app[1])
    if not selected:
        raise ValueError("fixture package app has no BUILD file")
    raw = b"blob " + str(len(SUPPORTED_RULE)).encode("ascii") + b"\0" + SUPPORTED_RULE
    algorithm = "sha1" if len(tree) == 40 else "sha256"
    if selected[1] != hashlib.new(algorithm, raw).hexdigest():
        raise ValueError(f"unsupported BUILD rule in {PACKAGE}/{selected[0]} (expected exact fixture rule)")
    for schema in (
        "meta (tree TEXT, query TEXT, build_file TEXT, build_oid TEXT)",
        "frames (depth INTEGER PRIMARY KEY, path TEXT, oid TEXT, ordinal INTEGER)",
        "directories (path TEXT PRIMARY KEY, oid TEXT)",
        "boundaries (path TEXT PRIMARY KEY)",
        "matches (path TEXT PRIMARY KEY)",
    ):
        db.execute("CREATE TABLE " + schema)
    db.execute("INSERT INTO meta VALUES (?,?,?,?)", (tree, QUERY, *selected))
    db.execute("INSERT INTO frames VALUES (0,?,?,0)", (PACKAGE, app[1]))
    db.execute("INSERT INTO directories VALUES (?,?)", (PACKAGE, app[1]))


def run(repo: Path, tree: str, state: Path, pause_after=None):
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree):
        raise ValueError("expected a full Git tree object ID")
    if pause_after is not None and pause_after < 1:
        raise ValueError("--pause-after requires a positive entry count")
    if subprocess.check_output(git_cmd(repo, "cat-file", "-t", tree), env=GIT_ENV).strip() != b"tree":
        raise ValueError("expected the object ID of a tree")
    stats = dict(entries_processed=0, prefix_entries_replayed=0, directory_streams_opened=0,
                 peak_open_streams=0, subpackage_lookups=0)
    with closing(sqlite3.connect(state)) as db, db:
        db.execute("PRAGMA cache_size=-256")  # KiB target for SQLite's page cache
        db.execute("BEGIN IMMEDIATE")
        resumed = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone())
        if resumed:
            if db.execute("SELECT tree,query FROM meta").fetchone() != (tree, QUERY):
                raise ValueError("checkpoint belongs to a different pinned tree or query")
        else:
            initialize(db, repo, tree)
        stack = [Frame(*row) for row in db.execute("SELECT path,oid,ordinal FROM frames ORDER BY depth")]
        open_streams = 0
        try:
            while stack and (pause_after is None or stats["entries_processed"] < pause_after):
                frame = stack[-1]
                if frame.entries is None:
                    frame.entries = directory_entries(repo, frame.oid)
                    open_streams += 1
                    stats["directory_streams_opened"] += 1
                    stats["peak_open_streams"] = max(stats["peak_open_streams"], open_streams)
                    # Immutable tree order is stable. Reopening and skipping is the
                    # cost of a portable ordinal cursor, including suspended parents.
                    for _ in range(frame.ordinal):
                        if next(frame.entries, None) is None:
                            raise ValueError("saved ordinal exceeds directory length")
                        stats["prefix_entries_replayed"] += 1
                entry = next(frame.entries, None)
                if entry is None:
                    stack.pop()
                    open_streams -= 1
                    continue
                name, is_dir, oid = entry
                frame.ordinal += 1
                stats["entries_processed"] += 1
                path = f"{frame.path}/{name}"
                if is_dir:
                    stats["subpackage_lookups"] += 1
                    if build_entry(repo, oid):
                        db.execute("INSERT INTO boundaries VALUES (?)", (path,))
                    else:
                        db.execute("INSERT INTO directories VALUES (?,?)", (path, oid))
                        stack.append(Frame(path, oid))
                elif matching_file(name):
                    db.execute("INSERT INTO matches VALUES (?)", (path[len(PACKAGE) + 1:],))
        finally:
            for frame in stack:
                if frame.entries is not None:
                    frame.entries.close()
        db.execute("DELETE FROM frames")
        positions = [(f.path, f.oid, f.ordinal) for f in stack]
        db.executemany("INSERT INTO frames VALUES (?,?,?,?)",
                       ((depth, *position) for depth, position in enumerate(positions)))
        stats.update(
            done=not stack, resumed=resumed, checkpoint_frames=len(stack),
            top_frame={"path": stack[-1].path, "ordinal": stack[-1].ordinal} if stack else None,
            cursor_payload_bytes=len(json.dumps([tree, QUERY, positions], separators=(",", ":")).encode()),
            matched_files=db.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
            traversed_directories=db.execute("SELECT COUNT(*) FROM directories").fetchone()[0],
            boundary_directories=db.execute("SELECT COUNT(*) FROM boundaries").fetchone()[0],
        )
    stats["state_file_bytes"] = state.stat().st_size
    return stats


def completed(state):
    db = sqlite3.connect(Path(state).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if db.execute("SELECT query FROM meta").fetchone() != (QUERY,):
            raise ValueError("checkpoint belongs to a different discovery query")
        if db.execute("SELECT 1 FROM frames LIMIT 1").fetchone():
            raise ValueError("discovery is incomplete")
        return db
    except BaseException:
        db.close()
        raise


def answer(state: Path):
    """Materialize the final output for the oracle comparison, outside traversal."""
    with closing(completed(state)) as db:
        return {"build_file": db.execute("SELECT build_file FROM meta").fetchone()[0],
                "matches": [row[0] for row in db.execute("SELECT path FROM matches ORDER BY path")]}


def read_trace(state: Path):
    """Export observations after completion. Directory OIDs refer to pinned Git objects."""
    with closing(completed(state)) as db:
        build, oid = db.execute("SELECT build_file,build_oid FROM meta").fetchone()
        return {"build_file": build, "build_oid": oid,
                "directories": dict(db.execute("SELECT path,oid FROM directories ORDER BY path")),
                "boundaries": [row[0] for row in db.execute("SELECT path FROM boundaries ORDER BY path")]}


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", required=True, type=Path)
    cli.add_argument("--tree", required=True)
    cli.add_argument("--state", required=True, type=Path)
    cli.add_argument("--pause-after", type=int)
    args = cli.parse_args()
    print(json.dumps(run(args.repo, args.tree, args.state, args.pause_after), sort_keys=True))
