# First implementation slice: evidence and remaining gates

28 October 2026. P00, P01 and P02 have working increments; none of their full
implementation-plan acceptance gates is complete. This change establishes
measurement and correctness infrastructure. It does not establish a speedup,
memory reduction, safe general restoration, or a cache/local computation race.

## Delivered

| Package | Implemented | Still required |
| --- | --- | --- |
| P00 | Paired Linux/WSL harness, schemas, deterministic native fixture, output verification, raw evidence and build provenance | Representative broad/sparse/mutation workloads, action-key comparisons, real idle-release/recovery sequences and a second-operator reproduction |
| P01 | Scalar ownership snapshots at command completion, with cleanup/concurrency tests and actual cache-hit evidence | Exclusive phase-cost attribution, allocations/retained heap, callback/index/scratch ownership, detailed-instrumentation control and a credible overhead comparison |
| P02 | Executable provider identity contract with two passing controls and two precisely classified known failures | Fix or conservatively exclude unsupported identity cases; complete restoration, invalidation, runfiles and mixed-evaluation coverage before widening eligibility |

The implementation uses a separate checkout based on the committed
`4f5ab75ca9aeffbbf288de558220fe0bed8c1de7` product sources. Documentation was added
at `2c2088c880392dd995fb4af5c38fccae96248e18`. The separate retained-v8 working tree
was inspected read-only and left in place. Its listener, retrieval, filesystem,
string-interning and transport changes are not prerequisites for this slice and
were not imported. These runs do not measure the paper's uncommitted v8 version.

Production telemetry is commit `0aaf94e525`; the manual identity fixture is
commit `de588ece3ce8f0397bff1f4f9da6dfd6476f01c7`. The candidate executable was
built from that latter commit with no tracked or untracked `src/` modifications.
The later harness and reporting changes do not alter the executable sources.

## Verification

The candidate build succeeded for `//src:bazel-dev`. The four focused Java
targets executed **111 test cases with zero failures or errors**:

| Target | Cases |
| --- | ---: |
| `LocalCacheSessionTest` | 68 |
| `LocalDependencyValidatorTest` | 34 |
| `LocalInputSnapshotTest` | 5 |
| `DeferredNestedSetCodecTest` | 4 |

Exact target labels and XML are in [java-tests](evidence/java-tests/summary.json).
Tests cover cleanup after success/failure, preservation of admission evidence
while sampling, detached scalar snapshots, retained roots and pending validation.
The final Python checks pass **34 tests**: 26 for paired execution/BEP/provenance
and eight for strict identity-result classification. Their commands and logs are
in [python-tests.json](evidence/python-tests.json).
The build command, BEP, profile, log, result and build helper are preserved in
[builds](evidence/builds/candidate-build.result.json). The build used an existing
remote action cache with uploads disabled; the subsequent smoke commands used
neither remote nor disk action caching.

The separate manual identity run executed all four cases: the original-sharing
and distinct-equal-content controls passed; independent restores and the
original/restored join both produced four provider leaves where three are
required. [The result](evidence/identity-01/result.json) is
`known_failure_reproduced`, with Bazel exit 3 and `eligibility_gate_passed: false`.
Runner exit 0 means the explicitly requested known-failure classification matched;
it does not mean the conformance gate passed. The default conformance invocation
continues to fail. This is a codec identity reproduction, not a diagnosis of the
previous broad-workload link failure.

## Native smoke observations

Both standalone executables completed a separately timed producer and two
balanced cache-off/cache-on pairs. All ten commands exited zero and produced
the same sole expected output, `fixture.txt`, with SHA-256
`aa090a75d6276bc4618ae796b40e5bf53bfb23a1bfc2a95f9c8b7dcf7322c02a`.
Each arm used its own batch JVM and output base. Cache-on arms used independent
seed copies; repository/install caches were shared and kernel pages uncontrolled.

| Executable | Source commit | SHA-256 |
| --- | --- | --- |
| Baseline | `2c2088c880392dd995fb4af5c38fccae96248e18` | `1a2ee50ed409cf8f44dc6ecc3c2ae1e47ccd8609b66f84bb1294e3b058456c42` |
| Candidate | `de588ece3ce8f0397bff1f4f9da6dfd6476f01c7` | `473bf57e400e3ddd72c592ee5a002ecbc4d1117bf998f7de672bf9d9437783f7` |

