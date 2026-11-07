# M1 native comparator preflight

2026-11-07; read-only audit of fork HEAD `db6c816b8235afec966941e0ecd898c28c2dc5f1`. This refines [m1-probe-design.md](m1-probe-design.md); M0 is still in progress. No source changes, Bazel commands, cache writes or runtime changes were performed. Paths below are repository-relative unless absolute.

The smallest experiment compares the same newly built fork binary in private and native modes. It measures the **native-mode bundle**, not JVM/JIT sharing alone: several mode-specific services and interning policies cannot be matched with command flags. Begin at N=1/2, then change the admission cap only after two-engine qualification.

## Admission patch and acceptance scope

- `src/main/java/com/google/devtools/build/lib/server/CommandServer.java:304`: replace the literal `2` with one named `private static final int MAX_RETAINED_WORKSPACE_ENGINES = 10`; derive the diagnostic at line 305 from it. The constructor already counts the first engine (line 499). Preserve synchronization, existing-slot lookup, failed-slot accounting, namespace validation and factory failure handling (lines 293–323). Closed slots stay in `sharedEngines`; failed slots stay in `failedEngines` until restart.
- `src/main/java/com/google/devtools/build/lib/runtime/BlazeServerStartupOptions.java:151`: update the two-engine help text to ten **retained** engines. No new public sizing option or eviction policy is needed for this experiment.
- `tools/multiworktree/native_integration_test.py`: add `--expected-engine-cap` (default 2 preserves the unpatched qualification). Lines 60, 193–214, 372–391 and 394–417 hard-code workspace creation, admission, final shutdown and cleanup/result counting. Create cap+1 workspaces, admit exactly cap to one PID with distinct output bases, reject cap+1 with exit 36, and shut down **all** admitted engines. Keep the substantive a/b barrier, action-sharing, mutation, cancellation and same-engine-lock cases; replace the `len(checks) == 15` success condition with an explicit expected check set when adding checks. Merely changing the third-engine assertion leaves extra engines alive and invalidates the last-shutdown test.
- Keep lifecycle checks explicit: closing an extra engine must not admit a replacement or reopen that engine; a fresh daemon must permit it. A focused `CommandServerTest` factory-failure case should verify that failed initialization consumes a slot, repeated attempts for that same failed key do not consume more, and another key is rejected at the total retained bound. The current test helper exposes `server()` and `setEngineFactory`; this needs no product test hook.
- Update `tools/multiworktree/NATIVE.md` for the experimental cap and loss of its former two-engine aggregate-admission bound. Do not edit historical measurement reports. Product scope is two Java files; verification/docs scope is the Python harness, focused server test, and native usage document. No C++ ownership, registry, scheduler or cache format change is required.

Relevant existing checks: `//src/test/java/com/google/devtools/build/lib/server:ServerTests`, `//src/test/cpp:shared_server_ownership_test`, `//src/test/shell/integration:shared_server_test`, and `//src/test/java/com/google/devtools/build/lib/remote:SharedActionExecutionRegistryTest`; preserve the native Python acceptance suite. The shell suite exercises incompatible startup settings, replaced directories, non-ASCII paths and ownership recovery. Its failed-startup case is not the same as a retained engine-factory failure.

## Matched benchmark flags and unavoidable differences

`BlazeCommandDispatcher.java:950–1015` appends final invocation-policy values after client policy. Native silently overrides conflicting ordinary execution/cache flags. Build the private control's policy from the following exact values and compare canonical BEP command lines, not only launcher argv.

| Setting | Native effective value / private control requirement |
| --- | --- |
| Resource admission | `--jobs=2 --loading_phase_threads=2 --local_test_jobs=2 --local_resources=cpu=2 --local_resources=memory=1920` per engine in both arms. |
| Execution | `--spawn_strategy=sandboxed,local --genrule_strategy=sandboxed,local --noreuse_sandbox_directories`; clear strategy, strategy-regexp and execution-platform strategy overrides. No worker/dynamic strategy. |
| Remote services | `--remote_executor= --remote_cache=`; leave remote downloader/output-service flags at defaults. |
| Completed action cache | One cohort disk CAS per arm, with independent equivalent populations. Native forces `<output_user_root>/shared_servers/v3/<name>/control/action-cache`; private processes should all receive their arm's shared `--disk_cache=<path>`. Do not share a live CAS between arms. |
| Cache behavior | `--remote_accept_cached --remote_upload_local_results --noremote_cache_async --remote_download_outputs=all --guard_against_concurrent_changes=full --cache_computed_file_digests=0 --noexperimental_non_deterministic_memory_optimizations`. |
| Repository downloads | `--repository_cache=<arm-download-cache>` is not forced by native; pass it explicitly. Seed equal archive-cache populations. |
| Repository contents | Native forces `<engine-output-base>/repo-contents-cache`; private must pass `--repo_contents_cache=<private-output-base>/repo-contents-cache` per engine. Defaulting to `<repository_cache>/contents` would share fetched contents only in the private arm. Alias: `--repository_contents_cache`. |
| Profiles | `--generate_json_trace_profile=no`; no `--profile`, `--memory_profile`, or `--starlark_cpu_profile`. Use BEP and external CPU/PSS/cgroup counters in both arms. |
| Startup | Identical copied binary and sanitized launcher environment; `--ignore_all_rc_files --noidle_server_tasks --host_jvm_args=-XX:ActiveProcessorCount=2`. Private heaps: 768 MiB each; native: N×768 MiB in one heap. Pin identical idle timeout and startup argument order within a native cohort. |

