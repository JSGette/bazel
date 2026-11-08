"""Post-M0, read-only source capture for the single known identical-N10 store.

Do not launch while the matrix is running. This helper does not establish that
the matrix finished or acquire runtime leases; the coordinator must establish a
quiet post-M0 boundary first. It never launches Bazel or writes to the store.

inventory --destination <new-copied-evidence-dir>
pair --inventory <copied-inventory.json> --inventory-sha256 <printed-sha256>
     --publication <id-A> --publication <id-B> --destination <new-pair-dir>

The CLI source store is fixed. Inventory enumerates at most two namespaces and
32 manifest filenames total, bounded before any content reads. Pair capture opens only
two manifests already copied and their named witness blocks; no directory walks,
value packs, indexes, source inputs or referenced parent publications are read.
Decoding and comparison use the copied files. In-memory metadata comparisons do
not establish the original rejection, current input validity, or an admission hit.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat

import decode_skycache_witnesses as decoder


KNOWN_STORE = Path("/mnt/shared-evaluation-20261107/skycache-matrix/identical-n10-skycache-v7b")
MAX_NAMESPACES = 2
MAX_PUBLICATIONS = 32
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INVENTORY_BYTES = 2 * 1024 * 1024
ARCHIVED_HISTORY = [
    {"run": "m0-v7b-01-i10-sky-r", "phase": "cache_after", "namespace_count": 1,
     "publication_count": 10, "publication_bytes": 7100,
     "archive_sha256": "7e87b0e547bc9ea95d358295c6d2ff0d8e100f18081b99fbfb81d6cc8fa66591"},
    {"run": "m0-v7b-01-i10-sky-w", "phase": "cache_after", "namespace_count": 2,
     "publication_count": 20, "publication_bytes": 14200,
     "archive_sha256": "d3e8ce41705f3041b8fb8655e49a2d80a85d41bf4f22eb166e14c2f89659e079"},
]


def now():
    return datetime.now(timezone.utc).isoformat()


def write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)


def save_json(path, value, maximum=None):
    data = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    if maximum is not None and len(data) > maximum:
        raise ValueError("JSON evidence exceeds capture budget")
    write_new(path, data)
    return decoder.sha256(data)


def canonical_directory(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path or not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("Directory must be absolute, present and non-redirected")
    return path


def bounded_entries(directory, maximum):
    canonical_directory(directory)
    entries = []
    with os.scandir(directory) as stream:
        for entry in stream:
            entries.append(Path(entry.path))
            if len(entries) > maximum:
                raise ValueError("Inventory entry bound exceeded; stop for assessment")
    return entries


def new_destination(path, source_store):
    path = Path(path)
    if (not path.is_absolute() or path.exists() or path.resolve() != path
            or path.is_relative_to(source_store) or source_store.is_relative_to(path)):
        raise ValueError("Destination must be new, non-redirected and outside the source store")
    canonical_directory(path.parent)
    path.mkdir()
    return path


def provenance():
    return {"capture_helper_sha256": decoder.sha256(Path(__file__).read_bytes()),
            "decoder_sha256": decoder.sha256(Path(decoder.__file__).read_bytes()),
            "format_sources": decoder.FORMAT_SOURCES}


def capture_inventory(source_store, destination):
    source_store = canonical_directory(source_store)
    namespaces = bounded_entries(source_store / "leased-v2/publications-v4", MAX_NAMESPACES)
    paths, seen_ids = [], set()
    for namespace_path in sorted(namespaces):
        decoder.identifier(namespace_path.name, "namespace directory")
        paths.extend(bounded_entries(namespace_path, MAX_PUBLICATIONS - len(paths)))
    for path in paths:
        if path.name != decoder.identifier(path.stem, "publication filename") + ".manifest":
            raise ValueError("Unexpected namespace entry")
        if path.stem in seen_ids:
            raise ValueError("Duplicate publication ID across namespaces")
        seen_ids.add(path.stem)
    destination = new_destination(destination, source_store)
    copies = destination / "manifests"
    copies.mkdir()
    rows = []
    for path in sorted(paths):
        namespace = path.parent.name
        before = path.lstat()
        data, source = decoder.read_snapshot(path, MAX_MANIFEST_BYTES)
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_mtime_ns):
            raise ValueError("Manifest timestamp/identity changed during inventory")
        parsed = decoder.parse_manifest(data, path.name, namespace)
        (copies / namespace).mkdir(exist_ok=True)
        write_new(copies / namespace / path.name, data)
        rows.append({"publication_id": parsed["publication_id"], "namespace": namespace, "source": source,
                     "mtime_ns": before.st_mtime_ns, "copied_manifest": "manifests/" + namespace + "/" + path.name,
                     "manifest": parsed})
    grouped = []
    for namespace_path in sorted(namespaces):
        ordered = sorted((row for row in rows if row["namespace"] == namespace_path.name),
                         key=lambda row: (row["mtime_ns"], row["publication_id"]))
        grouped.append({"namespace": namespace_path.name, "manifest_count": len(ordered),
                        "manifest_bytes": sum(row["source"]["bytes"] for row in ordered),
                        "oldest_mtime_ns": ordered[0]["mtime_ns"] if ordered else None,
                        "oldest_two_ids": [row["publication_id"] for row in ordered[:2]]})
    eligible = sorted((group for group in grouped if group["manifest_count"] >= 1),
                      key=lambda group: (group["oldest_mtime_ns"], group["namespace"]))
    suggested = ({"namespace": eligible[0]["namespace"], "publications": eligible[0]["oldest_two_ids"]}
                 if eligible and eligible[0]["manifest_count"] >= 2 else None)
    result = {"schema_version": 1, "kind": "skycache_manifest_capture", "captured_utc": now(),
              "source_store": str(source_store), "namespace_count": len(namespaces), "namespaces": grouped,
              "manifest_count": len(rows), "manifest_bytes": sum(row["source"]["bytes"] for row in rows),
              "manifests": rows, "source_blocks_read": 0,
              "archived_history": ARCHIVED_HISTORY,
              "suggested_pair_by_oldest_mtime": suggested,
              "pair_selection_note": "Modification-time order is a heuristic; archives do not identify producer publication IDs. No client attribution is asserted. No pair is suggested if the oldest nonempty namespace has fewer than two publications.",
              "snapshot_note": "Per-file reads are checked; no catalog lease or atomic cross-file snapshot is claimed. No historical count is required to remain unchanged.",
              "limits": {"namespaces": MAX_NAMESPACES, "manifests": MAX_PUBLICATIONS, "bytes_per_manifest": MAX_MANIFEST_BYTES,
                         "metadata_bytes_per_publication": decoder.MAX_METADATA_BYTES},
              **provenance()}
    result_hash = save_json(destination / "inventory.json", result, MAX_INVENTORY_BYTES)
    return {"inventory": str(destination / "inventory.json"), "inventory_sha256": result_hash,
            "suggested_pair_by_oldest_mtime": result["suggested_pair_by_oldest_mtime"]}


def compare_witnesses(left, right):
    def flattened(report):
        return {(path, record["listing"]): record["digest"]
                for path, operations in report["witness_map"].items() for record in operations.values()}
    a, b = flattened(left), flattened(right)
    differences, identical = [], 0
    for path, listing in sorted(a.keys() | b.keys()):
        key = (path, listing)
        if key in a and key in b and a[key] == b[key]:
            identical += 1
            continue
        repository_binding = not listing and path.startswith("external/") and bool(path[9:]) and "/" not in path[9:]
        differences.append({"path": path, "listing": listing, "left": a.get(key), "right": b.get(key),
                            "repository_binding_shape": repository_binding})
    return {"left_publication": left["manifest"]["publication_id"],
            "right_publication": right["manifest"]["publication_id"],
            "identical_witnesses": identical, "differing_witnesses": len(differences),
            "differences": differences,
            "interpretation": "Exact recorded metadata differences only. Null means absent from that table. No runtime fingerprinting, producer attribution, historical first-rejection trace, or cache-admission verdict."}


def capture_pair(source_store, inventory_path, inventory_sha256, publications, destination,
                 selection_reason="Explicit IDs; no archived producer-client attribution"):
    source_store = canonical_directory(source_store)
    decoder.identifier(inventory_sha256, "inventory SHA-256")
    data, inventory_source = decoder.read_snapshot(inventory_path, MAX_INVENTORY_BYTES)
    if inventory_source["sha256"] != inventory_sha256:
        raise ValueError("Copied inventory SHA-256 mismatch")
    inventory = json.loads(data)
    if (inventory.get("schema_version") != 1 or inventory.get("kind") != "skycache_manifest_capture"
            or inventory.get("source_store") != str(source_store)
            or type(inventory.get("manifest_count")) is not int
            or not 0 <= inventory["manifest_count"] <= MAX_PUBLICATIONS
            or len(inventory.get("manifests", [])) != inventory["manifest_count"]):
        raise ValueError("Unsupported or differently scoped inventory")
    ids = [decoder.identifier(value, "selected publication") for value in publications]
    if len(ids) != 2 or len(set(ids)) != 2:
        raise ValueError("Select exactly two distinct publication IDs")
    rows = {}
    for row in inventory["manifests"]:
        key = decoder.identifier(row["publication_id"], "inventory publication ID")
        decoder.identifier(row["namespace"], "inventory namespace")
        if key in rows:
            raise ValueError("Duplicate inventory publication ID")
        rows[key] = row
    if any(key not in rows for key in ids):
        raise ValueError("Selected publication is absent from the captured inventory")
    if len({row["namespace"] for row in rows.values()}) > MAX_NAMESPACES:
        raise ValueError("Inventory exceeds namespace bound")
    if len({rows[key]["namespace"] for key in ids}) != 1:
        raise ValueError("Select a pair from the same namespace")
    namespace = rows[ids[0]]["namespace"]
    inventory_root = Path(inventory_path).absolute().parent
    selected = []
    for key in ids:
        copied = inventory_root / "manifests" / namespace / (key + ".manifest")
        manifest_bytes, manifest_source = decoder.read_snapshot(copied, MAX_MANIFEST_BYTES)
        if manifest_source["sha256"] != rows[key]["source"]["sha256"]:
            raise ValueError("Copied publication manifest changed since inventory")
        parsed = decoder.parse_manifest(manifest_bytes, copied.name, namespace)
        selected.append((key, manifest_bytes, parsed))
    destination = new_destination(destination, source_store)
    decoded, captured = [], []
    for key, manifest_bytes, parsed in selected:
        output = destination / key
        output.mkdir()
        copy_manifest = output / (key + ".manifest")
        write_new(copy_manifest, manifest_bytes)
        sources = []
        for descriptor in parsed["blocks"]:
            # Construct from validated fixed components, never paths from inventory JSON.
            origin = source_store / "leased-v2/objects/segmented-v1" / key / descriptor["filename"]
            block, source = decoder.read_snapshot(origin, decoder.MAX_BLOCK_BYTES, descriptor["encoded_bytes"])
            if source["sha256"] != descriptor["sha256"]:
                raise ValueError("Source witness block checksum mismatch")
            write_new(output / descriptor["filename"], block)
            sources.append(source)
        report = decoder.inspect_metadata(copy_manifest, namespace, output)
        decoded_hash = save_json(output / "decoded.json", report)
        decoded.append(report)
        captured.append({"publication_id": key, "manifest_sha256": decoder.sha256(manifest_bytes),
                         "block_sources": sources, "decoded_sha256": decoded_hash})
    comparison = compare_witnesses(*decoded)
    comparison_hash = save_json(destination / "comparison.json", comparison)
    complete = {"schema_version": 1, "kind": "skycache_pair_capture", "completed_utc": now(),
                "source_store": str(source_store), "namespace": namespace,
                "inventory_source": inventory_source, "selection_reason": selection_reason,
                "publications": captured, "comparison_sha256": comparison_hash,
                "copied_blocks_only": True, "source_writes": False, **provenance()}
    capture_hash = save_json(destination / "capture.json", complete)
    return {"capture": str(destination / "capture.json"), "capture_sha256": capture_hash,
            "comparison": str(destination / "comparison.json"),
            "differing_witnesses": comparison["differing_witnesses"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--destination", type=Path, required=True)
    pair = commands.add_parser("pair")
    pair.add_argument("--inventory", type=Path, required=True)
    pair.add_argument("--inventory-sha256", required=True)
    pair.add_argument("--publication", action="append", required=True)
    pair.add_argument("--destination", type=Path, required=True)
    pair.add_argument("--selection-reason", default="Explicit IDs; no archived producer-client attribution")
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            result = capture_inventory(KNOWN_STORE, args.destination)
        else:
            result = capture_pair(KNOWN_STORE, args.inventory, args.inventory_sha256,
                                  args.publication, args.destination, args.selection_reason)
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(2, "Metadata capture stopped; preserve partial evidence: " + str(error) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
