# rules_go paging and concurrent SkyCache checkpoint

Measured 2026-10-19; results published 2026-10-20. Implementation branch:
[`jsgette/skyframe-go-paging`](https://github.com/JSGette/bazel/tree/jsgette/skyframe-go-paging), based on
`009e2761ff3e7a2a63167e821dc3bca996c46a4d`.
The implementation checkpoint is [`1af5db48b405`](https://github.com/JSGette/bazel/commit/1af5db48b405a53ad883fe3c6241364a1df0326f).
Reports and plans are maintained on `jsgette/master`; experimental code stays on the implementation branch.

## Result and decision

Implemented opt-in `tiered_go` paging in the Bazel fork and measured a real Datadog Agent build.
Correctness checks pass, but this residency policy is not a memory/performance win. Keep the
default `inmemory` engine. Post-warm live heap increases 54%, and unchanged builds are about
40 times slower. Sharing completed analysis and reducing per-server residency remain separate
workstreams: the concurrent filesystem storage foundation is implemented and tested, while
portable cross-worktree analysis integration is the next milestone.

The Rust component in this fork is a private storage sidecar, not a Rust Skyframe evaluator.
Java still owns evaluation, scheduling and invalidation. Lazy storage is feasible, but this
experiment does not establish a graph whose total RAM scales with jobs/CPUs.

## Workload and controls

- Public `DataDog/datadog-agent` main at `0cde96beb70963d0838119c308b9d0422ee66be0`.
- `//cmd/cluster-agent:cluster-agent`: 5,473 packages, 57,298 configured targets and 6,789 actions.
  The paged graph initially has 318,018 nodes. This is a substantial real rules_go workload.
- rules_go 0.64.2 with the repository's patches; the repository's `tools/bazel` wrapper is used.
- The source-built Bazel regenerated `MODULE.bazel.lock`. Both modes use that resolved snapshot;
  it is preserved with the measurements, and the checkout is restored after the experiment.
- Same copied source-built Bazel binary in both modes; independent fresh output bases;
  `-Xmx6g`, `--jobs=4`, `--local_resources=memory=4096`; paging limit 256 eligible nodes.
- WSL2 Linux 6.6.87.2, 32 logical CPUs, 15.4 GiB guest RAM and 4 GiB swap. Heavy jobs are serialized
  with a shared measurement lock; other task JVMs are stopped during these runs.
- Graph-cold, **warm repository/action/OS caches**. Initial dependency/action priming completed
  separately. Each measured cold build has 5,704 disk action-cache hits and 1,085 internal actions.
  These timings isolate graph/loading/analysis overhead rather than a clean Go compilation.
- The main comparison runs paging then control, with one cold/edit sequence per engine and three
  warm repetitions. It is a single-host exploratory comparison, not a randomized statistical study.
  Large regressions are decisive here; tiny peak-memory differences should not be overinterpreted.
- Durations use Python's monotonic clock. RSS/PSS sample the JVM, launcher and descendants including
  Rust every 100 ms. Full-GC class histograms measure live Java heap separately.

## Timing

| Scenario | In-memory | Paged Go | Ratio |
|---|---:|---:|---:|
| Cold graph build | 22.193 s | 68.428 s | 3.1x |
| Warm median (three runs) | 0.598 s | 24.019 s | 40.2x |
| Build after full GC | 0.559 s | 69.593 s | 124.4x |
| Source edit | 0.704 s | 58.140 s | 82.6x |
| Source revert | 0.749 s | 91.523 s | 122.2x |
| Starlark edit | 0.861 s | 76.123 s | 88.4x |
| Starlark revert | 0.864 s | 67.367 s | 78.0x |

Warm samples: control 0.629 / 0.555 / 0.598 s; paging 20.853 / 24.019 / 25.583 s.
Warmup is excluded from the median (control 1.662 s; paging 35.802 s).

## Memory and paging behavior

| Metric | In-memory | Paged Go | Change |
|---|---:|---:|---:|
| Live heap after warm runs | 248.4 MiB | 381.4 MiB | +53.6% |
| Live heap after edit/revert matrix | 249.5 MiB | 514.9 MiB | +106.3% |
| Peak sampled process-tree PSS | 4068.4 MiB | 4102.4 MiB | +0.8% |
| Process-tree PSS after final GC | 1949.8 MiB | 2742.7 MiB | +40.7% |

PSS is sampled process memory, not whole-machine RAM. It excludes unmapped filesystem cache and
kernel metadata; WSL host/device cache is uncontrolled. Immediate post-GC PSS also reflects JVM
heap commitment and page-release timing, so it is not synonymous with graph live bytes. The
before-edit post-GC PSS readings were 3,918.2 MiB control and 4,076.9 MiB paging.

At the end of the first paged build, 314,159 nodes are pinned, 256 eligible nodes are resident,
and 3,603 are cold. Thus only about 1.1% of the entire graph is cold despite the small eligible
limit. The graph retains Java handles, metadata, locks, keys and an ID dictionary for all nodes.
The heap histogram contains 318,018 `DiskBackedNodeEntry` objects (14.6 MiB), their metadata
(7.3 MiB), and substantial extra lock, weak-interner, string and array storage. These are class
histogram observations, not a complete retained-size ownership analysis.

By the end of the main matrix, cumulative counters reach 112,612 page-ins, 116,215 evictions,
1,457,455,440 node bytes read, 597,518,200 node bytes written, 289,109 shared-chunk reads and
1,110,527 shared-chunk write requests. The codec pins four configurations, 16 Starlark definitions
and 560 Runfiles identities. Counts do not measure their transitive retained bytes.

Rust's guest-kernel counters report zero physical read bytes and 838,963,200 write bytes over the
matrix. Reads were served from the guest page cache; this is not a cold-SSD throughput test.
Logical graph counters and `/proc` I/O accounting are distinct. The data is consistent with
repeated serialization, hydration and lookup overhead; CPU/ownership profiling is needed before
attributing every cost. Identical-put suppression prevents repeated chunks from expanding the
spill file, but does not eliminate Java encoding or IPC work.

## Correctness and implementation

All nine main artifact comparisons match across engines: cold, three warm builds, full-GC reload,
source edit/revert and Starlark edit/revert. Each semantic edit changes the executable hash; each
revert restores the original. The source edit changes `os.Exit(-1)` to `os.Exit(-2)` in the binary;
the Starlark edit changes its default agent version in `bazel/rules/go/go_binary.bzl`.

An additional independent matrix targets the pageable library `//pkg/version:version`: it edits
an error string in `pkg/version/version.go`, then adds `gc_goopts = ["-N"]` in its BUILD file.
Both edits and both reverts produce matching executable hashes across engines. This checks
library action invalidation and configured-value reconstruction beyond the top-level binary.
Its control runs first to prime edited actions; those timings are correctness diagnostics and
are not included as an unbiased performance comparison above.

The local codec allows native genrules and `go_library` values with exact, non-discovering
`StarlarkAction` actions. Enhanced/pruning actions and unsupported rule families remain pinned.
Focused tests compare command lines, action keys, providers, resources, artifact/configuration
identity, mixed resident/decoded depset equality, shared provider fields, cycles and missing chunks.
The fixture forces actual cold chunk decoding instead of relying on a surviving weak identity.

Real-workload failures found and fixed during development:

1. Add artifact-array support for multi-output compile actions.
2. Share the transitive GoArchive provider DAG instead of duplicating it per configured value.
3. Preserve Runfiles identity inside GoArchiveData to prevent duplicate Go linker packages.
4. Give the disk store its own 1 GiB logical / 4 GiB append limits; preserve the 64 MiB RAM-store cap.
5. Compare and skip identical Rust writes; keep checksum verification and repair damaged entries.
6. Use monotonic serialization latency measurement after WSL's wall clock moved backwards.
7. Reconnect after interrupted/failed IPC rather than retaining a closed client channel.

Earlier failed runs are diagnostic evidence, not successful benchmark results. The complete main
pair is `dd-go-r6` / `dd-control-r1`. All ten selected Bazel test targets passed, including 289
serialization cases, 14 Rust cases, Go/native codec tests, graph/IPC recovery, Clippy and rustfmt.
The strengthened concurrent-store assertions and both codec targets were rerun successfully.
`//src:bazel-dev` builds successfully.

## Concurrent SkyCache implementation started

`ConcurrentFileFingerprintValueStore` implements the existing byte-store API with a compatibility
namespace, SHA-256 key paths and payload verification, bounded records, private staging, and atomic
no-replace hard-link publication followed by directory fsync. It uses direct lookups, so a reader
opened before writers sees later publications. Same-key/same-bytes puts are idempotent; conflicting
bytes cannot replace a completed entry. Missing, partial or corrupt optional cache data is a miss.

Four independent live writer JVMs publish distinct and identical records; an existing reader sees
their writes. A two-process conflicting publication test verifies that the successful writer's
bytes survive. Namespace isolation and corruption/truncation checks pass. The tested environment
is a trusted same-user local POSIX/ext4 filesystem, not a network filesystem or Windows.

This is a storage correctness foundation: one file per entry, blocking I/O, no GC, no production
module activation. **Independent Bazel servers do not yet reuse analysis through this backend.**
Portable values, root rebinding, exact source witnesses, dependency-closure publication and
candidate discovery/validation remain required. The private paging codec's numeric identities
must never be published as cross-server records.

The revised [roadmap](../plans/skycache_local_backend_f13e5f7f.plan.md) prioritizes that integration,
then nearby-commit validation, byte budgets/compact metadata/batched I/O and a future gRPC adapter.
It explicitly replaces the unsafe HEAD-plus-dirty-filename cache key and rejects startup-only
indexes or uncoordinated pack sweeping for concurrent writers. Jobs/CPU limits control admission
and prefetch; global graph consumers and waiting dependency fanout need separate residency work.

## Published data

- [Main measurements and comparison](skyframe-lazy-go-datadog-linux-2026-10-19/benchmark-results.json): command arguments, elapsed times, memory, paging/I/O counters and artifact hashes.
- [Library output checks](skyframe-lazy-go-datadog-linux-2026-10-19/library-output-checks.json): source/BUILD edits and both reverts across engines.
- [Resolved Datadog dependency lockfile](skyframe-lazy-go-datadog-linux-2026-10-19/datadog-MODULE.bazel.lock).
- [Provenance and checksums](skyframe-lazy-go-datadog-linux-2026-10-19/provenance.json).

The earlier [native genrule](skyframe-lazy-genrule-linux-2026-10-19.md),
[idle-tier](skyframe-rust-idle-linux-2026-10-19.md), and
[IPC](skystore-uds-linux-2026-10-19.md) reports are retained for context.

## Reproduction and provenance

Check out the implementation branch before building or invoking the harness:

```sh
git fetch origin
git switch --detach 1af5db48b405a53ad883fe3c6241364a1df0326f
```

The [harness](https://github.com/JSGette/bazel/blob/1af5db48b405a53ad883fe3c6241364a1df0326f/tools/skystore/profile_rules_go.py)
and [prototype build guide](../skystore-prototype.md) belong to that implementation.

Source: `/home/jgett/REPOS/bazel-go-paging`.
Raw measurements: `/home/jgett/REPOS/.work/skyframe-go/work/` under the run names above.
The tracked harness is `tools/skystore/profile_rules_go.py`; it restores temporary edits and
shuts down its own server in `finally`. No `bazel clean` is used.

Bazel SHA-256: `aa933bf576aab42313bec2e478a386eba48b16e4ee2652b8fc642971f0180d9b`.
Rust sidecar SHA-256: `98ecf20c9b83751d33f32598c8cdf2e334f6044bc6c2451e61a7ee6b04931c7c`.
Resolved Datadog lockfile SHA-256: `7f32675fc29a9cb837256a760cfca19dfab2bf040492cfa0c9bb40444b8d9290` (saved as `datadog-MODULE.bazel.lock`).

Build and copy `//src:bazel-dev` and `//tools/skystore:skystore`, then run each mode serially:

```sh
python3 tools/skystore/profile_rules_go.py \
  --bazel=/absolute/copied-bazel --workspace=/absolute/datadog-agent \
  --output=/absolute/new-run-directory --disk-cache=/absolute/shared-action-cache \
  --engine=tiered_go --skystore=/absolute/copied-skystore --resident-nodes=256 \
  --edit-file=cmd/cluster-agent/main.go --source-replace 'os.Exit(-1)' 'os.Exit(-2)' \
  --bzl-edit-file=bazel/rules/go/go_binary.bzl \
  --starlark-replace 'agent_version = None,' 'agent_version = "0.0.0-skyframe-benchmark",' \
  --artifact='*/bin/cmd/cluster-agent/cluster-agent_/cluster-agent' \
  //cmd/cluster-agent:cluster-agent
```

For control, use `--engine=inmemory`, omit `--skystore`, and select another fresh output directory.
Prewarm the source/Starlark variants in the shared action cache for a matched timing comparison.
For the extra library proof use `--edits-only`, the library files/replacements described above,
and separate output directories. Use a disposable checkout with no concurrent source edits.

```sh
bazel test --config=efcache --bes_backend= --jobs=4 --local_resources=memory=4096 \
  //src/test/java/com/google/devtools/build/lib/skyframe/serialization:SerializationTests \
  //src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:ConcurrentFileFingerprintValueStoreTest \
  //src/test/java/com/google/devtools/build/lib/skyframe/tiering:LocalGoValueCodecTest \
  //src/test/java/com/google/devtools/build/lib/skyframe/tiering:LocalGenruleValueCodecTest \
  //src/test/java/com/google/devtools/build/lib/skyframe/tiering:SkystoreGraphTest \
  //tools/skystore:all //src:bazel-dev
```

The local appliance configuration is optional for source builds/tests; workload measurements use
`--config=no-remote-cache` and the explicit local action cache. Portable analysis sharing, broad
codec support, restart persistence, compact shared packs, safe GC and cross-machine gRPC remain
future work. No production enablement or shared-cache speedup is claimed by this checkpoint.