The smoke JVM was the standalone binary's embedded Zulu OpenJDK 27+35, with a
2048 MiB maximum heap. This differs from the Zulu JDK 25 used to launch the Bazel
source build. Machine, environment, flags, source state and input identities are
recorded per cohort. The candidate manifest truthfully records untracked harness
files in the source checkout; its compiled `src/` tree was committed. The harness
records an operator-declared source/binary association; the separate build
records supply supporting provenance, not a hermetic reproducible-build proof.

| Cohort | Producer seconds | Pair 0: off / on seconds | Pair 1: on / off seconds |
| --- | ---: | ---: | ---: |
| [Baseline 02](evidence/smoke-baseline-02/summary.json) | 11.700 | 9.139 / 10.030 | 9.964 / 9.469 |
| [Candidate 01](evidence/smoke-candidate-01/summary.json) | 11.195 | 8.909 / 8.890 | 8.970 / 8.990 |

These are raw smoke observations, not an instrumentation-overhead estimate or
an optimization result. The two executable cohorts ran sequentially, the sample
is tiny, and this cold-process fixture includes substantial repository/module
initialization and other command overhead; no exclusive attribution is claimed.
RSS is recorded as
maximum child-process RSS, not a simultaneous process-tree sum or retained heap.
Do not infer a speedup, latency tail, or memory benefit from this table.

Both candidate cache-on arms recorded 164 CAS gets, all hits, receiving 31,397
value bytes. They recorded zero new logical writes and three deduplicated writes.
That demonstrates the fixture actually exercises reading and inherited reuse.
The new ownership report gave identical endpoint counts in those two arms:

| Owner/reference count | Before finish | After session close |
| --- | ---: | ---: |
| Candidate entries | 1 | 0 |
| Candidate witness references | 98 | 0 |
| Accounted candidate metadata bytes | 24,536 | 0 |
| Shared-put entries | 3 | 0 |
| Used parent / inspected publication entries | 1 / 1 | 0 / 0 |
| Admitted witness entries | 98 | 0 |
| Observed witness entries | 106 | 106 |
| Accepted root entries | 1 | 1 |
| Service retained root entries | 0 | 1 |
| Validated node / witness entries | 114 / 106 | 114 / 106 |
| Pending validation entries | 0 | 0 |

These are advisory component snapshots and reference counts, not atomic global
snapshots, exclusive object counts, actual retained heap bytes or peak residency.
They show session cleanup and the retained-root handoff without pretending all
client/validator state is released at that boundary. Sampling does not consume
evidence, copy collections or create a persistent per-key trace map. It scans
candidate entries twice per command; large-cohort overhead is still unmeasured.

## Evidence history and boundaries

[Baseline 01](evidence/smoke-baseline-01/summary.json) retains its original failed
status. Three native commands had succeeded, but the initial validator rejected
the extra unconfigured empty-abort BEP placeholder emitted on a cache hit. The
corrected validator requires exactly one successful configured completion and
records that precise placeholder separately. It also checks stream termination,
successful `buildFinished`, all announced child events and exact output digests.

All 13 saved commands from the failed attempt and two complete cohorts passed
[read-only revalidation](evidence/revalidation.json). Original statuses and
timings were not rewritten. Candidate 01 used the preserved
[harness snapshot](evidence/harness-smoke-candidate-01.py). Baseline 02 preceded
the terminal-completeness guard and was revalidated afterward. The subsequent
worker-hash guard is unit-tested; it does not change the command or output
validation behavior observed here. The harness was unchanged during Candidate 01.

The committed evidence includes raw commands, metrics, BEPs, profiles, passive GC
logs, diagnostics, test XML/logs, manifests and checksums. Full binaries, output
bases, cache populations and output files remain outside Git at
`/home/jgett/REPOS/.work/skycache-analysis-foundation-evidence/`. Archived absolute
paths describe this run; use the documented CLI to generate a new run directory.
Revalidating output bytes requires those retained outputs or a fresh run.
[SHA256SUMS.json](evidence/SHA256SUMS.json) covers the published evidence files.

## Next implementation decision

Finish the P00 workload and idle-lifecycle coverage, extend P01 phase/heap
attribution, and use that evidence to choose one of P03 publication, P06 root
metadata or P07 witness/index work. Keep the identity reproduction failing until
the identity contract is preserved or the unsupported cohort is excluded through
the existing admission boundary. Equal-content interning is insufficient: the
distinct-origins control must continue to pass. P04 acquisition ownership and P05
bytes-only prefetch follow their existing gates; dual SkyFunction execution and
general paging are not enabled by this slice.
