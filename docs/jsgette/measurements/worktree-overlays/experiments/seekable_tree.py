"""Compare resuming pinned Git directory streams by ordinal or by raw-object byte offset.

The shared seek index consists of immutable, unpacked Git tree objects. Its files
contain the original entries, not query answers. Matches are emitted in Git DFS
order, which is bytewise ordering of package-relative regular-file paths.
"""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time


CHUNK = 16 * 1024
COUNTERS = (
    "entries_visited", "prefix_entries_replayed", "reader_evictions", "directory_opens",
    "git_metadata_requests", "git_tree_requests", "git_payload_bytes", "git_drain_bytes",
    "index_bytes_read", "index_bytes_written", "index_objects_built", "index_open_hits",
    "index_concurrent_duplicates", "git_processes", "root_lookups", "subpackage_checks",
    "subpackages_pruned", "special_entries_skipped", "max_frames", "match_count",
    "output_bytes", "max_position_bytes",
)


class Batch:
    """One sequential Git batch protocol channel. A tree body must finish before reuse."""

    def __init__(self, repo, metadata, stats):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0")
        mode = "--batch-check" if metadata else "--batch"
        self.proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", mode], env=env,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.stats = stats
        self.stats["git_processes"] += 1

    def headers(self, specs):
        # Only hex IDs, a caller-validated package, and fixed BUILD names reach the protocol.
        self.proc.stdin.write(b"".join(spec.encode("utf-8") + b"\n" for spec in specs))
        self.proc.stdin.flush()
        for _ in specs:
            header = self.proc.stdout.readline().rstrip(b"\n")
            if header.endswith(b" missing"):
                yield None
            else:
                fields = header.split()
                if len(fields) != 3:
                    raise ValueError(f"unexpected Git batch response {header!r}")
                yield fields[0].decode("ascii"), fields[1], int(fields[2])

    def info(self, *specs):
        self.stats["git_metadata_requests"] += len(specs)
        return list(self.headers(specs))

    def body(self, oid):
        item, = self.headers([oid])
        if item is None or item[:2] != (oid, b"tree"):
            raise ValueError(f"expected pinned Git directory {oid}")
        self.stats["git_tree_requests"] += 1
        return GitBody(self, item[2])

    def close(self):
        self.proc.stdin.close()
        self.proc.stdout.close()
        self.proc.wait()


class GitBody:
    def __init__(self, batch, size):
        self.batch = batch
        self.left = size

    def read(self, count):
        count = min(self.left, count)
        part = self.batch.proc.stdout.read(count)
        if len(part) != count:
            raise EOFError("truncated Git tree payload")
        self.batch.stats["git_payload_bytes"] += len(part)
        self.left -= len(part)
        return part

    def close(self):
        # A persistent batch process cannot accept its next request until the
        # previous body and framing newline have been consumed.
        self.batch.stats["git_drain_bytes"] += self.left
        while self.left:
            self.read(CHUNK)
        if self.batch.proc.stdout.read(1) != b"\n":
            raise EOFError("invalid Git batch terminator")


class Directory:
    """Incremental raw tree record parser. Each open depth has one bounded read buffer."""

    def __init__(self, stream, hash_bytes, stats, from_index):
        self.stream = stream
        self.hash_bytes = hash_bytes
        self.stats = stats
        self.from_index = from_index
        self.buf = b""
        self.i = 0

    def fill(self):
        block = self.stream.read(CHUNK)
        if self.from_index:
            self.stats["index_bytes_read"] += len(block)
        self.buf = self.buf[self.i:] + block
        self.i = 0
        return bool(block)

    def next(self):
        # Raw Git format: ASCII mode, space, filename, NUL, binary object ID.
        if self.i == len(self.buf) and not self.fill():
            return None
        while True:
            nul = self.buf.find(b"\0", self.i)
            if nul >= 0 and nul + 1 + self.hash_bytes <= len(self.buf):
                break
            if not self.fill():
                raise EOFError("malformed raw tree entry")
        mode, name = self.buf[self.i:nul].split(b" ", 1)
        end = nul + 1 + self.hash_bytes
        result = mode, name, self.buf[nul + 1:end].hex(), end - self.i
        self.i = end
        return result

    def close(self):
        self.stream.close()


