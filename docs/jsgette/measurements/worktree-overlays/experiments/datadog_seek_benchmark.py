"""Gate a seekable cursor on real Bazel, then measure it on pinned Datadog Agent trees."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
from bazel_glob_fixture import create_fixture, oracle
from measure_process import measure


HERE = Path(__file__).resolve().parent
REVISION = "85ad968bca856fa3d4e07b4d8e11961dabe6866f"


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_REPLACE_OBJECTS="1")
    return subprocess.check_output(["git", "-C", str(repo), *args], env=env).decode().strip()


def worker(repo, tree, case, strategy, window, index, *extra):
    args = [sys.executable, "-B", str(HERE / "seekable_tree.py"),
            "--repo", str(repo), "--tree", tree, "--package", case["package"], "--suffix", case["suffix"],
            "--strategy", strategy, "--window", str(window), "--index-dir", str(index)]
    if not case["recursive"]:
        args.append("--direct-only")
    return args + list(map(str, extra))


def execute(command, interval=None):
    stdout, measurement = measure(command, interval_ms=interval)
    return json.loads(stdout), measurement


def verify(stats, expected):
    if (not stats["done"] or stats["match_count"] != expected["match_count"]
            or stats["match_digest"] != expected["match_digest"]):
        raise AssertionError(f"cursor result {stats} does not match reference {expected}")


def fixture_gate(root, bazel):
    """A process really exits with an incomplete checkpoint before the larger test."""
    repo, views = create_fixture(root / "fixture")
    case = {"package": "app", "suffix": ".txt", "recursive": True}
    tree = views["base"]
    fresh = oracle(repo, tree, root / "real-bazel", bazel)
    names = sorted(p.encode() for p in fresh["matches"])
    expected = {"match_count": len(names), "match_digest": hashlib.sha256(
        b"".join(p + b"\0" for p in names)).hexdigest()}
    idx = root / "fixture-index"
    base = worker(repo, tree, case, "seek", 32, idx)
    checkpoint = root / "checkpoint.json"
    sink = root / "fixture-matches"
    pause, _ = execute(base + ["--checkpoint", str(checkpoint), "--max-windows", "1",
                               "--matches-out", str(sink)])
    position = json.loads(checkpoint.read_text(encoding="utf-8"))["frames"][-1]
    if (pause["done"] or pause["entries_visited"] != 32 or position["path"] != "wide/"
            or position["ordinal"] <= 0 or position["offset"] <= 0):
        raise AssertionError(f"fixture did not stop mid-directory: {position}")
    resumed, _ = execute(base + ["--checkpoint", str(checkpoint), "--matches-out", str(sink)])
    verify(resumed, expected)
    actual = sink.read_bytes().split(b"\0")
    if actual[-1] != b"" or sorted(actual[:-1]) != names:
        raise AssertionError("resumed paths disagree with fresh Bazel labels")
    plain, _ = execute(worker(repo, tree, case, "ordinal", 0, idx))
    verify(plain, expected)
    if resumed["prefix_entries_replayed"] or resumed["entries_visited"] != plain["entries_visited"]:
        raise AssertionError("seekable resume revisited completed directory entries")
    return {"bazel": subprocess.check_output([bazel, "--version"], cwd=repo, text=True).strip(),
            "tree": tree, "reference": expected, "fresh_bazel_paths": fresh["matches"],
            "saved_position": position, "pause": pause, "new_process_resume": resumed, "uninterrupted": plain}


def run_case(repo, tree, case, expected, scratch, windows, rounds):
    arms = [("uninterrupted", "ordinal", 0)] + [
        (f"{name}_w{w}", strategy, w) for w in windows
        for name, strategy in (("ordinal", "ordinal"), ("seek_empty", "seek"), ("seek_warm", "seek"),
                               ("indexed_ordinal_warm", "indexed-ordinal"))]
    warm = scratch / "seeded-index"
    seed, _ = execute(worker(repo, tree, case, "seek", 0, warm))
    verify(seed, expected)
    observations = {name: {"wall_ms": [], "scan_ms": []} for name, _, _ in arms}
    for r in range(rounds + 1):
        # With the default nine rounds, each of the nine arms occupies each
        # position once. Memory sampling is a final, separate measurement.
        sample = r == rounds
        order = arms[r % len(arms):] + arms[:r % len(arms)]
        for name, strategy, window in order:
            with tempfile.TemporaryDirectory(dir=scratch) as trial:
                index = warm if "_warm_" in name else Path(trial) / "index"
                result, measurement = execute(worker(repo, tree, case, strategy, window, index),
                                              interval=20 if sample else None)
                verify(result, expected)
            row = observations[name]
            if sample:
                row["sampled_run"] = {"worker": result, "measurement": measurement}
            else:
                row["wall_ms"].append(measurement["elapsed_ms"])
                row["scan_ms"].append(round(1000 * result["elapsed_seconds"], 3))
    for row in observations.values():
        row["median_wall_ms"] = round(statistics.median(row["wall_ms"]), 3)
        row["median_scan_ms"] = round(statistics.median(row["scan_ms"]), 3)
    return {"case": case, "expected": {"match_count": expected["match_count"],
                                       "match_digest": expected["match_digest"]},
            "warm_index_seed": seed, "arms": observations}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", type=Path, help="Existing Datadog Agent checkout or bare repo")
    cli.add_argument("--bazel", default="bazel", help="Bazel 9.3.1 for the functional gate")
    cli.add_argument("--oracle", type=Path, default=HERE / "datadog_bazel_oracle.json")
    cli.add_argument("--gate-only", action="store_true")
    cli.add_argument("--rounds", type=int, default=9)
    cli.add_argument("--windows", type=int, nargs="+", default=[32, 256])
    cli.add_argument("--only", nargs="+", help="Restrict the set of recorded cases")
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    if args.rounds < 1 or any(w < 1 for w in args.windows) or len(set(args.windows)) != len(args.windows):
        cli.error("rounds and distinct eviction windows must be positive")
    if not args.gate_only and not args.repo:
        cli.error("--repo is required for the larger benchmark")
    bazel = shutil.which(args.bazel)
    if not bazel:
        cli.error(f"fixture Bazel unavailable: {args.bazel}")
    with tempfile.TemporaryDirectory(prefix="ddseek-") as temp:
        scratch = Path(temp)
        out = {"host": {"os": platform.platform(), "python": platform.python_version(),
                        "logical_cpus": os.cpu_count()}, "fixture_gate": fixture_gate(scratch, bazel)}
        if not args.gate_only:
            from datadog_cases import CASES
            roots = json.loads(args.oracle.read_text(encoding="utf-8"))
            if roots["revision"] != REVISION:
                raise ValueError("oracle commit differs from benchmark commit")
            repo = args.repo.resolve()
            tree = git(repo, "rev-parse", "--verify", REVISION + "^{tree}")
            out.update(revision=REVISION, tree=tree, git=git(repo, "--version"), rounds=args.rounds,
                       windows=args.windows, cases={})
            selected = [c for c in CASES if not args.only or c["name"] in args.only]
            if not selected or (args.only and set(args.only) != {c["name"] for c in selected}):
                cli.error("unknown or empty case selection")
            for case in selected:
                name = case["name"]
                print(f"Benchmarking {name} ({args.rounds} uninstrumented rounds + memory sample)",
                      file=sys.stderr, flush=True)
                folder = scratch / name
                folder.mkdir()
                out["cases"][name] = run_case(repo, tree, case, roots["cases"][name], folder,
                                              args.windows, args.rounds)
    data = json.dumps(out, indent=2) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8")
    else:
        print(data, end="")


if __name__ == "__main__":
    main()