Native startup accepts only Linux build/test/info/shutdown, rejects explicit `--output_base`, batch/exec-server, delete-on-shutdown and user-cgroup mode (`src/main/cpp/startup_options.cc:798`, `src/main/cpp/blaze.cc:1974`). Use `--output_user_root` plus the shared name; obtain engine paths with `info output_base`. Private daemons may use explicit task-owned output bases. `PWD`/`OLDPWD` are excluded from native JVM identity; other launch environment values must match exactly. Record a digest and changed variable names, not raw environment values (`blaze.cc:1714–1782`).

Do not describe flag matching as perfect mode equivalence:

- `BlazeRuntime.java:1038–1081` disables global graph interner pooling and excludes CommandProfiler, CpuProfiler, AllocationTracker, SystemMemoryPressure, SystemSuspension, StarlarkDebugger, Worker, DynamicExecution, PackageMetrics, PostGCMemoryUseRecorder and GcAfterBuild modules, plus TraceProfilerService. `InMemoryGraphImpl.java:99` makes the pooling difference real. Private modules remain installed even when unused. Avoid extra product changes just to hide this difference; report it as residual attribution.
- Native skips global profiler initialization (`BlazeRuntime.java:401`), idle tasks (line 1455), per-command global output patching (`BlazeCommandDispatcher.java:693,942`) and RxJava reporter replacement (`remote/RemoteModule.java:571`). BEP memory/network/worker process metrics are absent/default in native (`metrics/MetricsCollector.java:355–395`); they are not zeros to sum across engines.
- `RemoteActionContextProvider.java:222` enables cross-workspace action flights only in native. `RemoteSpawnCache.java:122–135` keys flights by disk-cache path, digest function and action digest. The registry's 256-key bound is separate from engine admission. Fully cached/analysis-only qualification reduces this confound; subsequent execution measurements must report local executions, cache hits and duplicate digests.
- Each `BlazeWorkspace.java:348` creates a private `ResourceManager`; `buildtool/ExecutionTool.java:989` configures it per command. N=10 permits up to twenty jobs and 19,200 MiB of estimated action memory, despite the frozen outer 6-CPU/10-GiB/no-swap limit. `ActiveProcessorCount=2` controls each JVM's perceived CPUs, not aggregate action admission: native has one JVM's JIT/GC/common pools; private has N. There is no fair aggregate scheduler in this patch.

## Building a fresh same-fork binary after M0

Parent-selected storage: `R=/mnt/shared-evaluation-20261107`, existing task volume with approximately 193 GiB free as reported by the parent. Reserve a new subtree such as `$R/m1-native-build`; do not reuse an M0 output base, modify the active mount, or build on WSL root. A read-only check found `/home/jgett` on the root filesystem at 100%, with only 113 MiB available.

Cold source-build requirements, established without running Bazel:

