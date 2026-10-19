# Skystore: Phase 0 IPC prototype

This is the first executable slice of the
[tiered Skyframe design](../../docs/jsgette/skyframe-tiered-engine.md).
It measures Java-to-Rust batched value transfers over a Unix-domain socket.
It is **not connected to Bazel's evaluator** and does not change build semantics.
Java retains the hot evaluation plane in the planned architecture.

The Rust binary has no third-party dependencies. It uses an ephemeral map so
measurements isolate protocol, copies, allocation, and socket overhead from
serialization, compression, and SSD I/O. Its fingerprints are opaque 16-byte
keys; it neither computes nor validates content hashes. Restart loses all values.
This is a measurement tool, not a persistent cache or a production server.

## Build and test on Linux

Requirements: the repository's pinned Bazel version, Rust/Cargo >= 1.85, and a
C++ toolchain for building Bazel. Bazel supplies Java 25 for Java targets.
Cargo, rustfmt, and Clippy must be available on PATH.

From the repository root:

    tools/skystore/test.sh
    bazel build //src:bazel-dev
    bazel test //src/test/java/com/google/devtools/build/skyframe:all

The test script runs Rust formatting, Clippy, all Rust tests, the Java protocol
tests, and a Java process against the actual release-mode Rust executable.
The latter checks batching, missing/empty values, duplicate keys, reconnect,
malformed requests without partial writes, occupied socket paths, restart,
and server death. Set BAZEL=/path/to/bazel to use a particular launcher.

With EngFlow Cache & Carry reachable, configure these opt-in lines in
~/.bazelrc as described in the
[EngFlow guide](https://docs.engflow.com/myengflow/cache-carry.html):

    build:efcache --remote_cache=grpc://10.55.0.1:8080
    build:efcache --bes_backend=grpc://10.55.0.1:8080
    build:efcache --bes_results_url=http://10.55.0.1:8088/invocation/
    build:efcache --remote_timeout=600

Then use:

    tools/skystore/test.sh --config=efcache
    bazel build --config=efcache //src:bazel-dev
    bazel test --config=efcache //src/test/java/com/google/devtools/build/skyframe:all

The Java and Bazel actions use the remote cache. Cargo builds the optional Rust
spike separately; it does not use Bazel's remote action cache. rules_rust and
embedding the sidecar are Phase 3 work. The normal Bazel bootstrap continues to
require no Rust installation.

## Benchmark

Run on an otherwise idle machine:

    tools/skystore/benchmark.sh > /tmp/skystore-uds.csv

The script starts the release Rust binary in a private temporary directory,
runs Java with batches of 1/64/256 and values of 64/1024 bytes, then stops the
sidecar and removes its socket. Each configuration checks every returned value,
warms up 1,000 put/get pairs, and measures 5,000 pairs. CSV rows report p50, p99,
mean end-to-end microseconds per request and values/second separately for put
and get. The benchmark JVM uses a 256 MiB initial / 512 MiB maximum heap.
Each Java invocation prints a CSV header. Validation of returned bytes
is outside the timed get interval, but client allocation and decoding are included.

Record the commit, OS/kernel, CPU, Rust/JDK versions, build mode, load, and raw
CSV. These are synthetic warm-map measurements, not a Skyframe speedup result.
No timing thresholds are asserted in tests. Compare with shared memory and real
serialized values before choosing a production transport.

Initial [Linux measurements and validation](../../docs/jsgette/measurements/skystore-uds-linux-2026-10-19.md)
are recorded with raw CSV and environment details.

## Wire protocol v1

All integers are unsigned, big-endian. Each frame has a four-byte body length
followed by a one-byte version (1), a one-byte operation, and its payload.
The response echoes version and operation. Only one request may be outstanding
on a connection. A single connection can carry many requests.

| Operation | Request payload | Response payload |
|---|---|---|
| 0 Ping | Empty | Empty |
| 1 PutValues | Count u32, then (fingerprint[16], length u32, value[length]) per entry | Count u32 acknowledged |
| 2 GetValues | Count u32, then fingerprint[16] per entry | Count u32, then (found u8, [length u32, value[length]]) per entry |

Get results preserve request order, including duplicate keys. found=0 is a
miss with no following length/value; found=1 can contain a zero-length value.
Duplicate put keys take the last value in the batch. Empty batches are valid.
A put is fully validated, including its resulting store budget, before mutation.

Limits: 16 MiB per frame body, 4,096 entries per batch, and 64 MiB of retained
key/value bytes (HashMap and allocator overhead are additional). Get responses
must also fit the frame limit, including repeated values. Reduce batch size on
overflow. Unknown operations/versions, malformed payloads, invalid lengths,
trailing bytes, and exceeded budgets close the client connection without an
acknowledgement; they do not terminate the server. A dropped connection is not
proof that a previously sent put was not applied.

The Java client applies a whole-request deadline and closes on I/O/protocol
errors. The prototype server handles one connection at a time with 30-second
read/write inactivity timeouts. This deliberate single-client model is for
measuring batch IPC, not concurrent evaluator traffic.

Create the socket inside a caller-owned private directory. The server refuses
to replace any existing path. The owner stops the process and removes its socket;
the server deliberately leaves stale sockets after exit. The benchmark and
interop harnesses own and clean up their private directories.

## Remaining gates

- Shared-memory ring comparison, macOS runs, and repeatable IPC decision memo.
- Heap attribution, working-set tracing, codec coverage and real-value
  compression measurements from Phase 0.
- Compact Java graph and conformance work before production tiering.
- Storage-engine comparison, checksums, checkpoints, crash recovery,
  multi-client scheduling, and production sidecar lifecycle in later phases.

No engine flag, value spilling, persisted edges, or graph invalidation is enabled
by this prototype.
