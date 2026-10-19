# Native genrule action paging: Linux measurements

Date: 2026-10-19. Implementation: [b862a69529](https://github.com/JSGette/bazel/commit/b862a695296c1b50cbb24543610728461d06e966).
Vanilla: `4cfc05f77ba91900a2e3c6ab135861498cb2a0bf`.
Raw numeric summaries: [skyframe-lazy-genrule-linux-2026-10-19.json](skyframe-lazy-genrule-linux-2026-10-19.json), [skyframe-lazy-genrule-linux-2026-10-19.csv](skyframe-lazy-genrule-linux-2026-10-19.csv).

## Result

The native action graph really leaves the JVM heap: **1,024 live native
GenRuleAction objects become 32**, with **992 cold node records**.
On this fixture, live Java heap falls **10.0%**.
The prototype does **not** establish a total-RAM win: post-GC JVM-plus-Rust
PSS rises **24.0%**.
Unchanged builds are effectively equal in this sample; alternating disjoint
working sets are **91.5% slower**, and the source-edit build is
**40.9% slower** than vanilla.

This is an experimental native-genrule implementation. Other action/value types
remain pinned. It is not full Skyframe codec coverage, a byte-bounded heap cache,
or evidence of parity on arbitrary Bazel builds.

## Matched comparison

| Metric | Vanilla | Modified default | Rust-backed paging |
|---|---:|---:|---:|
| Unchanged build, median ms | 168.20 | 168.19 | 167.96 |
| Alternating disjoint sets, median ms | 170.09 | 169.28 | 325.65 |
| Source edit affecting all actions, median ms | 1115.41 | 1116.27 | 1571.65 |
| Live Java heap after full GC, MiB | 50.52 | 50.62 | 45.49 |
| Live GenRuleAction objects | 1024.00 | 1024.00 | 32.00 |
| Post-GC process-tree PSS, MiB | 739.88 | 717.29 | 917.13 |
| Post-GC process-tree RSS, MiB | 743.19 | 720.56 | 922.00 |
| Sampled warm peak process-tree PSS, MiB | 687.55 | 682.28 | 696.18 |

Warm timing uses ten samples per variant across two fresh-server rounds;
disjoint timing uses twenty samples per variant. Edit timing and each heap/PSS
summary use two observations. Medians pool the timing samples; memory values
are the median of the two diagnostic runs. No confidence interval or general
performance-equivalence claim is justified by this small synthetic sample.

The disjoint targets each request 256 of the 512 producer/consumer pairs,
far exceeding the 32-node cache. The initial disjoint-set transition is included
in the median/range data. An unchanged build can avoid requesting the action
values at all; similar unchanged latency does not establish equal paging cost.

The modified-default control isolates ordinary source changes from the enabled
engine. It retains 1,024 actions and has approximately the same unchanged and
disjoint latency as vanilla.

## Reads with and without the guest page cache

Five paired measurements alternate between equally sized disjoint sets. A full
GC precedes **both** conditions so weak object caches cannot substitute for
decoded values. Outside the timed build, the harness either reads the whole
spill file to warm it or calls `posix_fadvise(DONTNEED)` on that file alone.
`mincore` confirms every page resident in the warm condition and zero resident
pages after eviction. No global drop-caches operation is used.

- Warm spill pages: **341.3 ms** median; guest block reads
  are **zero in all five measurements**.
- Evicted spill pages: **422.4 ms** median, an additional
  **23.8%**; the Rust process reads
  **7.33–7.85 MiB**
  from the guest block device per build.
- Both conditions read approximately
  **9.67 MiB**
  through the Rust file API. These logical bytes differ from block bytes because
  of shared chunks, cache hits, and filesystem read-ahead.
- The append file occupies **43.56 MiB** in this run,
  including obsolete records. Warming it also consumes filesystem-cache memory,
  which process RSS/PSS does not include.

The spill file is on WSL ext4 `/dev/sdd`, backed by the Windows C: NVMe SSD
(WDC WD BLACK SDBPNTY-512G-1106). `/proc/PID/io` proves reads passed the Linux
guest page cache. Windows and SSD caches remain uncontrolled: **these are
guest-cache-evicted reads, not a measurement of uncached physical SSD latency**.

Only `lazy-io-final-tiered` is used for this comparison. Earlier exploratory
eviction runs were excluded because they either revisited an unchanged target
or did not explicitly warm both disjoint sets. Main comparison runs have
identical command histories across all variants and exclude the I/O experiment.

## Profiles and memory interpretation

| Variant | Sampled allocation weight, MiB (two runs) | GC pause sum, ms (two runs) |
|---|---:|---:|
| vanilla | 179.4, 124.7 | 129.7, 135.9 |
| default | 219.9, 155.2 | 132.7, 130.1 |
| tiered | 585.1, 474.1 | 147.0, 150.5 |

Each main run includes a separate clean/rebuild diagnostic pass with a Bazel
JSON trace, Bazel memory profile, JFR execution/allocation/GC samples, full-GC
class histogram, and Skyframe count/value dumps. Diagnostic times are excluded
from the latency table. Engine options are preserved for clean and dump commands.

The heap histogram is direct evidence of action eviction: vanilla/default have
1,024 `GenRuleAction` instances; each tiered run has 32. The tiered graph has
2,701 stable handles, 1,677 pinned nodes, 32 resident eligible nodes, and 992 cold
nodes. Configurations, BuildOptions, keys, package/rule values, and other
unsupported values remain resident. Per-pinned-kind retained bytes and external
active-reader retention are not yet attributed.

JFR repeatedly shows substantially higher allocation weight with paging. Byte
arrays dominate the extra sampled allocation, with serialization, array copies,
buffer decoding, and node paging represented in stacks. These observations
support reducing serialization/copy churn and avoiding unnecessary value
decoding as the next performance work. JFR weights are sampled estimates,
not exact allocation totals; GC sums include diagnostic forced collections.
The recordings sample Java, not Rust CPU, and are not whole-process CPU-time
measurements.

Total process PSS after diagnostics increases despite the smaller live Java
heap. JVM committed heap, native memory, loaded codec classes, JIT code and the
sidecar all matter. Sampled warm PSS is separately reported; a 50 ms sampler can
miss brief peaks. Whole-machine RAM savings cannot be claimed because filesystem
cache is additional and only the owned spill file's residency was measured.

## Correctness and recovery evidence

- Full core Skyframe suite: **1,640 case records; 1,634 passed, six skipped**,
  including 177 forced-eviction graph/evaluator cases. The latest invocation
  reused valid cached results for unchanged core code.
- Native codec round trip uses the real rules_rs-built Rust process; it verifies
  arguments, transitive package metadata, nested sets, output artifact identity,
  and configuration identity. Resetting the shared cache forces real chunk reads.
  Two reads of a missing chunk both fail instead of hanging.
- Rust graph integration, existing idle-tier tests, Java client/IPC tests, Rust
  unit tests, Clippy, and rustfmt pass on Linux: nine relevant Bazel test targets.
- SequencedSkyframeExecutorTest and ParallelBuilderTest: 51 case records,
  50 passed and one skipped.
- Real-command matrix: **28 tiered checks**, **23 vanilla checks**.
  All common exit codes and output SHA-256 values match.
  **All 64 action keys match**, including derived-input producer/consumer chains.
  Scenarios include source/glob/BUILD/bzl/flag edits, disjoint targets, errors,
  cycles, query, state dropping, clean, restart, killed sidecar, mode switches,
  and unavailable storage on reset.
- Five additional lifecycle checks pass. SIGINT after a slow action starts
  returns Bazel exit 8 while preserving the server; subsequent builds succeed.
  Tracking off/on uses the regular edgeless graph and a fresh paged generation.
  The interrupted-thread fallback branch itself was not exercised: this runtime
  consumed the interrupt before resetting and created a safe empty generation.

This is relevant Linux coverage, not a claim that every test in the Bazel
repository or every native/Starlark action type was validated.

## Reproduction and environment

WSL Ubuntu Linux, Ryzen 9 9950X3D, 15.44 GiB guest RAM, embedded OpenJDK 27+35;
Java heap maximum 4 GiB; four jobs. Rust 1.99.1 is built with rules_rs 0.1.0.
The frozen vanilla and modified binaries have SHA-256 hashes in the JSON
summary and each run's environment.json. Measured binaries were built before an import-only
source cleanup; the functional implementation matches the recorded commit.

The synthetic fixture has 512 native genrule producer/consumer pairs and
approximately 8 KiB of command padding per action. This emphasizes retained
action payloads; it is not a representative C++/Java/Starlark repository.
The generator reproduces the measured BUILD and input bytes exactly, checked
with `cmp`; hashes are recorded in the JSON.

```sh
python3 tools/skystore/generate_lazy_fixture.py --output=/absolute/new/fixture
python3 tools/skystore/profile_bazel.py   --bazel=/absolute/copied-bazel --workspace=/absolute/new/fixture   --output=/absolute/new/profile --jcmd=/absolute/jdk/bin/jcmd --execute   --build-arg=--config=efcache --build-arg=--bes_backend=   --command-arg=--experimental_skyframe_engine=tiered_genrule   --command-arg=--experimental_skyframe_resident_nodes=32   --command-arg=--experimental_skystore_binary=/absolute/skystore   --alternate-target=//:left --alternate-target=//:right   --edit-file=/absolute/new/fixture/input.txt //:all
```

For vanilla/default omit the three tier command arguments. Run order is
vanilla/default/tiered, then tiered/default/vanilla. Main timing passes have no
JFR or forced GC. Fresh-server timings include startup; repository, OS, and
remote action caches may be warm. EngFlow Cache & Carry remains enabled over
USB at `grpc://10.55.0.1:8080`; BES uploads are disabled during measurements.

For the separate disk-read experiment, omit `--edit-file` and add
`--guest-evict-spill`. Both alternate targets are required for this workload
to force reads; repeated builds of the same target can be no-ops.

## Remaining work

Keep this opt-in. Expand lossless codec audits beyond native genrules; replace
the eligible-node count with measured byte budgets; attribute pinned/active
memory; reduce allocation and paging overhead; compact obsolete records and
key metadata; test larger real repositories and longer incremental histories.
The prototype still has a 64 MiB logical store limit and 1 GiB append-file cap,
serial IPC operations, and ephemeral per-generation storage. These limits and
the performance regressions prevent production promotion.
