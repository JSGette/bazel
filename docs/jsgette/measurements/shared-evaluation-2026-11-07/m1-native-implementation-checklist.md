# M1 native/private implementation checklist

Read-only patch map, 2026-11-08; inspected fork HEAD `4862608d940b49293c68c014e413ecc9dc98aa8e`. M0 is still running. This makes [the probe design](m1-probe-design.md) and [native preflight](m1-native-preflight.md) concrete; no product patch or run described below has been performed. Paths are repository-relative. Keep the frozen M0 harness/manifest unchanged; implement M1 measurement changes in a separately versioned entry point.

## Order and deliverables

1. After M0 permits continuation and its owned daemons have stopped, freeze a source snapshot and build an unchanged-cap candidate. Qualify N=2 correctness and accounting before raising admission.
2. Apply the two-file cap patch plus focused verification changes. Rebuild and pin a separate cap-ten candidate. Re-run N=2 qualification on this candidate, then N=10. All reported private/native pairs, including the final N=2 and N=10 comparison, must use the same cap-ten binary hash.
3. Keep source commit, source-tree/patch digest, bootstrap and candidate digests, effective policy, fixture manifest, cache provenance, harness digest, resource guard evidence and pool history together. Record any deviation before interpreting a result. Do not reuse the M0 safe-subset binary as the private control.

## Smallest product and acceptance patch

| File and current location | Intended edit / proof |
| --- | --- |
| `src/main/java/com/google/devtools/build/lib/server/CommandServer.java:283–323` | Introduce `MAX_RETAINED_WORKSPACE_ENGINES = 10`, replace `>= 2` at 304 and derive the diagnostic from it. Preserve existing-key lookup before admission, failed-key rejection, `sharedEngines + failedEngines` counting, synchronization, namespace checks and retained closed slots. No eviction or scheduler change. |
| `src/main/java/com/google/devtools/build/lib/runtime/BlazeServerStartupOptions.java:151` | Change help to ten retained private engines, explaining that closed/failed slots remain reserved until restart. |
| `src/test/java/com/google/devtools/build/lib/server/CommandServerTest.java:114–169,762–765` | Add a factory-failure admission test through the existing RPC stub and `setEngineFactory`. Pin the daemon instance ID with `ping`; form each output path as initial output-parent plus MD5 of workspace bytes, as required by `CommandServer:358–361`. First engine already consumes one slot. Fail initialization of nine distinct additional keys, repeat a failed key without another factory call, then require the next key to reject without factory execution. Check error code and reason, not just a nonzero exit. |
| Same test helper; `CommandServer.java:601–608,412–429` | Supply real temporary `EngineDirectories` for the initial engine when installing a factory. The ordinary helper passes null; shared cleanup dereferences directories and would otherwise fail independently of admission. Extend test-local helper paths if needed; no new product test hook. Preserve cleanup on every assertion path. |
| `tools/multiworktree/native_integration_test.py:29–60,193–214,372–417` | Add `--expected-engine-cap` (default 2 for initial qualification). Create cap+1 workspaces, admit cap, require one PID and cap distinct `info output_base` results, then reject cap+1 with exit 36 and the cap diagnostic. Preserve all existing a/b barrier, action-sharing, source/config/environment/glob, cancellation and serialization checks. Track admitted engines and shut down all of them; hard-coded a/b cleanup is insufficient at ten. Replace `len(checks) == 15` with explicit required check names and a run-completed flag. |
| Same acceptance harness | After closing one engine while another is live, reject both reopening its slot and admitting a replacement; the last remaining engine's shutdown must terminate the daemon. A new process may admit the previously closed workspace. Preserve the a/b shutdown-isolation case by closing extra engines first. Never perform unconditional cleanup calls that start a replacement daemon after a successful final shutdown; gate cleanup on the recorded live process identity. |
| Same acceptance harness; `case`:102–161 | Allow an explicit workspace list with a/b as the default, then add one cap-wide divergent-action barrier. Every admitted engine must reach its own ready marker before release; verify every output. Preserve the existing two-engine cases. This proves N=10 execution progress rather than merely ten registered or waiting clients. |
| `tools/multiworktree/NATIVE.md:3,34–39,62–70` | Update the experimental cap, acceptance flag and loss of the former two-engine aggregate-admission bound. Keep historical reports unchanged. |

