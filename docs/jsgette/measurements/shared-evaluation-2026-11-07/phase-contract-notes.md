# Existing phase overlap and the remaining experiment

Read-only source audit, 8 November 2026. This corrects the phase premise without changing the running M0 matrix or advancing M1.

**Bazel already overlaps top-level target analysis and action execution.** In the pinned vanilla source (`/home/jgett/skycache-20261029/bazel-vanilla`, `a5c50fcb`), `BuildRequestOptions.java:517` defaults `--experimental_merged_skyframe_analysis_execution` to true; the overlap percentage defaults to 100 at line 526. The preserved SkyCache source has the same relevant option, SkymeldModule, BuildDriverFunction and SkyframeBuildView files. Our fork retains those defaults at `src/main/java/com/google/devtools/build/lib/buildtool/BuildRequestOptions.java:551` and `:560`.

The effective mode is selected by `SkymeldModule`, not just the raw flag. It falls back to separate phases for `--nobuild` (including aquery/cquery), `--explain`, multiple package-path roots, a nonpositive CPU-heavy SkyKey pool size, an aquery-after-build dump, or `--check_licenses`. See `src/main/java/com/google/devtools/build/lib/skyframe/SkymeldModule.java:34`. The CPU-heavy pool normally defaults to host CPU count (`analysis/AnalysisOptions.java:126`). `buildtool/BuildTool.java:390` branches on the effective command-environment setting.

## What overlaps, and what still orders work

All following source paths are under `src/main/java/com/google/devtools/build/lib/` unless stated otherwise.

- `skyframe/BuildDriverFunction.java:175` first requests a complete configured target or top-level aspect. It then checks action conflicts (`:204`), compatibility and additional post-analysis dependencies (`:304`), before requesting that root's execution (`:316`, target-completion key at `:617`). Execution for one ready root can therefore proceed while other top-level roots are still analyzed.
- `skyframe/SkyframeBuildView.java:668` creates drivers for top-level configured targets and aspects. Its watcher threshold is **remaining top-level root count**, not elapsed analysis time or percentage of all graph nodes (`:708`). With 100% overlap the first concluded root releases queued execution; with 0% the watcher waits for all roots. The event logic is `analysis/AnalysisOperationWatcher.java:70`.
- The queue gate applies to stallable target/aspect completion keys; separate regular, CPU-heavy and execution pools remain (`concurrent/MultiExecutorQueueVisitor.java:105`; `com/google/devtools/build/skyframe/NodeEntryVisitor.java:252`). Workspace-status artifacts are also evaluated and explicitly excluded from the ordinary execution-time signal (`skyframe/SkyframeExecutor.java:2604`). This is not a claim that every subprocess waits behind the target-completion gate; repository rules can also execute tools during repository setup.
- There is still an all-analysis-finished callback that collects metrics, optionally clears analysis state, and disables further normal analysis (`skyframe/SkyframeBuildView.java:925`, `:971`). Exclusive tests run sequentially after the main evaluation (`:775`); coverage generation follows tests (`:804`).
- This is scheduling overlap of existing dependency contracts. It does **not** by itself allow arbitrary rule analysis to read generated action-output contents and then discover new analysis dependencies. Such an extension would require its own explicit read, cycle, invalidation and reproducibility semantics.

The normalizer workload usually requests one top-level target. Its configured-target subtree must be analyzed before its ordinary target-completion execution is requested, so it provides little opportunity to study overlap between independent top-level analyses. The auxiliary glob scenario supplies another root, but is not designed as an overlap benchmark. There can still be workspace-status work and analysis-finalization tails; “one root” does not imply every timestamp is disjoint.

## Implications for evidence and later work

The profile labels `runAnalysisAndExecutionPhase` (`buildtool/AnalysisAndExecutionPhaseRunner.java:97`) and `skyframeExecutor.evaluateBuildDriverKeys` (`skyframe/SkyframeBuildView.java:736`) cover the **combined** path. Their duration is not pure analysis CPU, and a label alone does not prove useful overlap occurred. Wall-time intervals can overlap; use node-category/thread CPU attribution and actual action intervals instead of adding or subtracting phase labels as disjoint costs.

Before changing architecture, a later controlled scheduling probe can hold merged mode on and compare `--experimental_skymeld_analysis_overlap_percentage=0` versus `100`, using multiple independent roots with deliberately different analysis/action costs. Keep the single-root workload as a limited-overlap control. Disabling merged mode entirely also changes orchestration, so it is a broader comparator.

A proposal beyond current Skymeld must name a stronger difference: execute a dependency's ready actions before its enclosing top-level analysis finishes; share immutable query results across snapshots/clients; make demand, readiness and cancellation independent of command-wide lifecycle; or introduce explicitly tracked execution-dependent queries. Each is a distinct contract and should earn its own correctness/performance gate. Merely “merge analysis and execution” would reproduce an existing default.
