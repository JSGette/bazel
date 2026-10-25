#!/usr/bin/env python3
"""Verify recorded extraction bytes and application without changing the real index.

Usage: python3 verify.py /path/to/fork
Requires the source commits and upstream base from audit.json in that repository.
This performs no build or runtime test. JSON results are written to stdout.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    repo = Path(sys.argv[1]).resolve()
    artifacts = Path(__file__).resolve().parent
    audit = json.loads((artifacts / "audit.json").read_text())

    def git(*args, env=None, data=None):
        return subprocess.run(
            ["git", "-C", str(repo), *args], input=data, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        ).stdout

    real_index = Path(git("rev-parse", "--path-format=absolute", "--git-path", "index")
                      .decode().strip())
    index_before = hashlib.sha256(real_index.read_bytes()).hexdigest()
    results = []
    with tempfile.TemporaryDirectory(prefix="skycache-upstream-audit-") as directory:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(directory) / "index"))
        for patch in audit["patches"]:
            data = (artifacts / patch["patch_file"]).read_bytes()
            assert hashlib.sha256(data).hexdigest() == patch["patch_sha256"], patch["id"]
            extraction = patch["extraction"]
            paths = extraction.get("paths", patch["paths"])
            source = git("diff", "--binary", "--full-index", extraction["from"],
                         extraction["to"], "--", *paths)
            selectors = extraction.get("hunk_selectors")
            if selectors:
                selected = []
                for part in re.split(rb"(?m)(?=^diff --git )", source):
                    if not part:
                        continue
                    path = part.splitlines()[0].decode().split(" b/", 1)[1]
                    hunks = re.split(rb"(?m)(?=^@@ )", part)
                    matching = [hunk for hunk in hunks[1:] if any(
                        selector["path"] == path and selector["contains"].encode() in hunk
                        for selector in selectors)]
                    if matching:
                        selected.append(hunks[0] + b"".join(matching))
                source = b"".join(selected)
            assert source == data, f"Changed extraction bytes: {patch['id']}"
            git("read-tree", audit["upstream_base"], env=env)
            git("apply", "--cached", "--check", "-", env=env, data=data)
            results.append({"id": patch["id"], "source_bytes_identical": True,
                            "sha256_verified": True, "applies_to_upstream_base": True})
    assert hashlib.sha256(real_index.read_bytes()).hexdigest() == index_before
    print(json.dumps({"upstream_base": audit["upstream_base"], "patches": results,
                      "real_index_unchanged": True, "upstream_runtime_tests_run": False},
                     indent=2))


if __name__ == "__main__":
    main()
