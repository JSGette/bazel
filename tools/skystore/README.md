# Skystore: experimental Rust-backed Skyframe value tier

The optional `tiered_idle` engine mode connects Bazel's real Skyframe graph to
the Rust skystore process. Java retains evaluation, invalidation, keys, versions,
and edges. After command completion, audited glob values can move to a
checksummed spill file; the next command restores them before any command module
can read the graph. The default remains `inmemory`.

This is the first integrated slice of the
[tiered Skyframe design](../../docs/jsgette/skyframe-tiered-engine.md).
It is **not the complete tiered engine**: there is no compact graph, in-command
paging, cold edge store, cross-restart checkpoint, or broad value codec coverage.
It targets idle retained memory, not peak evaluation memory.

## Build and run on Linux

Build the optional sidecar through
[rules_rs](https://github.com/hermeticbuild/rules_rs) 0.1.0 using the repository's
pinned launcher (currently Bazel 9.3.0). Bazel downloads Rust 1.99.1, rustfmt, and
Clippy from the checksummed toolchain recorded in MODULE.bazel.lock; no local
Rust or Cargo installation is needed. Rust targets use edition 2021.

    bazel build --config=efcache -c opt //tools/skystore:skystore
    cp bazel-bin/tools/skystore/skystore /absolute/path/to/skystore
    bazel build --config=efcache //src:bazel-dev

The Rust library, binary, and unit tests load rules_rs wrappers. Formatting and
Clippy use the patched upstream rules supplied by rules_rs. The existing C/C++
toolchain selection is unchanged. Cargo.toml/Cargo.lock remain available for
editor support and standalone development; the build and validation scripts use
Bazel. The source bootstrap still needs only Java and C++: the sidecar is
optional and is not embedded in the Bazel binary.

Copy the resulting binary before running it against a workload in a separate
output base:

    /absolute/path/to/copied-bazel --output_base=/absolute/new/output-base build \
      --experimental_skyframe_engine=tiered_idle \
      --experimental_skystore_binary=/absolute/path/to/skystore \
      //your:target

Both flags are command options. Changing back to `inmemory` restores the graph
and stops the sidecar. A missing/unusable binary produces a warning and uses the
resident engine. An explicitly configured tier is therefore **best effort**;
check `OUTPUT_BASE/skyframe-tiering.json` to verify actual spill/restore counts,
serialized bytes, timings, recovery count, and sidecar PID.

The allowlist currently includes exact `GlobsValue` and
`GlobValueWithImmutableSet` classes. Metadata wrappers, subclasses, errors,
nested-set globs, dirty entries, and edgeless entries stay resident. Encodings
smaller than 1 KiB stay resident too. The 32 MiB limit counts serialized values
plus keys; it is not a heap-memory budget. Acknowledgement precedes detaching a
value. Restores validate SHA-256 and the versioned codec before publishing.

Maintenance runs only after all command-completion hooks. Entry replacement is
an identity-checked operation that leaves versions and dependency topology
unchanged. Storage I/O never occurs while holding a node-entry monitor.
A missing/corrupt value or dead sidecar causes the entire evaluator to be reset
before the next command evaluates anything. Failed writes leave values resident;
if recovery of already-detached values fails, the graph is reset.

The sidecar owns a new private directory under Linux /tmp, containing its Unix
socket and spill file. Files are never reused across restarts. Shutdown stops
the child and removes its files; a parent pipe also stops it after a JVM crash.
A crash can leave a private directory on disk, which is never reopened.
No fsync/durable recovery is promised or required for this ephemeral tier.

## Cache & Carry

The verified WSL setup uses the appliance over USB at 10.55.0.1 and enables the
following configuration only with `--config=efcache`, as described in the
[EngFlow guide](https://docs.engflow.com/myengflow/cache-carry.html):

    build:efcache --remote_cache=grpc://10.55.0.1:8080
    build:efcache --bes_backend=grpc://10.55.0.1:8080
    build:efcache --bes_results_url=http://10.55.0.1:8088/invocation/
    build:efcache --remote_timeout=600

Bazel/Java and rules_rs Rust actions use this remote action cache. Add
`--bes_backend=` during measurements to exclude build event upload latency while
retaining the remote action cache.

## Validation

Run on Linux:

    tools/skystore/test.sh --config=efcache
    bazel test --config=efcache \
      //src/test/java/com/google/devtools/build/skyframe:all \
      //src/test/java/com/google/devtools/build/lib/skyframe/tiering:all
    python3 tools/skystore/engine_integration_test.py \
      --bazel=/absolute/path/to/copied-bazel \
      --skystore=/absolute/path/to/skystore \
      --output=/absolute/new/test-directory --cache

`test.sh` runs `bazel test //tools/skystore:all`, including Rust unit tests,
Clippy (warnings are errors), rustfmt, Java client tests, and the Java/Rust
process interoperability test. The latter obtains the Rust binary through
Bazel runfiles, so it also works inside the Linux test sandbox.

The Rust tests cover framing, batches, limits, checksums, corrupt/truncated
files, atomic index publication, exclusive file creation, and clearing.
Java tests cover transport deadlines and validation, exact codec round trips,
metadata/dirty-value pinning, identity guards, version/edge preservation, storage
failures, and option registration.

The process integration script validates actual build outputs across source,
glob addition/deletion, BUILD and .bzl edits, flag changes, keep-going errors,
cycles, query, mode changes, clean, state dropping, restart, killed sidecars,
and deliberately corrupted spill files. Its output directory must be new.
The Java/Rust IPC interop test also checks malformed requests,
reconnect, empty/missing values, occupied sockets, and restart loss.

## Profiling

`profile_bazel.py` separates unprofiled analysis timing from diagnostic passes.
It records a fresh-output-base analysis, two warmups, repeated warm analysis,
process-tree RSS/PSS sampled every 50 ms, JFR CPU/allocation samples, a Bazel
trace and memory profile, post-GC heap histogram, and Skyframe value attribution.

    python3 tools/skystore/profile_bazel.py \
      --bazel=/absolute/path/to/copied-bazel \
      --workspace=/absolute/path/to/frozen-workload \
      --output=/absolute/new/profile-directory \
      --jcmd=/absolute/path/to/jdk/bin/jcmd \
      --build-arg=--config=efcache --build-arg=--bes_backend= \
      --build-arg=--experimental_skyframe_engine=tiered_idle \
      --build-arg=--experimental_skystore_binary=/absolute/path/to/skystore \
      //src:bazel-dev

Compare vanilla, modified-default, and modified-tiered binaries with identical
workloads and flags apart from the tier switch. Repeat in balanced order.
The sampler discovers descendants by PPID, including children spawned by
non-leader JVM threads, and includes the Rust process. RSS can double-count
shared pages; PSS apportions them. Timing runs do not enable JFR or forced GC.
The diagnostic pass clears and rebuilds the analysis graph. The memory profile
forces GC before the post-command spill; the separate class histogram reflects
the idle graph after spilling.

Add --execute to profile full builds including action execution. The engine integration script also accepts --vanilla to run the same edit/output matrix without tier-specific fault injection.

Analysis-only timings do not establish execution performance. Repository and
OS caches may already be warm, sampled peaks can miss brief spikes, and codec
coverage is deliberately narrow. Record raw results and avoid interpreting
serialized bytes as saved RAM. Large JFR recordings and dumps belong outside Git.

## Wire protocol v1

Integers are big-endian. Frames are a four-byte body length, version byte 1,
operation byte, and payload. Responses echo version and operation. One request
may be outstanding per connection.

| Operation | Request | Response |
|---|---|---|
| 0 Ping | Empty | Empty |
| 1 PutValues | Count u32, then (key[16], length u32, value[length]) | Count u32 acknowledged |
| 2 GetValues | Count u32, then key[16] | Count u32, then (found u8, [length u32, value[length]]) |
| 3 Clear | Empty | Empty |

Keys are opaque; the idle tier uses unique IDs within each cleared generation,
with SHA-256 integrity checks held separately in Java. GET preserves order,
duplicates, empty values and misses. PUT validates the whole request and budget
before mutation. Disk index updates are published only after all writes succeed.
A lost acknowledgement does not prove a PUT was not applied. CLEAR is used only
after restoring every live reference.

Limits: 16 MiB frames, 4,096 entries per batch, 64 MiB retained logical key/value
bytes, and a 1 GiB physical append-file cap. The disk backend keeps offsets,
lengths, and CRC32C checksums in RAM, not payloads. CLEAR truncates the file.
Corruption, malformed requests, invalid versions/operations, and exceeded
budgets close the connection. Later clients can reconnect. CRC32C detects
accidental disk corruption; it is not authentication.

The Java client uses a whole-request deadline. The server handles one connection
at a time with 30-second inactivity timeouts. The integrated controller disconnects
between commands. Standalone IPC benchmarks can still use the memory backend:

    tools/skystore/benchmark.sh > /tmp/skystore-uds.csv

The benchmark script builds the sidecar with `-c opt` through rules_rs and
resolves its configured Bazel output directory. Those synthetic warm-map
results isolate transport costs and are not evidence
of a Skyframe speedup. Initial
[IPC measurements](../../docs/jsgette/measurements/skystore-uds-linux-2026-10-19.md)
remain available. Production promotion still needs the compact graph,
working-set policy, wider audited codecs, concurrency/rewinding conformance,
bounded paging, and persistent validity/checkpoint work from the design.

The [integrated Linux comparison](../../docs/jsgette/measurements/skyframe-rust-idle-linux-2026-10-19.md) records the vanilla/default/tiered results and promotion decision.

The published October 19 measurements predate the rules_rs build integration
and used Cargo release builds with Rust 1.99.1. They remain results for those
recorded binaries; a rules_rs build can use different compiler/linker flags and
must be measured separately before making performance claims about it.
