# Bazel multi worktree Phase 0 baseline and verification

31 October 2026. **Status: in progress.** Phase 0 establishes a reproducible baseline and verifies the benchmark machinery. It does not implement the shared daemon or claim a sharing speedup.

This is the execution record for Stage 0 of the separate [implementation plan](multi-worktree-implementation-plan.md). Raw manifests, the fixture and harness, and observations belong in [the measurement directory](../measurements/multi-worktree-phase0-2026-10-31/). Later implementation phases remain gated on the completion checklist below.

## Baseline identities

The machine-readable [baseline lock](../measurements/multi-worktree-phase0-2026-10-31/baseline-lock.json) separates the implementation source from the released executable used to validate the harness.

| Item | Pin or observation |
| --- | --- |
| Implementation repository | `bazelbuild/bazel`; user fork remote `JSGette/bazel` |
| Implementation base | `9a2ababbd5cf113c31450fbe9214b501be8c58a2` |
| Local working branch | `jsgette/multi-worktree-phase0` |
| Bootstrap version requested by that source | Bazel `9.3.1` |
| Stock smoke executable | Official Linux x86_64 Bazel `9.3.1` release |
| Published SHA-256, verified against downloaded bytes | `7f775b54bbec487efaf0f49ccda1d2ed02cd34f582edfdd6aeeec76e39ef585c` |
| Upstream `9.3.1` release tag | `fee2bf2d1c14bc2096068a4d46f1842472092044` |
| Actual executable JVM | Embedded Azul OpenJDK `25.0.2+10-LTS` |
| Host | Linux `6.18.57`, x86_64 |
| CPU limit | cgroup `400000 100000`, or four CPU equivalents; cpuset `0-4` |
| Memory limit | 16 GiB, no swap |

The stock release is **not** a build of the pinned implementation commit. These first runs establish fixture/harness behavior and stock observations only. Before comparing a patch, build a stock control from the implementation base using the same toolchain, then record the candidate patch and executable checksums. Keep earlier Skycache experiments out of that comparison unless an independently reviewed change is explicitly included.

The checkout is sparse to keep the setup small. Expand it before building Bazel itself. No remote branch or pull request is required for the initial local experiment.

## Acceptance criteria fixed before implementation comparisons

Correctness and experiment validity are mandatory gates:

1. Every supported fixture produces its independently expected output and exit status. An output mismatch, unexpected success/failure or missing evidence fails the run; it is not a slower successful sample.
2. Each worktree has a distinct output base and server identity. Concurrent batches record overlapping invocation intervals. These intervals establish command overlap, not necessarily overlap of a particular analysis or execution subphase.
3. All compared arms use declared source states, equivalent aggregate CPU/resource budgets, identical compiler/toolchain selections, and explicitly controlled cache populations.
4. Normalization may remove only declared incidental differences such as timestamps and invocation identifiers. Never normalize away source paths, diagnostics or action inputs whose correctness is under test.
5. Cleanup targets only experiment-owned servers and directories. Failed and interrupted runs retain their logs and record cleanup outcomes.

Proposed qualification budgets for later implementation arms, to be changed only before collecting those arms:

- Warm single-worktree median complete-command latency: no more than 5% above its matched control; p95 no more than 10% above control. These are project acceptance budgets, not established measurements.
- Concurrent throughput must not regress beyond measurement uncertainty at equal aggregate resource budgets. Report queue latency separately from service time.
- Promote compilation sharing only when it delivers a repeatable improvement in complete-command latency or total retained memory on the representative workload, outside measurement noise, without violating the single-worktree budgets. Hit count or fewer JVMs alone cannot pass.
- No unexplained output differences, invocation-state leaks, unbounded growth under worktree churn, or cross-worktree lifecycle interference.

Use at least 30 interleaved paired trials for a performance comparison and report raw observations and an uncertainty interval. Make p95 claims only with at least 100 observations per arm. Repeat a claimed improvement in a second session. Smoke trials deliberately do not meet these statistical requirements.

## Workload matrix

| Cohort | Purpose | Current execution scope |
| --- | --- | --- |
| Small deterministic Starlark/action fixture | Verify worktree isolation, output oracle, launch timing and result capture | First executable smoke cohort |
| Identical sources with cold servers and primed disk cache | Separate server/analysis startup from reusable action results | Harness cohort; cache priming must be declared |
| Warm repeated commands | Measure the existing incremental baseline | Harness cohort; separate from cold starts |
| Worktree-specific source edits | Detect cross-worktree result contamination and exercise incremental execution | Harness cohort |
| BUILD and `.bzl` divergence | Exercise loading/analysis invalidation and distinct semantics | Required before Phase 0 completion |
| Missing paths, glob membership and symlink changes | Cover negative, directory and path-resolution dependencies | Required before Phase 0 completion |
| Different module overrides, repo mappings, toolchains and relevant environment | Detect context-sensitive aliasing | Required before Phase 0 completion |
| Pinned public DataDog build/test workload | Establish representative cost and memory distribution | Selection and execution tracked separately below |
| Worktree churn and lifecycle failures | Validate experiment cleanup and later implementation isolation | Initial harness cleanup; full lifecycle matrix belongs to Stages 5–7 |

