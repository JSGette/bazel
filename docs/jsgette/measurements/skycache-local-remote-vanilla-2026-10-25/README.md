# Local, remote and upstream comparison — 2026-10-25

Local SkyCache is close to upstream Bazel on this synthetic workload. Remote SkyCache
is **14–56% slower than local** and **16–60% slower than upstream**, despite correct reuse.
The workload has cheap analysis and deliberately large serialized command strings;
these results establish cache overhead, not performance on a large production build.

## Complete command time

Medians of three measured builds per cell; server read paths explicitly warmed:

| Packages read / input | Vanilla Bazel | Local SkyCache | Remote SkyCache | Remote vs local |
| --- | ---: | ---: | ---: | ---: |
| 16 / unchanged | 7.351 s | 7.461 s | 8.499 s | +13.9% |
| 16 / one subtree changed | 7.380 s | 7.458 s | 8.540 s | +14.5% |
| 512 / unchanged | 8.136 s | 8.091 s | 12.209 s | +50.9% |
| 512 / one subtree changed | 8.193 s | 8.427 s | 13.148 s | +56.0% |

[All samples, ranges and ratios](summary.json) are preserved. Three samples do not establish
small differences statistically; in particular, this does not establish a local-cache speedup.
Times include command startup, repository resolution, analysis, action execution and publication.
Cache priming, copying local metadata, backend startup and backend warm-up are outside the timer.

## Profile evidence

The common `compute main repo mapping` span takes about 7.1 seconds in every group.
Each profile contains 283 BCR registry `download file` spans. Those spans show registry
access; they do not measure exclusive network time or external network bytes. Consequently,
whole-command percentages mask the larger relative cost in the phase SkyCache affects.

Median `runAnalysisAndExecutionPhase` durations:

| Packages read / input | Vanilla | Local | Remote |
| --- | ---: | ---: | ---: |
| 16 / unchanged | 0.137 s | 0.136 s | 0.936 s |
| 16 / one subtree changed | 0.129 s | 0.142 s | 1.037 s |
| 512 / unchanged | 0.859 s | 0.841 s | 4.885 s |
| 512 / one subtree changed | 0.890 s | 0.974 s | 5.564 s |

Skymeld combines loading, analysis and execution. This is **not exclusive analysis time**,
and it excludes separately profiled `beforeCommand` initialization and potentially later
publication work. Do not subtract independently calculated medians or add overlapping spans.
For the wide reader, the combined phase reaches its first recorded genrule after
3.762 / 4.833 seconds remotely (unchanged / changed), versus 0.161 / 0.273 seconds locally.
Most of the extra remote time is therefore before the first recorded genrule. This locates
the overhead but does not apportion it among RPCs, local chunk reads, hashing and deserialization.

[Warm profiles](warm-profiles.json) cover all 36 measured commands. The saved profile and
log archive preserves the original traces, and the parser verifies output base, invocation
time window and profile path/build ID. No log invocation IDs were available in these logs.
[Cold profiles](cold-profiles.json) cover 30/36 commands because default retention removed
the earliest unchanged sample in each arm; the cold wall-time data still has all 36 samples.

## Transfers and correctness

These are median TCP wire bytes through the counting proxy, including gRPC framing:

| Packages read / input | Download | Upload |
| --- | ---: | ---: |
| 16 / unchanged | 16,697,846 B | 15,619 B |
| 16 / one subtree changed | 16,698,811 B | 67,123 B |
| 512 / unchanged | 19,408,349 B | 17,285 B |
| 512 / one subtree changed | 19,409,433 B | 302,092 B |

Local and vanilla have zero SkyCache wire traffic. The 16-package reader still downloads
about 16.7 MB from a roughly 19.4 MB primer. The existing fixed 1 MiB transfer units have
substantial read amplification for this access pattern. Small changed-subtree uploads
show that remote parent reuse is working; a small upload alone does not make reads cheap.

Both cache modes hit 17 / 16 configured targets for the narrow reader and 513 / 512 for
the wide reader, unchanged / changed respectively. Vanilla has zero SkyCache hits.
Every output is compared with its exact expected bytes; the changed BUILD command must
change the selected leaf's output. All **72 measured outputs** across cold and warmed
matrices, plus 12 excluded client warm-up outputs, passed. No cache failure, read-budget
skip, scan-limit hit, tool-tree change or cleanup failure was observed. See
[verification](verification.json) and the four raw benchmark JSON files.

## Controls and limitations

- Local and remote use the same integrated client built at
  `6931630c83d9222049e30bf7c50190ae810519f5`, including the parallel local optimizations and
  cached-record/remote-parent reuse. Vanilla is a separate clean upstream build at
  `a5c50fcb65841b505ec72840d83381a704e70afa`.