Existing checks to run under the task guard after building: `//src/test/java/com/google/devtools/build/lib/server:ServerTests`, `//src/test/cpp:shared_server_ownership_test`, `//src/test/shell/integration:shared_server_test`, and `//src/test/java/com/google/devtools/build/lib/remote:SharedActionExecutionRegistryTest`; then the parameterized native Python acceptance suite. No C++ or cache-format edit is necessary. The shell test at line 59 deliberately verifies **no PID file exists inside an engine output base**.

The acceptance harness currently hard-codes `-Xmx3g`, `ActiveProcessorCount=4`, and a minimal environment without task temporary paths. Add explicit harness options for heap/processors/temp root or replace those constants with the qualification policy (N×768 MiB, two processors, stable task-owned TMP/TMPDIR/JVM temp). Apply identical startup options across its workspaces; keep these functional tests separate from benchmark timing.

## M1 runner changes: separate implementation, frozen M0 inputs

Add `harness/native_comparison.py` with tests in `harness/test_native_comparison.py`; reuse `fixture.py`'s source/oracle checks and the guard's ownership protocol. Do not route native mode through unchanged `baseline.py`. Use a new `harness/run_native_comparison.ps1` coordinator, with an immutable run manifest, rather than editing the active matrix.

| Existing assumption | Required M1 replacement |
| --- | --- |
| `baseline.py:508,510` permits N=1/10 and vanilla/SkyCache modes. | Explicit `hosting=private/native`, N=1/2/10, a pinned candidate, and no SkyCache-only flags. `fixture.prepare` already accepts two clients. Keep evaluator tracing disabled. |
| `baseline.py:300–301,324–330` invents `oNN` and always passes `--output_base`. | Private: explicit `oNN`. Native: one output user root/shared name and **no** `--output_base`; launcher derives `shared_servers/v3/<name>/engines/<workspace-hash>`. Register and verify paths during qualification/preparation. Keep every native client's launch environment/startup arguments identical. |
| `baseline.py:182–189` reads `<engine>/server/server.pid.txt`. | Private: retain per-base PID files. Native: poll `<user-root>/shared_servers/v3/<name>/control/server/server.pid.txt`; verify each worktree's `info server_pid` and `info output_base` map separately. Native engine count N and daemon count 1 are different assertions. |
| `Sampler.sample`:194–210 sums PSS per output base. | Resolve all expected engines, then sample each unique `(PID,start-time)` once. Deduplicate CPU/PSS/cgroup reads while retaining the full engine→daemon mapping. Reject missing, replaced, unexpected or outside-scope processes. Never accept N−1 engines merely because one PID was found. |
| `Sampler.loop`:218–220 waits after variable-cost reads. | Equal cadence in both arms; record timestamps, actual interval, read duration/count/failures, and `time.thread_time_ns()` around every initial/background/final read. Include a sparse-sampling qualification pair. Preserve raw scope and daemon CPU; observer CPU is diagnostic, not a subtraction that repairs perturbation. |
| `build`:365–407 verifies outputs before returning; `cohort`:421–436 times that work. | Worker futures initially return only process result/exit timestamps. Freeze a build-only window after all Bazel clients exit, before any BEP parsing, executable hashing or oracle execution. Retain a separate full instrumented end-to-end window. Required verification occurs afterward; any failure rejects the row. |
| `output_bases.Pool.require_stopped`:94–101 knows only `oNN` PID files. | Use an M1-specific binding/lease including hosting, shared name, native control root, actual engine map, heap/startup/environment digest, policy/cache paths, binary/fixture/harness hashes and history. Verify native control process stopped. Preserve no-reset/no-recovery semantics for failed or incomplete prior rows. |

Sampler tests must cover N engines mapping to one PID counted once, a missing engine, PID reuse with a changed start time, wrong cgroup membership, missing PSS reads and CPU baselines for pre-existing versus newly started JVMs. Runner tests should reject policy/binary/pool-history mismatch and prove that verification work starts only after the build-only boundary. These validate measurement meaning, not just implementation helpers.

**Restart trap:** do not run `info` immediately before a restarted timed build; it starts and warms the JVM. Verify stable output paths during preparation, discover the new daemon through its PID file during measurement, then confirm every engine through `info` after build-only counters stop. Warm qualification can issue matched identity calls before timing. Record those calls in both arms' histories.

## Effective policy to match

