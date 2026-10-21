# Concurrent local SkyCache: implementation handoff

The implementation is an experimental filesystem analysis cache for independent Bazel servers
working in linked Git worktrees of one repository. Both servers may read and publish concurrently.
Each retains its own full Skyframe graph, output base and action execution. Integration uses Bazel's
existing analysis serialization and service interfaces; the Skyframe evaluator implementation is
unchanged. There is no Rust evaluator, graph paging or graph-size limit derived from `--jobs`.

Implementation: [`jsgette/skycache-concurrent`](https://github.com/JSGette/bazel/tree/jsgette/skycache-concurrent).
The measured binary was built from `802d66bc21bd706a20dc5476791d305020363303`; the later
`34688d19d6244ef3ccf633ebcca6e1376f12c87b` commit changes only the validation harness to enforce
the user-requested 16-build limit. Documentation and results are on
`jsgette/master`. The [usage guide](skycache-local-usage.md) provides flags and operating details;
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

## Validation and measured performance

The current binary passes 280 Java cases across 18 targets. Three retained-validation routing
regressions fail before the fix. Its separate 34-build concurrent membership matrix covers edits,
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

| Storage cohort | Phase | Pair/command wall time (s) | Peak combined PSS (GiB) | Hits | Result |
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

All large builds use pinned Datadog revision `0cde96beb70963d0838119c308b9d0422ee66be0`, target
`//cmd/cluster-agent:cluster-agent`, and jobs=2/action memory 3072 MiB/Xmx4g per server. Remote action
caching is disabled; edited builds and controls also disable disk action caching. Standalone
source builds/tests use available CPUs with memory headroom and Cache&Carry. Every server keeps
its full graph; this work does not claim a reduction in aggregate graph RAM.

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

## Suggested next work

Use this branch as an opt-in local prototype and measure the intended workload on native SSD-backed
ext4. Repeat matched cold/warm/restarted builds with two and more worktrees, reporting full command
time, analysis time, memory and physical I/O. If validation still dominates, profile that phase
before changing its correctness rules. Broaden rule/platform and long-running lifecycle coverage
before treating this as a general replacement for recomputation. Keep remote transport separate
until local benefit and operating behavior are understood.

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
