#!/usr/bin/env python3
"""Generate a bounded deterministic Starlark workspace and exact cohort manifests."""

import argparse
from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULUS = 1_000_000_007


@dataclass(frozen=True)
class Shape:
    partitions: int
    layers: int
    width: int
    fan_in: int = 4
    payload_fields: int = 32
    payload_chars: int = 128
    retain_provider_graph: bool = False

    def validate(self):
        if not (2 <= self.partitions <= 16 and 2 <= self.layers <= 32
                and 2 <= self.width <= 64 and 1 <= self.fan_in <= self.width
                and 1 <= self.payload_fields <= 64 and 1 <= self.payload_chars <= 256
                and isinstance(self.retain_provider_graph, bool)
                and self.partitions * self.layers * self.width <= 16_384):
            raise ValueError("shape exceeds the bounded fixture envelope")


SIZES = {"quick": Shape(4, 8, 16), "default": Shape(8, 16, 32),
         "large": Shape(8, 32, 64)}


def name(partition, layer, column):
    return f"n_p{partition}_l{layer}_c{column}"


def graph(shape):
    shape.validate()
    return {name(partition, layer, column): [
        name(partition, layer - 1, (column + offset) % shape.width)
        for offset in range(shape.fan_in)] if layer else []
        for partition in range(shape.partitions)
        for layer in range(shape.layers) for column in range(shape.width)}


def roots(shape):
    return {f"root_p{partition}": [name(partition, shape.layers - 1, column)
                                  for column in range(shape.width)]
            for partition in range(shape.partitions)}


def closure(nodes, seeds):
    pending, visited = list(seeds), set()
    while pending:
        key = pending.pop()
        if key not in visited:
            visited.add(key)
            pending.extend(nodes[key])
    return visited


def outputs(shape, nodes):
    """Independent Python oracle: scalar recurrence plus explicit reachability sets."""
    checksums, payload_bytes = {}, {}
    for partition in range(shape.partitions):
        for layer in range(shape.layers):
            for column in range(shape.width):
                key = name(partition, layer, column)
                lengths = [len(f"{key}:f{index}:" + "x" * shape.payload_chars)
                           for index in range(shape.payload_fields)]
                code = (partition + 1) * 1000003 + (layer + 1) * 1009 + (column + 1) * 97
                payload_bytes[key] = sum(lengths)
                checksums[key] = (code * 31 + sum((i + 1) * n for i, n in enumerate(lengths))
                                  + sum((i + 1) * checksums[dep] + payload_bytes[dep]
                                        for i, dep in enumerate(nodes[key]))) % MODULUS
    result = {}
    for partition, (root, dependencies) in enumerate(roots(shape).items()):
        ids = sorted(closure(nodes, dependencies))
        checksum = sum((i + 1) * checksums[dep] for i, dep in enumerate(dependencies)) % MODULUS
        result[root + ".txt"] = (
            f"partition={partition}\nanalysis_nodes={len(ids)}\n"
            f"last_layer_inputs={len(dependencies)}\nchecksum={checksum}\n"
            f"last_layer_payload_bytes={sum(payload_bytes[dep] for dep in dependencies)}\n"
            f"ids={','.join(ids)}\n").encode()
    return result, sum(payload_bytes.values())


def encode(value):
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def fixture_files(shape):
    nodes = graph(shape)
    root_dependencies = roots(shape)
    expected, payload_total = outputs(shape, nodes)
    cohorts = {"broad": list(root_dependencies), "sparse": ["root_p0"],
               "disjoint": ["root_p0", f"root_p{shape.partitions - 1}"]}
    files = {
        "MODULE.bazel": b'module(name = "skycache_analysis_dag", version = "0.0.0")\n',
        "analysis_dag.bzl": (HERE / "analysis_dag.bzl").read_bytes(),
        "BUILD.bazel": ('load(":analysis_dag.bzl", "analysis_dag")\n\nanalysis_dag(\n' +
                        "".join(f"    {key} = {value},\n" for key, value in asdict(shape).items()) +
                        ")\n").encode(),
    }
    cohort_stats = {}
    for cohort, selected in cohorts.items():
        reachable = closure(nodes, [dep for root in selected for dep in root_dependencies[root]])
        manifest = {"schema_version": 1, "targets": ["//:" + root for root in selected],
                    "outputs": {root + ".txt": hashlib.sha256(expected[root + ".txt"]).hexdigest()
                                for root in selected}}
        files[cohort + ".json"] = encode(manifest)
        cohort_stats[cohort] = {
            "root_rule_targets": len(selected), "reachable_node_rule_targets": len(reachable),
            "reachable_node_dependency_edges": sum(len(nodes[key]) for key in reachable),
            "root_dependency_edges": sum(len(root_dependencies[root]) for root in selected),
            "targets": manifest["targets"],
        }
    topology = {
        "schema_version": 1, "shape": asdict(shape), "node_rule_targets": len(nodes),
        "retain_provider_graph": shape.retain_provider_graph,
        "root_rule_targets": len(root_dependencies),
        "node_dependency_edges": sum(map(len, nodes.values())),
        "root_dependency_edges": sum(map(len, root_dependencies.values())),
        "node_payload_ascii_characters": payload_total,
        "payload_measurement": "constructed string characters; not allocated or retained heap bytes",
        "depset_elements": "stable node ID strings; content equality, no provider-wrapper identity",
        "restored_provider_scope": (
            "Roots export NodeInfo with child-provider tuples and transitive ID depsets, retaining the provider DAG."
            if shape.retain_provider_graph else
            "Roots export only DefaultInfo; restoring root values does not retain the node-provider payload DAG."),
        "oracle_scope": "Exact root outputs; does not independently traverse restored child-provider fields or prove alias identity.",
        "partition_note": "No node-rule edges cross partitions; package/rule loading and Bazel infrastructure are shared.",
        "cohorts": cohort_stats,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "rules_sha256": hashlib.sha256(files["analysis_dag.bzl"]).hexdigest(),
        "expected_output_sha256": {key: hashlib.sha256(value).hexdigest() for key, value in expected.items()},
    }
    files["topology.json"] = encode(topology)
    return files


def generate(output, shape):
    output = Path(output)
    files = fixture_files(shape)
    output.mkdir(exist_ok=False)  # Never overwrite a prior fixture or mutate a measured workspace.
    for filename, data in files.items():
        (output / filename).write_bytes(data)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", choices=sorted(SIZES), default="default")
    parser.add_argument("--output", type=Path, required=True, help="new directory with an existing parent")
    parser.add_argument("--retain-provider-graph", action="store_true",
                        help="root providers retain child-provider tuples for encoded DAG hydration")
    args = parser.parse_args()
    print(generate(args.output, replace(SIZES[args.size], retain_provider_graph=args.retain_provider_graph)))


if __name__ == "__main__":
    main()