- Both binaries use the same explicit `bazel_tools` tree: 575 files, 184,637,261 bytes,
  inventory SHA-256 `7eb749015850de7331fafe547e0d7176b5045c8523dcf7a1d1fe9186dfe46330`.
  This avoids different embedded-tool bindings invalidating the comparison.
- The existing Java worker serves REAPI CAS/ByteStream plus the custom catalog.
  The warm-run provenance hashes every classpath JAR before and after, not only its shell
  launcher. The client build/source records come from the parallel integration session;
  its 562-case test run predates this benchmark and was not rerun here.
- Each matrix primes 512 packages once. Local reads a fresh copy of that exact primer's
  objects/publications; remote reads the same primer through a fresh backend catalog copy
  and starts with empty client metadata/chunk scratch. Priming and copying are excluded.
- Every measured command follows `bazel clean`, resetting the graph and action state.
  Client JVMs remain warm after one excluded build per arm. Ordinary remote/disk action
  caches are disabled, and actions execute in all arms. This models reuse after graph loss,
  not repeated no-op builds in an already populated Bazel graph.
- Mode order rotates over three repetitions. A shared `flock` serializes these matrices
  against the parallel session's builds/measurements. Machine: WSL2 Ubuntu, Ryzen 9 9950X3D,
  32 logical CPUs and approximately 15.4 GiB WSL memory; jobs=4, client heap cap=2 GiB.
  There is no CPU pinning or frequency control.
- The server is restarted for every remote invocation to keep discovery frozen. The main
  comparison runs three untimed rounds of discovery, SHA-verified reads of all primer blobs,
  and exact original idempotent commits. It checks unchanged candidates and receipt bytes.
  This warms read paths; **new-publication validation/writes are not warmed**. It is not a
  fully steady-state, long-lived production-server benchmark. Warm-up uses the direct
  backend port and is excluded from proxy counters. Its dedicated smoke test passed.
- Cold-server matrices remain as separate evidence. Their same qualitative result guards
  against making the conclusion depend on the warm-up. Cross-matrix timing differences
  are not a controlled estimate of warm-up savings: the fixtures are independently primed,
  and runtime/packing variation remains.
- This is loopback without latency/bandwidth shaping; repository resolution can access BCR.
  It measures neither WAN behavior nor peak memory. Large command comments inflate serialized
  metadata without expensive analysis. The existing six-target Datadog publication is about
  1.5 GB, beyond the remote MVP's 256 MiB logical closure bound, so its local-only timings
  are not mixed into this three-way comparison.

The next performance experiment should isolate chunk-cache disk reads/hashing and RPC
overhead, then compare smaller fixed chunks and bounded batching. An analysis-heavy real
workload within the current bound, followed by larger-closure support, is needed before
claiming that remote SkyCache makes production builds faster. CDC remains a separate
deduplication experiment; this comparison supplies no new evidence in its favor.

## Reproduction and evidence

Build the two recorded revisions, use a common embedded-tools directory, and install the
optional warm-up dependency in a task-local environment:

```sh
python3 -m venv /absolute/path/rpc-venv
/absolute/path/rpc-venv/bin/python -m pip install grpcio==1.76.0
flock /path/to/shared-measurement.lock python3 tools/skycache/remote_read_benchmark.py \
  --vanilla-bazel /absolute/path/upstream-bazel \
  --new-bazel /absolute/path/integrated-skycache-bazel \
  --worker /absolute/path/worker \
  --tools-dir /absolute/path/common/embedded_tools \
  --server-warmup-python /absolute/path/rpc-venv/bin/python \
  --root /absolute/path/new-result-directory \
  --packages 512 --selected 16 --repetitions 3
```

Repeat with a new root and `--selected 512`. Omit `--server-warmup-python` for the cold-server
control. The helper requires the development server's local catalog receipts and is intended
only for this benchmark. It does not require a generated/forked REAPI proto module.

The exact host-specific runners, current harness/helper snapshots, dependency versions,
binary hashes, classpath inventory and CPU/memory metadata are retained here. The runner's
output directory must be changed for a repeat; it deliberately refuses to overwrite results.
`warm-profiles-and-logs.tar.gz` and `cold-profiles-and-logs.tar.gz` contain per-case results,
logs and available profiles. To recalculate phase metrics from an extracted archive:

```sh
mkdir /absolute/path/extracted-warm
tar -xzf warm-profiles-and-logs.tar.gz -C /absolute/path/extracted-warm
python3 analyze-profiles.py /absolute/path/extracted-warm --output /absolute/path/new-phases.json
sha256sum -c SHA256SUMS
```

Checksums cover the files in this evidence directory. No production implementation or wire
protocol changed in this comparison; changes are benchmark instrumentation and documentation.
