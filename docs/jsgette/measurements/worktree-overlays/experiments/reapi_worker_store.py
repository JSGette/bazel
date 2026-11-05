"""Disk CAS and Merkle materialization for the trusted local REAPI experiment."""

import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile

from build.bazel.remote.execution.v2 import remote_execution_pb2 as api


HEX = re.compile(r"[a-f0-9]{64}")
EMPTY = hashlib.sha256(b"").hexdigest()


def digest(data):
    return api.Digest(hash=hashlib.sha256(data).hexdigest(), size_bytes=len(data))


def relative(name, *, empty=False):
    if empty and name == "":
        return "."
    parts = name.split("/")
    if (not name or any(p in ("", ".", "..") for p in parts) or "\\" in name
            or ":" in name or "\0" in name):
        raise ValueError(f"Unsupported relative path {name!r}")
    return os.path.join(*parts)


def member(name):
    if "/" in name:
        raise ValueError(f"Directory entry must be a component: {name!r}")
    return relative(name)


def atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=os.path.dirname(path), delete=False) as stream:
        scratch = stream.name
        try:
            stream.write(data)
        except BaseException:
            stream.close()
            os.unlink(scratch)
            raise
    try:
        os.replace(scratch, path)
    finally:
        if os.path.exists(scratch):
            os.unlink(scratch)


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.cas = self.root / "cas"
        self.ac = self.root / "ac"
        self.incoming = self.root / "incoming"
        for directory in (self.cas, self.ac, self.incoming):
            directory.mkdir(parents=True, exist_ok=True)
        (self.cas / EMPTY).touch(exist_ok=True)

    def path(self, key):
        if not HEX.fullmatch(key.hash) or key.size_bytes < 0:
            raise ValueError("Expected SHA-256 digest")
        # High-cardinality hashes stay strings. Some Python versions intern
        # components when a pathlib object is converted to a filesystem path.
        return os.path.join(self.cas, key.hash)

    def has(self, key):
        try:
            return os.stat(self.path(key)).st_size == key.size_bytes
        except FileNotFoundError:
            return False

    def read(self, key):
        if not self.has(key):
            raise FileNotFoundError(f"Missing blob {key.hash}/{key.size_bytes}")
        with open(self.path(key), "rb") as stream:
            return stream.read()

    def put(self, data, expected=None):
        key = digest(data)
        if expected is not None and key != expected:
            raise ValueError("CAS digest mismatch")
        if not self.has(key):
            # Move only after closing. Concurrent writers supply identical content.
            self.install_bytes(key, data)
        return key

    def install_bytes(self, key, data):
        with tempfile.NamedTemporaryFile(dir=self.incoming, delete=False) as stream:
            scratch = stream.name
            try:
                stream.write(data)
                stream.close()
                self.install(scratch, key)
            finally:
                stream.close()
                os.unlink(scratch)

    def install(self, scratch, key):
        dest = self.path(key)
        try:
            # No replacement of an already open Win32 input file.
            os.link(scratch, dest)
        except FileExistsError:
            if not self.has(key):
                raise ValueError("Existing CAS blob has wrong length")

    def load_tree(self, key, dest):
        os.makedirs(dest, exist_ok=True)
        directory = api.Directory.FromString(self.read(key))
        for file in directory.files:
            target = os.path.join(dest, member(file.name))
            path = self.path(file.digest)
            if not self.has(file.digest):
                raise FileNotFoundError(f"Missing input {file.digest.hash}")
            shutil.copyfile(path, target)
            if os.name != "nt" and file.is_executable:
                os.chmod(target, 0o755)
        for child in directory.directories:
            self.load_tree(child.digest, os.path.join(dest, member(child.name)))
        if directory.symlinks:
            raise ValueError("Symlink inputs are outside this worker's fixture support")

    def output_tree(self, directory):
        children = {}

        def visit(path):
            message = api.Directory()
            with os.scandir(path) as entries:
                ordered = sorted(entries, key=lambda f: f.name)
            for entry in ordered:
                if entry.is_symlink():
                    raise ValueError("Symlink outputs are outside this worker's fixture support")
                if entry.is_dir():
                    node, key = visit(entry.path)
                    children[key.hash] = node
                    message.directories.add(name=entry.name, digest=key)
                elif entry.is_file():
                    with open(entry.path, "rb") as f:
                        key = self.put(f.read())
                    message.files.add(name=entry.name, digest=key,
                                      is_executable=os.name != "nt" and os.access(entry.path, os.X_OK))
                else:
                    raise ValueError(f"Unsupported output {entry}")
            key = self.put(message.SerializeToString(deterministic=True))
            return message, key

        root, root_key = visit(directory)
        tree = api.Tree(root=root, children=list(children.values()))
        return self.put(tree.SerializeToString(deterministic=True)), root_key
