"""Prepare private Python bindings for Bazel's pinned remote execution protocol."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request


GOOGLEAPIS = "de157ca34fa487ce248eb9130293d630b501e4ad"
PROTOS = {
    "google/bytestream/bytestream.proto": "961b833f35f4bdc51df4bca017cffdba299893e89762bf8041465560106dd3d6",
    "google/longrunning/operations.proto": "fcf560f3b8b9d1bdda82803026ac8caa616b0ee19d0f554e8eb9ef7b2f201fc2",
}
PACKAGES = ["grpcio==1.76.0", "grpcio-tools==1.76.0", "protobuf==6.33.5",
            "googleapis-common-protos==1.72.0", "typing-extensions==4.16.0",
            "setuptools==84.2.1", "psutil==6.1.1"]


def prepare(destination):
    root = destination.resolve()
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError("The bindings destination must be an empty private directory")
    deps = root / "deps"
    subprocess.run([sys.executable, "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
                    "install", "--only-binary=:all:", "--no-warn-script-location",
                    "--target", str(deps), *PACKAGES], check=True)
    includes = root / "protos"
    for name, digest in PROTOS.items():
        url = f"https://raw.githubusercontent.com/googleapis/googleapis/{GOOGLEAPIS}/{name}"
        with urllib.request.urlopen(url, timeout=30) as response:
            content = response.read()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError(f"Unexpected protocol source: {name}")
        path = includes / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    generated = root / "generated"
    generated.mkdir()
    bazel_apis = Path(__file__).resolve().parents[5] / "third_party/remoteapis"
    env = dict(os.environ, PYTHONPATH=str(deps), PYTHONNOUSERSITE="1")
    sources = ["google/bytestream/bytestream.proto", "build/bazel/semver/semver.proto",
               "build/bazel/remote/execution/v2/remote_execution.proto"]
    subprocess.run([sys.executable, "-B", "-m", "grpc_tools.protoc",
                    *(f"-I={p}" for p in (includes, bazel_apis, deps, deps / "grpc_tools/_proto")),
                    f"--python_out={generated}", f"--grpc_python_out={generated}", *sources],
                   env=env, check=True)
    env["PYTHONPATH"] = os.pathsep.join(map(str, (generated, deps)))
    subprocess.run([sys.executable, "-B", "-c",
                    "from build.bazel.remote.execution.v2 import remote_execution_pb2_grpc; "
                    "from google.bytestream import bytestream_pb2_grpc"], env=env, check=True)
    print(json.dumps({"PYTHONPATH": env["PYTHONPATH"], "packages": PACKAGES,
                      "googleapis_revision": GOOGLEAPIS}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    prepare(parser.parse_args().destination)
