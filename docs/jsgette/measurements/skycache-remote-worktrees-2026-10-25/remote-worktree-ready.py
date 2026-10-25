"""Read-only gRPC readiness check against an unused, valid SkyCache namespace."""
import json
import sys
import time
import uuid

import grpc

endpoint, instance = sys.argv[1:]


def blob(field, data):
    # The fixed scope below and all its fields fit a one-byte protobuf length.
    assert field < 16 and len(data) < 128
    return bytes([field * 8 + 2, len(data)]) + data


scope = blob(1, instance.encode()) + blob(2, b'0' * 64)
request = blob(1, scope) + b'\x10\x01'
request_metadata = (blob(1, blob(1, b'skycache-worktree-readiness'))
                    + blob(2, b'readiness') + blob(3, uuid.uuid4().hex.encode()))
metadata = (('build.bazel.remote.execution.v2.requestmetadata-bin', request_metadata),)
started = time.monotonic()
with grpc.insecure_channel(endpoint.removeprefix('grpc://')) as channel:
    grpc.channel_ready_future(channel).result(timeout=30)
    discover = channel.unary_unary('/build.bazel.skycache.v1.SkyCacheCatalog/DiscoverPublications')
    response = discover(request, timeout=30, wait_for_ready=True, metadata=metadata)
    assert response == b'', 'Readiness namespace unexpectedly contains publications'
print(json.dumps({'ready': True, 'method': 'DiscoverPublications', 'response_bytes': len(response),
                  'namespace': '0' * 64, 'grpcio': grpc.__version__,
                  'seconds': time.monotonic() - started, 'catalog_mutated': False}))
