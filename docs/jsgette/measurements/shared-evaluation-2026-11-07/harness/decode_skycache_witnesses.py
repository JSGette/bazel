"""Offline SKS4 metadata decoder; use explicitly copied evidence, never a live cache.

Inventory (does not open blocks):
  python -B decode_skycache_witnesses.py <id>.manifest --namespace <64-hex>
Decode exact referenced files from a copied directory, without enumeration:
  python -B decode_skycache_witnesses.py <id>.manifest --namespace <64-hex> \
      --blocks-directory <copied-blocks> [--block-index 0 --block-index 1]

The default decode selects every descriptor; explicit indices select a subset.
Witness maps are keyed by exact logical path, then "file" (listing=false) or
"listing" (listing=true). Paths are metadata only and are never dereferenced.
Source fingerprints are recorded assertions, not checked against any filesystem.
This tool neither imports cache values nor decides cache admission or a milestone.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import zlib


MAX_METADATA_BYTES = 32 * 1024 * 1024
MAX_BLOCK_BYTES = 1024 * 1024 + 128 * 1024
MAX_BLOCKS = 16384
MAX_BLOCK_ENTRIES = 4096
MAX_PATH_BYTES = 65535
MAX_PARENT_COUNT = 1_000_000
MAGIC = 0x534B5334
HEX_ID = re.compile(r"[0-9a-f]{64}\Z")
FORMAT_SOURCES = {
    "checkout": "/home/jgett/skycache-20261029/bazel",
    "base_commit": "a23ae68903f03f08ce83dcf1be85cdff54bb1eeb",
    "working_tree_modified": True,
    "package": "src/main/java/com/google/devtools/build/lib/skyframe/serialization/local",
    "files": {
        "LocalCacheSession.java": "f4a6ae49f2715e0b0b5aaf358d2c7f2f6bdc61e800dd9a73d5420b0937a6c777",
        "LocalWitnessTable.java": "bedf45b0b1c2448e2cd4625fdf77fbe5f732d6aed2605cc75219b4ce0377f95d",
    },
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def identifier(value, label):
    if not isinstance(value, str) or not HEX_ID.fullmatch(value):
        raise ValueError("Invalid " + label)
    return value


class Reader:
    """Only the fixed big-endian primitives present in these two file formats."""

    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.offset:
            raise ValueError("Truncated metadata")
        start = self.offset
        self.offset += size
        return self.data[start:self.offset]

    def number(self, kind):
        return struct.unpack(">" + kind, self.take(struct.calcsize(">" + kind)))[0]

    def hex_utf(self, label):
        # Java writeUTF is modified UTF-8, but every string here is canonical
        # lowercase ASCII hex. Its only valid encoding is a 64-byte ASCII string.
        length = self.number("H")
        if length != 64:
            raise ValueError("Invalid " + label + " length")
        try:
            return identifier(self.take(length).decode("ascii"), label)
        except UnicodeDecodeError as error:
            raise ValueError("Invalid " + label + " encoding") from error

    def finish(self):
        if self.offset != len(self.data):
            raise ValueError("Trailing metadata bytes")


def read_snapshot(path, limit, exact_size=None):
    """One bounded regular-file snapshot; reject aliases and changed file identity."""
    path = Path(path).absolute()
    if path.resolve(strict=True) != path:
        raise ValueError("Copied evidence path must not be redirected")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError("Non-regular or over-budget evidence file")
    if exact_size is not None and before.st_size != exact_size:
        raise ValueError("Evidence file length mismatch")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Evidence file identity changed before read")
        data = stream.read(before.st_size + 1)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if len(data) != before.st_size or any(identity(item) != identity(before) for item in (after, final)):
        raise ValueError("Evidence file changed during read")
    return data, {"path": str(path), "bytes": len(data), "sha256": sha256(data)}


def check_descriptor(entries, encoded, decoded, expanded):
    if (any(type(number) is not int for number in (entries, encoded, decoded, expanded))
            or not 1 <= entries <= MAX_BLOCK_ENTRIES
            or not 1 <= encoded <= MAX_BLOCK_BYTES
            or not 1 <= decoded <= MAX_BLOCK_BYTES
            or not 38 * entries <= expanded <= (MAX_PATH_BYTES + 38) * entries):
        raise ValueError("Witness descriptor outside bounds")


def parse_manifest(data, filename, expected_namespace):
    identifier(expected_namespace, "expected namespace")
    if not 32 <= len(data) <= MAX_METADATA_BYTES:
        raise ValueError("Manifest length outside metadata budget")
    payload, checksum = data[:-32], data[-32:]
    if hashlib.sha256(payload).digest() != checksum:
        raise ValueError("Manifest checksum mismatch")
    reader = Reader(payload)
    if reader.number("i") != MAGIC:
        raise ValueError("Unsupported manifest format")
    publication = reader.hex_utf("publication ID")
    if filename != publication + ".manifest":
        raise ValueError("Manifest filename differs from publication ID")
    namespace = reader.hex_utf("namespace")
    if namespace != expected_namespace:
        raise ValueError("Manifest namespace mismatch")
    source_digest = reader.hex_utf("source digest")
    parent_count = reader.number("i")
    if not 0 <= parent_count <= MAX_PARENT_COUNT:
        raise ValueError("Parent count outside bounds")
    parents, seen = [], set()
    for _ in range(parent_count):
        parent = reader.hex_utf("parent ID")
        if parent == publication or parent in seen:
            raise ValueError("Self or duplicate parent ID")
        seen.add(parent)
        parents.append(parent)
    count = reader.number("i")
    if not 0 <= count <= MAX_BLOCKS:
        raise ValueError("Block count outside bounds")
    blocks, metadata_bytes = [], len(data)
    for index in range(count):
        entries, encoded, decoded = (reader.number("i") for _ in range(3))
        expanded, digest = reader.number("q"), reader.take(32).hex()
        check_descriptor(entries, encoded, decoded, expanded)
        metadata_bytes += max(encoded, decoded, expanded)
        if metadata_bytes > MAX_METADATA_BYTES:
            raise ValueError("Publication exceeds 32 MiB metadata budget")
        name = "witnesses-%05d.block" % index
        blocks.append({"index": index, "filename": name, "entries": entries,
                       "encoded_bytes": encoded, "decoded_bytes": decoded,
                       "expanded_bytes": expanded, "sha256": digest,
                       "relative_object_path": "leased-v2/objects/segmented-v1/" + publication + "/" + name})
    reader.finish()
    return {"publication_id": publication, "namespace": namespace,
            "source_digest_assertion": source_digest, "parents": parents, "blocks": blocks,
            "metadata_bytes_charged": metadata_bytes,
            "relative_manifest_path": "leased-v2/publications-v4/" + namespace + "/" + publication + ".manifest"}


def logical_path(value):
    # These POSIX strings are metadata, not host-platform paths. Preserve spelling;
    # never normalize an unsafe spelling into an apparently safe witness.
    if "\0" in value:
        raise ValueError("NUL in witness path")
    prefix, separator, suffix = value.partition("/")
    if not separator or prefix not in ("workspace", "external", "absolute"):
        raise ValueError("Unknown logical witness root")
    if prefix == "absolute":
        if not suffix.startswith("/"):
            raise ValueError("Relative absolute witness")
        suffix = suffix[1:]
    if suffix and any(part in ("", ".", "..") for part in suffix.split("/")):
        raise ValueError("Noncanonical or escaping witness path")
    return value


def decode_block(encoded, descriptor):
    check_descriptor(descriptor["entries"], descriptor["encoded_bytes"],
                     descriptor["decoded_bytes"], descriptor["expanded_bytes"])
    if descriptor["expanded_bytes"] > MAX_METADATA_BYTES:
        raise ValueError("Witness block exceeds 32 MiB metadata budget")
    if len(encoded) != descriptor["encoded_bytes"] or sha256(encoded) != descriptor["sha256"]:
        raise ValueError("Witness block length or checksum mismatch")
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        # A one-byte excess detects expansion beyond the checksummed declaration;
        # do not call unbounded decompress() or flush() on untrusted compressed data.
        plain = inflater.decompress(encoded, descriptor["decoded_bytes"] + 1)
    except zlib.error as error:
        raise ValueError("Invalid raw-deflate witness block") from error
    if (len(plain) != descriptor["decoded_bytes"] or not inflater.eof
            or inflater.unused_data or inflater.unconsumed_tail):
        raise ValueError("Witness decompression length, end or trailing-data mismatch")
    reader, previous, expanded, records = Reader(plain), b"", 0, []
    for _ in range(descriptor["entries"]):
        prefix, suffix = reader.number("H"), reader.number("H")
        if prefix > len(previous) or prefix + suffix > MAX_PATH_BYTES:
            raise ValueError("Invalid witness path prefix")
        expanded += prefix + suffix + 38
        if expanded > descriptor["expanded_bytes"]:
            raise ValueError("Witness expanded paths exceed declared budget")
        path_bytes = previous[:prefix] + reader.take(suffix)
        try:
            path = logical_path(path_bytes.decode("utf-8", errors="strict"))
        except UnicodeDecodeError as error:
            raise ValueError("Invalid witness UTF-8 path") from error
        listing, tag = reader.number("B"), reader.number("B")
        if listing not in (0, 1):
            raise ValueError("Noncanonical witness listing boolean")
        if tag == 0:
            digest = reader.take(32).hex()
        elif tag in (1, 2):
            digest = "missing" if tag == 1 else "not-directory"
        else:
            raise ValueError("Invalid witness digest tag")
        records.append((path, bool(listing), digest))
        previous = path_bytes
    reader.finish()
    if expanded != descriptor["expanded_bytes"]:
        raise ValueError("Witness expanded size differs from descriptor")
    return records


def inspect_metadata(manifest_path, expected_namespace, blocks_directory=None, block_indices=None):
    data, origin = read_snapshot(manifest_path, MAX_METADATA_BYTES)
    manifest = parse_manifest(data, Path(manifest_path).name, expected_namespace)
    report = {"schema_version": 1, "kind": "manifest_inventory", "format": "SKS4",
              "format_sources": FORMAT_SOURCES, "manifest_source": origin, "manifest": manifest,
              "metadata_budget_bytes": MAX_METADATA_BYTES,
              "budget_note": "Manifest bytes plus sum(max(encoded, decoded, expanded) per block); not a Python RSS limit.",
              "compatibility_note": "Canonical writer encodings and this tool's 32 MiB budget are stricter than some Java reader forms; rejection does not by itself establish corruption or Java-reader rejection.",
              "decoder_sha256": sha256(Path(__file__).read_bytes()),
              "interpretation": "Checksummed metadata only; no independent authenticity proof, runtime input checks, admission decision or milestone verdict."}
    if blocks_directory is None:
        if block_indices is not None:
            raise ValueError("Block selection requires an explicit copied-block directory")
        return report
    indices = list(range(len(manifest["blocks"]))) if block_indices is None else list(block_indices)
    if len(set(indices)) != len(indices) or any(type(i) is not int or not 0 <= i < len(manifest["blocks"]) for i in indices):
        raise ValueError("Invalid or duplicate block selection")
    witness_map, sources = {}, []
    for index in sorted(indices):
        descriptor = manifest["blocks"][index]
        block, provenance = read_snapshot(Path(blocks_directory) / descriptor["filename"],
                                          MAX_BLOCK_BYTES, descriptor["encoded_bytes"])
        records = decode_block(block, descriptor)
        for path, listing, digest in records:
            operation = "listing" if listing else "file"
            operations = witness_map.setdefault(path, {})
            if operation in operations:
                raise ValueError("Duplicate witness path/operation")
            operations[operation] = {"listing": listing, "digest": digest}
        sources.append({"block_index": index, **provenance})
    report.update(kind="witness_metadata", selected_block_indices=sorted(indices),
                  all_blocks_decoded=len(indices) == len(manifest["blocks"]),
                  witness_count=sum(len(operations) for operations in witness_map.values()),
                  witness_map=witness_map, block_sources=sources,
                  completeness_note="Parents and unselected blocks are not opened; this is not closure/admission validation.")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--blocks-directory", type=Path)
    parser.add_argument("--block-index", type=int, action="append")
    parser.add_argument("--output", type=Path, help="New JSON file; otherwise stdout. Never overwrite evidence.")
    args = parser.parse_args()
    try:
        report = inspect_metadata(args.manifest, args.namespace, args.blocks_directory, args.block_index)
        if args.output is None:
            print(json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2))
        else:
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=True, sort_keys=True, indent=2)
                stream.write("\n")
    except (OSError, ValueError) as error:
        parser.exit(2, "Metadata decode failed: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