`BlazeCommandDispatcher.sharedInvocationPolicy:950–1014` is authoritative. Native appends FINAL values after client policy; compare canonical BEP options and expected absolute-path mappings, not argv alone. No product policy edit is needed to match the private arm.

```text
--jobs=2 --loading_phase_threads=2 --local_test_jobs=2
--local_resources=cpu=2 --local_resources=memory=1920
--spawn_strategy=sandboxed,local --genrule_strategy=sandboxed,local
--noreuse_sandbox_directories --remote_executor= --remote_cache=
--remote_accept_cached --remote_upload_local_results --noremote_cache_async
--remote_download_outputs=all --guard_against_concurrent_changes=full
--cache_computed_file_digests=0 --noexperimental_non_deterministic_memory_optimizations
--generate_json_trace_profile=no
```

Omit strategy/strategy-regexp/platform-strategy overrides, JSON/memory/Starlark profiles, remote downloader and remote output-service options so their defaults match native's reset policy. Keep BEP, fixture flags, compilation mode, lockfile behavior, test/environment policy and oracle requirements equal. Private receives its own per-engine `--repo_contents_cache=<base>/repo-contents-cache`; native forces this path. Each arm has one separate action disk CAS with equivalent preparation: native forces `<control>/action-cache`, private explicitly points every engine to its arm's shared CAS. Seed equal repository **download** caches, not one shared live contents cache. Do not introduce the Gazelle host-cache experiment into this comparison.

Use `--ignore_all_rc_files --noidle_server_tasks --max_idle_secs=300 --host_jvm_args=-XX:ActiveProcessorCount=2`, the same copied candidate, normalized launch environment and task-owned temporary directories. Private heap is 768 MiB per JVM; native heap is 1536 MiB at N=2 or 7680 MiB at N=10. Within a native cohort the JVM temporary path and launch TMP/TMPDIR must be identical; worktree-specific startup values can trigger identity rejection.

Residuals remain: native disables global graph interner pooling and several modules/process-wide metrics; eligible action flights can coalesce across engines; JIT/GC/common pools are JVM-wide; every engine still has a separate `ResourceManager`. Record missing native metrics as unavailable, and count executed actions/cache hits. This measures the existing native implementation bundle, not pure JVM hosting or shared graph evaluation.

## Build commands after M0 permits continuation

Re-inventory storage only then; earlier free-space observations are stale. Reserve `S=/mnt/shared-evaluation-20261107/m1-native-build` and place source, bootstrap, output/install/cache/tmp state beneath it. Copy/hash bootstrap Bazel 9.3.1 and seed a new download cache from verified archives. Preserve `.bazelrc` except its optional `user.bazelrc` import in `$S/build.bazelrc`; record the transformation. Java 25 compiles the server; `bazel-dev` packages JDK 27. A cold Java/C++/protobuf build is required unless matching compiled artifacts are independently verified.

Future Linux command body, run from a guarded script after paths/source/cache are prepared:

```sh
set -euo pipefail
cd "$S/source"
export TMP="$S/tmp" TMPDIR="$S/tmp"
BUILD_STARTUP=(--nosystem_rc --nohome_rc --noworkspace_rc
  "--bazelrc=$S/build.bazelrc" "--output_user_root=$S/build-user"
  "--output_base=$S/build-output" --host_jvm_args=-Xmx3g
  --host_jvm_args=-XX:ActiveProcessorCount=2 "--host_jvm_args=-Djava.io.tmpdir=$S/tmp")
trap '"$S/tools/bazel-9.3.1" "${BUILD_STARTUP[@]}" shutdown' EXIT
BUILD_OPTIONS=(--jobs=2 --local_resources=cpu=2 --local_resources=memory=4096
  "--repository_cache=$S/download-cache" "--repo_contents_cache=$S/repo-contents"
  --lockfile_mode=error "--repo_env=TMP=$S/tmp" "--repo_env=TMPDIR=$S/tmp"
  "--action_env=TMP=$S/tmp" "--action_env=TMPDIR=$S/tmp")
"$S/tools/bazel-9.3.1" "${BUILD_STARTUP[@]}" build //src:bazel-dev \
  "${BUILD_OPTIONS[@]}" "--build_event_json_file=$S/build.bep.jsonl"
"$S/tools/bazel-9.3.1" "${BUILD_STARTUP[@]}" test \
  //src/test/java/com/google/devtools/build/lib/server:ServerTests \
  //src/test/cpp:shared_server_ownership_test \
  //src/test/shell/integration:shared_server_test \
  //src/test/java/com/google/devtools/build/lib/remote:SharedActionExecutionRegistryTest \
  "${BUILD_OPTIONS[@]}" --test_output=errors "--build_event_json_file=$S/tests.bep.jsonl"
```

