"""C-backed fake publication stores only. Never launch capture against KNOWN_STORE."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import capture_skycache_metadata as capture
import decode_skycache_witnesses as decoder
from test_decode_skycache_witnesses import block, manifest, NAMESPACE


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".metadata-capture-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.store = self.root / "fake-store"
        self.namespace = self.store / "leased-v2/publications-v4" / NAMESPACE
        self.namespace.mkdir(parents=True)
        self.ids = ["%064x" % index for index in range(1, 11)]
        for index, publication in enumerate(self.ids):
            records = [("external/repository", False, "%064x" % index),
                       ("external/repository/input.txt", False, "c" * 64)]
            encoded, descriptor = block(records)
            path = self.namespace / (publication + ".manifest")
            path.write_bytes(manifest([descriptor], publication=publication))
            os.utime(path, ns=(1_000_000_000 + index, 1_000_000_000 + index))
            objects = self.store / "leased-v2/objects/segmented-v1" / publication
            objects.mkdir(parents=True)
            (objects / "witnesses-00000.block").write_bytes(encoded)
            (objects / "index").write_text("must not read", encoding="utf-8")
            (objects / "values.pack").write_text("must not read", encoding="utf-8")

    def inventory(self):
        return capture.capture_inventory(self.store, self.root / "inventory")

    def pair(self, result, publications=None):
        return capture.capture_pair(self.store, result["inventory"], result["inventory_sha256"],
                                    publications or self.ids[:2], self.root / "pair")

    def test_inventory_opens_only_ten_manifests_and_records_selection_uncertainty(self):
        read = decoder.read_snapshot
        reads = []
        def tracked(path, *args):
            reads.append(Path(path))
            self.assertEqual(Path(path).suffix, ".manifest")
            return read(path, *args)
        with patch.object(decoder, "read_snapshot", side_effect=tracked):
            result = self.inventory()
        self.assertEqual(len(reads), 10)
        report = json.loads(Path(result["inventory"]).read_bytes())
        self.assertEqual(report["source_blocks_read"], 0)
        self.assertEqual(result["suggested_pair_by_oldest_mtime"], {"namespace": NAMESPACE, "publications": self.ids[:2]})
        self.assertIn("heuristic", report["pair_selection_note"])
        self.assertEqual(decoder.sha256(Path(result["inventory"]).read_bytes()), result["inventory_sha256"])
        self.assertEqual(len(list((self.root / "inventory/manifests" / NAMESPACE).iterdir())), 10)

    def test_pair_reads_only_selected_named_blocks_and_decodes_copies(self):
        result = self.inventory()
        read = decoder.read_snapshot
        runtime_reads = []
        def tracked(path, *args):
            path = Path(path)
            if path.is_relative_to(self.store):
                runtime_reads.append(path)
                self.assertEqual(path.name, "witnesses-00000.block")
                self.assertIn(path.parent.name, self.ids[:2])
            return read(path, *args)
        with patch.object(decoder, "read_snapshot", side_effect=tracked), patch.object(capture, "bounded_entries", side_effect=AssertionError("pair must not enumerate")):
            output = self.pair(result)
        self.assertEqual(len(runtime_reads), 2)
        comparison = json.loads(Path(output["comparison"]).read_bytes())
        self.assertEqual(comparison["identical_witnesses"], 1)
        self.assertEqual(comparison["differing_witnesses"], 1)
        difference = comparison["differences"][0]
        self.assertEqual(difference["path"], "external/repository")
        self.assertTrue(difference["repository_binding_shape"])
        self.assertEqual(difference["left"], "0" * 64)
        self.assertEqual(difference["right"], "%064x" % 1)
        self.assertEqual(output["capture_sha256"], decoder.sha256(Path(output["capture"]).read_bytes()))
        copied_names = {p.name for p in (self.root / "pair" / self.ids[0]).iterdir()}
        self.assertEqual(copied_names, {self.ids[0] + ".manifest", "witnesses-00000.block", "decoded.json"})
        self.assertIn("historical first-rejection", comparison["interpretation"])

    def test_namespace_count_bound_stops_before_content_reads(self):
        (self.namespace.parent / ("e" * 64)).mkdir()
        (self.namespace.parent / ("f" * 64)).mkdir()
        with patch.object(decoder, "read_snapshot", side_effect=AssertionError("must not read")):
            with self.assertRaisesRegex(ValueError, "entry bound"):
                self.inventory()

    def test_manifest_count_bound_stops_before_content_reads(self):
        for index in range(100, 123):
            (self.namespace / ("%064x.manifest" % index)).write_bytes(b"not read")
        with patch.object(decoder, "read_snapshot", side_effect=AssertionError("must not read")):
            with self.assertRaisesRegex(ValueError, "entry bound"):
                self.inventory()

    def test_changed_count_is_recorded_instead_of_assuming_historical_ten(self):
        (self.namespace / (self.ids[-1] + ".manifest")).unlink()
        result = self.inventory()
        report = json.loads(Path(result["inventory"]).read_bytes())
        self.assertEqual(report["manifest_count"], 9)
        self.assertEqual(report["archived_history"][0]["publication_count"], 10)

    def test_twenty_publications_in_two_namespaces_keep_pair_in_oldest_namespace(self):
        namespace = "b" * 64
        directory = self.namespace.parent / namespace
        directory.mkdir()
        ids = ["%064x" % index for index in range(21, 31)]
        for publication in ids:
            (directory / (publication + ".manifest")).write_bytes(manifest(publication=publication, namespace=namespace))
        result = self.inventory()
        report = json.loads(Path(result["inventory"]).read_bytes())
        self.assertEqual(report["manifest_count"], 20)
        self.assertEqual(report["namespace_count"], 2)
        self.assertEqual([row["manifest_count"] for row in report["namespaces"]], [10, 10])
        self.assertEqual(result["suggested_pair_by_oldest_mtime"], {"namespace": NAMESPACE, "publications": self.ids[:2]})
        with self.assertRaisesRegex(ValueError, "same namespace"):
            self.pair(result, [self.ids[0], ids[0]])

    def test_missing_historic_pair_never_silently_suggests_newer_namespace(self):
        for publication in self.ids[1:]:
            (self.namespace / (publication + ".manifest")).unlink()
        namespace = "b" * 64
        directory = self.namespace.parent / namespace
        directory.mkdir()
        for index in (21, 22):
            publication = "%064x" % index
            (directory / (publication + ".manifest")).write_bytes(manifest(publication=publication, namespace=namespace))
        result = self.inventory()
        self.assertIsNone(result["suggested_pair_by_oldest_mtime"])

    def test_unexpected_namespace_entry_rejects_without_copying(self):
        original = self.namespace / (self.ids[-1] + ".manifest")
        original.rename(self.namespace / "unknown.data")
        with self.assertRaisesRegex(ValueError, "publication filename"):
            self.inventory()
        self.assertFalse((self.root / "inventory").exists())

    def test_inventory_hash_and_copied_manifest_hash_are_required(self):
        result = self.inventory()
        with self.assertRaisesRegex(ValueError, "inventory SHA"):
            self.pair({**result, "inventory_sha256": "a" * 64})
        copied = self.root / "inventory/manifests" / NAMESPACE / (self.ids[0] + ".manifest")
        copied.write_bytes(copied.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            self.pair(result)
        self.assertFalse((self.root / "pair").exists())

    def test_source_block_hash_failure_preserves_partial_evidence_without_completion(self):
        result = self.inventory()
        origin = self.store / "leased-v2/objects/segmented-v1" / self.ids[0] / "witnesses-00000.block"
        data = origin.read_bytes()
        changed = data[:-1] + bytes([data[-1] ^ 1])
        origin.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.pair(result)
        self.assertEqual(origin.read_bytes(), changed)
        self.assertTrue((self.root / "pair" / self.ids[0] / (self.ids[0] + ".manifest")).exists())
        self.assertFalse((self.root / "pair/capture.json").exists())

    def test_pair_selection_must_be_two_distinct_present_safe_ids(self):
        result = self.inventory()
        for selected in ([self.ids[0]], [self.ids[0]] * 2, self.ids[:3], ["../escape", self.ids[0]], ["f" * 64, self.ids[0]]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.pair(result, selected)
        self.assertFalse((self.root / "pair").exists())

    def test_destination_cannot_modify_source_or_overwrite_evidence(self):
        for destination in (self.store / "capture", self.store, self.root):
            with self.subTest(destination=destination), self.assertRaisesRegex(ValueError, "Destination"):
                capture.capture_inventory(self.store, destination)
        result = self.inventory()
        before = Path(result["inventory"]).read_bytes()
        with self.assertRaisesRegex(ValueError, "Destination"):
            self.inventory()
        self.assertEqual(Path(result["inventory"]).read_bytes(), before)

    def test_comparison_keeps_missing_table_entries_distinct_from_missing_digest(self):
        def report(publication, values):
            return {"manifest": {"publication_id": publication}, "witness_map": values}
        left = report("left", {"external/a": {"file": {"listing": False, "digest": "missing"}}})
        right = report("right", {"external/a": {"listing": {"listing": True, "digest": "not-directory"}}})
        result = capture.compare_witnesses(left, right)
        self.assertEqual(result["differing_witnesses"], 2)
        self.assertEqual(result["differences"][0]["left"], "missing")
        self.assertIsNone(result["differences"][0]["right"])


if __name__ == "__main__":
    unittest.main()
