#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Generate a deterministic native producer/consumer workload for lazy graph profiling."""

import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=512)
    args = parser.parse_args()
    if args.pairs < 2:
        parser.error("--pairs must be at least two")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "MODULE.bazel").write_text("module(name='lazy_profile')\n")
    (root / "input.txt").write_text("fixture-input\n")
    lines = []
    for i in range(args.pairs):
        padding = f"payload-{i:04d}-" + "x" * 8180
        lines += [
            f'genrule(name="a{i}", srcs=["input.txt"], outs=["a{i}.out"], cmd="cat $(SRCS) > $@ # {padding}")',
            f'genrule(name="b{i}", srcs=[":a{i}"], outs=["b{i}.out"], cmd="cat $(SRCS) > $@ # {padding}-consumer")',
        ]
    for name, indexes in [("left", range(args.pairs // 2)),
                          ("right", range(args.pairs // 2, args.pairs)),
                          ("all", range(args.pairs))]:
        lines.append(f'filegroup(name="{name}", srcs={[f":b{i}" for i in indexes]!r})')
    (root / "BUILD.bazel").write_text("\n".join(lines) + "\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "MODULE.bazel", "BUILD.bazel", "input.txt"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c",
                    "user.email=fixture@example.invalid", "commit", "-qm",
                    "Deterministic native genrule paging fixture"], cwd=root, check=True)


if __name__ == "__main__":
    main()