Run 1, 2 and 4 worktrees on this host. Eight-worktree qualification is deferred: eight independent clients with `--jobs=1` exceed the four-job aggregate action budget used by the initial matrix. An eight-worktree stress test is possible, but must be explicitly labeled oversubscribed or moved to a host with an appropriate budget; it cannot silently replace an equal-budget comparison.

## Measurement contract

Each cohort must retain a manifest containing source commits and dirty-input hashes; binary checksum and version; actual Java runtime and heap settings; OS and cgroup limits; targets; complete commands; output-base and cache paths; and the reset/priming sequence. Record only an allowlist of environment settings needed for reproduction. Do not dump process environments or credentials.

Each invocation must retain timestamps, wall duration, exit status, stdout/stderr, BEP, trace profile, expected/observed outputs and digests, and available server identity. Preserve failed invocations alongside successful ones. Record batch elapsed time separately from the sum of command durations.

Report memory scopes accurately. A sampled sum of Bazel-server RSS is not total host memory, peak Java heap, allocation, or aggregate worker memory; sampling can miss spikes and RSS can double-count shared pages. Qualification needs JVM allocation/GC/retained-heap evidence and a whole-process-tree or cgroup measure where available. Distinguish cache-owned references from payloads still held by engines.

Cold state is multidimensional. Record server/graph, disk action cache, downloaded repository contents and OS page-cache state separately. A new output base does not imply cold machine caches. A warm command must preserve its own engine and output base. Do not delete a running server's output tree to manufacture a cold run.

The synthetic fixture isolates the harness from C++/Java toolchain installation. It cannot establish the cost distribution of a large monorepo, nor prove that shared Starlark compilation is worthwhile.

## Phase 0 execution record

### Representative workload selected

