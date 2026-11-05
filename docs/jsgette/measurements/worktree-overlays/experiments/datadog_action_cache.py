"""Private HTTP action cache for the one-action Datadog coordination experiment."""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading


class Cache(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256

    def __init__(self, root, key, lookup):
        self.root = root
        for name in ("cas", "ac", "incoming"):
            (root / name).mkdir(parents=True)
        self.key = key
        self.lookup = lookup
        self.prefix = secrets.token_hex(16)
        self.lock = threading.Lock()
        self.errors = []
        self.get_counts = {"ac_hit": 0, "ac_miss": 0, "cas_hit": 0, "cas_miss": 0}
        self.cas_misses = []
        super().__init__(("127.0.0.1", 0), Handler)

    def url(self, caller):
        return f"http://127.0.0.1:{self.server_port}/{self.prefix}/{caller}"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    def handle(self):
        try:
            super().handle()
        except ConnectionResetError:
            # Abrupt cancellation also closes unused HTTP keep-alive connections.
            self.close_connection = True

    def log_message(self, *args):
        pass

    def request_path(self):
        match = re.fullmatch("/" + self.server.prefix + r"/(producer|w[0-4])/(cas|ac)/([0-9a-f]{64})", self.path)
        if not match:
            self.send_error(404)
            return None
        caller, space, key = match.groups()
        return caller, space, key, self.server.root / space / key

    def answer(self, status, size=0):
        self.send_response(status)
        self.send_header("Content-Length", str(size))
        self.end_headers()

    def do_GET(self):
        route = self.request_path()
        if not route:
            return
        caller, space, key, path = route
        try:
            if self.server.lookup and space == "ac" and key == self.server.key and caller == "producer":
                # A failed publication can leave an unvalidated descriptor in HTTP staging.
                # Only a fresh local execution may supply the independent producer's receipt.
                with self.server.lock:
                    self.server.get_counts["ac_miss"] += 1
                self.answer(404)
                return
            if self.server.lookup and space == "ac" and key == self.server.key and caller != "producer":
                path = self.server.lookup(caller, key, self.connection)
            hit = path.is_file()
            with self.server.lock:
                self.server.get_counts[space + ("_hit" if hit else "_miss")] += 1
                if not hit and space == "cas" and len(self.server.cas_misses) < 20:
                    self.server.cas_misses.append({"caller": caller, "digest": key})
            if not hit:
                self.answer(404)
                return
            with path.open("rb") as stream:
                self.answer(200, os.fstat(stream.fileno()).st_size)
                shutil.copyfileobj(stream, self.wfile, length=1024 * 1024)
        except (ConnectionError, BrokenPipeError):
            self.close_connection = True
        except Exception as exc:
            self.server.errors.append(f"GET {space} {key}: {exc!r}")
            self.answer(500)

    def do_PUT(self):
        route = self.request_path()
        if not route:
            return
        caller, space, key, path = route
        if self.server.lookup and space == "ac" and key == self.server.key and caller != "producer":
            self.server.errors.append(f"Unexpected {caller} publication for the owned action")
            self.close_connection = True
            self.answer(409)
            return
        scratch = None
        try:
            remaining = int(self.headers["Content-Length"])
            if not 0 <= remaining <= 2 * 1024**3:
                raise ValueError("Blob outside fixture size limit")
            digest = hashlib.sha256()
            with tempfile.NamedTemporaryFile(dir=self.server.root / "incoming", delete=False) as stream:
                scratch = Path(stream.name)
                while remaining:
                    data = self.rfile.read(min(remaining, 1024 * 1024))
                    if not data:
                        raise ConnectionError("Incomplete upload")
                    stream.write(data)
                    digest.update(data)
                    remaining -= len(data)
            if space == "cas" and key != digest.hexdigest():
                raise ValueError("CAS upload did not match its SHA-256")
            with self.server.lock:
                refresh_recipe = self.server.lookup and caller == "producer" and space == "ac" and key == self.server.key
                if refresh_recipe or not path.exists():
                    os.replace(scratch, path)
            self.answer(200)
        except (OSError, ValueError, TypeError) as exc:
            self.server.errors.append(f"PUT {space} {key}: {exc!r}")
            self.close_connection = True
            self.answer(400)
        finally:
            if scratch:
                scratch.unlink(missing_ok=True)


def fields(proto):
    """Decode the Protobuf wire types used by the pinned REAPI messages below."""
    offset = 0

    def number():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            head = proto[offset]
            offset += 1
            value |= (head & 127) << shift
            if head < 128:
                return value
        raise ValueError("Invalid protobuf varint")

    while offset < len(proto):
        tag = number()
        wire = tag & 7
        if wire == 0:
            value = number()
        else:
            if wire not in (1, 2, 5):
                raise ValueError(f"Unexpected protobuf wire type {wire}")
            length = number() if wire == 2 else {1: 8, 5: 4}[wire]
            end = offset + length
            if end > len(proto):
                raise ValueError("Truncated protobuf")
            value, offset = proto[offset:end], end
        yield tag >> 3, value


def verify_result(result, cas):
    """Check the GoStdlib ActionResult and every reachable output blob before delivery."""
    seen = {}
    trees = 0

    def read_digest(raw, allow_tree=None):
        digest = dict(fields(raw))
        key, size = digest[1].decode("ascii"), digest.get(2, 0)
        if not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError("Invalid SHA-256 reference")
        if allow_tree is not None:
            if key not in allow_tree:
                raise ValueError(f"Directory missing from encoded Tree: {key}")
            data = allow_tree[key]
        elif size == 0:
            data = b""
        else:
            data = (cas / key).read_bytes()
        if len(data) != size or hashlib.sha256(data).hexdigest() != key:
            raise ValueError(f"Invalid output blob {key}")
        seen[key] = size
        return data

    def verify_directory(raw, children, ancestors):
        identity = hashlib.sha256(raw).hexdigest()
        if identity in ancestors:
            raise ValueError("Directory cycle")
        for tag, node in fields(raw):
            if tag in (1, 2):
                digest = dict(fields(node))[2]
                payload = read_digest(digest, children if tag == 2 else None)
                if tag == 2:
                    verify_directory(payload, children, ancestors | {identity})

    for tag, value in fields(result):
        if tag == 4 and value:
            raise ValueError(f"Producer action exit {value}")
        if tag in (6, 8):
            read_digest(value)
        if tag == 2:
            read_digest(dict(fields(value))[2])
        if tag == 3:
            directory = dict(fields(value))
            if 3 in directory:
                tree = list(fields(read_digest(directory[3])))
                children = {hashlib.sha256(raw).hexdigest(): raw for field, raw in tree if field == 2}
                root, = (raw for field, raw in tree if field == 1)
                verify_directory(root, children, set())
                if 5 in directory and read_digest(directory[5]) != root:
                    raise ValueError("Output root directory disagrees with its encoded Tree")
            elif 5 in directory:
                verify_directory(read_digest(directory[5]), None, set())
            else:
                raise ValueError("Output directory has no tree digest")
            trees += 1
    if not trees or len(seen) < 5:
        raise ValueError("Expected a nonempty Go standard-library output tree")
    # Counts include digest-addressed Directory messages embedded in Tree, so summed bytes
    # describe verified payloads rather than network transfer volume.
    return {"tree_artifacts": trees, "verified_unique_blobs": len(seen), "verified_blob_bytes": sum(seen.values())}
