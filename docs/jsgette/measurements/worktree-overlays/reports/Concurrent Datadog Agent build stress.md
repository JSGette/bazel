# Concurrent Datadog Agent build stress

The tests exposed three issues relevant to a multi-overlay MVP. Four stock Bazel processes sharing a disk cache each compiled the same Go standard library, with more than 41 seconds of simultaneous duplicate execution. The native fork coordinated identical actions through a shared registry, but cancellation of the producing build caused its surviving subscriber to restart the action. Concurrent Windows launchers also raced while creating a common missing output-user-root directory. Correctness checks on completed selected-target builds passed across the tested source edits, BUILD edits and reversions.

## Workload and controls

Every overlay starts from Datadog Agent commit `85ad968bca856fa3d4e07b4d8e11961dabe6866f`. The Windows tests use its pinned Bazel 9.1.0. They extract committed Git objects into private directories and retain the original module, lockfile, relevant BUILD files, rule implementations and sources. Edits apply to the private directories. These are selected Agent targets from a sparse projection. A complete Agent distribution build exercises a much larger graph. [Pinned Agent sources](https://github.com/DataDog/datadog-agent/tree/85ad968bca856fa3d4e07b4d8e11961dabe6866f) · [Projection and independent output oracles](../experiments/datadog_stress_target.py)

The cold Windows runs submit the same eight requests every time. Four views have base target inputs, two edit source and two edit the BUILD rule. Each view has a distinct unrelated draft file. At capacity one the clients run serially. At capacity eight all eight can run together. Every request starts its own batch Bazel JVM with a fresh output base. Within a cohort the clients share one initially empty disk action cache. Download and installation caches have already been exercised by the isolated reference builds. OS file caches are left in place. The late request starts with another fresh output base and the preceding eight-way cohort's action cache. Each suite checks the actual output bytes against three isolated builds, which are themselves checked against independently derived digests. [Cold-run harness](../experiments/datadog_build_stress.py)

The host runs Windows 11 Pro on a Ryzen 9 9950X3D with 32 logical CPUs. A Windows Job Object restricts the entire measured cohort to the same eight logical CPUs and 8 GiB of committed memory. In the generator matrices, each JVM has a 640 MiB heap limit. Bazel's job slots, loading threads, advertised CPU resources and JVM processor count divide the eight CPUs among active clients. The compiled-tool tests use 768 MiB heaps and two Bazel execution slots per client under the same aggregate job cap. Batch time runs from first client launch to last client exit. Client latency excludes time queued for a launch slot. Peak committed memory comes from the kernel. Peak working set is the sampled sum across processes, can count shared pages more than once and can miss short spikes. These memory measures include the entire processes. [Resource controls and BEP/profile reader](../experiments/bazel_stress_metrics.py)

## Cold header generation

The original `//rtloader:constants_gen` expands a template into a C header. The base, template edit and substitution edit produced three verified answers of 209, 235 and 209 bytes. Bazel reported two internal actions and zero external spawns per build, including the late request. This workload exposes concurrent startup and analysis costs. The next workload exercises disk caching of an external process. [Windows build records](../experiments/datadog_build_stress_windows.json)

| Concurrent clients | Eight builds (s) | Median client (s) | Median analysis (s) | Peak commit (MiB) | Peak working set (MiB) |
|---:|---:|---:|---:|---:|---:|
| 1 | 42.25 | 5.23 | 1.23 | 425 | 329 |
| 2 | 24.34 | 5.90 | 1.46 | 769 | 657 |
| 4 | 15.84 | 7.32 | 2.08 | 1,421 | 1,099 |
| 8 | 14.70 | 14.20 | 2.89 | 2,421 | 1,874 |

All eight clients actually overlapped at capacity eight. Moving from four to eight saved 1.14 seconds for the batch and added about 1,000 MiB of peak commit. Each matrix build reported 4,179 Skyframe nodes at completion. Median execution time was 20.5–26.5 ms across the four cohorts. The JVM, module setup and analysis dominate this particular target. These are single fixed-work batches at each setting. [Raw timings, action counts and profiles](../experiments/datadog_build_stress_windows.json)

## Cold external-action generation

The original `//pkg/ebpf:include_headers_src` executes a shell command to generate the Go source for the Agent's header processor. The three views returned 6,166, 6,173 and 6,172 bytes, each checked against an independent transformation of the pinned source. Two rounds ran capacities 1, 2, 4, 8 and then 8, 4, 2, 1. All 64 matrix builds, three references and one late request succeeded. [External-action results](../experiments/datadog_spawn_stress_windows.json)

| Concurrent clients | Eight builds, round 1 / 2 (s) | Median client, round 1 / 2 (s) | Peak commit, round 1 / 2 (MiB) | External executions / disk hits, round 1 and 2 |
|---:|---:|---:|---:|---|
| 1 | 30.44 / 30.58 | 3.77 / 3.69 | 420 / 422 | 3 / 5; 3 / 5 |
| 2 | 19.55 / 18.73 | 4.78 / 4.52 | 786 / 790 | 3 / 5; 3 / 5 |
| 4 | 12.67 / 11.91 | 6.25 / 5.51 | 1,441 / 1,463 | 3 / 5; 3 / 5 |
| 8 | 11.78 / 11.00 | 11.49 / 10.76 | 2,532 / 2,425 | 4 / 4; 3 / 5 |

At eight concurrent clients in round one, the two source-edited views executed the **same full action digest twice**. The recorded local-process intervals overlap by approximately 73 ms. One started at 22:11:25.539 UTC and ran for 164 ms. The other started at 22:11:25.630 UTC and ran for 98 ms. The same-key outputs were correct. Every other cohort executed one producer for each of the three distinct action keys and consumed five cached results. The late request consumed a disk hit. These observations show that a shared completed-result cache can still permit simultaneous duplicate execution. The second eight-way round illustrates the effect of request timing. [Per-request execution logs and BEP counters](../experiments/datadog_spawn_stress_windows.json)

The recorded keys are stable across capacities and distinct across the three variants. Timing instrumentation was added before the eight-way runs. Earlier cohorts retain action digests and execution/cache counts. Cached records can carry the original producer's timestamp, so only local-process records were used for the overlap calculation. The two rounds characterize this workload on this host. A large compile/link workload will have different action durations, resource needs and overlap opportunities.

## Editing four live workspaces

Four persistent stock Bazel servers each built the header and `//pkg/ebpf:include_headers_src`, which generates Go source by running an external command. Each server retained its own output base and action cache. Cross-server disk and remote action caching were disabled for this test. The same four distinct JVM PIDs stayed in the governed job for all eight bursts. All 32 requests succeeded, all 64 artifacts matched their independent byte oracles and all four servers shut down cleanly. [Wave records, PIDs and output hashes](../experiments/datadog_incremental_stress_windows.json)

| Four requests per burst | Last client finished (s) | External processes executed |
|---|---:|---:|
| First build in all four workspaces | 27.225 | 4 |
| First unchanged rebuild | 2.345 | 0 |
| Second unchanged rebuild | 0.323 | 0 |
| Divergent source and BUILD edits | 0.487 | 3 |
| Rebuild the divergent views unchanged | 0.291 | 0 |
| Rotate edits among workspaces | 0.440 | 3 |
| Restore base files | 0.364 | 2 |
| Rebuild restored views unchanged | 0.262 | 0 |

The four equal cold requests each executed the generator. All 16 unchanged requests later ran zero external processes, including the slower first rebuild. By the last wave each server still reported 3,893–3,894 Skyframe nodes. Peak job commit was 4,003 MiB and the highest sampled sum of working sets was 2,089 MiB. File edits were applied between waves. This tests invalidation and reversions across concurrently used workspaces. Snapshotting a workspace during an arbitrary editor write is a separate problem. [Warm harness](../experiments/datadog_incremental_stress.py) · [Warm measurements](../experiments/datadog_incremental_stress_windows.json)

An earlier cold start exposed a Windows launcher race. Three of four clients exited with code 36 while creating the same missing output-user-root directory. They reported Windows error 183 and completed zero targets. Preparing the common directory and extracting the Bazel installation once before starting the four servers removed the race in the measured run. The failed attempt is retained separately in the results. [Bootstrap diagnostics](../experiments/datadog_incremental_stress_windows.json)

## Action ownership and cancellation on the native fork

On Ubuntu under WSL2, two workspaces shared one JVM from the archived branch executable, SHA-256 `c5d3867ac6a1d0c22a9c43c51edc0192c8cb670ead0f51c789227275f4d2b643`. A controlled rule hashed 129 real Agent eBPF headers. A gate held the producer inside its action body. For all nine equal-key cases, a pre-release JVM thread dump showed the follower blocked inside `SharedActionExecutionRegistry.acquire`, reached from `RemoteSpawnCache.lookup`. A disk hit after the fact alone would not establish that the second request had reached the running action. [Harness and gate](../experiments/datadog_native_stress.py) · [Thread stacks, runner logs and output hashes](../experiments/datadog_native_stress.json)

| Gated case | Repetitions | Action body starts per pair | Outcome |
|---|---:|---:|---|
| Identical requests | 3 | 1 | Both clients received the correct 129-header manifest. |
| Edit one header and introduce a subpackage in one workspace | 3 | 2 | Both bodies reached the gate concurrently. The views returned 95 and 129 entries. |
| Cancel the waiting client | 3 | 1 | Producer completed. A late request got a disk hit. |
| Cancel the producing client | 3 | 2 | The survivor restarted the action. A late request got a disk hit. |

All six deliberate cancellations returned exit 8. The 24 successful build requests matched their independently computed manifests. A third workspace was rejected with exit 36 before being admitted to the two-engine server. The gate is an instrumented test action built from Agent inputs. Its timings would not represent an unmodified Agent rule. [Native results](../experiments/datadog_native_stress.json)

The same fork also built both original Agent targets in two live workspaces through four pairs of requests. Base/base, source/BUILD, BUILD/source and restored base/base all returned the expected artifacts, for 16 verified outputs. The external generator ran three times for three different actions. The other five requests obtained disk hits, including consuming a variant previously produced by the opposite workspace. These runs used local copies of five previously extracted Git dependencies. The fork's module resolution upgraded three dependencies relative to the original module requirements. Their names and warnings are retained in the results, so this arm supports output correctness and sharing observations under that configuration. Performance comparisons between the fork and stock 9.1.0 would need version-matched dependencies and controls. [Original-target native runs](../experiments/datadog_native_stress.original.json)

## Compiled Agent tool

The original `//pkg/ebpf:include_headers` builds the Agent's Go header processor. Each run configured 11,415 targets across 162 loaded packages. The default configuration failed because the configured GCC executable for `runtime/cgo` was absent. The successful runs use `--@rules_go//go/config:pure=true`, which is suitable for this tool's pure-Go implementation. The failed configuration and its diagnostic are retained. [Compiled-tool results](../experiments/datadog_compiled_stress_windows.json)

Two independent cold builds, with separate workspaces and output bases and disk action caching disabled, each executed seven external processes. Their 2,100,736-byte executables were byte-identical. They took 50.51 and 49.99 seconds of client wall time. A source edit in one workspace and a BUILD edit in the other then rebuilt concurrently, overlapping for 9.141 seconds. Each executed its generator, Go compilation and linker. Each produced a different binary. All four executables processed a small C input and skipped precisely the header selected by their variant. Peak aggregate job commit was 1,396 MiB under the eight-CPU, 8-GiB budget. [Executable fingerprints and functional outputs](../experiments/datadog_compiled_stress_windows.json) · [Compiled probe](../experiments/datadog_compiled_probe.py)

Four additional fresh workspaces then built base, base, source-edit and BUILD-edit variants simultaneously, sharing one initially empty disk action cache. All four executables matched the earlier private reference bytes and passed the functional test. The batch took **89.015 seconds**, with all four clients overlapping for 88.546 seconds. Peak job commit reached **4,988 MiB**, or **4.87 GiB**. The four builds executed 27 external processes and consumed one disk-cache hit. [Four-way compiled results](../experiments/datadog_compiled_cached_stress_windows.json)

| Action with an identical full digest | Local executions of that digest | Individual execution durations | Overlap among all copies |
|---|---:|---|---:|
| Go toolchain helper build | 4 | 15.85–17.75 s | 15.655 s |
| Go standard library | 4 | 41.87–43.98 s | 41.601 s |
| Compile the two base-view packages | 2 | 59–74 ms | 56 ms |
| Link the two base-view binaries | 2 | 490–494 ms | 490 ms |

This is direct evidence of expensive simultaneous duplicate execution on the same host with a common disk action cache. Digest equality, local runner records and reported execution intervals all agree. The counts include eight copy actions whose records omit digests. The equality claims above cover the actions with recorded digests. [Action records and timestamps](../experiments/datadog_compiled_cached_stress_windows.json)

The first attempt at this four-way arm reproduced the missing-directory startup race. Two launchers exited 36 and two compiled successfully. The retry precreated the installation root and extracted Bazel with a setup-only help command, then verified that the shared disk action cache was empty before launching the four target builds. Both attempts are retained in the final record. The result measures one bounded four-way compile burst. Additional repetitions and larger Agent binaries would be needed to characterize throughput broadly. [Bootstrap preparation and prior attempt](../experiments/datadog_compiled_cached_stress_windows.json)

## What this means for the next MVP

The four-way compiled run identifies action coordination as a concrete first intervention. Its common toolchain and standard-library actions consume tens of seconds concurrently in separate processes. A registry can address that duplicated execution independently of how analysis stores its graph.

The build request should subscribe to an eligible action whose identity includes its real inputs, command and execution context. In the tested fork, one live key already coordinates execution and subsequent publication across workspace engines. The observable ownership gap is cancellation of the originating build. A surviving subscriber currently restarts the producer's action. The next ownership experiment should detach producer lifetime from any individual build request, retain it while another subscriber needs it and release its admission slot only after completion, failure or agreed cancellation. [Cancellation evidence](../experiments/datadog_native_stress.json) · [Current registry](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/lib/remote/SharedActionExecutionRegistry.java)

An admission limit must cover all builds together. Separate JVMs can shorten the fixed batch, but the small cold workload exhibits diminishing returns by eight clients and a growing memory footprint. The fork's two retained engines also impose an explicit limit on workspace count. Generalizing the action scheduler to arbitrary subscribers and paging workspace/evaluator state are separate changes, with separate tests.

Warm graphs gave subsecond edit and no-op batches for these selected targets. A lazy evaluator needs to preserve that behavior while reducing retained graph state. This series measures node counts and whole-process memory. A bounded resident graph, eviction/recovery of continuations and whole-Agent compilation remain further experiments. Linux measurements here run under WSL2. macOS execution remains untested.

## Reproduction

Run from this research workspace with Python 3.11 or later, Git, and a Datadog checkout containing the pinned commit. Use a Bazel 9.1.0 executable or Bazelisk for the Windows tests. `psutil` supplies working-set samples. The Windows job budget remains enforced independently of those samples.

```text
python -B experiments/datadog_build_stress.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --output experiments/datadog_build_stress_windows.json
python -B experiments/datadog_build_stress.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --target-module datadog_spawn_target --rounds 2 --output experiments/datadog_spawn_stress_windows.json
python -B experiments/datadog_incremental_stress.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --output experiments/datadog_incremental_stress_windows.json
python -B experiments/datadog_compiled_probe.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --output experiments/datadog_compiled_stress_windows.json
python -B experiments/datadog_compiled_probe.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --concurrent-cached --references experiments/datadog_compiled_stress_windows.json --output experiments/datadog_compiled_cached_stress_windows.json
```

The native test requires Linux, the archived fork executable matching the recorded SHA-256, its installation or a location where it can extract itself, and a repository cache. The cache and Python temporary directory must share a filesystem because the harness hardlinks immutable archive blobs into private scratch storage. Set `TMPDIR` on that filesystem when necessary. The gated test can use a previously extracted `platforms` repository for offline resolution. The original-target arm also takes an external-repository directory containing the five pinned Git dependencies listed in its result. Both arms create their own workspaces and stop their own daemon.

```text
python3 experiments/datadog_native_stress.py --repo PATH_TO_AGENT --bazel PATH_TO_FORK --repository-cache PATH_TO_DOWNLOAD_CACHE --install-base PATH_TO_FORK_INSTALL --platforms-repo PATH_TO_PLATFORMS --output experiments/datadog_native_stress.json
python3 experiments/datadog_native_stress.py --repo PATH_TO_AGENT --bazel PATH_TO_FORK --repository-cache PATH_TO_DOWNLOAD_CACHE --install-base PATH_TO_FORK_INSTALL --original-git-modules PATH_TO_EXTRACTED_GIT_DEPENDENCIES --output experiments/datadog_native_stress.original.json
```
