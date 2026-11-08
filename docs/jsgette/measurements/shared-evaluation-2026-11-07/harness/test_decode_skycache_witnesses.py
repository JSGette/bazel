"""Offline byte fixtures only; no Bazel, Java, subprocesses or runtime-cache reads."""

import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

import decode_skycache_witnesses as decoder


PUBLICATION, NAMESPACE, SOURCE = "1" * 64, "2" * 64, "3" * 64


def compressed(plain):
    writer = zlib.compressobj(level=1, wbits=-zlib.MAX_WBITS)
    return writer.compress(plain) + writer.flush()


def block(records):
    plain, previous, expanded = b"", b"", 0
    for path, listing, digest in records:
        raw = path.encode("utf-8")
        prefix = 0
        while prefix < min(len(raw), len(previous)) and raw[prefix] == previous[prefix]:
            prefix += 1
        plain += struct.pack(">HH", prefix, len(raw) - prefix) + raw[prefix:] + bytes([int(listing)])
        plain += b"\x01" if digest == "missing" else b"\x02" if digest == "not-directory" else b"\x00" + bytes.fromhex(digest)
        expanded += len(raw) + 38
        previous = raw
    encoded = compressed(plain)
    return encoded, {"entries": len(records), "encoded_bytes": len(encoded),
                     "decoded_bytes": len(plain), "expanded_bytes": expanded,
                     "sha256": hashlib.sha256(encoded).hexdigest()}


def manifest(blocks=(), *, parents=(), publication=PUBLICATION, namespace=NAMESPACE,
             source=SOURCE, magic=decoder.MAGIC, trailing=b""):
    def utf(value):
        data = value.encode("ascii")
        return struct.pack(">H", len(data)) + data
    payload = struct.pack(">i", magic) + utf(publication) + utf(namespace) + utf(source)
    payload += struct.pack(">i", len(parents)) + b"".join(utf(parent) for parent in parents)
    payload += struct.pack(">i", len(blocks))
    for item in blocks:
        payload += struct.pack(">iiiq", item["entries"], item["encoded_bytes"], item["decoded_bytes"], item["expanded_bytes"])
        payload += bytes.fromhex(item["sha256"])
    payload += trailing
    return payload + hashlib.sha256(payload).digest()


class DecoderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".witness-decoder-test-", dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.manifest = self.root / (PUBLICATION + ".manifest")
        self.records = [("absolute//opt/sdk", False, "4" * 64),
                        ("external/repo", False, "5" * 64),
                        ("external/repo", True, "not-directory"),
                        ("external/repo/café", False, "missing"),
                        ("external/repo/cafê", False, "6" * 64)]
        self.encoded, self.descriptor = block(self.records)

    def write(self, blocks):
        self.manifest.write_bytes(manifest([desc for _, desc in blocks]))
        for index, (encoded, _) in enumerate(blocks):
            (self.root / ("witnesses-%05d.block" % index)).write_bytes(encoded)

    def parse(self, data):
        return decoder.parse_manifest(data, self.manifest.name, NAMESPACE)

    def test_inventory_does_not_open_blocks_and_exposes_exact_copy_paths(self):
        self.manifest.write_bytes(manifest([self.descriptor], parents=["9" * 64]))
        report = decoder.inspect_metadata(self.manifest, NAMESPACE)
        self.assertEqual(report["kind"], "manifest_inventory")
        self.assertNotIn("witness_map", report)
        self.assertEqual(report["manifest"]["parents"], ["9" * 64])
        self.assertEqual(report["manifest"]["blocks"][0]["relative_object_path"],
                         "leased-v2/objects/segmented-v1/" + PUBLICATION + "/witnesses-00000.block")
        self.assertEqual(report["manifest_source"]["sha256"], hashlib.sha256(self.manifest.read_bytes()).hexdigest())

    def test_decode_preserves_prefix_utf8_listing_tags_and_provenance(self):
        self.write([(self.encoded, self.descriptor)])
        report = decoder.inspect_metadata(self.manifest, NAMESPACE, self.root)
        self.assertTrue(report["all_blocks_decoded"])
        self.assertEqual(report["witness_count"], len(self.records))
        for path, listing, digest in self.records:
            self.assertEqual(report["witness_map"][path]["listing" if listing else "file"],
                             {"listing": listing, "digest": digest})
        self.assertEqual(report["block_sources"][0]["sha256"], self.descriptor["sha256"])
        self.assertEqual(report["format_sources"]["files"], decoder.FORMAT_SOURCES["files"])
        self.assertNotIn("passed", report)

    def test_manifest_checksum_magic_namespace_and_filename_are_required(self):
        good = manifest([self.descriptor])
        cases = [(good[:-1] + bytes([good[-1] ^ 1]), "checksum"),
                 (manifest(magic=0), "format"), (manifest(namespace="a" * 64), "namespace"),
                 (manifest(publication="f" * 64), "filename")]
        for data, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.parse(data)
        for namespace in ("../unsafe", "A" * 64, "z" * 64, "2" * 63):
            with self.subTest(namespace=namespace), self.assertRaisesRegex(ValueError, "expected namespace"):
                decoder.parse_manifest(good, self.manifest.name, namespace)

    def test_noncanonical_ids_self_and_duplicate_parents_reject(self):
        for data in (manifest(publication="A" * 64), manifest(source="../" + "a" * 61),
                     manifest(parents=[PUBLICATION]), manifest(parents=["8" * 64, "8" * 64])):
            with self.assertRaises(ValueError):
                self.parse(data)

    def test_manifest_truncation_and_authenticated_trailing_data_reject(self):
        good = manifest([self.descriptor])
        for size in (0, 31, 32, len(good) - 1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                self.parse(good[:size])
        with self.assertRaisesRegex(ValueError, "Trailing"):
            self.parse(manifest(trailing=b"extra"))
        # Valid outer checksum, incomplete descriptor inside.
        payload = good[:-33]
        with self.assertRaisesRegex(ValueError, "Truncated"):
            self.parse(payload + hashlib.sha256(payload).digest())

    def test_descriptor_bounds_and_total_expanded_budget_reject_before_block_io(self):
        for field, value in (("entries", 0), ("entries", 4097), ("encoded_bytes", 0),
                             ("encoded_bytes", decoder.MAX_BLOCK_BYTES + 1), ("decoded_bytes", -1),
                             ("expanded_bytes", 37 * self.descriptor["entries"]),
                             ("expanded_bytes", 65574 * self.descriptor["entries"])):
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, "bounds"):
                self.parse(manifest([{**self.descriptor, field: value}]))
        large = {**self.descriptor, "entries": 4096, "expanded_bytes": 8 * 1024 * 1024}
        self.manifest.write_bytes(manifest([large] * 4))
        with self.assertRaisesRegex(ValueError, "32 MiB"):
            decoder.inspect_metadata(self.manifest, NAMESPACE, self.root)

    def test_invalid_manifest_counts_are_rejected(self):
        payload = manifest()[:-32]
        parent_offset = 4 + 3 * (2 + 64)
        for offset, count in ((parent_offset, -1), (parent_offset, 1_000_001),
                              (parent_offset + 4, -1), (parent_offset + 4, 16385)):
            changed = payload[:offset] + struct.pack(">i", count) + payload[offset + 4:]
            with self.subTest(offset=offset, count=count), self.assertRaisesRegex(ValueError, "count"):
                self.parse(changed + hashlib.sha256(changed).digest())

    def test_corrupt_block_length_and_checksum_reject(self):
        self.write([(self.encoded, self.descriptor)])
        path = self.root / "witnesses-00000.block"
        path.write_bytes(self.encoded + b"x")
        with self.assertRaisesRegex(ValueError, "length"):
            decoder.inspect_metadata(self.manifest, NAMESPACE, self.root)
        path.write_bytes(bytes([self.encoded[0] ^ 1]) + self.encoded[1:])
        with self.assertRaisesRegex(ValueError, "checksum"):
            decoder.inspect_metadata(self.manifest, NAMESPACE, self.root)

    def test_raw_deflate_must_end_exactly_with_no_extra_stream_or_expansion(self):
        for encoded, decoded in ((self.encoded[:-1], self.descriptor["decoded_bytes"]),
                                 (self.encoded + b"trailer", self.descriptor["decoded_bytes"]),
                                 (self.encoded + compressed(b"another"), self.descriptor["decoded_bytes"]),
                                 (self.encoded, self.descriptor["decoded_bytes"] - 1),
                                 (compressed(b"x" * 10000), 5),
                                 (zlib.compress(b"wrapped"), 7)):
            desc = {**self.descriptor, "encoded_bytes": len(encoded), "decoded_bytes": decoded,
                    "sha256": hashlib.sha256(encoded).hexdigest()}
            with self.subTest(decoded=decoded, bytes=len(encoded)), self.assertRaisesRegex(ValueError, "deflate|decompression"):
                decoder.decode_block(encoded, desc)

    def test_direct_block_decoder_also_checks_allocation_limits(self):
        for field, value in (("entries", 4097), ("decoded_bytes", decoder.MAX_BLOCK_BYTES + 1),
                             ("encoded_bytes", decoder.MAX_BLOCK_BYTES + 1)):
            with self.subTest(field=field), patch.object(decoder.zlib, "decompressobj", side_effect=AssertionError("must not inflate")):
                with self.assertRaisesRegex(ValueError, "bounds"):
                    decoder.decode_block(self.encoded, {**self.descriptor, field: value})
        with self.assertRaisesRegex(ValueError, "32 MiB"):
            decoder.decode_block(self.encoded, {**self.descriptor, "entries": 4096,
                                               "expanded_bytes": decoder.MAX_METADATA_BYTES + 1})

    def decode_plain(self, plain, *, entries=1, expanded=60):
        encoded = compressed(plain)
        desc = {"entries": entries, "encoded_bytes": len(encoded), "decoded_bytes": len(plain),
                "expanded_bytes": expanded, "sha256": hashlib.sha256(encoded).hexdigest()}
        return decoder.decode_block(encoded, desc)

    def test_path_prefix_encoding_boolean_digest_tag_and_trailing_bytes(self):
        with self.assertRaisesRegex(ValueError, "prefix"):
            self.decode_plain(struct.pack(">HH", 1, 0))
        with self.assertRaisesRegex(ValueError, "UTF-8"):
            self.decode_plain(struct.pack(">HH", 0, 1) + b"\xff\x00\x01", expanded=39)
        raw = b"external/repo"
        head = struct.pack(">HH", 0, len(raw)) + raw
        for suffix, message in ((b"\x02\x01", "boolean"), (b"\x00\x03", "tag"),
                                (b"\x00\x01x", "Trailing"), (b"\x00\x00", "Truncated")):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.decode_plain(head + suffix, expanded=len(raw) + 38)

    def test_expanded_size_and_record_count_must_match(self):
        for desc in ({**self.descriptor, "expanded_bytes": self.descriptor["expanded_bytes"] - 1},
                     {**self.descriptor, "expanded_bytes": self.descriptor["expanded_bytes"] + 1},
                     {**self.descriptor, "entries": self.descriptor["entries"] + 1},
                     {**self.descriptor, "entries": self.descriptor["entries"] - 1}):
            with self.assertRaises(ValueError):
                decoder.decode_block(self.encoded, desc)

    def test_duplicate_witness_rejects_within_and_across_blocks(self):
        for groups in ([self.records[:1] * 2], [self.records[:1], self.records[:1]]):
            self.write([block(records) for records in groups])
            with self.assertRaisesRegex(ValueError, "Duplicate witness"):
                decoder.inspect_metadata(self.manifest, NAMESPACE, self.root)

    def test_unsafe_logical_paths_are_metadata_errors_not_resolved_paths(self):
        for path in ("external/../escape", "workspace//alias", "absolute/relative", "other/root", "external/x\0y"):
            encoded, desc = block([(path, False, "missing")])
            with self.subTest(path=path), self.assertRaises(ValueError):
                decoder.decode_block(encoded, desc)
        for path in ("workspace/", "external/", "absolute//", "absolute//a/back\\slash"):
            encoded, desc = block([(path, False, "missing")])
            self.assertEqual(decoder.decode_block(encoded, desc)[0][0], path)

    def test_subset_is_explicit_and_does_not_open_other_blocks(self):
        first, second = block(self.records[:1]), block(self.records[1:])
        self.manifest.write_bytes(manifest([first[1], second[1]]))
        (self.root / "witnesses-00001.block").write_bytes(second[0])
        report = decoder.inspect_metadata(self.manifest, NAMESPACE, self.root, [1])
        self.assertFalse(report["all_blocks_decoded"])
        self.assertEqual(report["selected_block_indices"], [1])
        self.assertEqual(report["witness_count"], 4)
        for indices in ([0, 0], [-1], [2], [True]):
            with self.assertRaisesRegex(ValueError, "selection"):
                decoder.inspect_metadata(self.manifest, NAMESPACE, self.root, indices)
        with self.assertRaisesRegex(ValueError, "directory"):
            decoder.inspect_metadata(self.manifest, NAMESPACE, block_indices=[1])

    def test_oversized_file_rejected_without_opening_or_allocating_content(self):
        self.manifest.write_bytes(manifest())
        with patch.object(decoder, "MAX_METADATA_BYTES", 32), patch.object(Path, "open", side_effect=AssertionError("must not read")):
            with self.assertRaisesRegex(ValueError, "over-budget"):
                decoder.inspect_metadata(self.manifest, NAMESPACE)

    def test_cli_inventory_writes_new_output_and_never_overwrites_evidence(self):
        self.manifest.write_bytes(manifest([self.descriptor]))
        output = self.root / "inventory.json"
        argv = ["decoder", str(self.manifest), "--namespace", NAMESPACE, "--output", str(output)]
        with patch("sys.argv", argv):
            self.assertEqual(decoder.main(), 0)
        before = output.read_bytes()
        self.assertEqual(json.loads(before)["kind"], "manifest_inventory")
        with patch("sys.argv", argv), patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as failure:
            decoder.main()
        self.assertEqual(failure.exception.code, 2)
        self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
