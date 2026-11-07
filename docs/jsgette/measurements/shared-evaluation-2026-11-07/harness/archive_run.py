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
    parser.add_argument("--abort-record", type=Path,
                        help="Explicit root-level abort JSON when report.json was never written")
    args = parser.parse_args()
    root = args.run.resolve(strict=True)
    report_path = root / "report.json"
    abort_record = None
    if report_path.is_file():
        if args.abort_record:
            parser.error("--abort-record is only for runs without report.json")
        report = json.loads(report_path.read_text())
        run_state = "completed" if report["passed"] else "failed"
    else:
        if not args.abort_record:
            parser.error("Missing report.json; supply an explicit --abort-record to preserve an interrupted run")
        record_path = args.abort_record.resolve(strict=True)
        if record_path.parent != root or record_path.suffix != ".json":
            parser.error("Abort record must be a root-level JSON file inside the run")
        abort_record = json.loads(record_path.read_text())
        if abort_record.get("status") != "aborted" or abort_record.get("passed") is not False:
            parser.error("Abort record must explicitly declare status=aborted and passed=false")
        report = {"passed": False}
        run_state = "aborted_without_report"
    files = set(root.glob("*.json"))
    files.update(root.glob("*.log"))
    for phase in ("preparation", "measured", "restart-shutdown", "final-shutdown"):
        directory = root / phase
        if directory.is_dir():
            files.update(path for path in directory.rglob("*") if path.is_file())
    if abort_record is not None:
        # Preserve bounded JVM diagnostics without archiving repository trees,
        # action outputs, or caches from the interrupted output bases.
        files.update(path for path in root.glob("o[0-9][0-9]/java.log.*")
                     if path.is_file() and not path.is_symlink())
        files.update(path for path in root.glob("o[0-9][0-9]/server/jvm.out") if path.is_file())
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
    manifest = {"schema_version": 2, "run": str(root), "passed": report["passed"],
                "run_state": run_state, "report_present": report_path.is_file(),
                "measured_phase_present": (root / "measured").is_dir(),
                "archive": args.destination.name, "members": members,
                "archive_sha256": hashlib.file_digest(args.destination.open("rb"), "sha256").hexdigest(),
                "archive_bytes": args.destination.stat().st_size,
                "scope": "Run JSON, logs, profiles, sidecars and behavior proofs; aborted runs also include bounded JVM logs; no cache or action-output trees"}
    if abort_record is not None:
        manifest.update(abort_record=args.abort_record.name, performance_eligible=False,
                        milestone_verdict="not_assessed")
    with args.destination.with_suffix(args.destination.suffix + ".json").open("x") as output:
        json.dump(manifest, output, indent=2)
        output.write("\n")
    print(json.dumps({key: value for key, value in manifest.items() if key != "members"}))


if __name__ == "__main__":
    main()
