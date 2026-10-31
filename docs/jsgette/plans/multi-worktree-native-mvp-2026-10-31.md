# Native multi-worktree MVP — verified 2026-10-31

The native Bazel launcher now hosts two private workspace engines in **one JVM**, runs their build/test commands concurrently, and shares eligible equal actions. The Python process supervisor is no longer the MVP entry point.

## Observed result

Two clean DataDog Agent worktrees at `a83c9fa5161b775ed24b1df825db4817ea562a89` built `//pkg/util/strings:strings_test` concurrently. Both reported server PID **519571**. Process sampling observed one matching daemon across 202 samples.

| Worktree | Spawn requests | Sandboxed executions | Disk-cache hits |
| --- | ---: | ---: | ---: |
| A | 15 | 2 | 13 |
| B | 15 | 13 | 2 |
| Total | 30 | **15** | **15** |

All 15 REAPI action digests occurred in both logs. Every digest had exactly one local execution and one cache hit. There were also 10 internal actions per engine, which are not external spawns. The earlier two-process supervisor executed all 30 spawns.

Before this run, the old daemon was confirmed stopped and its shared disk action cache, both local action caches, and both execroots were removed. Fetched external repositories were retained. The new JVM had cold graphs. The exact removed paths are in `cold-state.json`; this was not a warm action-cache demonstration.

Both generated test executables passed their original tests and have SHA-256 `fc412a3056d67e88fd6ed38c83da578135669514c661893bbefdabdd6e302de8`, matching the independent-server baseline. Both source worktrees were restored to clean status, and both experiment daemons were stopped. Recorded build times were about 40 seconds each; this is a correctness/action-sharing result, not a performance benchmark.

## Follow-up timing and graph ownership

Three matched cold-action runs compared this native candidate against the preserved unmodified source-built control from the same upstream revision. Median client wall time, including JVM startup, was **35.216 s** for one vanilla build, **38.466 s** for concurrent workspace A, and **38.452 s** for B. The pair finished in **38.466 s**, about **9.23%** longer than one vanilla build. Both modes used four CPU affinity slots and a 4 GiB JVM heap; vanilla had four action jobs and shared mode two per engine. Downloaded repositories and OS page cache were retained, while JVM graphs, action caches and execroots were reset. All outputs matched and tests passed. `duration-comparison.json` retains all three rounds and exclusions. These numbers characterize this target and host only.

The graphs remain duplicated: the last round retained **80,837** Skyframe nodes in vanilla and **80,847 per shared engine**, or **161,694** combined. Each engine loaded 206 packages and configured 17,486 targets. `graph-ownership.json` records these BEP counts. There is no shared base graph with workspace-specific changed subtrees; the follow-up below measures total live Java heap, rather than attributing retained memory to individual graphs.

## First immutable-payload reuse improvement

A small follow-up weakly interns equal `CompactSortedDirents` payloads and Starlark
line-offset arrays. Directory names and entry types form the complete directory
payload identity. Newline offsets form the complete line-table identity; filenames,
file sizes, AST nodes and bindings stay local. Neither interner holds a strong
reference to discarded payloads. This reuses Guava's existing weak-interner
mechanism and leaves evaluator ownership and invalidation unchanged.

One sequential live-heap trial built the same Agent target in A, then B, using a
fresh daemon for each binary. Each stage used two full-GC class histograms; the
second is reported. JVM/action settings and retained dependencies were identical.

| Measurement | Before | After |
| --- | ---: | ---: |
| One-engine live object bytes | 107.39 MiB | 107.58 MiB |
| Two-engine live object bytes | 169.52 MiB | 167.31 MiB |
| Added live bytes for engine B | 62.12 MiB | 59.73 MiB |
| Two-engine directory payloads | 4,249 | 1,729 |
| Two-engine graph nodes | 161,694 | 161,694 |

The after candidate retained 698 canonical line tables for 1,654 `FileLocations`
objects; neither canonical payload count grew when B was added. Raw two-engine
heap fell **2.21 MiB (1.30%)**, and B's increment fell **2.39 MiB (3.85%)**. These
are whole-JVM live shallow-object sums, including JVM housekeeping objects, not
retained-size attribution to the graphs or RSS. The single-engine total increased
0.18 MiB; JVM filler-object allocation varied by 0.21 MiB at that stage. This is a
small measured reuse improvement, not a broadly shared graph or a general memory
benchmark. `payload-reuse.json` retains raw snapshot totals, class counts, binary
hashes, limitations and validation results.

All **629 focused tests** passed across syntax, compact directory entries, glob
invalidation and directory/expression/statement serialization. All **15 native
acceptance checks** passed. The added case builds globs concurrently, changes both
names and a file into a directory in A, verifies B remains unchanged, and restores
A. Existing overlapping build/test, action-deduplication and cancellation checks
also pass.

