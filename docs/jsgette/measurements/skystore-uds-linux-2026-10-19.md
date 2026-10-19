# Initial skystore UDS measurements — Linux, 2026-10-19

This records the optional [Phase 0 prototype](../../../tools/skystore/README.md)
in the commit introducing this file, based on fork commit 4cfc05f77b.
It is a protocol and lifecycle implementation with a synthetic IPC measurement,
not a production tiered Skyframe engine or a completed Phase 0 decision memo.

## Environment and method

- WSL2 Ubuntu, Linux 6.6.87.2-microsoft-standard-WSL2, x86_64.
- AMD Ryzen 9 9950X3D, 16 cores / 32 logical CPUs; WSL memory limit about 15 GiB.
- Rust 1.99.1, Cargo 1.99.1; release profile, no third-party Rust dependencies.
- Bazel 9.3.0 launcher; benchmark runtime Zulu OpenJDK 25.0.4+7-LTS.
- Java heap: 256 MiB initial, 512 MiB maximum.
- Run started 2026-10-19 17:38:52 UTC, after the source build and tests finished.
- One local Rust server, one Java client, warm in-memory values, no disk I/O,
  compression, Skyframe serialization, or concurrent client load.
- Six configurations, each in a new Java process: 1,000 warmup put/get pairs,
  then 5,000 measured pairs. Every get result is checked byte-for-byte outside
  its timed interval. Allocation, framing, transport, and decoding are timed.
- One run, no CPU pinning. WSL scheduling, GC, and other host activity may affect
  the numbers; the host was not otherwise controlled.

Reproduce from the repository root:

    tools/skystore/benchmark.sh > /tmp/skystore-uds.csv

[Raw CSV](skystore-uds-linux-2026-10-19.csv) includes p50, p99, mean request
latency and values/second derived from the mean timed request.

## Results

| Value bytes | Batch | Put p50 µs | Get p50 µs | Put p99 µs | Get p99 µs |
|---:|---:|---:|---:|---:|---:|
| 64 | 1 | 59.984 | 61.001 | 132.289 | 133.377 |
| 64 | 64 | 86.043 | 69.862 | 130.700 | 120.584 |
| 64 | 256 | 114.864 | 85.109 | 190.964 | 188.720 |
| 1024 | 1 | 67.673 | 66.364 | 133.647 | 138.086 |
| 1024 | 64 | 106.229 | 79.136 | 184.315 | 184.886 |
| 1024 | 256 | 222.925 | 168.270 | 352.899 | 328.312 |

Batching amortizes per-value IPC cost in this prototype. The single-value
end-to-end times exceed the design's estimated 5–15 µs raw socket round trip,
but this experiment includes Java allocation/decoding, store work and WSL
scheduling. It does not isolate syscall latency or identify its bottleneck.

Continue Phase 0 measurement. These numbers do not approve a production
transport, replacing hot-path graph operations with RPC, or starting value
spilling. Shared-memory comparison, native Linux/macOS runs, real serialized
values, heap attribution, and storage measurements remain open.

## Validation

All builds and tests ran in WSL Linux:

- Rust: 8 tests pass; rustfmt and Clippy with warnings denied pass.
- Java protocol client: 8 tests pass under Bazel.
- Java/Rust integration: actual Rust process passes large and empty values,
  missing keys, ordered duplicate reads, overwrite, empty batches, reconnect,
  rejected malformed writes without mutation, occupied socket protection,
  restart cache loss, and server-death checks.
- Existing Skyframe suites: 3,142 reported cases, 3,136 pass, 6 existing skips,
  zero failures or errors (SkyframeTests and SkyframeEvictionTest).
- Bazel source: //src:bazel-dev builds successfully; copied binary builds a
  genrule in an isolated workspace and output base and produces expected output.
- Six benchmark configurations finish with all returned values verified.

The initial build and tests used local execution. Cache & Carry configuration
was prepared, but Windows bound the connected Raspberry Pi USB Gadget
(2e8a:0013) to the serial driver on COM7. The official signed network-driver
installer's administrator request was canceled. Cache reachability and a
cache-enabled Bazel build were therefore not verified in this run.
