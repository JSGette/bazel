"""Archive bounded benchmark evidence, excluding output bases and caches."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = args.run.resolve(strict=True)
    report = json.loads((root / "report.json").read_text())
    files = set(root.glob("*.json"))
    for phase in ("preparation", "measured", "restart-shutdown", "final-shutdown"):
        directory = root / phase
        if directory.is_dir():
            files.update(path for path in directory.rglob("*") if path.is_file())
    members = []
    for path in sorted(files):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("Evidence path escaped run root: " + str(path))
        if path.stat().st_size > 32 * 1024**2:
            raise ValueError("Unexpected oversized evidence file: " + str(path))
        members.append({"path": path.relative_to(root).as_posix(),
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest()})
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    with args.destination.open("xb") as target:
        with gzip.GzipFile(fileobj=target, mode="wb", filename="", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for member in members:
                    archive.add(root / member["path"], arcname=member["path"], recursive=False)
    manifest = {"schema_version": 1, "run": str(root), "passed": report["passed"],
                "archive": args.destination.name, "members": members,
                "archive_sha256": hashlib.file_digest(args.destination.open("rb"), "sha256").hexdigest(),
                "archive_bytes": args.destination.stat().st_size,
                "scope": "Run JSON, logs, profiles, sidecars and behavior proofs; no caches/output bases"}
    with args.destination.with_suffix(args.destination.suffix + ".json").open("x") as output:
        json.dump(manifest, output, indent=2)
        output.write("\n")
    print(json.dumps({key: value for key, value in manifest.items() if key != "members"}))


if __name__ == "__main__":
    main()