class Source:
    def __init__(self, stack, repo, index, stats, hash_bytes):
        self.stack, self.repo, self.index = stack, repo, index
        self.stats, self.hash_bytes = stats, hash_bytes
        self.meta = self.new_batch(True)
        self.channels = {}

    def new_batch(self, metadata):
        batch = Batch(self.repo, metadata, self.stats)
        self.stack.callback(batch.close)
        return batch

    def payload(self, depth, oid):
        if depth not in self.channels:
            self.channels[depth] = self.new_batch(False)
        return self.channels[depth].body(oid)

    def cached(self, oid):
        # Only complete objects become visible. Each writer uses its own temporary
        # file, and races may duplicate construction before an immutable file wins.
        path = self.index / "raw-trees-v1" / oid[:2] / oid
        try:
            stream = path.open("rb", buffering=0)
            self.stats["index_open_hits"] += 1
            return stream
        except FileNotFoundError:
            pass
        path.parent.mkdir(parents=True, exist_ok=True)
        body = self.payload(0, oid)
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".building-", delete=False) as out:
                tmp = Path(out.name)
                while chunk := body.read(CHUNK):
                    out.write(chunk)
                    self.stats["index_bytes_written"] += len(chunk)
            self.stats["index_objects_built"] += 1
            try:
                os.link(tmp, path)
            except FileExistsError:
                self.stats["index_concurrent_duplicates"] += 1
            except OSError:
                # On filesystems without hard links, rename still makes complete,
                # identical bytes visible atomically. Windows rejects replacement.
                try:
                    os.rename(tmp, path)
                    tmp = None
                except FileExistsError:
                    self.stats["index_concurrent_duplicates"] += 1
        finally:
            body.close()
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        return path.open("rb", buffering=0)

    def open(self, frame, depth, strategy):
        self.stats["directory_opens"] += 1
        if strategy != "ordinal":
            stream = self.cached(frame["oid"])
            if strategy == "seek":
                stream.seek(frame["offset"])
                return Directory(stream, self.hash_bytes, self.stats, True)
            reader = Directory(stream, self.hash_bytes, self.stats, True)
        else:
            reader = Directory(self.payload(depth, frame["oid"]), self.hash_bytes, self.stats, False)
        for _ in range(frame["ordinal"]):
            if reader.next() is None:
                raise ValueError("saved ordinal exceeds pinned directory")
            self.stats["prefix_entries_replayed"] += 1
        return reader


def frame(path, oid):
    return {"path": path, "oid": oid, "ordinal": 0, "offset": 0}


