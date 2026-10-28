import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import generate_action_fanout as fixture


class ActionFanoutTest(unittest.TestCase):
    def test_tiny_oracle_has_every_output_and_exact_content(self):
        outputs = fixture.expected_outputs(fixture.Shape(2, 2, 2), [0, 1])
        self.assertEqual(len(outputs), 8)
        self.assertEqual(outputs["writer_p1_w0_o1.txt"],
                         b"skycache_action_fanout_v1\noutput=writer_p1_w0_o1.txt\ntag=fixture\n")
        self.assertEqual(set(outputs), {
            "writer_p0_w0_o0.txt", "writer_p0_w0_o1.txt", "writer_p0_w1_o0.txt", "writer_p0_w1_o1.txt",
            "writer_p1_w0_o0.txt", "writer_p1_w0_o1.txt", "writer_p1_w1_o0.txt", "writer_p1_w1_o1.txt"})

    def test_default_scope_and_overlapped_demand_do_not_add_outputs(self):
        files = fixture.fixture_files(fixture.SIZES["default"])
        broad, sparse, overlapped = [json.loads(files[name + ".json"])
                                     for name in ["broad", "sparse", "overlapped"]]
        self.assertEqual(len(broad["outputs"]), 4096)
        self.assertEqual(len(sparse["outputs"]), 512)
        self.assertEqual(len(broad["targets"]), 8)
        self.assertEqual(len(overlapped["targets"]), 520)
        self.assertEqual(len(set(overlapped["targets"])), 520)
        self.assertEqual(overlapped["outputs"], broad["outputs"])
        self.assertEqual(overlapped["targets"][:8], broad["targets"])
        self.assertEqual(set(overlapped["targets"][8:]),
                         {f"//:writer_p{p}_w{w}" for p in range(8) for w in range(64)})
        self.assertTrue(set(sparse["outputs"]).issubset(broad["outputs"]))
        self.assertTrue(all(broad["outputs"][name] == digest for name, digest in sparse["outputs"].items()))
        topology = json.loads(files["topology.json"])
        self.assertEqual(topology["declared_file_write_actions"], 4096)
        self.assertEqual(topology["root_dependency_edges"], 512)

    def test_shared_case_has_one_output_for_two_owners(self):
        files = fixture.semantic_files()
        shared = json.loads(files["shared.json"])
        self.assertEqual(shared["targets"], ["//semantics:shared_left", "//semantics:shared_right"])
        self.assertEqual(shared["outputs"], {"semantics/shared.txt": hashlib.sha256(
            b"skycache_action_fanout_v1\noutput=shared.txt\ntag=same\n").hexdigest()})
        cases = json.loads(files["semantic-cases.json"])
        self.assertEqual(cases["success_case"]["declared_actions"], 2)
        self.assertEqual(cases["success_case"]["distinct_output_paths"], 1)
        self.assertNotIn("outputs", cases)  # Deliberately not a success-only harness manifest.
        failures = cases["failure_cases"]
        self.assertEqual({case["name"] for case in failures},
                         {"different_content", "different_executable_bit", "output_path_prefix"})
        self.assertFalse(set(shared["targets"]) & {target for case in failures for target in case["targets"]})

    def test_semantic_declarations_change_only_intended_key_or_path(self):
        build = fixture.semantic_files()["semantics/BUILD.bazel"].decode()
        # The executable-bit conflict must keep equal bytes, or it would only test content conflict.
        self.assertIn('name = "mode_left", paths = ["mode.txt"], content_tag = "same", executable = False', build)
        self.assertIn('name = "mode_right", paths = ["mode.txt"], content_tag = "same", executable = True', build)
        self.assertIn('name = "content_left", paths = ["content.txt"], content_tag = "left"', build)
        self.assertIn('name = "content_right", paths = ["content.txt"], content_tag = "right"', build)
        self.assertIn('paths = ["tree"]', build)
        self.assertIn('paths = ["tree/leaf.txt"]', build)

    def test_output_manifest_hashes_are_exact(self):
        shape = fixture.Shape(1, 1, 1)
        files = fixture.fixture_files(shape)
        broad = json.loads(files["broad.json"])
        self.assertEqual(broad["outputs"], {"writer_p0_w0_o0.txt": hashlib.sha256(
            b"skycache_action_fanout_v1\noutput=writer_p0_w0_o0.txt\ntag=fixture\n").hexdigest()})
        self.assertEqual(broad["targets"], ["//:actions_p0"])

    def test_reproducible_generation_never_overwrites(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture.generate(root / "first", fixture.SIZES["quick"])
            fixture.generate(root / "second", fixture.SIZES["quick"])
            inventory = lambda path: {str(file.relative_to(path)): file.read_bytes()
                                      for file in path.rglob("*") if file.is_file()}
            before = inventory(root / "first")
            self.assertEqual(before, inventory(root / "second"))
            with self.assertRaises(FileExistsError):
                fixture.generate(root / "first", fixture.SIZES["default"])
            self.assertEqual(inventory(root / "first"), before)

    def test_invalid_shape_fails_before_creating_directory(self):
        for shape in [fixture.Shape(0, 1, 1), fixture.Shape(17, 1, 1),
                      fixture.Shape(1, 129, 1), fixture.Shape(1, 1, 33),
                      fixture.Shape(16, 128, 32), fixture.Shape(True, 1, 1),
                      fixture.Shape(1, 1.0, 1)]:
            with self.subTest(shape=shape), tempfile.TemporaryDirectory() as temporary:
                output = Path(temporary) / "invalid"
                with self.assertRaises(ValueError):
                    fixture.generate(output, shape)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
