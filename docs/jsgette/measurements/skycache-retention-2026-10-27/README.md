# Advisory retention removes synchronous chunk durability

27 October 2026; source base f94779009f. Continues the
[digest and cold-start investigation](../skycache-digests-cold-2026-10-26/README.md).

**Cold remote median moves from 30.388 s to 29.139 s (4.1% lower in this
experiment).** The matched cold local median is 23.718 s. The retained-store diagnostic
drops from 15.633 to 0.247 accumulated worker-seconds. All 637 tests,
15 primary measurements and two separate diagnostics pass their required checks.

## What changed

[RemoteChunkCache.store](../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteChunkCache.java)
still checks the caller's complete regular file and declared length, checks every ancestor
without following symlinks, and installs a non-replacing hardlink atomically. It now leaves
advisory chunk writeback to the operating system. It no longer recursively forces the parent
directory chain or forces the chunk and prefix directory on every cache miss. Initial cache
root/algorithm setup retains its previous synchronization.

This deliberately changes **retained-chunk durability after a system crash**. Recent entries
can disappear or be incomplete and need downloading again. A normal process exit leaves the
kernel's writeback intact. No background Java queue or command-close sync was introduced.

Integrity and graph-publication durability remain separate:

- A later reader privately pins a retained file and verifies its complete CAS SHA-256 and
  length before admitting it. Missing, empty, truncated, corrupt or symbolic entries become
  misses; a network read can repair them. Cached-only reads still do not issue RPCs.
- Active range reads still authenticate requested covering blocks. Decoded record checksums
  and fresh input/dependency validation remain in place.
- Full local publication and authenticated hydration still force their data/directories and
  install manifests only after the complete closure is durable. Those paths were not edited.
- Soft retention limits, pruning, permissions, atomic visibility and non-replacement are
  unchanged. No protocol, format, option, budget or concurrency limit changes.

The new directory helper preserves the old ancestor checks while omitting durability. It
does not cache directory identities or assume a previously observed path is still safe.

## Measurements

| Scenario | Samples | Median s | Mean s | Range s | Mean Bazel CPU-s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold local publication | 3 | 23.718 | 23.821 | 23.596–24.148 | 79.06 |
| Cold remote, previous | 3 | 30.388 | 30.151 | 29.647–30.420 | 105.38 |
| Cold remote, advisory retention | 3 | 29.139 | 28.669 | 27.295–29.574 | 107.16 |
| Warm remote, previous | 3 | 20.213 | 20.301 | 20.163–20.527 | 83.86 |
| Warm remote, advisory retention | 3 | 20.485 | 20.652 | 20.291–21.180 | 83.37 |

Every primary arm has three samples in a rotated order. The observed cold local/remote median
gap changes from 6.669 s to 5.420 s. Warm median changes by
+0.272 s. Warm reads do not enter the changed store path when all chunks
are retained, but this small sample neither establishes a warm improvement nor rules out a
small regression. These measurements do not establish tail latency or statistical significance.

All cold remote commands still download **1,463,742,717 bytes in 1,613 chunks**, reuse the two
remote parents, produce 18 analysis / 11 execution hits and publish the new child. Every
restored child retains the same 103 witness blocks. Warm commands download zero blob bytes.
Six original binary digests and the new publication probe match in every accepted run.

Means below partition the command clock. The middle includes admission, validation,
restoration, analysis/execution and command overhead; it is not pure storage time.

| Cold scenario | Setup s | Middle s | Finalization s | Total s |
| --- | ---: | ---: | ---: | ---: |
| Local | 0.406 | 20.521 | 2.894 | 23.821 |
| Remote, previous | 1.736 | 25.167 | 3.249 | 30.151 |
| Remote, advisory | 1.309 | 24.036 | 3.324 | 28.669 |

## Why this path was selected

Before editing, call-site probes separated recursive durable-directory setup, chunk-file
force and retained-prefix force. They consumed 8.823, 3.977 and 2.593 accumulated
worker-seconds respectively, inside a 15.633-second retained-store total. The trace
identified synchronous durability in this advisory tier as the next optimization target.

The separate before/after diagnostic uses the same fixture and cold-page controls:

| Operation | Before worker-s | After worker-s | Before active union s | After active union s |
| --- | ---: | ---: | ---: | ---: |
| Transport download | 18.712 | 20.749 | 10.698 | 9.432 |
| Ancestor-directory setup | 8.823 | 0.192 | 4.898 | 0.178 |
| Chunk file force | 3.977 | 0.000 | 3.350 | 0.000 |
| Retained-prefix directory force | 2.593 | 0.000 | 2.212 | 0.000 |
| Complete retained store (inclusive) | 15.633 | 0.247 | 8.106 | 0.228 |
| Block hash table | 0.614 | 0.639 | 0.577 | 0.584 |
| Enclosing download (inclusive) | 36.355 | 23.033 | 13.522 | 9.976 |

