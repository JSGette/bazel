"""Coordinate eight release-note globs on private uncommitted Datadog Agent trees."""

import argparse
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import threading

from measure_process import measure
from seekable_tree import run


REV = "85ad968bca856fa3d4e07b4d8e11961dabe6866f"
PACKAGE = "releasenotes/notes"
EXTRA = "overlay-experiment-only-new-release-note.yaml"


def git(repo, *args, data=None):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_OPTIONAL_LOCKS="0",
               GIT_NO_REPLACE_OBJECTS="1")
    p = subprocess.run(["git", "-C", str(repo), "-c", "gc.auto=0", *args],
                       input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if p.returncode:
        raise RuntimeError(f"git {args[0]}: {p.stderr.decode('utf-8', 'replace')}")
    return p.stdout


def oid(repo, expr):
    return git(repo, "rev-parse", "--verify", expr).strip().decode("ascii")


def names_manifest(names):
    return b"".join(name + b"\0" for name in sorted(names))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def edit_tree(repo, tree, path, new_blob):
    """Copy only ancestor Git trees in the private object database."""
    members = {}
    for record in git(repo, "ls-tree", "-z", tree).split(b"\0"):
        if record:
            meta, name = record.split(b"\t", 1)
            members[name] = meta
    name = path[0]
    if len(path) > 1:
        mode, kind, child = members[name].split()
        if kind != b"tree":
            raise ValueError("expected source directory")
        updated = edit_tree(repo, child.decode("ascii"), path[1:], new_blob)
        members[name] = mode + b" tree " + updated.encode("ascii")
    elif new_blob is None:
        del members[name]
    else:
        members[name] = b"100644 blob " + new_blob
    data = b"".join(meta + b"\t" + name + b"\0" for name, meta in members.items())
    return git(repo, "mktree", "-z", "--missing", data=data).strip().decode("ascii")


def prepare(source, revision, root, window):
    source = Path(source).resolve()
    commit = oid(source, revision + "^{commit}")
    base = oid(source, commit + "^{tree}")
    store = git(source, "rev-parse", "--path-format=absolute", "--git-path", "objects").decode().strip()
    kind = git(source, "rev-parse", "--show-object-format").decode().strip()
    repo = root / "objects.git"
    repo.mkdir()
    git(repo, "init", "--bare", "-q", "--template=", f"--object-format={kind}", ".")
    (repo / "objects/info/alternates").write_bytes((Path(store).as_posix() + "\n").encode("utf-8"))
    package_oid = oid(repo, f"{base}:{PACKAGE}")
    base_rows = git(repo, "ls-tree", "-z", package_oid).split(b"\0")[:-1]
    entries = [row.split(b"\t", 1) for row in git(repo, "ls-tree", "-rz", package_oid).split(b"\0") if row]
    files = {name for meta, name in entries if meta.split()[0] in (b"100644", b"100755")}
    boundaries = {name.rpartition(b"/")[0] + b"/" for name in files
                  if b"/" in name and name.rpartition(b"/")[2] in (b"BUILD", b"BUILD.bazel")}
    matched = {name for name in files if name.endswith(b".yaml") and not name.rpartition(b"/")[2].startswith(b".")
               and not any(name.startswith(boundary) for boundary in boundaries)}
    existing = min(name for name in matched if b"/" not in name)
    extra = EXTRA.encode()
    if extra in {name for _, name in entries}:
        raise ValueError("synthetic added path already exists")
    oracles = {"base": names_manifest(matched), "added": names_manifest(matched | {extra}),
               "removed": names_manifest(matched - {existing})}
    for key, manifest in oracles.items():
        (root / (key + ".expected")).write_bytes(manifest)

    # Each tuple names one independently published edit; no user index, worktree
    # or branch is touched. Two distinct added blobs intentionally share a name.
    package = [part.encode() for part in PACKAGE.split("/")]
    edits = [(f"outside_{i}", [f".cohort-outside-{i}".encode()], b"outside\n", "base")
             for i in range(4)]
    edits += [("add_a", package + [extra], b"first contents\n", "added"),
              ("add_b", package + [extra], b"other contents\n", "added"),
              ("content", package + [existing], b"changed contents\n", "base"),
              ("remove", package + [existing], None, "removed")]
    views = {}
    for name, path, raw, expected in edits:
        blob = None if raw is None else git(repo, "hash-object", "-w", "--stdin", data=raw).strip()
        tree = edit_tree(repo, base, path, blob)
        views[name] = {"tree": tree, "package_oid": oid(repo, f"{tree}:{PACKAGE}"),
                       "kind": expected, "edit": b"/".join(path).decode(),
                       "expected_sha256": digest(oracles[expected]), "expected_count": oracles[expected].count(b"\0")}
    if len({v["tree"] for v in views.values()}) != 8:
        raise AssertionError("expected eight different view roots")
    return {"source_commit": commit, "source_tree": base, "package": PACKAGE,
            "base_package_tree": package_oid, "base_directory_entries": len(base_rows), "base_count": len(matched),
            "base_manifest_sha256": digest(oracles["base"]), "window": window, "views": views}


def produce(root, tree, window, name):
    return run(root / "objects.git", tree, PACKAGE, ".yaml", window, "seek",
               root / "warm-index", recursive=True, matches_out=root / (name + ".out"))


def cohort(root, share):
    cfg = json.loads((root / "spec.json").read_text(encoding="utf-8"))
    views = cfg["views"]
    expected = {kind: (root / (kind + ".expected")).read_bytes() for kind in ("base", "added", "removed")}
    gate = threading.Barrier(len(views), timeout=120)
    lock = threading.Lock()
    flights, producers = {}, {}
    # Same 4-worker pool and start policy in both arms. The barrier forces all
    # callers to register before any producer can finish; arrivals are controlled.
    with ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
        def caller(item):
            name, view = item
            sub = oid(root / "objects.git", view["tree"] + ":" + PACKAGE)
            if sub != view["package_oid"]:
                raise AssertionError("request resolved a different source view")
            key = ("regular-files-v1", PACKAGE, "**/*.yaml", sub) if share else name
            with lock:
                owner = key not in flights
                if owner:
                    flights[key] = Future()
                flight = flights[key]
            gate.wait()
            if owner:
                try:
                    stats = pool.submit(produce, root, view["tree"], cfg["window"], name).result()
                    with lock:
                        producers[name] = stats
                    flight.set_result((name, stats))
                except BaseException as exc:
                    flight.set_exception(exc)
                finally:
                    with lock:
                        del flights[key]
            leader, stats = flight.result()
            received = (root / (leader + ".out")).read_bytes()
            if (received != expected[view["kind"]] or stats["match_digest"] != view["expected_sha256"]
                    or stats["match_count"] != view["expected_count"] or not stats["done"]):
                raise AssertionError("wrong matched paths delivered to " + name)
            return name, {"producer": leader, "match_count": stats["match_count"],
                          "delivered_sha256": digest(received), "delivered_bytes": len(received)}

        with ThreadPoolExecutor(max_workers=len(views)) as callers:
            deliveries = dict(callers.map(caller, views.items()))
    expected_producers = len({v["package_oid"] for v in views.values()}) if share else len(views)
    if len(producers) != expected_producers or flights:
        raise AssertionError("cohort did not execute the expected producer groups")
    keys = ("entries_visited", "prefix_entries_replayed", "git_payload_bytes",
            "index_bytes_read", "index_bytes_written", "output_bytes", "reader_evictions")
    return {"coalesced": share, "producer_count": len(producers), "join_count": len(views) - len(producers),
            "request_count": len(views), "worker_process_limit": 4, "deliveries": deliveries,
            "producer_totals": {k: sum(s[k] for s in producers.values()) for k in keys}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path)
    parser.add_argument("--ref", default=REV)
    parser.add_argument("--window", type=int, default=64)
    parser.add_argument("--reps", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--arm", choices=["on", "off"])
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--check", action="store_true", help="Run one unmeasured shared group as a smoke test")
    args = parser.parse_args()
    if args.arm:
        print(json.dumps(cohort(args.prepared.resolve(), args.arm == "on")))
        return
    if not args.repo or args.window < 1 or args.reps < 1:
        parser.error("supply --repo and positive --window/--reps")
    with tempfile.TemporaryDirectory(prefix="dd-cohort-") as tmp:
        root = Path(tmp).resolve()
        cfg = prepare(args.repo, args.ref, root, args.window)
        oracle_path = Path(__file__).with_name("datadog_bazel_oracle.json")
        if oracle_path.exists():
            captured = json.loads(oracle_path.read_text(encoding="utf-8"))
            if captured["revision"] == cfg["source_commit"]:
                case = captured["cases"]["agent_notes"]
                if (case["source_subtree"], case["match_count"], case["match_digest"]) != (
                        cfg["base_package_tree"], cfg["base_count"], cfg["base_manifest_sha256"]):
                    raise AssertionError("independent base manifest differs from the Bazel query")
                cfg["base_bazel_oracle"] = {"version": captured["bazel"], "target": case["label"],
                                            "kind": case["kind"], "evidence": oracle_path.name}
        (root / "spec.json").write_text(json.dumps(cfg), encoding="utf-8")
        seed = {}
        for name, view in cfg["views"].items():
            if view["package_oid"] not in seed:
                seed[view["package_oid"]] = produce(root, view["tree"], args.window, "seed-" + name)
        cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "--prepared", str(root), "--arm"]
        if args.check:
            print(subprocess.check_output(cmd + ["on"], text=True))
            return
        trials = []
        # Separate sampled memory trials from latency replicates. All arms
        # start with every needed seek payload present; OS caches are uncontrolled.
        for i in range(args.reps + 1):
            sampled = i == args.reps
            for arm in (("off", "on") if i % 2 == 0 else ("on", "off")):
                out, metrics = measure(cmd + [arm], interval_ms=20 if sampled else None)
                result = json.loads(out)
                if result["producer_totals"]["index_bytes_written"]:
                    raise AssertionError("a purported warm trial built an index object")
                trials.append({"arm": arm, "round": i + 1, "sampled_memory_trial": sampled,
                               "measurement": metrics, **result})
        timing = {arm: statistics.median(t["measurement"]["elapsed_ms"] for t in trials
                                        if t["arm"] == arm and not t["sampled_memory_trial"])
                  for arm in ("on", "off")}
        report = {"host": {"os": platform.system(), "python": platform.python_version()},
                  "fixture": cfg, "oracle": "Independent recursive git ls-tree of pinned base, subpackage exclusions and explicit name edits; exact NUL-delimited bytes checked per caller.",
                  "schedule": "Eight registering threads meet at a barrier; four spawned process slots. Includes controller/pool start/exit, eight Git lookups and exact answer reads. Uncontrolled OS cache.",
                  "memory": "Separate 20 ms sampled sums of current process-tree RSS/working sets; shared pages can count twice and brief Git helpers can be missed.",
                  "seed_runs": seed, "trials": trials, "median_unsampled_group_ms": timing}
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
