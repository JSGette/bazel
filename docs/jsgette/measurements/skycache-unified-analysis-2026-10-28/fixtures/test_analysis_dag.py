"""Topology/oracle checks for generated fixtures; these tests do not execute Bazel."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

PATH = Path(__file__).with_name("generate_analysis_dag.py")
SPEC = importlib.util.spec_from_file_location("generate_analysis_dag", PATH)
dag = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = dag
SPEC.loader.exec_module(dag)


class AnalysisDagTest(unittest.TestCase):
    def test_default_broad_sparse_and_disjoint_have_expected_work(self):
        shape = dag.SIZES["default"]
        files = dag.fixture_files(shape)
        topology = json.loads(files["topology.json"])
        self.assertEqual(topology["node_rule_targets"], 4096)
        self.assertEqual(topology["node_dependency_edges"], 15_360)
        self.assertEqual(topology["root_dependency_edges"], 256)
        self.assertEqual([topology["cohorts"][cohort]["reachable_node_rule_targets"]
                          for cohort in ("broad", "sparse", "disjoint")], [4096, 512, 1024])
        self.assertEqual([len(json.loads(files[cohort + ".json"])["outputs"])
                          for cohort in ("broad", "sparse", "disjoint")], [8, 1, 2])

    def test_partitions_are_disjoint_and_all_root_nodes_reachable(self):
        shape = dag.SIZES["quick"]
        nodes, roots = dag.graph(shape), dag.roots(shape)
        closures = [dag.closure(nodes, dependencies) for dependencies in roots.values()]
        self.assertEqual(set.union(*closures), set(nodes))
        for index, current in enumerate(closures):
            self.assertEqual(len(current), shape.layers * shape.width)
            for other in closures[index + 1:]:
                self.assertFalse(current.intersection(other))
        for key, dependencies in nodes.items():
            layer = int(key.split("_l")[1].split("_")[0])
            self.assertEqual(len(dependencies), shape.fan_in if layer else 0)
            self.assertEqual(len(set(dependencies)), len(dependencies))
            self.assertTrue(all(int(dep.split("_l")[1].split("_")[0]) == layer - 1
                                for dep in dependencies))

    def test_small_oracle_matches_hand_calculated_scalar_values_and_ids(self):
        shape = dag.Shape(2, 2, 2, fan_in=2, payload_fields=1, payload_chars=1)
        outputs, payload = dag.outputs(shape, dag.graph(shape))
        self.assertEqual(payload, 120)
        self.assertEqual(outputs["root_p0.txt"], (
            "partition=0\nanalysis_nodes=4\nlast_layer_inputs=2\nchecksum=372524697\n"
            "last_layer_payload_bytes=30\n"
            "ids=n_p0_l0_c0,n_p0_l0_c1,n_p0_l1_c0,n_p0_l1_c1\n").encode())
        self.assertIn(b"checksum=744525813\n", outputs["root_p1.txt"])

    def test_payload_is_observable_in_expected_output(self):
        first = dag.Shape(2, 2, 2, fan_in=2, payload_fields=1, payload_chars=1)
        second = dag.Shape(2, 2, 2, fan_in=2, payload_fields=1, payload_chars=2)
        self.assertNotEqual(dag.outputs(first, dag.graph(first))[0],
                            dag.outputs(second, dag.graph(second))[0])

    def test_retained_provider_variant_changes_input_identity_not_output_oracle(self):
        original = dag.SIZES["default"]
        retained = dag.replace(original, retain_provider_graph=True)
        plain_files = dag.fixture_files(original)
        retained_files = dag.fixture_files(retained)
        self.assertIn(b"retain_provider_graph = False", plain_files["BUILD.bazel"])
        self.assertIn(b"retain_provider_graph = True", retained_files["BUILD.bazel"])
        self.assertNotEqual(plain_files["BUILD.bazel"], retained_files["BUILD.bazel"])
        plain_topology = json.loads(plain_files["topology.json"])
        retained_topology = json.loads(retained_files["topology.json"])
        self.assertFalse(plain_topology["retain_provider_graph"])
        self.assertTrue(retained_topology["retain_provider_graph"])
        self.assertNotEqual(plain_topology["restored_provider_scope"],
                            retained_topology["restored_provider_scope"])
        self.assertEqual(plain_topology["expected_output_sha256"],
                         retained_topology["expected_output_sha256"])
        for cohort in ("broad", "sparse", "disjoint"):
            self.assertEqual(plain_files[cohort + ".json"], retained_files[cohort + ".json"])
        self.assertEqual(dag.outputs(original, dag.graph(original))[0],
                         dag.outputs(retained, dag.graph(retained))[0])

    def test_generation_is_byte_deterministic_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = dag.generate(root / "first", dag.SIZES["quick"])
            second = dag.generate(root / "second", dag.SIZES["quick"])
            self.assertEqual({p.name: p.read_bytes() for p in first.iterdir()},
                             {p.name: p.read_bytes() for p in second.iterdir()})
            marker = (first / "BUILD.bazel").read_bytes()
            with self.assertRaises(FileExistsError):
                dag.generate(first, dag.SIZES["quick"])
            self.assertEqual((first / "BUILD.bazel").read_bytes(), marker)

    def test_out_of_bounds_shapes_fail_before_creating_output(self):
        invalid = (dag.Shape(1, 2, 2), dag.Shape(8, 64, 64),
                   dag.Shape(2, 2, 2, fan_in=3), dag.Shape(2, 2, 2, payload_fields=0))
        with tempfile.TemporaryDirectory() as temporary:
            for index, shape in enumerate(invalid):
                output = Path(temporary) / str(index)
                with self.assertRaises(ValueError):
                    dag.generate(output, shape)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
