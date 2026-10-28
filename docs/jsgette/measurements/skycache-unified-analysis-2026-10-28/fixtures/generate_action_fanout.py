#!/usr/bin/env python3
"""Generate bounded FileWrite workloads and separate action-sharing semantic cases."""

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Shape:
    partitions: int
    writers_per_partition: int
    outputs_per_writer: int

    def validate(self):
        values = asdict(self).values()
        if (not all(type(value) is int and value > 0 for value in values)
                or self.partitions > 16 or self.writers_per_partition > 128
                or self.outputs_per_writer > 32
                or self.partitions * self.writers_per_partition * self.outputs_per_writer > 16_384):
            raise ValueError("shape exceeds the bounded fixture envelope")


SIZES = {"quick": Shape(4, 8, 4), "default": Shape(8, 64, 8), "large": Shape(8, 128, 16)}


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def content(path, tag="fixture"):
    """Exact byte oracle, independent of Bazel execution and output path prefixes."""
    return f"skycache_action_fanout_v1\noutput={path}\ntag={tag}\n".encode()


def writer_name(partition, writer):
    return f"writer_p{partition}_w{writer}"


def expected_outputs(shape, partitions):
    return {
        f"{writer_name(partition, writer)}_o{output}.txt": content(
            f"{writer_name(partition, writer)}_o{output}.txt")
        for partition in partitions for writer in range(shape.writers_per_partition)
        for output in range(shape.outputs_per_writer)
    }


def manifest(targets, outputs):
    return {"schema_version": 1, "targets": targets,
            "outputs": {path: hashlib.sha256(data).hexdigest() for path, data in outputs.items()}}


def semantic_files():
    """Small, separately requested cases; negative cases are not success manifests."""
    definitions = [
        ("shared_left", "shared.txt", "same", False),
        ("shared_right", "shared.txt", "same", False),
        ("content_left", "content.txt", "left", False),
        ("content_right", "content.txt", "right", False),
        ("mode_left", "mode.txt", "same", False),
        ("mode_right", "mode.txt", "same", True),
        ("prefix_file", "tree", "same", False),
        ("prefix_child", "tree/leaf.txt", "same", False),
    ]
    build = 'load("//:action_fanout.bzl", "action_writer")\n\n'
    for name, path, tag, executable in definitions:
        build += (f'action_writer(name = "{name}", paths = ["{path}"], '
                  f'content_tag = "{tag}", executable = {executable})\n')
    cases = {
        "schema_version": 1,
        "status": "generated expectations; native outcomes must be checked before claiming coverage",
        "success_manifest": "shared.json",
        "success_case": {"targets": ["//semantics:shared_left", "//semantics:shared_right"],
                         "declared_actions": 2, "distinct_output_paths": 1,
                         "claim": "equal FileWrite keys and output paths can share across two owners"},
        "failure_cases": [
            {"name": "different_content", "targets": ["//semantics:content_left", "//semantics:content_right"],
             "expected_failure_kind": "action conflict"},
            {"name": "different_executable_bit", "targets": ["//semantics:mode_left", "//semantics:mode_right"],
             "expected_failure_kind": "action conflict"},
            {"name": "output_path_prefix", "targets": ["//semantics:prefix_file", "//semantics:prefix_child"],
             "expected_failure_kind": "artifact prefix conflict"},
        ],
        "validation": "Failure cases require nonzero exit and matching structured failure/diagnostic evidence; never pass them to the success-only paired harness.",
    }
    return {
        "semantics/BUILD.bazel": build.encode(),
        "shared.json": encode(manifest(cases["success_case"]["targets"],
                                       {"semantics/shared.txt": content("shared.txt", "same")})),
        "semantic-cases.json": encode(cases),
    }


def fixture_files(shape):
    shape.validate()
    root_targets = [f"//:actions_p{partition}" for partition in range(shape.partitions)]
    writers = [f"//:{writer_name(partition, writer)}" for partition in range(shape.partitions)
               for writer in range(shape.writers_per_partition)]
    all_outputs = expected_outputs(shape, range(shape.partitions))
    cohorts = {
        "broad": (root_targets, all_outputs),
        "sparse": ([root_targets[0]], expected_outputs(shape, [0])),
        "overlapped": (root_targets + writers, all_outputs),
    }
    files = {
        "MODULE.bazel": b'module(name = "skycache_action_fanout", version = "0.0.0")\n',
        "action_fanout.bzl": (HERE / "action_fanout.bzl").read_bytes(),
        "BUILD.bazel": ('load(":action_fanout.bzl", "action_fanout")\n\naction_fanout(\n' +
                        "".join(f"    {key} = {value},\n" for key, value in asdict(shape).items()) +
                        ")\n").encode(),
    }
    for cohort, (targets, outputs) in cohorts.items():
        files[cohort + ".json"] = encode(manifest(targets, outputs))
    files.update(semantic_files())
    files["topology.json"] = encode({
        "schema_version": 1, "shape": asdict(shape),
        "writer_rule_targets": len(writers), "root_rule_targets": len(root_targets),
        "root_dependency_edges": len(writers),
        "declared_file_write_actions": len(all_outputs), "distinct_output_paths": len(all_outputs),
        "output_content_bytes": sum(map(len, all_outputs.values())),
        "cohorts": {name: {"requested_targets": len(targets), "expected_outputs": len(outputs),
                           "targets": targets} for name, (targets, outputs) in cohorts.items()},
        "overlap": "Overlapped requests every root and every writer already reachable from those roots; it adds no fixture actions or output paths.",
        "scope": "Action-owner graph collection and registration, with toolchain-free FileWrite actions. Broad workload has distinct output paths, not conflicting or shareable duplicate actions.",
        "limitations": [
            "4096-action default has 512 writer rules plus 8 roots; it is not an ALV-count-matched comparison to the 4096-node provider DAG.",
            "File writing, artifact materialization, BEP size, and output hashing grow with output count; whole-command differences do not isolate conflict checking.",
            "Shared and conflicting action semantics are separate tiny cases; generated expectations are not native correctness evidence.",
            "Exact output hashes do not establish arbitrary restored provider identity, action-key equivalence, or full-build speedups.",
        ],
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rules_sha256": hashlib.sha256(files["action_fanout.bzl"]).hexdigest(),
    })
    return files


def generate(output, shape):
    output = Path(output)
    files = fixture_files(shape)
    output.mkdir(exist_ok=False)
    for name, data in files.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=sorted(SIZES), default="default")
    parser.add_argument("--output", type=Path, required=True, help="new directory with an existing parent")
    args = parser.parse_args()
    print(generate(args.output, SIZES[args.size]))


if __name__ == "__main__":
    main()
