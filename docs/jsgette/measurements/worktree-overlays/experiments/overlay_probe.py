"""Exercise source-view and operation identities in a temporary Git repository."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import tempfile
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Fixture:
    def __init__(self, scratch):
        self.repo = scratch / "work"
        self.repo.mkdir()
        empty = scratch / "empty"
        empty.mkdir()
        config = scratch / "empty.config"
        config.touch()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(
            GIT_CONFIG_NOSYSTEM="1", GIT_ATTR_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(config),
            GIT_AUTHOR_NAME="Overlay Study", GIT_COMMITTER_NAME="Overlay Study",
            GIT_AUTHOR_EMAIL="experiment@example.invalid", GIT_COMMITTER_EMAIL="experiment@example.invalid",
            GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00",
        )
        self.options = ["-c", "core.autocrlf=false", "-c", "core.filemode=false",
                        "-c", f"core.attributesFile={config}", "-c", f"core.excludesFile={config}",
                        "-c", f"core.hooksPath={empty}", "-c", "gc.auto=0"]
        self.git("init", "-q", "--object-format=sha1", "-b", "main", f"--template={empty}")
        for path, data in {"app/main.c": b"base\n", "app/old.h": b"old\n", "lib/math.c": b"lib\n"}.items():
            file = self.repo / path
            file.parent.mkdir(exist_ok=True)
            file.write_bytes(data)
        self.git("add", "-f", "--", "app/main.c", "app/old.h", "lib/math.c")
        self.git("commit", "-qm", "base")
        self.base = self.oid("HEAD")
        self.main = self.repo / "app/main.c"
        self.main.write_bytes(b"staged A\n")
        self.git("add", "--", "app/main.c")
        self.index_before = (self.repo / ".git/index").read_bytes()
        self.main.write_bytes(b"edit A\n")

    def git(self, *args, index=None, data=None):
        env = self.env if index is None else dict(self.env, GIT_INDEX_FILE=str(index))
        return subprocess.run(["git", *self.options, *args], cwd=self.repo, env=env,
                              input=data, stdout=subprocess.PIPE, check=True).stdout

    def oid(self, revision):
        return self.git("rev-parse", "--verify", revision).decode("ascii").strip()

    def publish(self, index, start_tree, edits):
        self.git("read-tree", start_tree, index=index)
        for path, data in edits.items():
            if data is None:
                self.git("update-index", "--force-remove", "--", path, index=index)
            else:
                blob = self.git("hash-object", "-w", "--stdin", "--no-filters", data=data).decode().strip()
                self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", index=index)
        return self.git("write-tree", index=index).decode().strip()


def operation_key(command, observations, mode="dbg", target="fixture-x"):
    encoded = json.dumps([command, mode, target, observations], separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def concurrent_batch(views, prepare, execute, while_first_waits=None):
    # Supply one OS thread per subscriber so the admission barrier cannot starve.
    # Only producers proceed to execute after all callers are registered.
    lock, started, registered = Lock(), Event(), Barrier(len(views), timeout=30)
    flights, counts, keys = {}, Counter(), {}

    def request(view):
        key, inputs = prepare(view)
        with lock:
            keys[view] = key
            counts["prepared_requests"] += 1
            producer = key not in flights
            if producer:
                future = flights[key] = Future()
                counts["producers"] += 1
            else:
                future = flights[key]
                counts["joins"] += 1
        started.set()
        registered.wait()
        if not producer:
            return future.result(timeout=30)
        try:
            result = execute(inputs)
            with lock:
                counts["executions"] += 1
            future.set_result(result)
            return result
        except BaseException as error:
            future.set_exception(error)
            raise
        finally:
            with lock:
                del flights[key]

    with ThreadPoolExecutor(max_workers=len(views)) as workers:
        submissions = [workers.submit(request, views[0])]
        require(started.wait(30), "first request did not register")
        if while_first_waits:
            while_first_waits()
        submissions.extend(workers.submit(request, name) for name in views[1:])
        results = {name: future.result(timeout=35) for name, future in zip(views, submissions)}
    require(not flights, "completed flight still registered")
    return dict(counts, active_records_after_completion=len(flights)), keys, results


def run(scratch, request_count):
    fixture = Fixture(scratch)
    roots = {"base": fixture.oid("HEAD^{tree}")}
    roots["A1"] = fixture.publish(scratch / "index-a", fixture.base, {"app/main.c": fixture.main.read_bytes()})
    new_header = {"app/old.h": None, "app/new.h": b"new\n"}
    roots["B1"] = fixture.publish(scratch / "index-b", fixture.base, new_header)
    # The oracles come from the declared fixture, independently of Git inspection.
    expected = {"A1": ("EDIT A\n", ["old.h"], False), "B1": ("BASE\n", ["new.h"], True)}
    for i in range(3, request_count + 1):
        name = f"V{i}"
        edits = {"app/main.c": f"edit {i}\n".encode()}
        if i % 2 == 0:
            edits.update(new_header)
        roots[name] = fixture.publish(scratch / ("index-" + name), fixture.base, edits)
        expected[name] = (f"EDIT {i}\n", ["new.h" if i % 2 == 0 else "old.h"], i % 2 == 0)
    views = list(expected)
    require(len({roots[name] for name in views}) == request_count, "views should be distinct")

    def library_inputs(view):
        path = "lib/math.c"
        blob = fixture.oid(f"{roots[view]}:{path}")
        return operation_key("uppercase/v1", [path, blob]), blob

    def advance_a():
        fixture.main.write_bytes(b"later A\n")
        roots["A2"] = fixture.publish(scratch / "index-a", roots["A1"], {"app/main.c": fixture.main.read_bytes()})

    lib_stats, lib_keys, library_results = concurrent_batch(
        views, library_inputs, lambda blob: fixture.git("cat-file", "blob", blob).upper(), advance_a)
    require(all(value == b"LIB\n" for value in library_results.values()), "library oracle mismatch")
    require(lib_stats["executions"] == 1 and lib_stats["joins"] == request_count - 1, "library sharing counts")

    def header_inputs(view):
        names = fixture.git("ls-tree", "-z", "--name-only", f"{roots[view]}:app").split(b"\0")
        headers = sorted(name.decode("ascii") for name in names if name.endswith(b".h"))
        return operation_key("header-name-manifest/v1", ["app/*.h", headers]), headers

    header_stats, header_keys, manifests = concurrent_batch(
        views, header_inputs, lambda headers: "\n".join(headers) + "\n")
    for view in views:
        require(manifests[view] == expected[view][1][0] + "\n", f"{view} manifest oracle")
    require(header_stats["executions"] == 2 and header_stats["joins"] == request_count - 2, "manifest sharing counts")

    # Additional control queries inspect one selected path, or one direct listing.
    expected["A2"] = ("LATER A\n", ["old.h"], False)
    observations, app_keys, list_keys, exists_keys = {}, {}, {}, {}
    for view, wanted in expected.items():
        tree = roots[view]
        blob = fixture.oid(f"{tree}:app/main.c")
        main = fixture.git("cat-file", "blob", blob).upper().decode()
        list_keys[view], headers = header_inputs(view)
        present = bool(fixture.git("ls-tree", "-z", "--name-only", tree, "--", "app/new.h"))
        require((main, headers, present) == wanted, f"{view} oracle mismatch")
        observations[view] = {"uppercase_main": main, "matching_header_names": headers, "new_header_exists": present}
        app_keys[view] = operation_key("uppercase/v1", ["app/main.c", blob])
        exists_keys[view] = operation_key("presence-report/v1", ["app/new.h", present])
    require(len(set(app_keys.values())) == len(expected), "different contents reused")
    for observed_keys in (list_keys, exists_keys):
        require(observed_keys["A1"] == observed_keys["A2"] != observed_keys["B1"], "bad membership equivalence")
    main_blob = fixture.oid(f"{roots['A1']}:app/main.c")
    config_keys = [operation_key("uppercase/v1", ["app/main.c", main_blob], mode, target)
                   for mode, target in (("dbg", "fixture-x"), ("opt", "fixture-x"), ("dbg", "fixture-y"))]
    require(len(set(config_keys)) == 3 and config_keys[0] == app_keys["A1"], "context omitted from key")

    # Deliberately unsound control. The second request reuses the first header manifest.
    bad_cache = {(fixture.base, "//app:header-names"): observations["A1"]["matching_header_names"]}
    bad_result = bad_cache[(fixture.base, "//app:header-names")]
    require(bad_result != expected["B1"][1], "negative control did not expose stale hit")
    require(roots["A1"] != roots["B1"] and lib_keys["A1"] == lib_keys["B1"], "full-root false miss control")
    require(fixture.oid("HEAD") == fixture.base, "HEAD changed")
    require((fixture.repo / ".git/index").read_bytes() == fixture.index_before, "real index changed")
    require(fixture.git("show", ":app/main.c") == b"staged A\n", "staged content changed")
    lib_trees = {fixture.oid(f"{root}:lib") for root in roots.values()}
    require(len(lib_trees) == 1, "library subtree diverged")
    return {
        "environment": {"os": platform.system(), "python": platform.python_version(),
                        "git": fixture.git("--version").decode().strip()},
        "base_commit": fixture.base, "tree_roots": roots, "common_lib_tree": lib_trees.pop(),
        "concurrent_overlay_requests_per_batch": len(views),
        "library_batch": {**lib_stats, "operation_classes": len(set(lib_keys.values())),
                          "per_caller_result": library_results["A1"].decode()},
        "header_manifest_batch": {**header_stats, "operation_classes": len(set(header_keys.values())),
                                  "outputs": dict(sorted(Counter(manifests.values()).items()))},
        "observations_after_A2": observations,
        "number_of_distinct_keys": {"app_transform": len(set(app_keys.values())),
                                    "header_name_manifest": len(set(list_keys.values())),
                                    "presence_report": len(set(exists_keys.values())),
                                    "one_file_three_declared_contexts": len(set(config_keys))},
        "controls": {"commit_and_label_returned_for_B1": bad_result, "B1_oracle": expected["B1"][1],
                     "root_keyed_library_entries": len(views), "observed_input_keyed_library_entries": len(set(lib_keys.values()))},
        "head_and_real_index_preserved": True, "oracle_checks_passed": True,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=8, help="Number of distinct overlays and real caller threads (minimum 3).")
    parser.add_argument("--out", type=Path, help="Save JSON to this path (also printed to stdout).")
    args = parser.parse_args()
    if args.requests < 3:
        parser.error("--requests must be at least 3")
    with tempfile.TemporaryDirectory(prefix="overlay-probe-") as directory:
        results = run(Path(directory), args.requests)
    report = json.dumps(results, indent=2) + "\n"
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    print(report, end="")