def run(repo, tree, package, suffix, window, strategy, index_dir, *, recursive=True,
        checkpoint=None, max_windows=None, matches_out=None):
    """Run one query. An existing checkpoint resumes it; max_windows stops an invocation early.

    Ordinary benchmark windows drop all directory readers/buffers in process and
    retain the frame positions, output digest, Git helpers, and counters. Supplying
    checkpoint and max_windows additionally supports actual process exit/resume.
    """
    t0 = time.perf_counter()
    repo = Path(repo)
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree):
        raise ValueError("tree must be a full immutable tree OID")
    if strategy not in ("ordinal", "indexed-ordinal", "seek") or window < 0:
        raise ValueError("unknown strategy or negative window")
    if package == ".":
        package = ""
    if package and (any(p in ("", ".", "..") for p in package.split("/"))
                    or any(c in package for c in "\0\r\n\\")):
        raise ValueError("expected a repository-relative Git package path")
    if max_windows is not None and (not checkpoint or max_windows < 1 or window < 1):
        raise ValueError("max_windows requires a checkpoint and positive window")
    if strategy != "ordinal" and index_dir is None:
        raise ValueError("indexed strategies require an index directory")
    stats = dict.fromkeys(COUNTERS, 0)
    signature = [1, tree, package, suffix, recursive, strategy, window]
    cp = Path(checkpoint) if checkpoint else None
    output = Path(matches_out) if matches_out else (cp.with_suffix(cp.suffix + ".matches") if cp else None)
    saved = json.loads(cp.read_text(encoding="utf-8")) if cp and cp.is_file() else None
    digest = hashlib.sha256()
    if saved is not None:
        if saved["query"] != signature:
            raise ValueError("checkpoint belongs to a different query, tree or strategy")
        if output.stat().st_size != saved["stats"]["output_bytes"]:
            raise ValueError("checkpoint match output has changed")
        with output.open("rb") as old:
            while part := old.read(65536):
                digest.update(part)
        if digest.hexdigest() != saved["digest"]:
            raise ValueError("checkpoint match digest mismatch")
        stats.update(saved["stats"])
    frames, readers = [], {}
    windows_this_run = 0
    suffix_bytes = suffix.encode("utf-8")
    with ExitStack() as resources:
        sink = resources.enter_context(output.open("ab" if saved else "wb")) if output else None
        source = Source(resources, repo, Path(index_dir) if index_dir else None, stats, len(tree) // 2)
        if saved is None:
            top, = source.meta.info(tree)
            if top is None or top[1] != b"tree":
                raise ValueError("expected a pinned Git source tree")
            selected, = source.meta.info(tree + ":" + package) if package else [top]
            if selected is None or selected[1] != b"tree":
                raise ValueError(f"missing directory {package!r}")
            frames.append(frame("", selected[0]))
            stats["root_lookups"] += 1
        else:
            frames = saved["frames"]
        steps = 0
        try:
            while frames:
                depth = len(frames) - 1
                stats["max_frames"] = max(stats["max_frames"], len(frames))
                cur = frames[-1]
                if depth not in readers:
                    readers[depth] = source.open(cur, depth, strategy)
                row = readers[depth].next()
                if row is None:
                    readers.pop(depth).close()
                    frames.pop()
                    continue
                mode, name, oid, size = row
                cur["ordinal"] += 1
                cur["offset"] += size
                stats["entries_visited"] += 1
                steps += 1
                if mode == b"40000" and recursive:
                    stats["subpackage_checks"] += 1
                    markers = source.meta.info(oid + ":BUILD.bazel", oid + ":BUILD")
                    if any(m and m[1] == b"blob" for m in markers):
                        stats["subpackages_pruned"] += 1
                    else:
                        frames.append(frame(cur["path"] + name.decode("utf-8") + "/", oid))
                elif mode in (b"100644", b"100755"):
                    if not name.startswith(b".") and name.endswith(suffix_bytes):
                        match = cur["path"].encode("utf-8") + name + b"\0"
                        digest.update(match)
                        if sink is not None:
                            sink.write(match)
                        stats["match_count"] += 1
                        stats["output_bytes"] += len(match)
                elif mode != b"40000":
                    stats["special_entries_skipped"] += 1
                if steps == window and window:
                    # Serialize only the live spine to model exactly what is saved.
                    positions = json.dumps(frames, ensure_ascii=False, separators=(",", ":")).encode()
                    stats["max_position_bytes"] = max(stats["max_position_bytes"], len(positions))
                    for reader in readers.values():
                        reader.close()
                    readers.clear()
                    frames = json.loads(positions)
                    stats["reader_evictions"] += 1
                    windows_this_run += 1
                    steps = 0
                    if max_windows is not None and windows_this_run == max_windows:
                        break
        finally:
            for reader in readers.values():
                reader.close()
    if cp:
        state = {"query": signature, "frames": frames, "digest": digest.hexdigest(), "stats": stats}
        cp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    return {"done": not frames, **stats, "match_digest": digest.hexdigest(),
            "elapsed_seconds": time.perf_counter() - t0}


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--repo", type=Path, required=True)
    cli.add_argument("--tree", required=True)
    cli.add_argument("--package", required=True)
    cli.add_argument("--suffix", required=True)
    cli.add_argument("--window", type=int, default=0)
    cli.add_argument("--strategy", choices=["ordinal", "indexed-ordinal", "seek"], required=True)
    cli.add_argument("--index-dir", type=Path)
    cli.add_argument("--direct-only", action="store_true")
    cli.add_argument("--checkpoint", type=Path)
    cli.add_argument("--max-windows", type=int)
    cli.add_argument("--matches-out", type=Path)
    args = vars(cli.parse_args())
    args["recursive"] = not args.pop("direct_only")
    print(json.dumps(run(**args), sort_keys=True))


if __name__ == "__main__":
    main()
