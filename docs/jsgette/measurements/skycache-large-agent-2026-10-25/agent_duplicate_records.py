#!/usr/bin/env python3
"""Read-only comparison of the steady remote pilot's 15 private records.

Reads authenticated root and relevant bucket indexes, then only matching packed
record ranges. Does not load entire pack files or contact any server.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct


CHILD = "63c83a1d68e55551f0bb130e5336f7caaf6bff100aaa27e8042b99515bdfc269"
FORMAT = Path("leased-v2/objects/segmented-v1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).parent / "agent-steady"
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    counts = {"root_index_bytes": 0, "segment_index_bytes": 0, "record_bytes": 0}
    index_count = 0
    record_count = 0

    def read_index(path, category):
        data = path.read_bytes()
        counts[category] += len(data)
        assert hashlib.sha256(data[:-32]).digest() == data[-32:], path
        return data

    def root_index(directory):
        data = read_index(directory / "index", "root_index_bytes")
        magic, count = struct.unpack_from(">II", data)
        assert magic == 0x53475331 and len(data) == 40 + count * 52
        return [
            (
                number,
                *struct.unpack_from(">IIQI", data, 8 + number * 52),
                data[28 + number * 52 : 60 + number * 52],
            )
            for number in range(count)
        ]

    def segment_index(directory, descriptor):
        nonlocal index_count
        number, bucket, entries, pack_bytes, index_bytes, digest = descriptor
        path = directory / f"segment-{number:05d}.index"
        data = read_index(path, "segment_index_bytes")
        index_count += 1
        assert len(data) == index_bytes == 48 + 48 * entries
        assert data[-32:] == digest, path
        assert struct.unpack_from(">IIQ", data) == (0x534B4901, entries, pack_bytes)
        assert (directory / f"segment-{number:05d}.pack").stat().st_size == pack_bytes
        return data

    def record(directory, number, offset, length):
        nonlocal record_count
        path = directory / f"segment-{number:05d}.pack"
        with path.open("rb") as stream:
            stream.seek(offset)
            data = stream.read(length)
        counts["record_bytes"] += len(data)
        record_count += 1
        magic, key_bytes, value_bytes = struct.unpack_from(">III", data)
        assert magic == 0x534B5001 and len(data) == 44 + key_bytes + value_bytes
        key, value = data[44 : 44 + key_bytes], data[44 + key_bytes :]
        assert hashlib.sha256(value).digest() == data[12:44], path
        return key, value

    def location(directory, number, offset, length):
        return {
            "publication": directory.name,
            "pack": str(directory / f"segment-{number:05d}.pack"),
            "offset": offset,
            "record_bytes": length,
        }

    child = args.root / "cache-pilot-minimal-remote" / FORMAT / CHILD
    parents = sorted((args.root / "cache-primer" / FORMAT).iterdir())
    child_records = []
    for descriptor in root_index(child):
        data = segment_index(child, descriptor)
        for position in range(16, len(data) - 32, 48):
            key_hash = data[position : position + 32]
            offset, length = struct.unpack_from(">QQ", data, position + 32)
            key, value = record(child, descriptor[0], offset, length)
            assert hashlib.sha256(key).digest() == key_hash
            child_records.append(
                (key, value, key_hash, location(child, descriptor[0], offset, length))
            )

    roots = {parent: root_index(parent) for parent in parents}
    indexes = {}
    rows = []
    for key, value, key_hash, child_location in child_records:
        matches = []
        for parent, descriptors in roots.items():
            for descriptor in reversed(descriptors):
                if descriptor[1] != (key_hash[0] & 15):
                    continue
                identity = (parent, descriptor[0])
                if identity not in indexes:
                    indexes[identity] = segment_index(parent, descriptor)
                data = indexes[identity]
                low, high = 0, descriptor[2] - 1
                while low <= high:
                    middle = (low + high) // 2
                    position = 16 + middle * 48
                    found_hash = data[position : position + 32]
                    if found_hash < key_hash:
                        low = middle + 1
                    elif found_hash > key_hash:
                        high = middle - 1
                    else:
                        offset, length = struct.unpack_from(">QQ", data, position + 32)
                        parent_key, parent_value = record(
                            parent, descriptor[0], offset, length
                        )
                        assert parent_key == key
                        matches.append(
                            {
                                **location(parent, descriptor[0], offset, length),
                                "value_sha256": hashlib.sha256(parent_value).hexdigest(),
                                "identical": parent_value == value,
                            }
                        )
                        break
        rows.append(
            {
                "key_fingerprint": key.hex(),
                "key_sha256": key_hash.hex(),
                "value_bytes": len(value),
                "value_sha256": hashlib.sha256(value).hexdigest(),
                "child": child_location,
                "parent_matches": matches,
            }
        )

    identical = sum(any(match["identical"] for match in row["parent_matches"]) for row in rows)
    assert len(rows) == 15 and identical == 15
    proof = {
        "root": str(args.root),
        "child": str(child),
        "record_count": len(rows),
        "identical_parent_record_count": identical,
        "child_value_bytes": sum(row["value_bytes"] for row in rows),
        "child_record_bytes": sum(row["child"]["record_bytes"] for row in rows),
        "verification": {
            "root_index_checksums": True,
            "segment_index_checksums": True,
            "segment_index_descriptor_digests": True,
            "pack_lengths": True,
            "child_key_hashes": True,
            "parent_record_keys": True,
            "child_and_parent_value_checksums": True,
            "all_parent_values_byte_identical": True,
        },
        "io": {
            **counts,
            "total_index_bytes": counts["root_index_bytes"] + counts["segment_index_bytes"],
            "total_bytes": sum(counts.values()),
            "parent_segment_indexes": len(indexes),
            "all_segment_indexes": index_count,
            "records_read": record_count,
        },
        "conclusion": "All 15 private child records already exist byte-identically in the frozen base.",
        "limitation": "Chunk availability at put time is unrecoverable after temporary download stages were removed; this proof does not assert that exact runtime cause.",
        "records": rows,
    }
    encoded = json.dumps(proof, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
        print(json.dumps({"proof": str(args.output), "identical_records": identical, "io": proof["io"]}))
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
