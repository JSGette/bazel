# Initial skystore UDS measurements — Linux, 2026-10-19

This records the optional [Phase 0 prototype](../skystore-prototype.md)
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
- Existing Skyframe suites: 1,464 testcase records, 1,458 pass, 6 existing skips,
  zero failures or errors (SkyframeTests and SkyframeEvictionTest). Counts use
  testcase elements; summing aggregate suite counters would double-count tests.
- Bazel source: //src:bazel-dev builds successfully; copied binary builds a
  genrule in an isolated workspace and output base and produces expected output.
- Six benchmark configurations finish with all returned values verified.

The initial build, tests, and IPC measurements used local execution. The
Cache & Carry checks below were performed later; the IPC table above was not
rerun and is not a comparison of Bazel engines.

## Cache & Carry follow-up

The connected Raspberry Pi USB Gadget (2e8a:0013) initially used Windows'
serial driver. Installing the signed Raspberry Pi RNDIS driver exposed the
USB network adapter. Windows Internet Connection Sharing then supplied the
appliance's startup license and clock synchronization over the same cable.

The appliance retains 10.55.0.1 for cache traffic. It also has a private
192.168.137.2 address for Windows' 192.168.137.1 internet-sharing gateway.
A systemd service restores that address, route, and DNS at boot. WSL uses
mirrored networking because its default NAT mode could not reach the appliance
while Windows internet sharing was enabled. Cache and internet connectivity
were verified again after rebooting the appliance.

- HTTP/2 GET http://10.55.0.1:8080/healthz returned 200 and HEALTHY.
- The Build & Test UI on port 8088 returned 200.
- A Linux genrule build uploaded its result in invocation
  4f3dbebe-ad19-44ea-9710-d3407841fbb3.
- A second build in a distinct, fresh output base reported one remote cache hit
  in invocation 911e8e8a-c557-4a4e-87dc-6fe5cd6bee3b. Both builds' output
  contents were checked, so this establishes upload and download functionality
  independently of Bazel's local action cache.

A fresh Linux output base then built //src:bazel-dev and ran the Skyframe and
Java client suites with --config=efcache, --jobs=4, and
--local_resources=memory=6144. Invocation
9fa95d2c-4f06-4854-8897-9a657ececd30 completed successfully: 5,785 actions,
all three test targets passing, and no cache errors. This first build populated
the cache; its actions executed locally.

The full prototype test script also passed with the same cache configuration:
8 Rust tests, rustfmt, Clippy, 8 Java client tests, and the Java/Rust process
integration checks. The newly built Bazel binary produced the expected genrule
output in a separate workspace/output base with Cache & Carry enabled
(invocation 930b7d5b-89fd-4922-ad52-437061f0a4c8).

Building the untouched fork baseline (4cfc05f77b) in a separate checkout and
fresh output base then reused 4,703 remote cached actions, with 1,002 internal
actions and no local compile actions reported. It completed in 29.524 seconds
(invocation de2d48b5-8919-4c8d-9f3c-0e2a0d035f10), confirming that the
appliance serves the Bazel build itself as well as the small smoke target.

These are functional cache checks, not performance comparisons. A vanilla
baseline and the integrated engine must be measured with matched workloads
and cache state, and the Rust process must be included in RAM accounting.

References: [EngFlow setup](https://docs.engflow.com/myengflow/cache-carry.html),
[Raspberry Pi driver](https://github.com/raspberrypi/rpi-usb-gadget/releases/tag/v1.0.6),
[WSL mirrored networking](https://learn.microsoft.com/en-us/windows/wsl/networking#mirrored-mode-networking).
