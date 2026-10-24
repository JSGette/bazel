# Concurrent local SkyCache: implementation handoff

The proposed [v2 implementation design](skycache-v2-design-2026-10-24.md) builds on this research
with Merkle input certificates, parallel validation, selective publication, batched storage,
and configurable idle retention for analysis and action-cache metadata.
It is a design proposal; the implementation and measurements below remain the current baseline.
The local [idle-retention prototype](skycache-idle-retention-prototype-2026-10-24.md) compiles
and passes four focused cases, but its three-attempt runtime experiment produced no valid A/B result.

The implementation is an experimental filesystem analysis cache for independent Bazel servers
working in linked Git worktrees of one repository. Both servers may read and publish concurrently.
Each retains its own full Skyframe graph, output base and action execution. Integration uses Bazel's
existing analysis serialization and service interfaces; the Skyframe evaluator implementation is
unchanged. There is no Rust evaluator, graph paging or graph-size limit derived from `--jobs`.

Implementation: [`jsgette/skycache-concurrent`](https://github.com/JSGette/bazel/tree/jsgette/skycache-concurrent).
The latest measured implementation binary is built from `6f451b2dc6f783dda879ff3c7ed3fc8146d0f410`.
Earlier capped validation and NVMe results below retain their original binary provenance.
Documentation and results are on `jsgette/master`. The [usage guide](skycache-local-usage.md) provides flags and operating details;
the [development protocol](measurements/skycache-concurrent-development-2026-10-20.md) preserves
individual measurements, failures, fixes and rejected alternatives.

## What is implemented

Commands write private immutable packed records. Successful commands freshly validate inputs and
publish a checksummed manifest after their data is durable. Readers admit compatible publications;
identical records can be reused without being written again. There is no exclusive designated
writer or five-minute snapshot timer. Publications share eligible analysis values, rather than a
single mutable graph or action-output cache.

Compatibility includes Bazel installation identity, configuration, Starlark semantics, stamp mode,
project identity and relevant environments. Linked worktrees share a Git common-directory identity.
Typed workspace/output paths relocate to the reader; arbitrary strings are left alone. Explicit
codec support covers the provider/depset, runfiles, aspect and repository-mapping cases exercised
by the tests.

Per-entry dependency evidence allows reuse across differing source snapshots. Content, file types,
modes, complete symlink chains, directory membership, absent paths, external bindings and relevant
environments are checked. Retained imported values are validated against their original evidence.
Missing, unsupported, inconsistent or corrupt evidence causes conservative misses or abandonment
of the optional publication. Git commit identity and modification timestamps do not substitute
for source-content validation.

Input digests are memoized only within one validation/capture pass, with guarded Unix file identity.
Dependency validation and candidate admission have bounded invocation-local memoization. Immutable
duplicate-write reuse, packed storage and inherited publication evidence reduce repeated work.
Local retained-value validation runs interruptibly in the command's invalidation stage; its full
cost remains part of build time. The remote dispatcher's deadlines are unchanged.

OS-held leases protect live readers/writers and their transitive publication dependencies. A
lease-aware collector coordinates discovery/deletion, removes child manifests before parents and
preserves unknown layouts. Default collection limits are a soft 5 GiB and seven days, attempted
after 30 idle seconds. Process death releases leases. JSON reports expose cache hits, shared reads,
writes, deduplication, validation, publication and collection behavior.

## Architecture experiments (2026-10-21)

Four timeboxed experiments are complete; none proves a reliable full-build speedup.
The [experiment report](skycache-architecture-experiments-2026-10-21.md) selects compact input
certificates as the primary mechanism lead (541 to 129 ms validation, 80% less allocation), with
selective publication a secondary size/memory lead. The tested synchronous frontier loses latency;
scan avoidance and bulk loading are not selected. All 15 planned full builds passed, but E1's setup
binding audit and E3's memory sampler failed and remain explicitly qualified. Final control drift is
10.9%; E3 is not a full-build result. Code is isolated on the pushed experiment branch; implementation
remains unchanged at `6f451b2dc6`.

## Latest optimization batch

Metadata/I/O batching and precompiled dependency-key parsing are implemented and pushed.
Matched NVMe controls take 19.388/19.307 s; the fresh writer takes 33.766 s and independent reader
25.285 s, still 30.7% slower than the control mean. All measured builds reuse 6,788 local actions
with zero digest mismatches and produce identical outputs. Setup falls by about 0.44 s versus the
prior reader observation, but historical whole-build improvement is confounded by preparation and
action-cache state. See the [batch report](skycache-optimization-batch-2026-10-21.md) for full results,
phase-shifting evidence, tests and limits. Net benefit remains unproven.

## Validation and measured performance

The current binary passes 284 Java cases across 18 targets and the six-build optimization batch
below. Earlier evidence includes three retained-validation routing regressions that fail before
their fix. The previous binary's separate 34-build concurrent membership matrix covers edits,
additions, unchanged-content renames, deletions and reverts against independent cache-off controls.
Focused recovery cases cover conflicting publishers, missing parents, corruption, process death,
reader pins and collection races. Source builds/tests used Cache&Carry.

Two focused optimizations have measured benefits in warm-filesystem microbenchmarks. Checking
inherited publication inputs once reduced logical reads by about 67% and median latency from
405.392 to 345.917 ms. Memoizing large file digests within one freshly validated pass reduced alias
benchmark reads by about 80% and median latency from 137.512 to 30.333 ms. Each comparison used two
warmups and five measured rounds with zero physical reads. These are scoped improvements, not
whole-build speedups; the development protocol retains every round and its regression tests.

**16 attempts: 14 passed and two timed out.** The final six correctness builds passed after
repository inputs moved to NVMe while retaining their logical paths. Both edited servers had
real shared analysis reads and published successfully; each distinct edited executable matched
its independent cache-off control. Warm reverts matched the original baseline. Raw BEP timestamps
confirm actual overlap in every successful paired phase. Sources were restored and servers stopped.

Pairs show separate A/B analysis-hit counts; single-server phases show one count.

| Storage cohort | Phase | Pair/command wall time (s) | Peak combined PSS (GiB) | Analysis hits (A/B) | Result |
|---|---|---:|---:|---|---|
| HDD | `control-base` | 316.74 | 3.847 | 0/0 | passed |
| HDD | `restarted-control-base` | 23.98 | 5.766 | 0/0 | passed |
| HDD | `single-base-writer` | 921.69 | 3.504 | 0 | passed |
| HDD | `first-independent-reader` | 568.37 | 3.141 | 4501 | passed |
| HDD | `warm-base` | 157.33 | 5.811 | 0/0 | passed |
| HDD | `first-cross-revision-readers` | 1800.14 | 11.098 | 6206/6206 | timed out |
| NVMe-inputs | `final-concurrent-edited-publication` | 1398.16 | 7.197 | 6206/6206 | passed |
| NVMe-inputs | `final-warm-reverts` | 198.77 | 7.678 | 3/3 | passed |
| NVMe-inputs | `final-independent-edited-controls` | 1182.38 | 8.028 | 0/0 | passed |

The two HDD failures remain failures: execution completed, but final input hashing exceeded
the 1,800-second deadline and publication did not complete. Native repository sharing restoration
alone had not resolved this. The final storage change does not modify product code or validation.

No matched speedup is claimed: earlier failed commands warmed local outputs, storage differs
between cohorts and these are single ordered observations. Process memory excludes page cache
and host VM overhead. The large post-GC rebuild pair was omitted to respect the cap; focused
collector tests and earlier lifecycle evidence are separate. Only repository inputs moved to
NVMe; output bases and SkyCache data stayed on the SATA HDD. Both use WSL ext4 loop images
backed by Windows files, so this does not characterize native SSD-backed ext4.
Source builds/tests used Cache&Carry.

The current unchanged warm reader retained 4,501 of 4,506 graph keys, rejected no dependency
roots and downloaded no configured targets. Five additional keys without direct lookup evidence
were conservatively invalidated. Source and counters indicate configuration forwarding; exact
destination mappings were not heap-inspected. Its command took 154.002 seconds, including about 129 seconds of
validation. Earlier restarted cache-enabled measurements were slower and used more process
memory than cache-off controls. The input-storage intervention is not a product-code optimization.

The 16-attempt run used pinned Datadog revision `0cde96beb70963d0838119c308b9d0422ee66be0`, target
`//cmd/cluster-agent:cluster-agent`, and jobs=2/action memory 3072 MiB/Xmx4g per server. Remote action
caching is disabled; edited builds and controls also disable disk action caching. Standalone
source builds/tests use available CPUs with memory headroom and Cache&Carry. Every server keeps
its full graph; this work does not claim a reduction in aggregate graph RAM.

## Full-NVMe follow-up: two additional builds

After the 16-attempt handoff, the user authorized two additional builds and higher resources.
Both ran on **E: WD Blue SN570 2TB NVMe**, entirely inside Ubuntu's native ext4 VHDX, with
**32 jobs, Xmx6g and 6144 MiB action memory**, sequentially. Both servers started graph-cold;
existing local action outputs and the disk action cache were warm. The binary was unchanged.

| Metric | Fresh SkyCache publication | Independent reader |
|---|---:|---:|
| Full command | 33.901 s | 27.762 s |
| Analysis phase | 8.211 s | 7.451 s |
| Peak process-tree PSS | 4.780 GiB | 5.139 GiB |
| Analysis hits | 0 | 4,501 |
| Newly configured targets | 57,369 | 14 |

Both builds succeeded and exactly matched the baseline executable. The reader deduplicated all
puts, so no new publication was needed. Both servers were stopped. The original harness's overly
strict requirement for a new reader publication was rejected by a separate offline evidence
audit; the original report remains preserved. No extra build or product change was made.

The workload now completes in tens of seconds. Compared with the earlier HDD runs, storage,
filesystem layering, resource limits and cache state changed together. This does not isolate an
SSD speedup, and writer-versus-reader timings do not establish benefit over cache-off Bazel.
No new concurrent, warm-in-memory invalidation or post-GC measurement was made. See
[Step 58](measurements/skycache-concurrent-development-2026-10-20.md#step-58--two-full-nvme-performance-observations-2026-10-21)
and the [curated measurements](measurements/skycache-concurrent-2026-10-20/nvme-two-builds-2026-10-21.json).

## NVMe cache-off comparison: two additional baselines

Two later builds used the **identical fork binary with SkyCache disabled**, providing a feature-off
baseline rather than a separate upstream release. Storage, resources, revision and target match
Step 58. Each server started with an empty graph; both commands succeeded and outputs matched.

| Metric | Cache off A | Cache off B | SkyCache reader | SkyCache writer |
|---|---:|---:|---:|---:|
| Full command | 20.471 s | 18.829 s | 27.762 s | 33.901 s |
| Analysis phase | 5.505 s | 4.972 s | 7.451 s | 8.211 s |
| Peak process-tree PSS | 3.823 GiB | 2.518 GiB | 5.139 GiB | 4.780 GiB |

Against the **19.650-second baseline mean**, the reader was **41.3% slower** and the writer
**72.5% slower** in these observations. Reader analysis also exceeded baseline mean analysis
by 42.2%, despite 4,501 configured-target hits. Its cache setup before analysis took 4.161 seconds;
this cost was negligible with the feature disabled. No speed or peak-memory benefit is demonstrated
for this workload on NVMe.

Initial action-output states differ: controls had 6,788 local hits each, while the reader had
4,538 local hits, 1,197 disk hits and 1,054 internal actions. Runs were ordered, with no filesystem
cache reset or repeated SkyCache sample. The observed percentages are not an isolated causal
estimate of SkyCache overhead. See [Step 59](measurements/skycache-concurrent-development-2026-10-20.md#step-59--two-cache-off-baselines-on-the-same-nvme-2026-10-21)
and the [curated comparison](measurements/skycache-concurrent-2026-10-20/nvme-cache-off-comparison-2026-10-21.json).

## Shortcomings and missing work

- **End-to-end benefit remains workload dependent.** Input capture, content hashing, repository
  validation, serialization and deserialization can cost more than recomputing analysis. Cold
  publication can be expensive. The measured result must be assessed against matched cache-off
  controls; microbenchmark improvements do not establish a whole-build speedup.
- **RAM is not shared or bounded by action parallelism.** Every server retains its graph; imported
  values, indexes and validation state can increase memory. Encoded metadata admission limits are
  not hard limits on decoded objects, total heap or system memory.
- **The tested compatibility scope is narrow.** Same-user Linux/WSL ext4, linked worktrees and
  compatible inputs are covered. Separate clones, arbitrary filesystems, all rule/value families,
  all host platforms and untrusted cache writers are not established. Checksums detect corruption;
  they do not provide an authenticated multi-tenant service.
- **External repositories are conservative reuse boundaries.** Whole binding evidence can reject
  otherwise reusable analysis. Native repository sharing should actually be in effect, not merely
  configured. Existing markers can preserve private repository materializations.
- **Some extra keys are conservatively invalidated.** Configuration forwarding can retain a deserialized
  value under a key with no direct lookup evidence. Keeping such keys would require explicit
  original-evidence alias tracking; accepting unknown evidence is not an acceptable shortcut.
- **Storage limits are soft.** Active leases and dependency closures may exceed configured limits;
  unknown/legacy data is preserved. Candidate and metadata limits can reduce hit rates as history
  grows. Larger histories and longer-running multi-server workloads need characterization.
- **Input validation is not an atomic filesystem snapshot.** It retains Bazel's expectation that
  build inputs remain stable during a command. It does not defend against privileged metadata
  forgery or every possible concurrent input ABA race.
- **gRPC is not implemented.** Existing value-store/service interfaces provide the extension point.
  Portable project/toolchain identities, remote discovery, lease semantics, batching, cancellation,
  authentication and failure/retry behavior still need a design and implementation.
- **Validation remains incomplete beyond the selected scope.** The 16-attempt cap retained two
  timeout failures and omitted large post-collection rebuilds and repeated restart phases. Current
  focused collector tests and earlier real-server lifecycle results do not substitute for every
  omitted large-workload check.

## Performance diagnosis

Read-only profile/source review identifies a 4.16-second cache setup stage containing an
unconditional whole-workspace content scan, a validator-wide lock over 163,378 dependency records,
and hundreds of thousands of tiny synchronous pack operations. Memoization is effective but does
not avoid that first proof. Writer final validation repeats the source scan. Extra action digest
mismatches confound the full-build comparison. An earlier idle-retention claim was retracted after
tracing the superclass, which already clears command state. Precise inner-method and heap
attribution remain unmeasured. See the
[ranked diagnosis and optimization order](skycache-performance-diagnosis-2026-10-21.md).

## Suggested next work

Treat this as a concurrency/correctness prototype. The
[architecture reassessment](skycache-architecture-reassessment-2026-10-21.md) identifies broad
freshness validation and fine-grained import as the main design mismatch. Retain the storage work;
first prove a cheaper input-identity and scoped-import path, with selective publication, before
further broad tuning. This is a recommendation, not an implemented redesign. Keep the graph in RAM
and allow all workers to publish. Net performance benefit and concurrent throughput remain unproven.

## Preserved evidence and cleanup

The private raw-evidence archive preserves 2,534 files, including failed
attempts and the successful final checks. Every archived file was read back against its SHA-256.
Raw BEP/JVM/environment data remains private. Archive SHA-256: `5fb50a263da2800933bc04b880842d665ea578455d8034580f3e8e2b383d1df7`.

The four owned final Datadog worktrees were unregistered. The temporary 64 GiB C image and 128 GiB D
image were normally unmounted, detached and removed. The original native fixtures and user
repositories were preserved. The earlier full C image had already been removed. Task helpers
were verified stopped after cleanup.

This closes the requested local prototype handoff within the tested scope. It does not establish
production readiness, a general performance improvement or a passing 16/16 run. The validation
omissions and product limitations above remain explicit follow-up work.


## WSL continuation, 2026-10-24

The cloud v2 handoff was restored and validated locally on branch
`jsgette/skycache-v2-local`. All 190 local SkyCache cases and 63 fixture builds
passed, including imported-only idle eviction, same-mtime content mutation,
concurrent worktrees and old/new binary sharing. Synthetic median recovery was
0.228 s with SkyCache versus 0.365 s without it. Eight-thread validator medians
were about 2.1× faster than the serial baseline; packed durable-write scaling was
small on the C:-backed ext4 loop volume. These do not establish a representative
real-build speedup.

See the [complete WSL protocol](skycache-v2-wsl-continuation-2026-10-24.md) for
commands, source/binary hashes, storage provenance, every setup failure, raw
samples and outstanding work. The original cloud handoff is archived separately
with its incomplete runs intact.
