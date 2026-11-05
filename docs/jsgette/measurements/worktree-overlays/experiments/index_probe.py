"""Cold reconstruction with a committed per-package index. Python stdlib and Git."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile


def blob_id(content):
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def parse(package, content, exists):
    """The whole toy language: 'dep NAME' and 'if BASENAME NAME'."""
    deps, observations = [], {}
    for line in content.decode("ascii").splitlines():
        words = line.split()
        if words[:1] == ["dep"] and len(words) == 2:
            deps.append(words[1])
        elif words[:1] == ["if"] and len(words) == 3:
            path = package + "/" + words[1]
            observations[path] = exists(path)
            if observations[path]:
                deps.append(words[2])
        else:
            raise ValueError("unknown declaration: " + line)
    return sorted(set(deps)), observations


def git(folder, *args):
    inherited = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env = {**inherited, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "Probe", "GIT_COMMITTER_NAME": "Probe",
           "GIT_AUTHOR_EMAIL": "probe@example.invalid", "GIT_COMMITTER_EMAIL": "probe@example.invalid",
           "GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z"}
    return subprocess.check_output(["git", "-C", str(folder), "-c", "core.autocrlf=false", *args], env=env)


def fixture(folder):
    definitions = {"app": b"dep lib\ndep util\nif feature.flag extra\n", "lib": b"dep core\n",
                   "core": b"", "util": b"", "extra": b"", "alt": b""}
    definitions.update({"unused%02d" % i: b"dep util\n" for i in range(58)})
    total_index = 0
    for package, content in definitions.items():
        path = folder / package / "BUILD.toy"
        path.parent.mkdir()
        path.write_bytes(content)
        # All fixture condition files are initially absent.
        deps, observations = parse(package, content, lambda _: False)
        record = {"schema": 1, "producer": "toy-parser/v1", "package": package,
                  "definition_blob": blob_id(content), "existence": observations, "deps": deps}
        raw = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        shard = folder / ".graph-hints" / (package + ".json")
        shard.parent.mkdir(exist_ok=True)
        shard.write_bytes(raw)
        total_index += len(raw)
    git(folder, "init", "--quiet", "--template=", "--object-format=sha1")
    git(folder, "add", "--all")
    git(folder, "commit", "--quiet", "-m", "Synthetic source and generated shards")
    return {"packages_parsed_at_generation": len(definitions), "shards": len(definitions),
            "index_bytes_committed": total_index, "base_commit": git(folder, "rev-parse", "HEAD").decode().strip()}


class Driver:
    """A pinned Git base plus a known sparse byte edit map. No file change discovery."""
    def __init__(self, folder, revision, edits):
        self.folder, self.revision, self.edits = folder, revision, edits
        self.stats = Counter()

    def oid(self, path):
        if path in self.edits:
            raw = self.edits[path]
            self.stats["overlay_lookups"] += 1
            if raw is not None:
                self.stats["overlay_bytes_hashed"] += len(raw)
                return blob_id(raw)
            return None
        self.stats["git_tree_path_lookups"] += 1
        line = git(self.folder, "ls-tree", "-z", self.revision, "--", path)
        return line.split(b"\t")[0].split()[2].decode() if line else None

    def read(self, path, oid, kind):
        raw = self.edits[path] if path in self.edits else git(self.folder, "cat-file", "blob", oid)
        self.stats[kind + "_bytes_read"] += len(raw)
        self.stats[kind + "_reads"] += 1
        return raw


def reconstruct(driver, mode):
    work, graph = ["app"], {}
    s = driver.stats
    while work:
        name = work.pop()
        if name in graph:
            continue
        path, shard = name + "/BUILD.toy", ".graph-hints/" + name + ".json"
        deps = None
        definition_oid = None
        if mode != "parse":
            shard_oid = driver.oid(shard)
            if shard_oid:
                try:
                    record = json.loads(driver.read(shard, shard_oid, "index"))
                    if (record["schema"], record["producer"], record["package"]) != (1, "toy-parser/v1", name):
                        raise ValueError("schema or producer mismatch")
                    if not isinstance(record["deps"], list) or not isinstance(record["existence"], dict):
                        raise ValueError("malformed dependency list or observation map")
                    if mode == "blind":
                        deps = record["deps"]
                    else:
                        definition_oid = driver.oid(path)
                        s["definition_validations"] += 1
                        valid = definition_oid == record["definition_blob"]
                        if valid:
                            for candidate, observed in record["existence"].items():
                                s["existence_validations"] += 1
                                if (driver.oid(candidate) is not None) != observed:
                                    valid = False
                                    break
                        if valid:
                            deps = record["deps"]
                        else:
                            s["stale_shards"] += 1
                except (ValueError, KeyError, TypeError):
                    s["invalid_shards"] += 1
            else:
                s["missing_shards"] += 1
        if deps is None:
            definition_oid = definition_oid or driver.oid(path)
            if definition_oid is None:
                raise RuntimeError("missing " + path)
            raw = driver.read(path, definition_oid, "definition")
            deps, _ = parse(name, raw, lambda candidate: driver.oid(candidate) is not None)
            s["packages_parsed"] += 1
        graph[name] = deps
        work.extend(reversed(deps))
    s["packages_reached"] = len(graph)
    return graph, dict(sorted(s.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    scenarios = {"base": {}, "changed_definition": {"lib/BUILD.toy": b"dep alt\n"},
                 "new_candidate": {"app/feature.flag": b""},
                 "missing_shard": {".graph-hints/util.json": None},
                 "bad_shard": {".graph-hints/util.json": b"broken"},
                 "schema_mismatch": {".graph-hints/util.json": b'{"schema":0,"producer":"old","package":"util"}'}}
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        generation = fixture(folder)
        result = {"generation": generation, "cases": {}}
        for label, edits in scenarios.items():
            expected = {"app": ["lib", "util"], "lib": ["core"], "core": [], "util": []}
            if label == "changed_definition":
                del expected["core"]
                expected.update(lib=["alt"], alt=[])
            elif label == "new_candidate":
                expected["app"] = ["extra", "lib", "util"]
                expected["extra"] = []
            arms = {}
            for mode in ("parse", "blind", "validate"):
                graph, counts = reconstruct(Driver(folder, generation["base_commit"], edits), mode)
                correct = graph == expected
                should_pass = mode != "blind" or label not in ("changed_definition", "new_candidate")
                if correct != should_pass:
                    raise AssertionError((label, mode, graph, expected))
                arms[mode] = {**counts, "matches_oracle": correct}
            result["cases"][label] = {"expected_graph": expected, "arms": arms}
    data = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(data, encoding="utf-8")
    else:
        print(data, end="")


if __name__ == "__main__":
    main()