- `.bazelversion` selects bootstrap Bazel **9.3.1**. Existing `/home/jgett/bazel-multiworktree-20261031/local-tools/bazel-9.3.1` exists (77,022,209 bytes). Copy/hash it into the new task subtree before use; pin its SHA-256 and version then. `/usr/bin/gcc`, `g++`, Python 3, `unzip`, `zip` and `git` exist; record versions before building.
- `.bazelrc:67–70` requires **Java 25** for target/tool compilation via `remotejdk_25`. Separately, `repositories.bzl:89–92` selects **Zulu JDK 27.0.0** for the embedded Linux runtime. `src/BUILD:324,354` makes `bazel-dev` use the all-modules JDK package. Do not conflate the compiling JDK with the runtime shipped inside the candidate.
- `//src:bazel-dev` packages the Java server, native client/JNI, process-wrapper, Linux sandbox, daemonizer, embedded tools and JDK (`src/BUILD:33–60,88–123,306–360`). Expect a full Java/C++ build in a fresh output base, including source-built protobuf/protoc (`.bazelrc:62`) and pinned Maven artifacts (`maven_install.json`), Java tools, rules/module archives, Python 3.11 and async-profiler. Historical 1–13 second incremental builds do not estimate this cold build.
- Existing `/home/jgett/bazel-multiworktree-20261031/control/repository-cache` contains `content_addressable/sha256` and `contents`. The exact embedded-JDK archive is present at `content_addressable/sha256/ccbc15c4edbedfdcc03c2d29a2aa2c6daf9e6ffb4cda7dd5ece0fcbb37350267/file`. Historical external entries for remotejdk25, Java tools, Python 3.11, async-profiler, protobuf, grpc and Maven exist; many resolve into that old `contents` cache. This is inventory evidence, not a complete offline-fetch proof.
- Seed a new **source-build download cache** from that archive store, independently of the Agent fixture cache. Reconstruct fetched repositories in a new contents cache under `$R`; do not point fresh `external/` entries at the old output tree or its GC-managed contents paths. Fresh compilation artifacts are still needed unless a verified matching action CAS is supplied. Missing archives may need network fetches using the pinned downloader/mirror and lockfile settings; no dependency upgrade or lockfile repin is authorized by this preflight.

Build-command sketch for later execution, with `S=$R/m1-native-build` and all directories prepared beforehand. Create `$S/build.bazelrc` from the pinned repository rc while excluding its optional `user.bazelrc` import; preserve required Java, Python bootstrap, protobuf, downloader, module-mirror and dependency checks. Supply the same outer resource guard as the experiment; a build is not a timed benchmark sample.

```sh
cd "$S/source"
TMPDIR="$S/tmp" "$S/tools/bazel-9.3.1" \
  --nosystem_rc --nohome_rc --noworkspace_rc --bazelrc="$S/build.bazelrc" \
  --output_user_root="$S/build-user" --output_base="$S/build-output" \
  --host_jvm_args=-Xmx3g --host_jvm_args=-XX:ActiveProcessorCount=2 \
  --host_jvm_args="-Djava.io.tmpdir=$S/tmp" \
  build //src:bazel-dev --jobs=2 --local_resources=cpu=2 \
  --local_resources=memory=4096 --repository_cache="$S/download-cache" \
  --repo_contents_cache="$S/repo-contents" --lockfile_mode=error \
  --repo_env="TMPDIR=$S/tmp" --action_env="TMPDIR=$S/tmp" \
  --build_event_json_file="$S/build.bep.jsonl"
```

Before execution, inspect that resolved source/build/cache/tmp/install paths remain under `$S` and preserve repository-required `.bazelrc` semantics. After success, copy the resulting `bazel-bin/src/bazel-dev` once to a pinned candidate path and use that exact hash for both arms. For a later cap-ten build, record the narrow patch, new source/hash and a distinct candidate. Do not modify or reuse a live installed binary.

The existing `/home/jgett/bazel-multiworktree-20261031/native-mvp/bazel-payload-reuse` (181,505,857 bytes) is a possible **diagnostic only** shortcut after M0: the prior report attributes SHA-256 `c5d3867ac6a1d0c22a9c43c51edc0192c8cb670ead0f51c789227275f4d2b643` to it, but this audit did not hash or execute it. Reverify before copying. It can expose launcher/environment/ownership issues without waiting for compilation; it cannot establish the current-source comparison, pass M1, or test a ten-engine patch.

## Measurable next steps, still gated

1. After M0 permits continuation and its daemons are stopped, inventory/hash source, bootstrap, seed cache and available task-volume space; create fresh build state. Fetch/build failures are infrastructure outcomes, not evaluator gate failures.
2. Build and hash the unchanged fork; run N=1/2 private/native analysis-only qualification and the two-engine acceptance harness. Require one native PID, distinct output bases, equivalent cache preparation/effective policies, no missing engines and matching output oracles. Measure tracing-free external CPU/PSS and explicit metric omissions.
3. Apply only the admission/docs/test patch above; rebuild, run boundary/failure/ownership tests and parameterized acceptance with cap=10. Explicitly observe engine overlap; ten registered PIDs/paths alone do not prove concurrent command progress.
4. Run matched N=1/2/10 restarted and warm cohorts, first with prepared actions, then source/BUILD/config/glob variants. Count residual action sharing, per-engine graph counts and external whole-daemon/resource totals. Preserve the fixed 6-CPU/10-GiB/no-swap budget and existing host guard; do not enlarge limits to accommodate the larger heap. Native's 7,680-MiB heap leaves only 2,560 MiB under that limit for all other charged memory.

No benchmark, admission change or milestone verdict is claimed by this note.