The public DataDog checkout is pinned to [`a83c9fa5161b775ed24b1df825db4817ea562a89`](https://github.com/DataDog/datadog-agent/tree/a83c9fa5161b775ed24b1df825db4817ea562a89). Its `.bazelversion` is 9.3.1. The [workload lock](../measurements/multi-worktree-phase0-2026-10-31/representative-workload.json) records Go/rules versions, source references, selected targets and execution constraints.

Start with `//pkg/util/strings:strings_test`; then use a three-target cohort adding `//pkg/util/cache:cache_test` and `//pkg/metrics:metrics_test`. These are inspected target definitions, not executed-test evidence. The larger trace-agent binary is not an initial correctness criterion because its BUILD file still identifies full Bazel build validation as outstanding.

Use the repository's Bazelisk/wrapper contract and preserve its `.bazelrc`. Explicitly disable private remote caching/execution, inspect any `user.bazelrc`, use experiment-owned cache/output directories, and provision toolchains before timing warm-dependency cohorts. Do not accidentally set the wrapper's CI mode, which can require a private Buildbarn identity. Check actual executed-test counts and incompatible-target exclusions. The synthetic fixture's `--ignore_all_rc_files` policy does not apply to this representative repository.

### Setup completed

Completed setup:

- Created a local branch from the reviewed, pinned upstream source; read the repository's `AGENTS.md`.
- Downloaded the official Bazel 9.3.1 executable and verified its published SHA-256.
- Queried the executable's actual runtime: embedded Azul JDK 25.0.2, separate from the host's JDK 21.
- Recorded the CPU and memory quotas rather than inferring capacity from host-visible CPU/RAM totals.
- Started a separate implementation-plan document and this Phase 0 record.

### Preserved bootstrap failure

The [first attempt](../measurements/multi-worktree-phase0-2026-10-31/smoke/summary.json) failed before analysis while fetching registry metadata. The bundled Java trust store did not include the managed environment's proxy CA, producing a PKIX certificate-chain failure. The harness rejected the command and preserved BEP, logs, expected/observed output records and its failure summary. This is not a build-time observation or successful benchmark sample.

An explicit experiment-local trust store was constructed by copying `/etc/ssl/certs/java/cacerts` and importing the public CA certificates already installed in `/usr/local/share/ca-certificates/`. Its SHA-256 is `51fd70017df628d89458efeb91afb90d0fad5c7b1725aec9bf0f931dc057e797`. Subsequent runs pass it through the JVM's `javax.net.ssl.trustStore` setting. TLS verification remains enabled; the run manifest records the exact path and checksum.

The initial shutdown also timed out because container PID 1 retained the exited Java process as a zombie. The harness now becomes a Linux child subreaper before launching children and reaps only its recorded daemon child, checking process identity. It does not call a blanket `waitpid(-1)` or reap unrelated clients. A real shutdown failure still fails the run. The initial failed cleanup remains visible in its original summary.

The [next preflight](../measurements/multi-worktree-phase0-2026-10-31/preflight/summary.json) verified the TLS and shutdown fixes, then exposed the fixture's assumption about empty globs. Bazel 9.3.1 rejects an empty `glob` by default. The optional-file expression now explicitly uses `allow_empty = True`; the failed fixture and runner are preserved with that attempt. This is a fixture repair, not a Bazel product change.

### Verified native smoke matrix

The [successful matrix](../measurements/multi-worktree-phase0-2026-10-31/smoke-verified/summary.json) completed **106 build commands in 46 cohorts**, with **8 successful owned-server shutdowns** and no output mismatches. Every two/four-worktree cohort recorded distinct server PIDs and full overlap of client-command intervals. The fixed runner also passes **11 focused tests**, including functional orphan adoption/reaping that preserves an unrelated client's exit status.

The [archive verifier](../measurements/multi-worktree-phase0-2026-10-31/harness/verify_results.py) independently checks saved result records, output hashes, successful BEP completion and decodable nonempty profiles. Its [derived observations](../measurements/multi-worktree-phase0-2026-10-31/smoke-observations.json) retain per-command Bazel timing and runner counters.

| Concurrent worktrees | Verified build commands, excluding prime | Cold-server command durations with primed disk cache | Second identical warm-repeat durations | Peak sampled sum of server RSS across that group |
| --- | ---: | --- | --- | ---: |
| 1 | 15 | 7.064 s | 0.115 s | 351 MiB |
| 2 | 30 | 9.221–9.488 s | 0.217–0.268 s | 704 MiB |
| 4 | 60 | 13.527–16.351 s | 0.377–0.475 s | 1,470 MiB |

The additional prime is one cold build. BEP confirms one fixture-action disk-cache hit for each of the seven fresh-output-base commands after priming. Across the entire matrix, fixture execution used the local sandbox 22 times and the disk cache 28 times; warm incremental commands often did not execute that action. Eight resulting module lockfiles were archived in [module-locks](../measurements/multi-worktree-phase0-2026-10-31/smoke-verified/module-locks/index.json); all have the same content hash. They were captured after the runs, not asserted to exist before the initial build.

These are smoke observations, not a speedup, scaling law or analysis-time estimate. Cold command durations include JVM startup, repository setup/loading, analysis and execution. Groups ran in fixed 1→2→4 order with shared caches accumulating edit variants. Each mutation case sets a specified fixture state and may restore the preceding case's edit; it is not an isolated timing experiment for only the mutation named in the case. Before qualification, use separately controlled resets, equivalent cache state and interleaved matched arms. Profiles were enabled on these smoke commands; low-overhead latency cohorts remain to be established.

Source, BUILD and `.bzl` divergence, optional-file appearance, glob additions and symlink retargeting all passed the synthetic oracle. These do not cover module/toolchain/environment divergence, production tests, the full lifecycle matrix or representative monorepo cost. `/bin/sh` and `/bin/cat` are undeclared host tools in this deliberately small fixture; their resolved paths and checksums are recorded, and no hermetic-toolchain claim is made.

### Fresh-directory reproduction

A second run in fresh state/results directories repeated the prime, two-worktree cold-server cohort and one warm repeat. All **5 additional build commands** passed; all **3 servers** shut down and were reaped. [Raw reproduction evidence](../measurements/multi-worktree-phase0-2026-10-31/reproduction/summary.json) and [verified observations](../measurements/multi-worktree-phase0-2026-10-31/reproduction-observations.json) are retained. Binary, harness and fixture content hashes match the full matrix. All successful-run server PIDs were absent on final verification. This reproduces the core setup and two-worktree path, not every mutation cohort or a performance conclusion.

## Completion checklist

- [x] Pin implementation source and stock smoke executable; distinguish their identities.
- [x] Verify stock executable integrity and actual JVM.
- [x] Record host constraints and experiment acceptance criteria.
- [x] Freeze a reusable fixture and a self-describing, failure-sensitive harness.
- [x] Run and archive real concurrent worktree smoke builds and validate outputs.
- [x] Exercise source/BUILD/`.bzl`/missing-file/glob/symlink mutations in the synthetic fixture.
- [ ] Complete module/repository-mapping/toolchain/environment divergence and the broader mutation matrix.
- [ ] Build and hash a stock control from the pinned implementation source.
- [ ] Pin and execute representative DataDog build and test cohorts.
- [ ] Attribute loading/compilation, ordinary analysis, execution and retained memory on representative commands.
- [x] Reproduce the core setup and two-worktree path from the saved harness in fresh experiment directories.
- [ ] Independently reproduce the full mutation and representative-workload matrices.
- [ ] Collect enough repeated observations for performance conclusions.

Phase 0 remains open while any required gate above is outstanding. The next implementation work should not rely on a speedup, memory reduction or source-built baseline that this record has not established.
