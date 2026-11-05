"""Compare a resumed package/glob cursor and validated hints with real Bazel."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from bazel_glob_cursor import answer
from bazel_glob_fixture import _git as git, create_fixture, oracle
from bazel_glob_trace import make_record, validate


def require(condition, why):
    if not condition:
        raise AssertionError(why)


def packed(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def with_file(repo, base, index, path, data):
    """Derive a tree through a private index without touching the working copy."""
    blob = git(repo, "hash-object", "-w", "--stdin", "--no-filters", data=data).decode().strip()
    git(repo, "read-tree", base, index=index)
    git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", index=index)
    return git(repo, "write-tree", index=index).decode().strip()


def ship_hint(repo, base, scratch, record):
    """Check a generated hint into a sibling commit using a private index."""
    tree = with_file(repo, base, scratch / "hint.index", "graph-hints/app-glob.json", packed(record))
    commit = git(repo, "commit-tree", tree, "-p", "HEAD",
                 data=b"Generated package/glob hint\n").decode().strip()
    git(repo, "update-ref", "refs/probe/shipped-hint", commit)
    return commit


def worker(repo, tree, state, pause=None, expected_error=None):
    args = [sys.executable, "-B", str(Path(__file__).with_name("bazel_glob_cursor.py")),
            "--repo", str(repo), "--tree", tree, "--state", str(state)]
    if pause is not None:
        args += ["--pause-after", str(pause)]
    proc = subprocess.run(args, capture_output=True, text=True, timeout=180)
    if expected_error:
        require(proc.returncode != 0 and expected_error in proc.stderr,
                f"expected {expected_error!r}, got status {proc.returncode}: {proc.stderr}")
        return
    if proc.returncode:
        raise RuntimeError(f"cursor exited {proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def exercise(root, bazel):
    repo, views = create_fixture(root)
    base = views["base"]
    overlays = {n: t for n, t in views.items() if n != "base"}
    require(len(set(overlays.values())) == len(overlays) >= 8, "expected at least eight distinct overlays")
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    real_index = (repo / ".git/index").read_bytes()
    states = root / "states"
    states.mkdir()
    results = {"host": {"os": platform.system(), "python": platform.python_version(),
                        "git": git(repo, "--version").decode().strip(),
                        "bazel": subprocess.check_output([bazel, "--version"], text=True).strip()},
               "base_commit": head, "base_tree": base, "overlay_count": len(overlays), "views": {}}

    # Each call materializes its own tree, then asks a fresh real Bazel process.
    expected = {}
    scratch = root / "bazel"
    scratch.mkdir()
    for n, t in views.items():
        print(f"Fresh Bazel query: {n}", file=sys.stderr, flush=True)
        expected[n] = oracle(repo, t, scratch, bazel)
        state = states / f"fresh-{n}.sqlite"
        stats = worker(repo, t, state)
        require(stats["done"] and answer(state) == expected[n], f"fresh cursor disagrees with Bazel: {n}")
        results["views"][n] = {"tree": t, "bazel_answer": expected[n], "fresh": stats}

    # Park all eight requests, with at most two active Python processes at once.
    initial, later = "content_only", "added_source"
    ref = "refs/probe/edit-session"
    git(repo, "update-ref", ref, views[initial])
    def park(item):
        n, tree = item
        state = states / f"parked-{n}.sqlite"
        part = worker(repo, tree, state, pause=32)
        top = part["top_frame"]
        require(not part["done"] and top["path"] == "app/wide" and 0 < top["ordinal"] < 128,
                f"{n} did not pause within the wide directory: {top}")
        return n, part
    with ThreadPoolExecutor(max_workers=2) as pool:
        for n, part in pool.map(park, overlays.items()):
            results["views"][n]["paused"] = part
    # All cursor processes have exited. Publish a different tree at the same name.
    git(repo, "update-ref", ref, views[later], views[initial])
    require(git(repo, "rev-parse", ref).decode().strip() == views[later], "publication failed")
    parked = states / f"parked-{initial}.sqlite"
    worker(repo, views[later], parked, expected_error="different pinned tree or query")
    def resume(item):
        n, tree = item
        state = states / f"parked-{n}.sqlite"
        stats = worker(repo, tree, state)
        require(stats["done"] and answer(state) == expected[n], f"resumed cursor disagrees with Bazel: {n}")
        return n, stats
    with ThreadPoolExecutor(max_workers=2) as pool:
        for n, stats in pool.map(resume, overlays.items()):
            results["views"][n]["resumed"] = stats
    require(expected[initial] != expected[later], "late publication must change a visible answer")
    results["restart"] = {"paused_processes_exited": len(overlays), "max_workers": 2,
                          "edit_session_before": initial, "edit_session_after": later,
                          "resume_from_other_root_rejected": True}

    # A sliding window can repeatedly evict the same wide directory. Count the
    # actual extra reads that a (tree ID, ordinal) cursor incurs on each restart.
    windows = []
    state = states / "repeated-eviction.sqlite"
    while True:
        window = worker(repo, base, state, pause=32)
        windows.append(window)
        if window["done"]:
            break
        require(window["entries_processed"] == 32, "cursor did not advance a full window")
    require(answer(state) == expected["base"], "repeatedly evicted traversal disagrees with Bazel")
    results["repeated_eviction"] = {
        "window": 32, "processes": len(windows), "windows": windows,
        "entries_processed": sum(w["entries_processed"] for w in windows),
        "prefix_entries_replayed": sum(w["prefix_entries_replayed"] for w in windows),
    }

    record = make_record(states / "fresh-base.sqlite")
    commit = ship_hint(repo, base, root, record)
    local_path = root / "local.sqlite"
    with closing(sqlite3.connect(local_path)) as db:
        db.execute("CREATE TABLE traces(key TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        db.execute("INSERT INTO traces VALUES (?,?)", ("base", packed(record)))
        db.commit()

    wrong_blind = []
    for n, tree in views.items():
        if expected[n] != expected["base"]:
            wrong_blind.append(n)
        with closing(sqlite3.connect(local_path)) as db:
            local = db.execute("SELECT payload FROM traces WHERE key='base'").fetchone()[0]
        shipped = git(repo, "show", f"{commit}:graph-hints/app-glob.json")
        require(local == shipped == packed(record), "comparison arms must start with the same evidence")
        modes = {}
        for mode, raw in (("local", local), ("shipped", shipped)):
            found, counters = validate(repo, tree, json.loads(raw))
            hit = found is not None
            entry = {"reused": hit, "validation": counters}
            if not hit:
                state = states / f"{mode}-fallback-{n}.sqlite"
                entry["fallback"] = worker(repo, tree, state)
                found = answer(state)
            require(found == expected[n], f"{mode} hint or fallback disagrees with Bazel: {n}")
            modes[mode] = entry
        require(modes["local"]["reused"] == modes["shipped"]["reused"], "hint storage changed validity")
        results["views"][n]["hints"] = modes
    require(wrong_blind, "missing an adversarial overlay")

    # A local memo can learn a changed view independently of the shipped seed.
    learned = make_record(states / f"fresh-{later}.sqlite")
    with closing(sqlite3.connect(local_path)) as db:
        db.execute("INSERT INTO traces VALUES (?,?)", (later, packed(learned)))
        db.commit()
    with closing(sqlite3.connect(local_path)) as db:
        raw = db.execute("SELECT payload FROM traces WHERE key=?", (later,)).fetchone()[0]
    found, counts = validate(repo, views[later], json.loads(raw))
    require(found == expected[later], "failed to reuse a learned local result")
    corrupted = {**record, "answer": {"build_file": "BUILD", "matches": []}}
    for invalid in ({}, {"schema": "unknown"}, None, corrupted):
        require(validate(repo, base, invalid)[0] is None, "missing or malformed record accepted")
    other = with_file(repo, base, root / "rule.index", "app/BUILD",
                      b'filegroup(name = "sources", srcs = ["root.txt"])\n')
    fallback, rejection = validate(repo, other, record)
    require(fallback is None, "base trace accepted a different Starlark rule")
    worker(repo, other, states / "unsupported.sqlite", expected_error="unsupported BUILD rule")
    delegated = oracle(repo, other, scratch, bazel)
    require(delegated == {"build_file": "BUILD", "matches": ["root.txt"]}, "unexpected changed rule result")
    results["unsupported_rule"] = {"validation": rejection, "cursor_refused": True, "bazel_answer": delegated}
    require(git(repo, "rev-parse", "HEAD").decode().strip() == head and
            (repo / ".git/index").read_bytes() == real_index, "probe changed the source HEAD or real index")
    results["hint_controls"] = {"base_record_bytes": len(packed(record)), "shipped_commit": commit,
                                "blind_base_wrong": wrong_blind, "warm_local_reuse": counts,
                                "missing_bad_or_corrupted_records_rejected": True}
    return results


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--bazel", default="bazel", help="Path or command for a Bazel 9.3.1 executable")
    cli.add_argument("--output", type=Path, help="Save JSON to this path instead of stdout")
    args = cli.parse_args()
    bazel = shutil.which(args.bazel)
    if not bazel:
        cli.error(f"Bazel executable unavailable: {args.bazel}")
    with tempfile.TemporaryDirectory(prefix="bg-") as tmp:
        results = exercise(Path(tmp), os.path.abspath(bazel))
    output = json.dumps(results, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
