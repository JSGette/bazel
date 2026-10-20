# JVM Skyframe optimization findings and measurements

Measured on 2026-10-20 in Linux/WSL. This report concerns the existing Java
Skyframe implementation. Rust execution and lazy SSD graph storage are parked.

## Result

Six separate implementation commits demonstrate improvements on specific
allocation-heavy paths. The two cumulative workloads do **not** establish a
meaningful retained graph-RAM reduction or a dependable general build-speedup.
The complete stack also shows an unexplained increase in young-GC activity in a
separate analysis diagnostic pass. These are useful experiments, not six
unqualified no-degradation wins.

The implementation is published separately on
[`jsgette/skyframe_optimizations`](https://github.com/JSGette/bazel/tree/jsgette/skyframe_optimizations).
This report lives on `jsgette/master`; the implementation commits are not merged
into that documentation branch. No upstream Bazel PR has been filed by this work.

The conservative upstream recommendation is to consider **one small C40 PR**.
C02b and C11 need stronger practical-impact evidence. Park C16 and C41b unless
representative workloads justify them. Hold C39b because of its unresolved
process-memory signal. Further experiments remain parked.

## Source identities and published data

- Vanilla comparison source: [`4cfc05f77ba9`](https://github.com/JSGette/bazel/commit/4cfc05f77ba91900a2e3c6ab135861498cb2a0bf). This is the pinned fork baseline, not a claim about the latest Bazel release.
- Complete candidate source: [`8ff2acd337ac`](https://github.com/JSGette/bazel/commit/8ff2acd337ac9e20fc4a932dc1be078205ac9d2d).
- [Machine-readable measurements](measurements/skyframe-jvm-2026-10-20.json): all 150 microbenchmark rows (including confirmations), all 66 historical per-patch server-run summaries, all 12 final server-run summaries, paired RSS/PSS/heap measurements, complete graph censuses, final JFR summaries and GC-event aggregates.
- [45-direction candidate ledger](skyframe-jvm-optimization-candidates.md): accepted variants, rejected or inconclusive variants, static exclusions and work parked by request.

The 150 micro rows represent 141 distinct cases across the six saved changes;
confirmation rows repeat cases. Results compare each individual change with its
immediate predecessor. They cannot be added to estimate the whole stack's benefit.
The final comparison measures the complete stack against the pinned baseline.

## Changes and measured local benefits

| ID | Commit | Change | Distinct cases / rows | Measured benefit |
| --- | --- | --- | --- | --- |
| C02b | [`fd902c998470`](https://github.com/JSGette/bazel/commit/fd902c998470f32ae93f2c68a8b04db26875a751) | Pooled node-creation callback allocation | 22 / 22 | 24 fewer temporary bytes per pooled key lookup; measured pooled hits take 17.5–23.4% less time. |
| C16 | [`ae4bee6aa009`](https://github.com/JSGette/bazel/commit/ae4bee6aa009d532f1425730eade30234fc4fa6d) | Uninitialized empty-dependency completion | 12 / 15 | 120 fewer temporary bytes on the uninitialized-empty path; measured 0/1-parent cases take 7.4–10.2% less time. |
| C11 | [`9c9e59ab11b0`](https://github.com/JSGette/bazel/commit/9c9e59ab11b0b5337256763c0d5bfeeaa1e2074f) | Skip-prefetch dependency traversal | 16 / 16 | Fresh-completion cases take 20–61% less time; fully fetched scans take 60–86% less time and allocate 0 B/op in the measured method. |
| C40 | [`87f524ba1a14`](https://github.com/JSGette/bazel/commit/87f524ba1a14530a3ec1d58266522075f8032530) | Skip normalization of empty child cycles | 9 / 9 | Exception-only aggregation takes 31–81% less time; allocation changes 56→24 B/op for one child and 82,000→24 B/op for 1,024 children. |
| C39b | [`889593298b94`](https://github.com/JSGette/bazel/commit/889593298b942f2bef8c35e9806be28cb03b65cc) | Obsolete-dependency set shortcuts | 50 / 54 | Singleton-obsolete-dependency cases take 36–95% less time with lower allocation. Process-memory concern remains unresolved. |
| C41b | [`8ff2acd337ac`](https://github.com/JSGette/bazel/commit/8ff2acd337ac9e20fc4a932dc1be078205ac9d2d) | Exact-size cycle rotation builder | 32 / 34 | 1,024-key rotations take 31–55% less time; allocation changes 17,320–28,008→4,328 B/op. |

These measurements are temporary allocation and method/lifecycle latency, not
retained node size. C40 and C41b optimize error/cycle processing; their percentages
are not normal successful-build throughput improvements. Some lifecycle fixtures
have setup/cleanup allocation included in GC accounting even when that work is
outside the timed method. The data retain every control and confirmation row.

## Measurement method

- AMD Ryzen 9 9950X3D, 32 logical host threads, 32 GiB host RAM. WSL exposed
  16,191,616 KiB (about 15.4 GiB) RAM and had 4 GiB swap configured.
- Ubuntu/WSL2, kernel `6.6.87.2-microsoft-standard-WSL2`, x86-64.
- Zulu JDK `25.0.4+7-LTS`; measured servers and JMH forks explicitly used
  `-XX:-UseCompactObjectHeaders`. Java, `jcmd` and JFR came from the same runtime.
  Binary hashes and the JDK release-file hash are in the data file.
- JMH 1.37, one benchmark worker, fixed 512 MiB heap, GC profiler and
  `FULL_DONTINLINE` result consumption. The default gate used four 500 ms warmups
  and five 500 ms measurement iterations per fork, in baseline/candidate/candidate/
  baseline order. Uncertain cases received longer confirmations. Finder/invalidation
  experiments additionally capped VM processors at four; this cap does not apply
  to all matrices or follow from a filename.
- Real production methods, validated fixtures, consumed results and identical
  harness classfiles across each baseline/candidate pair. Repeatable adverse micro
  controls caused rejection of several otherwise promising variants.
- Builds: four jobs, 4 GiB server heap and 4,096 MiB local action-memory setting.
  Cache & Carry supplied the configured `efcache`; BES was disabled. Timings held
  an exclusive shared resource lock. Only owned Bazel servers were stopped.
- Each final workload used three fresh-server/output-base pairs in AB/BA/AB order.
  Warmups preceded measured repeated commands. Three pairs show variability, not
  statistical significance or universal no-degradation.
- Process-tree RSS/PSS sampled every 50 ms. Short peaks can be missed; the reported
  phase values are medians of sampled command peaks. RSS can count shared pages
  repeatedly; PSS apportions them.
- Bazel traces, JFR, memory profiles, forced-GC histograms and graph censuses were
  collected in a separate diagnostic pass. JFR allocation weights are sampled
  estimates, not exact allocated-byte totals.

### Workloads

**Ordinary build:** a fixed metadata-heavy fixture of 1,024 native-genrule actions
in two layers, with 8 KiB command padding (about 8.1 MiB BUILD metadata). This is
not a compiler-heavy production workload. Original/edit/revert actions were
preseeded through Cache & Carry outside timing. Per server: one cold build, two
warmups, seven measured warm builds, 14 alternating disjoint-target builds, one
source edit and one revert.

**Repository self-analysis:** `//src:bazel-dev` and
`//src/test/java/com/google/devtools/build/skyframe:SkyframeTests`, using `--nobuild`
and `--lockfile_mode=off`. Both binaries analyzed the same final accepted source.
Per server: one cold analysis, two warmups and five measured warm analyses. This
does not compile Bazel or execute those test targets. Disabling lockfile use adds
cold module-extension work equally to both variants.

“Cold” means a fresh Bazel server/output base. OS, repository, remote and action
caches can be warm. These measurements are not uncached storage or SSD graph-read
benchmarks.

## Complete-stack timing comparison

Values are medians of the three server summaries, in seconds. Each server's
repeated phase summary is itself a median. Paired changes use
`(candidate / baseline - 1) × 100`; positive means slower. Paired differences and
the difference between independently computed medians are different statistics.

| Workload | Phase | Baseline seconds | Candidate seconds | Pair 1 / 2 / 3 |
| --- | --- | --- | --- | --- |
| ordinary | cold | 3.736921 | 3.746076 | +4.67% / -5.15% / -3.36% |
| ordinary | warm | 0.155903 | 0.153445 | -3.82% / +0.55% / -1.64% |
| ordinary | disjoint | 0.157565 | 0.153565 | -0.77% / -2.48% / -2.76% |
| ordinary | edit | 1.027607 | 0.985681 | -4.87% / -3.07% / +2.32% |
| ordinary | revert | 0.995577 | 0.987949 | -1.74% / -0.88% / +2.79% |
| selfanalysis | cold | 15.221778 | 15.170129 | +8.76% / -4.42% / -1.60% |
| selfanalysis | warm | 0.163665 | 0.163554 | +4.59% / -2.26% / -2.25% |

Ordinary median differences were cold +0.25%, warm −1.58%, disjoint −2.54%,
edit −4.08% and revert −0.77%. Only the disjoint phase improved in all three
pairs. Analysis medians changed −0.34% cold and −0.07% warm, with mixed paired
signs. These observations do not support a dependable general speedup.

## Memory and retention

The following medians and paired differences are MiB. Full RSS/PSS values for
every phase are in the data file. Heap totals cover the whole Java heap after
the diagnostic pass and forced GC, not Skyframe objects alone.

| Workload | Metric | Baseline MiB | Candidate MiB | Paired MiB changes |
| --- | --- | --- | --- | --- |
| ordinary | warm sampled_peak_process_tree_pss | 608.830 | 573.283 | +2.220 / +9.021 / -73.786 |
| ordinary | post_gc whole_java_heap | 53.108 | 53.250 | +0.142 / +0.225 / -0.236 |
| ordinary | post_gc process_tree_pss | 774.375 | 745.243 | -84.506 / +83.767 / -29.132 |
| selfanalysis | warm sampled_peak_process_tree_pss | 1295.078 | 1239.017 | +77.641 / -56.062 / -227.911 |
| selfanalysis | post_gc whole_java_heap | 117.132 | 117.040 | -0.092 / -0.157 / +0.070 |
| selfanalysis | post_gc process_tree_pss | 1316.029 | 1308.518 | -64.509 / +19.438 / +16.673 |

Graph censuses were exactly 2,703 nodes in every ordinary run and 61,940 nodes in
every self-analysis run, with matching per-SkyFunction counts. This establishes
equal observed counts, not equal graph bytes. Whole post-GC heap was nearly
unchanged. Large, mixed process-PSS differences prevent treating a lower median
as a stable memory saving. No meaningful retained graph-RAM reduction was shown.

**C39b remains a hold:** its individual comparison had warm-PSS changes of
+45.1, +50.3 and −18.1 MiB initially, followed by +35.7 and +29.8 MiB in matched
confirmation. That is higher PSS in four of five pairs. Similar whole-heap totals
and node counts do not explain this process-memory signal. The cumulative stack
comparison cannot attribute or dismiss an individual patch's effect.

## Profiling findings and adverse GC signal

Final JFR CPU leaves remain varied map, Starlark and evaluator work. Estimated
allocation weights vary substantially between runs and do not establish a
repeatable reduction in total allocation. No candidate-specific causal hot path
was established by these whole-workload recordings.

The analysis diagnostics consistently show more G1 young collections:

| Pair | Young GC count | Summed young pauses, ms | Difference, ms |
| --- | --- | --- | --- |
| 1 | 9 → 14 | 68.550 → 88.859 | +20.309 |
| 2 | 8 → 14 | 53.137 → 80.449 | +27.312 |
| 3 | 8 → 14 | 62.499 → 85.831 | +23.333 |

All runs had three `System.gc()` full collections and one heap-inspection
collection. Normalized invocation/reset sequences match, so extra requested
profiling steps do not explain the increase. Total GC event counts were
16→24, 14→23 and 14→23; concurrent old-generation cycles also increased.
Do not equate those counts with distinct stop-the-world pauses or sum overlapping
concurrent durations as stopped application time. Full-GC pauses were lower in
the candidate recordings; sums of all event pause fields had mixed changes.

The diagnostic analysis wall times also had mixed signs. This separate profiling
workload does **not** establish a regression in the timed cold/warm commands.
The cause and per-patch attribution are unresolved. It does rule out claiming
universally lower GC cost or allocation for the complete stack.

## Correctness validation

The final Linux run passed all **150 selected test targets**. Its 182 result/shard
XML files contain **4,414 executed testcase records, 46 skipped records and zero
failures/errors**. All results used run 1 / attempt 1; no cached test results were
observed. These are testcase records, not a deduplicated count of logical tests.

Selection:

```text
//src/test/java/com/google/devtools/build/skyframe:SkyframeTests
//src/test/java/com/google/devtools/build/skyframe:SkyframeEvictionTest
//src/test/java/com/google/devtools/build/lib/skyframe/...
```

The 46 skips consist of 14 explicit ignores and 32 assumption-gated parameter
cases. They cover existing flaky/disabled tests, non-incremental or catastrophic
evaluator combinations, filesystem applicability and unsupported include-scanning/
header-module test-harness combinations. Skips are unexecuted coverage, not pass
evidence. This is Skyframe-focused validation, not the whole Bazel test repository.

The first invocation stopped before tests because the installed Bazel rejected
`--test_strategy=local`. The successful retry used `standalone`. That setup
failure is retained separately and is not counted as a source test failure.

For the equivalent target selection in an environment with `efcache` configured:

```sh
bazel --host_jvm_args=-Xmx4g --host_jvm_args=-XX:-UseCompactObjectHeaders test \
  --config=efcache --bes_backend= --jobs=4 --local_resources=memory=4096 \
  --local_test_jobs=2 --java_runtime_version=remotejdk_25 \
  --tool_java_runtime_version=remotejdk_25 --test_strategy=standalone \
  --cache_test_results=no --test_output=errors \
  //src/test/java/com/google/devtools/build/skyframe:SkyframeTests \
  //src/test/java/com/google/devtools/build/skyframe:SkyframeEvictionTest \
  //src/test/java/com/google/devtools/build/lib/skyframe/...
```

This is a target/flag recipe, not an exact timing reproducer. The measured setup
also pinned server Java, used an isolated output base, cleared test filters and
collected BEP/XML identities. Build action caching was enabled while test-result
caching was disabled.

## Retained uncertainty and rejected alternatives

- Initial C02b self-analysis changed `MODULE.bazel.lock`; that set was excluded
  and replaced by six fixed-input runs with `--lockfile_mode=off`.
- Earlier JDK/header combinations produced crashes or impossible-state exceptions,
  including unchanged baselines. Final results use the stated JDK25/default-header
  setup, but this is not proof of a runtime fix.
- Original C41 had an unchanged-baseline `ImmutableList.hashCode` NPE in one
  benchmark fork. The partial group was excluded; complete repeats and refined
  C41b matrices passed. Cause remains unproven.
- Original C39 and C41 variants were rejected for repeatable control slowdowns;
  their refined C39b/C41b versions are separate experiments. C38b was rejected
  after a repeatable allocation increase despite improvements elsewhere.
- Large cached-action stalls appeared in some earlier baseline and candidate
  profile runs. They are retained in the historical summaries. Their causes are
  unproven and anomalous baseline stalls are not used as headline speedups.
- C11's earlier warm-PSS concerns did not consistently repeat. C40/C41b retained
  small adverse timing signals in some confirmations. Finite noisy measurements
  cannot establish no regression on every workload.
- Older C02b/C16/C11/C40 profiles lack per-run owned-server shutdown proofs.
  Newer C39b/C41b and all 12 final runs have successful owned-server shutdown
  verification. Historical evidence is not retroactively upgraded.
- C11 now traverses documented unique dependencies in group order instead of
  incidental copied-HashSet order. With multiple undone dependencies, the first
  reported key and values cached before failure may differ. No supported
  correctness blocker was found, but diagnostic order is not byte-for-byte equal.

The ledger contains 45 engineering directions, not proof that every possible
Skyframe optimization has been exhausted. C44 invalidation work is staged but
was explicitly parked without a C44 build, measurement or implementation commit.

## Should these changes go upstream?

| Change | Recommendation | Reason / next evidence |
| --- | --- | --- |
| C02b — Pooled node-creation callback allocation | Conditional | Establish frequency on representative builds and verify allocation savings on upstream-supported JVMs before filing. |
| C16 — Uninitialized empty-dependency completion | Park | Correctness review found no blocker, but real-workload frequency and practical benefit are unquantified. |
| C11 — Skip-prefetch dependency traversal | Conditional | Needs a stronger practical-impact argument; disclose the incidental dependency encounter-order change. |
| C40 — Skip normalization of empty child cycles | Consider one small PR | Small guard removes unnecessary empty-cycle work; keep claims confined to the measured error path. |
| C39b — Obsolete-dependency set shortcuts | Hold | Warm process PSS increased in four of five per-patch pairs; keep the experiment separately reversible. |
| C41b — Exact-size cycle rotation builder | Park | Measured cycle-rotation improvements are narrow; prioritize only if realistic cycle/error workloads justify review. |

The realistic starting point is one small C40 patch with focused behavioral
tests and bounded allocation/error-path claims. Do not present the whole series
as a RAM or broad build-performance improvement. No current-upstream applicability,
upstream CI, supported-JVM coverage or CLA status is asserted. Rebase and validate
any proposed patch before submission. The remaining work is parked, not scheduled.

## Evidence availability

This repository publication contains the findings, complete derived measurement
tables, source identities and diagnostic aggregates. Large raw JFR recordings,
Bazel traces, BEP/XML/logs, benchmark harnesses, controller scripts and rejected
patch variants remain in the separately retained local archive
`skyframe-jvm-evidence.zip`; it is **not** uploaded with these Git branches.
The publication is therefore not a self-contained microbenchmark reproducer.

- Archive size: 74,228,252 bytes; 10,491 files verified against its manifest.
- Archive SHA-256: `e66f4d7f9023d01c494841dd0b916f7e62180cfd116d315d661f7240aa49bf03`.
- The JSON records the source-report hash, binary/JDK hashes and raw GC-event
  file hashes. Raw samples and large recordings can be matched to the archive.

The six commits include production changes and focused correctness tests. Local
benchmark scratch sources were archived and removed from the implementation
checkout; the branch was clean when published.
