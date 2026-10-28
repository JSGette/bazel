# Action checking and shared execution

28 October 2026. Skycache bypasses per-target construction and owner assignment on an accepted analysis hit. It **does not remove cross-target conflict checking or execution sharing**. Cached roots that are valid separately can conflict when composed, including with a locally computed target. The design paper now describes that boundary and illustrates the flow.

## Delivered correction

Commit `e4dcbf9a35` removes two redundant operations in the existing checker paths:

- The incremental conflict finder claims each starting root in its existing visited set. Repeated roots and roots also visited as dependencies no longer repeat graph traversal and action registration. Pruned roots still wait for predecessors and perform required conflict reruns with fresh local state.
- Owner assignment skips a second compatibility check of the identical output artifact object within the same iteration. It retains foreign-owner validation and all checks for distinct artifacts. Positive comparison results are not memoized across output assignments: assigning generating action indexes can change equality for distinct nonshareable artifacts.

No persistent action map, new flag, wire-format change, or widened restoration eligibility is introduced. The root claim can add one visited-set entry per previously unvisited top-level root. This is not a guaranteed memory saving. Ordinary action-result caching and execution-sharing lifetimes are unchanged.

The focused regression tests show registrations falling from two to one and action-key computations falling from four to two per action in a two-output shared-artifact case. These are operation-count proofs for those cases, not whole-build performance estimates.

## Native correctness evidence

The final build passes **542 executed Java cases** across ActionsTests (384), IncrementalArtifactConflictFinderTest (8), OutputArtifactConflictTest (114), and SequencedSkyframeExecutorTest (36). Eleven cases were skipped by existing test conditions. Tests ran uncached with one attempt; `//src:bazel-dev` built successfully. All 68 Python harness, fixture and identity-runner tests pass.

The first red run contains the expected action comparison failure, four repeated-registration failures, and two test-fixture errors caused by trying to mock abstract sealed Artifact. After correcting the fixture to mock concrete DerivedArtifact, the second red run has exactly four registration-count failures and four passing controls. Green passes all focused tests and the broader conflict/sharing suites. All failed evidence is retained; the fixture error is not presented as a product regression.

Raw commands, logs, XML shards, source patches and the new-test archive are under [evidence-actions](evidence-actions/). [Source binding](evidence-actions/source-binding.json) identifies the baseline, built candidate, and final source commit. The baseline is `f5552db2b0`, and the candidate binary SHA-256 is `d4a533a7d79708e9681181d334035a59a960fda0984bbea6fa921af3ef9b3a51`. The workload manifests record the pre-commit HEAD plus source diff; those source bytes and archived tests were checked before committing. Binary identity was checked throughout each cohort. Independent rebuild/reproduction remains open.

## Action-heavy workload observations

The new [fixture](fixtures/ACTION_FANOUT.md) generates 512 writer rules, eight parent roots, and 4,096 independent FileWrite actions and files. Broad demand selects the eight roots; overlapping demand also selects all 512 writers. Both request exactly the same 4,096 output hashes. This isolates overlapping traversal demand without inventing more actions.

Four cohorts ran one producer and two balanced cache-off/cache-on pairs each: **20 successful commands**. Every command produced the exact output set and executed 4,096 FileWrite actions. Every cache-on consumer reported 520 configured-target hits. All arms use fresh batch JVMs/output bases with ordinary remote and disk action caches disabled. Repository caches are warmed within cohorts, OS page state is uncontrolled, and baseline/candidate cohorts were sequential rather than randomized against one another.

| Cohort | Producer seconds | Cache-off seconds, pair 0 / 1 | Cache-on seconds, pair 0 / 1 |
| --- | ---: | ---: | ---: |
| Baseline broad | 13.928 | 9.894 / 9.729 | 9.759 / 10.094 |
| Candidate broad | 13.060 | 10.199 / 9.484 | 9.625 / 9.563 |
| Baseline overlapping | 11.802 | 9.431 / 9.471 | 9.866 / 9.774 |
| Candidate overlapping | 12.283 | 9.357 / 9.495 | 9.511 / 9.690 |

**These samples do not establish a whole-command latency or memory improvement.** Candidate broad cache-on maximum-child RSS was 718,200 and 611,212 KiB, versus baseline 547,244 and 552,580 KiB. Overlapping cache-on RSS was 613,716 and 626,856 KiB, versus baseline 646,128 and 618,864 KiB. The observations do not satisfy a demonstrated memory-improvement gate. The narrow same-artifact owner-assignment optimization is exercised by its unit regression, not claimed as a hot path in this fixture.

`ALV collection` includes exclusive-lock waiting, and `Go through actions` includes waiting for asynchronous workers. Their summed overlapping spans are not exclusive deduplication CPU. For example, overlapping cache-on collection sums were baseline 1,058.109 / 1,099.591 ms and candidate 1,117.515 / 894.203 ms: mixed results. Complete measured rows, profiler interval unions, ownership snapshots and cost-extraction limitations are preserved in the cohort cost CSVs and [observations](evidence-actions/observations.json).

## Composition and execution-sharing matrix

The separate semantic runner checks equal-output sharing, different content, different makeExecutable flags, and file/prefix collisions. It requires complete terminal BEP, exact expected analysis failure codes, and diagnostics naming both owners and intended paths. Successful shared pairs require correct bytes and exactly one executed FileWrite, so equal final content cannot conceal duplicate execution. Separate producer selections plus exact aggregate configured-target hit counts distinguish cold, restored/local, and restored/restored cases; hit accounting is aggregate, not per-label instrumentation.

The initial five-command run failed an oracle assumption: physical output executability was compared directly to the action's makeExecutable flag. Bazel normalizes ordinary output permissions to 0555 by default (0755 with writable outputs). The corrected runner records physical modes as diagnostics; the executable-flag conflict remains an action-key test. Original failed run01 and its exact helper are retained. See [permission normalization](../../../../src/main/java/com/google/devtools/build/lib/skyframe/ActionOutputMetadataStore.java) and [output permission policy](../../../../src/main/java/com/google/devtools/build/lib/vfs/OutputPermissions.java).

The corrected run passes all **14 commands**: two successful producers and twelve semantic checks. The three shared cases each execute exactly one FileWrite; configured-target hit counts are exactly 0, 1 and 2 for cold, restored/local and restored/restored demand. All nine negative cases report the expected conflict. Results and complete raw evidence are in [run02](evidence-actions/actions-semantics-02/summary.json).

Existing merged analysis/execution scheduling can execute an already accepted branch before another branch reveals a conflict. The restored/local negative cases each observed one FileWrite before the failed build completed. The check does not claim a global analysis barrier or zero execution in every failed build; it preserves Bazel's existing error and sharing behavior.

Reproduce with `fixtures/run_action_semantics.py --bazel /absolute/bazel --workspace /absolute/generated/actions4096 --run-dir /absolute/new/run`. The runner and nine focused Python tests are committed; fixture generation and paired commands are documented in [ACTION_FANOUT.md](fixtures/ACTION_FANOUT.md).

## Remaining work

These changes preserve the existing conflict boundary. Skipping whole restored closures would need authenticated action/closure revisions, output namespace and checker semantics, plus exact-path and prefix checks at every cached/cached and cached/local composition boundary. It is not justified by a payload checksum or by previously passing one root.

P00/P01/P02 remain incomplete; the known provider-identity failures are unchanged. Producer allocation/serialization, exclusive cost attribution, idle recovery, and independently reproduced performance gates remain ahead of broad reuse, paging, or speculative acquisition. The next optimization must be justified by its full producer/consumer costs.
