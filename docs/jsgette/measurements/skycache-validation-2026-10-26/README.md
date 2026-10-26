# Bounded parallel final validation — 26 October 2026

Fresh final validation now uses at most four workers for sets of at least 16,384 witnesses.
Smaller sets remain serial. Each worker performs the existing typed observations, and workers
share the bounded content-digest cache from this one fresh pass. The measured median command
time fell **11.9%, from 26.222 s to 23.096 s**, against the witness-table-reuse implementation
at `350705c8b4`. The post-build interval fell **41.7%, from 6.753 s to 3.940 s**.

These are three observations per binary on the existing Datadog fixture with warm local
storage and a loopback metadata service. They establish a useful local improvement, not a
WAN, tail-latency or production forecast. All 626 test cases across 23 selected targets passed.

## Why this path

The preceding investigation identified fresh final input validation as the dominant remaining
publication-preparation work. A replay of the recorded 418,229 global witnesses compares
scheduling choices using the existing private observation methods through method handles.
Every trial also captures the source tree afresh and rejects any mismatch. One serial warmup
is excluded; each variant has three trials in a rotated order within one JVM.

| Replay variant | Median validation | Content bytes hashed |
|---|---:|---:|
| Serial | 4.817 s | 5,048,629,414 |
| Four workers, partitioned by expected digest, independent caches | 1.832 s | 6,850,068,782 |
| Four workers, digest partitioning, shared cache | 2.307 s | 5,048,629,414 |
| Four workers, round-robin partitioning, shared cache | 2.235 s | 5,048,629,414 |

Independent caches made the warm replay faster but hashed 35.7% more bytes. Sharing the cache
preserved the serial read total and still reduced the median by 53.6%. Round-robin partitioning
was selected for its simple scheduling and observed result; the small difference between the
two shared-cache variants does not establish a general scheduling advantage.

The mathematical operation remains a conjunction over the complete witness set:
`unchanged = namespace_matches AND source_matches AND all(fresh_observation(w) == expected(w))`.
Partitioning this conjunction changes scheduling, not the required evidence. The basic cost
model is serial source/large-file work plus parallel observation work divided among workers,
with scheduling and filesystem overhead. That model motivates a bounded experiment; it does
not predict fourfold speedup. No witnesses are omitted and no probabilistic clean decision is
introduced.

The recent research review remains relevant to the preservation boundary.
[Incr, OSDI 2026](https://www.usenix.org/conference/osdi26/presentation/xie-yizheng) combines
dependency/effect tracking with optimizations of incrementalization overhead.
[DepReduce, ISSTA October 2026](https://xuhongxu.com/issta26-depreduce.pdf) optimizes declared
artifact dependencies under a defined objective and explicitly distinguishes those dependencies
from file-access observations. Our inference is to optimize evaluation of the existing complete
typed evidence first. Neither paper measures this patch or authorizes removing SkyCache witnesses.

## Implementation and preservation

`LocalInputSnapshot.isUnchanged()` retains the fresh namespace and source capture, then splits
a large witness set into at most `min(4, availableProcessors)` batches. There is one task per
batch. A completion queue detects a mismatch or exception, cancels outstanding tasks, and the
executor is joined on every exit before the caller can release publication leases. Interruption
is preserved. Files, directory membership, absent paths, permissions and symlinks retain their
existing fingerprint formats.

Each worker owns its hashing counters and scratch use. Its `LocalContentHasher` shares only
the digest map allocated for the current final-validation pass, including digests from that
pass's source capture. The existing 1 MiB minimum file size and 256-entry bound are unchanged.
Large versioned reads serialize on the shared map, including a miss, so aliases cannot duplicate
the same in-flight read. Small-file reads remain independent. Each reuse still requires fresh
device/inode/change-time/mtime/size observations and a matching version after use. Providers
without the required identity/change-time attributes continue to read content again. No digest
cache crosses final-validation invocations.

The replay's unchanged byte count is specific to this trace. When more than 256 versions compete
for admission, scheduling can change which versions fit. The worker cap and 16,384-witness
threshold are conservative defaults, not a measured optimum for every CPU or storage device.
There are additional thread and partition-array costs; peak memory and total CPU were not
measured. Admission validation, authenticated remote reads, publication formats, parent retention
and remote upload behavior are unchanged.

## Controlled command comparison

The baseline binary is the preserved witness-reuse build; the candidate is a freshly built
binary. Each measured command starts a fresh JVM with empty graph/action state and a private
cache containing retained metadata chunks. Binary-specific primers are stabilized to two
publications. The recorded repository environment and tool bindings are pinned. Each command
builds the six original binaries and a fresh tiny genrule, forcing a new publication on both sides.
Priming, stabilization, startup/cleaning and retained-chunk warmups are excluded.

| Metric, median of three per binary | Baseline | Candidate | Reduction |
|---|---:|---:|---:|
| Complete timed command | 26.222 s | 23.096 s | 11.9% |
| Build-complete log arrival to command exit | 6.753 s | 3.940 s | 41.7% |
| Build-complete arrival to first upload | 6.293 s | 3.460 s | 45.0% |
| First upload through catalog commit | 0.421 s | 0.447 s | — |

The ABBAAB sequence pairs baseline/candidate runs as `(r1,r2)`, `(r4,r3)` and `(r5,r6)`.
Post-build savings were **3.216 s, 2.813 s and 2.611 s**; command savings were **4.859 s,
3.916 s and 2.827 s**. The post-build interval includes other publication work, so it is not an
exact timer for `isUnchanged()`. Differences before build completion and independent primer
pack layouts limit attribution of the whole-command result. RPC starts, command starts and
log arrivals now share the same Python monotonic clock; epoch timestamps are not used to
compute phase intervals.

All six measured commands:

- restored 18 configured-target and 11 execution entries;
- matched all six original binary hashes and the probe output hash;
- made new writes and successfully committed a publication;
- downloaded zero metadata blob bytes;
- reused 103 witness blocks totaling 10,075,663 bytes.

All six child witness tables were byte-identical. Source/worktree states and frozen binaries
were unchanged during measurement. There were no failed or excluded measured runs in this
cohort. The first baseline warmup took 58.27 s with cold local storage and is excluded along
with all other preparation. This study retains the prior cohort's established fixture and
does not combine timings across cohorts.

## Verification and evidence

New tests cover concurrent aliases, cache capacity, restored-mtime changes, fresh cache epochs,
typed observations, source/conflict rejection, I/O propagation, cancellation and worker joining.
The selected local/remote/worker and serialization gates passed **626 cases in 23 targets**;
this is not the full Bazel suite.

- [Comparison and paired differences](comparison.json)
- [Commands, output hashes and runtime counters](runs.json)
- [Complete run records including individual RPC timings and build metrics](runs.full.json.gz)
- [Build/test results](build-result.json), [build command](build.command.json), [test XML](tests/)
- [Witness equality](witness-verification.json)
- [Benchmark harness](benchmark.py), [analysis script](analyze.py)
- [Replay results and interpretation](replay/README.md)
- [Source and evidence hashes](provenance.json), [tested implementation patch](implementation.patch.gz)

The remaining pre-build-complete median is about 19.28 s. The next useful investigation is
profiling authenticated retained-chunk reads and restoration under this binary. Further
validation parallelism should require evidence that it helps under resource pressure; upload
batching remains a smaller opportunity in this loopback publication workload.