Both diagnostic runs contain 1,613 stores. The old run contains 1,613 calls to each explicit
file/prefix force; the new run contains none. The new directory-setup span covers the checked
creation walk. **These are nested/concurrent intervals and cannot be added as wall time.**
Active union means at least one operation is active, not isolated critical-path cost.
Diagnostic JFR and probes are excluded from the primary timing statistics.

[Linux fsync documentation](https://man7.org/linux/man-pages/man2/fsync.2.html) explains that
forcing file contents does not itself persist the directory entry; forcing both is a
durability protocol. [Java 25 FileChannel](https://docs.oracle.com/en/java/javase/25/docs/api/java.base/java/nio/channels/FileChannel.html)
likewise specifies storage forcing separately from normal reads/writes. The patch preserves
atomic visibility and delegates crash durability of this cache tier to OS writeback. The
content-addressed verification path makes loss recoverable as a cache miss.

## Correctness and compatibility evidence

**637 cases across 23 targets pass.** Two new tests reject incomplete/symbolic private source
files and post-construction symlink replacement of the cache root or its ancestors. The
existing retained-corruption repair test now also covers missing and zero-length entries,
proves cached-only misses leave the target untouched, verifies exact network repair, and
then reopens the cache without another download. Existing concurrent publication/pruning,
active pin, interruption, eviction, cancellation and publication tests also pass.

These tests inject possible recovery states; they do not simulate a real machine power cut
or validate every filesystem's crash behavior. The stronger full-publication path is
unchanged, and the selected source hashes match the tested build. The 8,821-class dynamic
registry and registry checksum match both the frozen runtime and previous production build.

## Controls and limits

One frozen installation and the identical two-publication seed are used for all arms.
A startup-only agent chooses exact compiled RemoteChunkCache/helper bytes while sharing the
same tested digest/buffer reader and AutoRegistry bytes. Installation identity, namespace,
authentication and freshness guards are unchanged. Per-run agent hashes and mode logs are
checked; class mapping/provenance is archived, and the diagnostic probes confirm the selected
store behavior. Other startup class-load stderr is not reliably forwarded to the client.
No per-read instrumentation is present in primary measurements.

Every build has a fresh JVM and empty Skyframe graph. Source/repository inputs and the
ordinary action/output-cache services remain warm. Private local publication files or
backend CAS files are synced and evicted with POSIX_FADV_DONTNEED. The predeclared acceptance
limit is less than 0.1% resident Linux metadata pages. mincore reports zero in nine of the
eleven measured cold/diagnostic commands; two primary runs retain 6 and 250 pages, respectively.
The maximum resident fraction is **0.06763%** (250 of 369,673 pages). Every
accepted run is retained in the results. Cold remote additionally starts
without retained client chunks. Host/device caches are not flushed. Worker startup, seed
copying, excluded warmups, page eviction and server startup/clean are outside command time;
normal finalization and retention pruning are included.

Source and binaries remain fixed within each cohort. Benchmarks do not overlap builds,
tests or another measured command. The environment is single-host WSL/Linux and loopback;
WAN behavior, other filesystems, CPU pressure and power-loss behavior need separate evaluation.
OS writeback can overlap later work; this is intentional and is not a claim that physical
write traffic disappears. The initial diagnostic harness setup error happened before timing
and is excluded; the successful rerun and every accepted sample are retained.

## Next step

Revisit bounded parallel import with authenticated remote-parent provenance preserved. The
previous full-import prototype caused publication to reread/rehash imported parents; fix that
integration before comparing it with this faster lazy reader. Its old timing results used
the synchronous-retention implementation and cannot be presented as new results here.
Then compare selective/changed target sets and WAN conditions before choosing an eager default.

- [Cold results](cold-comparison.json), [warm results](warm-comparison.json),
  [before diagnostic](diagnostic-before-v2-comparison.json),
  [after diagnostic](diagnostic-after-comparison.json), [stage intervals](trace-comparison.json).
- [Tests/build](build-advisory/result.json), [registry equality](registry-comparison.json),
  [agent provenance](agent/provenance.json), [full provenance](provenance.json),
  [verification](verification.json), [evidence index](evidence-index.json).
- Exact harnesses, compressed results/timelines and helper sources are archived here. They
  depend on the private Datadog fixture, fixed seed, JDK 25, Python gRPC environment and action
  cache. Large binaries/profiles/JFR files remain at the native paths recorded in provenance.
