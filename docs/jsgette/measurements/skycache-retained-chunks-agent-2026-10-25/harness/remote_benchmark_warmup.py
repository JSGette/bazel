#!/usr/bin/env python3
# Copyright 2026 The Bazel Authors. All rights reserved.
# Licensed under the Apache License, Version 2.0. See the repository LICENSE.
"""Warm SkyCache server read paths without changing its publication catalog.

Requires only grpcio. Uses generic gRPC calls and the subset of protobuf wire
types present in skycache.proto and google/bytestream/bytestream.proto.
An existing CatalogReceipt contains the exact original idempotent CommitRequest;
its final 32 HMAC bytes are server-private and are omitted when decoding it.
This does NOT warm the first-time publication commit/receipt-write path.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import time
import uuid

import grpc


def varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def integer(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError('truncated varint')
        byte = data[offset]
        offset += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
    raise ValueError('oversized varint')


def fields(data):
    result = {}
    offset = 0
    while offset < len(data):
        tag, offset = integer(data, offset)
        number, kind = tag >> 3, tag & 7
        if not number:
            raise ValueError('invalid field number')
        if kind == 0:
            value, offset = integer(data, offset)
        elif kind in (1, 2, 5):
            if kind == 2:
                length, offset = integer(data, offset)
            else:
                length = 8 if kind == 1 else 4
            end = offset + length
            if end > len(data):
                raise ValueError('truncated field')
            value, offset = data[offset:end], end
        else:
            raise ValueError('unsupported wire type')
        result.setdefault(number, []).append(value)
    return result


def one(data, number, default=None):
    values = fields(data).get(number, [])
    if len(values) > 1:
        raise ValueError('duplicate singular field')
    return values[0] if values else default


def blob(number, data):
    return varint(number << 3 | 2) + varint(len(data)) + data


def digest(data):
    sha, size = one(data, 1).decode('ascii'), one(data, 2, 0)
    if not re.fullmatch('[0-9a-f]{64}', sha) or not 0 <= size <= 1024 * 1024:
        raise ValueError('invalid or oversized SHA-256 digest')
    return sha, size


def receipt_snapshot(catalog):
    result = {}
    for path in sorted(catalog.glob('receipt-*.pb')):
        with path.open('rb') as contents:
            encoded = contents.read(1024 * 1024 + 33)
        if not 32 < len(encoded) <= 1024 * 1024 + 32:
            raise ValueError('truncated or oversized catalog receipt')
        result[path.name] = encoded
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True, help='host:port, plaintext local worker')
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error('--rounds must be positive')
    stored_receipts = receipt_snapshot(args.catalog)
    receipts = []
    for encoded in stored_receipts.values():
        receipt = encoded[:-32]
        request, publication = one(receipt, 1), one(receipt, 2)
        scope = one(request, 1)
        manifest = digest(one(request, 2))
        if (not one(request, 3, b'') or manifest != digest(one(publication, 1))
                or one(publication, 2, 0) < 1):
            raise ValueError('invalid original idempotent receipt')
        receipts.append((request, publication, scope, manifest))
    if not receipts:
        raise ValueError('no primer receipts')

    started = time.monotonic()
    calls = {'discover': 0, 'read': 0, 'idempotent_commit': 0}
    read_bytes = 0
    # The worker rejects ByteStream calls without this REAPI RequestMetadata header.
    # Fields: ToolDetails tool_name=1, action_id=2, tool_invocation_id=3.
    request_metadata = (blob(1, blob(1, b'skycache-benchmark-warmup'))
                        + blob(2, b'server-read-path-warmup')
                        + blob(3, uuid.uuid4().hex.encode('ascii')))
    metadata = (('build.bazel.remote.execution.v2.requestmetadata-bin', request_metadata),)
    with grpc.insecure_channel(args.endpoint.removeprefix('grpc://')) as channel:
        grpc.channel_ready_future(channel).result(timeout=30)
        discover_rpc = channel.unary_unary('/build.bazel.skycache.v1.SkyCacheCatalog/DiscoverPublications')
        commit_rpc = channel.unary_unary('/build.bazel.skycache.v1.SkyCacheCatalog/CommitPublication')
        read_rpc = channel.unary_stream('/google.bytestream.ByteStream/Read')

        def discover(scope):
            calls['discover'] += 1
            response = discover_rpc(blob(1, scope) + varint(2 << 3) + varint(64),
                                    metadata=metadata, timeout=30)
            if one(response, 2, b''):
                raise ValueError('helper requires primer to fit one discovery page')
            return sorted((digest(one(ref, 1)), one(ref, 2, 0))
                          for ref in fields(response).get(1, []))

        def read(instance, target):
            nonlocal read_bytes
            sha, size = target
            resource = ((instance + '/') if instance else '') + f'blobs/{sha}/{size}'
            data = bytearray()
            responses = read_rpc(blob(1, resource.encode()), metadata=metadata, timeout=30)
            try:
                for response in responses:
                    chunk = one(response, 10, b'')
                    if len(chunk) > size - len(data):
                        raise ValueError('ByteStream read exceeds declared size: ' + resource)
                    data.extend(chunk)
            finally:
                responses.cancel()
            if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
                raise ValueError('ByteStream digest mismatch: ' + resource)
            calls['read'] += 1
            read_bytes += len(data)
            return bytes(data)

        scopes = sorted({scope for _, _, scope, _ in receipts})
        before = {scope: discover(scope) for scope in scopes}
        for _, publication, scope, manifest in receipts:
            if (manifest, one(publication, 2, 0)) not in before[scope]:
                raise ValueError('original receipt is absent from the frozen catalog')
        for _ in range(args.rounds):
            for scope in scopes:
                if discover(scope) != before[scope]:
                    raise ValueError('catalog changed before warm-up')
            chunks = set()
            for _, _, scope, manifest in receipts:
                instance = one(scope, 1, b'').decode()
                bundle = read(instance, manifest)
                for publication in fields(bundle).get(4, []):
                    for file in fields(publication).get(3, []):
                        chunks.update((instance, digest(chunk))
                                      for chunk in fields(file).get(3, []))
            for instance, target in sorted(chunks):
                read(instance, target)
            for request, publication, _, _ in receipts:
                # Replay the exact stored request, including its original idempotency key.
                response = commit_rpc(request, metadata=metadata, timeout=30)
                if digest(one(response, 1)) != digest(one(publication, 1)) or (
                        one(response, 2, 0) != one(publication, 2, 0)):
                    raise ValueError('idempotent commit changed receipt')
                calls['idempotent_commit'] += 1
        if any(discover(scope) != before[scope] for scope in scopes):
            raise ValueError('catalog changed during warm-up')

    if receipt_snapshot(args.catalog) != stored_receipts:
        raise ValueError('catalog receipt files changed during warm-up')
    receipt_hash = hashlib.sha256(json.dumps(
        [(name, hashlib.sha256(encoded).hexdigest())
         for name, encoded in stored_receipts.items()], separators=(',', ':')).encode()).hexdigest()

    result = {'passed': True, 'rounds': args.rounds, 'calls': calls,
              'read_bytes': read_bytes, 'seconds': time.monotonic() - started,
              'catalog_candidates_unchanged': True,
              'catalog_receipts_unchanged': True, 'catalog_receipts_sha256': receipt_hash,
              'request_metadata_sent': True,
              'new_publication_commit_warmed': False}
    encoded = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(encoded)
    print(encoded, end='')


if __name__ == '__main__':
    main()