One further cold-action comparison after the patch measured **36.668 s** for one
vanilla build and **38.931 / 38.943 s** for simultaneous A/B builds in one PID.
The pair executed exactly **15** actions across **30** spawn requests, with 15
cache hits; all fifteen digest pairs had one execution and one hit. Outputs
matched the preserved control and both test binaries passed. This single follow-up
is a functional/performance smoke check, not a new statistical speed comparison.
`payload-reuse-duration.json` records settings and timings.

The follow-up binary is
`/home/jgett/bazel-multiworktree-20261031/native-mvp/bazel-payload-reuse`, SHA-256
`c5d3867ac6a1d0c22a9c43c51edc0192c8cb670ead0f51c789227275f4d2b643`.
Source continues on `jsgette/native-multi-worktree-mvp`, based on commit
`7faa5a48e6c60b879a17b2bf3864b8ea7e82b2b7`. The original timing and validation results
elsewhere in this report describe the preceding candidate unless explicitly
identified as follow-up.

## Implementation

The existing native named-server flag routes authenticated commands through a daemon-owned registry to a complete, private `BlazeRuntime` and dispatcher per workspace. Each engine owns its evaluator, modules, output base, action state, repositories, command lock, logs and cancellation context. Native output ownership and pinned directory checks continue to fence replacement paths. Closing one engine drains it; closing the last engine terminates the daemon.

Process-wide crash ownership covers both runtimes. Startup-only services and hooks initialize once. Global graph interner pooling, profilers, debugger, worker and dynamic execution modules are disabled in this slice; command-scoped BEP and spawn logs remain available. The test-mode crash logger also installs only once.

For eligible spawns, the existing REAPI descriptor and disk CAS provide content identity, publication and output materialization. A bounded daemon registry holds only immutable action keys while local execution and synchronous publication finish. An equal request then reads the complete cached result into its own execroot. Uncacheable actions stay private. Cancelling a waiter leaves the producer running; a failed or cancelled producer releases its key so a surviving caller can retry. This does not yet run producer work independently of its originating command.

## Validation

All **14 native acceptance cases** passed against the final copied binary: one PID, bounded admission, divergent source builds at an execution barrier, identical-action sharing, warm A/B/A, environment divergence, configuration divergence, simultaneous tests at a barrier, ordinary failure isolation, same-engine locking, cancelled waiter, cancelled producer, scoped shutdown, and final process exit.

All **163 focused regression test cases** passed: 99 server tests, 21 native startup/protocol shell cases, 9 native ownership tests, 30 existing `RemoteSpawnCacheTest` cases, and 4 new flight-registry tests. The shell suite exposed and drove the fix for duplicate test-mode crash-logger installation. Its first run also required making its explicit installation-cache directory writable in the outer test sandbox.

The final C++ formatting-only pass rebuilt the candidate after the shell run. The resulting binary then passed all 14 acceptance cases and the cold Agent experiment; Java/native functional changes had already passed the focused suites.

## Run and scope

See `tools/multiworktree/NATIVE.md` for usage. The verified Linux executable is:

`/home/jgett/bazel-multiworktree-20261031/native-mvp/bazel-native-final`

From each worktree, use the same binary and startup settings:

```sh
/home/jgett/bazel-multiworktree-20261031/native-mvp/bazel-native-final \
  --output_user_root=/tmp/bazel-native-worktrees \
  --experimental_shared_server=demo \
  --host_jvm_args=-Xmx4g --host_jvm_args=-XX:ActiveProcessorCount=4 \
  build //path:target
```

Use `info server_pid` in both worktrees to verify attachment. The real Agent experiment used its unchanged Bazelisk/tools-bazel entry point and native rc parsing. Its small adapter normalized workspace-specific launcher PATH/PWD while forwarding the original values as command client environment; the adapter and commands are retained with the evidence.

Linux only; at most two retained engines; supported commands are build, test, info and shutdown. Other JVM startup environment/settings must match. Execution budgets are fixed at two jobs/two estimated CPUs/1920 MiB action memory per engine, not hard resource quotas. Remote execution, persistent workers, dynamic execution, sandbox reuse, global profiling/debugging, and process-wide metrics are excluded. Closed or failed engine slots require daemon restart before reuse. No idle eviction, automatic orphan recovery or disk-cache collection is provided. A JVM crash remains a shared failure domain. Shared Skyframe graphs and shared Starlark compilation are not implemented; only the bounded immutable-payload savings described above have been measured.

## Provenance and evidence

Source repository: `/home/jgett/bazel-multiworktree-20261031/bazel`, branch `jsgette/multi-worktree-phase0`, implementation patch based on `b62bbdbbd5047b9b72310f8acdca0c427144997a`.

Final binary SHA-256: `be6b153b7b233aa7a11eaf20c6363bda53e8de12cad0333bdb1e069d0e8b0966`.

Compact evidence: `docs/jsgette/measurements/multi-worktree-native-mvp-2026-10-31/`. Full experiment: `/home/jgett/bazel-multiworktree-20261031/native-agent-20261031/results/`. Final acceptance logs: `/home/jgett/bazel-multiworktree-20261031/native-acceptance-final/`. Windows task copies are in `native-mvp-results/`, with `native-mvp.patch` and the binary in `native-mvp-bin/`.