Copy/hash `bazel-bin/src/bazel-dev` to a new immutable candidate path after success, before any source/cap rebuild. Give each build/test phase distinct BEP/log evidence. The cap-ten incremental rebuild can retain this task-owned source-build output base; benchmark pools cannot change candidate hash in place. The old native MVP binary is only a launcher diagnostic shortcut, not M1 evidence.

## Guard and matched N=2 → N=10 run contract

- Before **each** build or cohort, mirror `run_matrix.ps1:15–45`: two host-headroom samples two seconds apart, at least 13,824 MiB available physical and 14,848 MiB available commit for a 10-GiB budget; record timeout without launch. Reuse `run_guarded.ps1` with `MemoryMaxMiB=10240`, `CpuQuotaPercent=600`, physical reserve 3072 MiB, commit reserve 4096 MiB, polling 500 ms, and `MemorySwapMax=0`. Its `CommandJson` is a literal argv array; a guarded build script needs no new launcher. `run_guarded.ps1` alone does not perform the larger budget-headroom preflight. Retain scope ownership evidence and final absence checks; archive aborts without treating them as completed rows.
- Generate independent same-binary private correctness oracles in a separate pool. Benchmark private and native arms start at the same successful pool run number, with equal install prewarming, repository-download seed and preparation counts. Stop/start does not erase mutable output state. Alternate arm order across repeated pairs; record actual order, not just intent.
- N=2 unchanged-cap qualification: acceptance barrier/isolation tests, one native PID/two outputs, policy audit, external sampler tests, then prepared `--nobuild` and full-build pairs. Analysis-only success cannot substitute for full output/behavior/glob checks.
- After the cap-ten rebuild and checks, repeat N=2 with that binary. Proceed to N=10 only after identity, isolation, accounting and guard qualification succeed. Require the cap-wide execution barrier and all ten outputs in acceptance; record overlapping client command intervals separately in the real-target cohort.
- **Restarted arm:** prepare one worktree, verify/map it, shut it down and prove daemon exit, then prepare the next, in both arms. In native this deliberately initializes only one engine per preparation daemon; do not pre-register all ten. After all preparations, launch N measured clients concurrently without intervening `info`, all inside the new scope. This matches the initial-build/shutdown history while excluding retained JVM state from measured startup.
- **Warm arm:** prepare all N sequentially while retaining their engines, with the same order and identity-call count in both arms; then launch N measured clients concurrently in that same scope. Use separate restarted/warm pools. Confirm every engine afterward, then shut down all N; native last shutdown must exit. Neither arm gets an extra oracle-generation cycle in its benchmark pool.
- Start with identical prepared actions, then source, BUILD, disjoint, configuration and glob variants using the same fixture/oracles. Report raw build-only/end-to-end scope CPU, distinct-daemon CPU/PSS, client/cohort wall, charged anonymous/file/kernel bytes, action/cache counts, mapping checks, sample overhead and lifecycle history. Do not add component peaks or call sampled PSS retained heap.

## Known reasons to stop or redesign this comparator

N=10's 7680-MiB maximum heap leaves only 2560 MiB under the hard 10-GiB cap for native allocations, clients, subprocesses and charged file memory. Private per-engine repository contents can make preparation large. The per-engine managers can admit twenty action jobs and 19,200 MiB of estimated action memory; this patch adds no aggregate scheduler. Host-budget/oom aborts require a recorded feasibility decision, not a larger hidden budget or a graph-sharing verdict.

Cold candidate compilation may require additional pinned downloads and substantial space/time. Unsupported native profiles/modules prevent an isolated JVM/JIT causal claim. Current native mode is Linux-only, keeps failed/closed slots until process exit and shares a failure domain. Any mismatch in output isolation, candidate provenance, policy or process accounting blocks performance interpretation; a valid but insufficient saving routes back to the plan's trace/replay experiment.

Protocol: only C-backed repository/document reads and this checklist write. No runtime scans, builds, product edits or changes to matrix execution.
