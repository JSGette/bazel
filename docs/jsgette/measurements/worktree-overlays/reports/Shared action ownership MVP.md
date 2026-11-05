# Shared action ownership across workspace builds

Four concurrent Datadog builds compiled the same Go standard library **once** with an independent action owner, compared with **four times** using an ordinary shared HTTP cache. Killing the first build during compilation preserved the producer and delivered correct binaries to the survivors. Across two matched comparisons, the four-build batch finished 3.63–3.91 seconds sooner and used about 1.7 GiB less peak committed memory. The previous two-engine fork restarted the action when its producing build was cancelled. [Previous stress tests](<Concurrent Datadog Agent build stress.md>) · [Cancellation evidence](../experiments/datadog_action_ownership_cancel.json)

## What runs

A client opens a loopback connection for an action key. The first accepted request starts a producer in coordinator-owned scratch storage. Other live requests for that key join it. Disconnecting a client removes its subscription. For this MVP, an already admitted producer finishes even if its last subscriber disconnects. A later request can rejoin it or consume its completed result. This policy trades some work after abandonment for a simpler cancellation guarantee.

The coordinator bounds the number of distinct running producers across all callers. When its window is full, a request for a new key receives `busy` and the submitting client can retry. Equal-key requests still join. The service holds records for live operations and subscribers, atomically renames a successful output directory into a private result store, and releases the live key. A failed producer discards its staging directory, reports failure to attached callers and permits a new attempt. A local SQLite writer lock admits one coordinator process per result store. [Implementation](../experiments/shared_action.py)

Each store belongs to a trusted execution context. A server-side recipe must resolve immutable inputs independently of the submitting build, and consumers must materialize private copies before editing outputs. The generic coordinator accepts an opaque SHA-256 key. Eligibility and correct key construction belong to its adapter. Production use would need an explicit namespace for execution environment, toolchain and cache policy, plus an authenticated source of execution specifications.

## Cancellation, failure and admission

The same probe ran on native Windows and Ubuntu under WSL2. Producer start markers and a gate verified actual overlap. The clients ran as separate processes, and each successful delivery was checked against expected bytes. Both hosts produced the following results. [Windows records](../experiments/shared_action_probe_windows.json) · [Linux records](../experiments/shared_action_probe_linux.json) · [Reproduction harness](../experiments/shared_action_probe.py)

| Case | Observed behavior |
|---|---|
| 1, 2, 4, 8 and 16 clients request one action | One physical execution per group. A late client received the original bytes after a previous client edited its private output. |
| Kill the first requester or a waiter while eight are attached | Seven survivors received correct outputs from the same worker in each case. |
| Disconnect every requester | The live subscriber count reached zero. A new requester could join before publication or hit the published result afterward. |
| A worker writes a partial file and fails | Four callers received errors. One new execution served the retry group. The failed partial output was never published. |
| Window of two, eight distinct keys | Peak producer count was two. Six requests initially received `busy`. Another request for an already active key still joined. |
| Restart, store contention and graceful drain | A fresh process delivered a published result with zero executions. A simultaneous second owner was refused. Graceful shutdown closed admission and delivered both outstanding results. |

## Resident coordinator state

A separate process published 2,048 tiny results, admitted 128 simultaneous connections to one new gated key, and restarted with all 2,049 results on disk. The live registry was empty after both waves. The restarted service opened three historical results without executing a producer. This probe used one process for the coordinator and another for the clients. The 128 subscriptions were separate connections. [Probe](../experiments/shared_action_residency.py) · [Windows](../experiments/shared_action_residency_windows.json) · [Linux/WSL](../experiments/shared_action_residency_linux.json)

| Coordinator state | Windows traced Python allocations (KiB) | Linux/WSL traced Python allocations (KiB) |
|---|---:|---:|
| Fresh owner, empty store | 35.8 | 26.0 |
| After publishing 2,048 results | 82.7 | 68.9 |
| With 128 subscribers on one running producer | 9,331.7 | 1,128.7 |
| After closing all 128 clients and allowing 250 ms for transport cleanup | 132.3 | 100.7 |
| Fresh owner, populated store | 35.8 | 26.0 |

The first implementation used a new temporary directory and a `pathlib` path containing the digest for each action. On Ubuntu's Python 3.12, the owner retained **396.4 KiB** after publishing 2,048 results, with 328,199 bytes traced to the line that interns path components. The final implementation uses plain strings for digest paths and one reusable staging name per worker inside a unique coordinator directory. The Linux owner retained **68.9 KiB** after the same workload and **66.6 KiB** in a separate 8,192-result check. The fresh service opens a completed result by key without enumerating historical keys at startup. [Before the fix](../experiments/shared_action_residency_pre_fix.json) · [8,192-result check](../experiments/shared_action_residency_linux_8192.json)

Live connections still consume memory. With 128 attached subscribers on Windows, 8,395,904 bytes were traced to the asyncio Proactor socket transport's buffer allocation. A producer window bounds running keys while live subscriber count and transport buffers also contribute to memory use. These are single snapshots of allocations traced by Python after garbage collection. Native allocations, kernel socket buffers, compiler memory and Skyframe residency require separate measurements.

## Abrupt coordinator failure

The crash probe killed the coordinator after a worker created a partial staging file. A replacement owner saw no published result, retried and published correct bytes. Another restart got a completed-result hit. A compiler-like subprocess survived the killed coordinator on both hosts and overlapped the replacement execution. Its writes stayed in the abandoned staging directory. This gives atomic publication across the tested process crashes while leaving a concrete duplicate-execution case during recovery. Storage durability through power loss and reclamation of abandoned staging are outside this experiment. [Windows](../experiments/shared_action_crash_windows.json) · [Linux/WSL](../experiments/shared_action_crash_linux.json) · [Crash harness](../experiments/shared_action_crash_probe.py)

## Datadog integration

The Windows adapter lets independent stock Bazel 9.1.0 builds request the original Agent target `//pkg/ebpf:include_headers` at commit `85ad968bca856fa3d4e07b4d8e11961dabe6866f`. Two views use base inputs, one changes the source and one changes the BUILD rule. The workload uses the previously qualified pure-Go configuration. The adapter recognizes one measured Go standard-library action digest, `80a0c564683f8138fbea0013d87890f54f36d53523126916c80c24db1d7d1865`. Other actions use ordinary HTTP caching. Each client retains its own Skyframe analysis and output base. [Independent binary references](../experiments/datadog_compiled_stress_windows.json) · [Build harness](../experiments/datadog_action_ownership.py)

An HTTP action-cache lookup supplies a digest. For this one digest, the adapter has an explicitly registered execution recipe. The coordinator launches a separate Bazel process in its own pinned workspace. A small Starlark rule in that workspace exposes the actual standard-library output tree from the rules_go provider. The producer must log a local execution with the requested Bazel action digest. The adapter checks the ActionResult and reachable output data before publishing it. Each successful consumer must record a remote cache hit for that action, match its reference executable hash and pass an executable behavior check. A fallback local build cannot satisfy these gates. [HTTP adapter and output verification](../experiments/datadog_action_cache.py)

The matched control uses ordinary HTTP caching on the same four variants. The coordinated run has an extra producer JVM and the cost of verifying and transferring its standard-library tree. Both use eight logical CPUs and an 8 GiB aggregate committed-memory limit. The cancellation test additionally gates the producer on four simultaneous subscribers. Its wall time includes this artificial synchronization.

### Cancelling the first Datadog build

The first caller was acknowledged with exactly one subscriber and zero joins before any other lookup could attach. After all four joined, the harness located the actual running Go standard-library builder in the separate producer's process tree and killed just the original Bazel client and its JVM. The bridge observed its HTTP disconnection. The live subscriber count dropped from four to three with the producer still running.

The producer executed the registered Go standard-library action **once**. All three survivors recorded remote hits for that action, and a late request in a fresh workspace also got a hit. The source-edit, BUILD-edit and base executables matched their reference bytes and behavior. The verifier checked 451 distinct digest-referenced objects comprising 212,183,582 bytes, including directory messages embedded in the tree. The HTTP server recorded 1,445 successful CAS reads, zero CAS misses and zero handler errors. The four-client burst finished in 85.80 seconds. The late request ran separately. [Cancellation and build evidence](../experiments/datadog_action_ownership_cancel.json)

After the staging-path memory fix, the final candidate passed another end-to-end replay. This time the first admitted client was the source-edited view. Killing it dropped the subscriber count from four to three. The producer executed the action once in 25.57 seconds. The three survivors and the late source-edited build passed all binary and behavior checks, with 1,442 CAS reads and zero misses. The record contains the SHA-256 of the coordinator source that ran. [Final-candidate replay](../experiments/datadog_action_ownership_cancel_final.json)

An initial transport configuration failed this test. The registered producer survived, but the three consumers encountered remote download errors, retried their builds and executed the same standard-library action locally. Binary hashes alone would have passed. The successful series limited each Bazel process to eight HTTP connections, increased the server listen backlog and requested eager output downloads. These settings changed together, so this experiment establishes a working configuration without assigning causality to one change. [Failed preflight](../experiments/datadog_action_ownership_transport_preflight.json)

### Four healthy builds, same resource budget

Each arm started with fresh independent workspaces, output bases and an empty private HTTP cache. The first pair ran coordinated then control. The second pair ran control then coordinated. All 16 binaries matched their references and passed the behavioral check. None of these builds retried due to transient cache errors. Times measure the first client launch to the last client finish. Workspace preparation is outside that interval.

| Mode | Physical stdlib executions per round | Four builds, round 1 / 2 (s) | Peak aggregate commit, round 1 / 2 (GiB) |
|---|---:|---:|---:|
| Stock HTTP cache | 4 / 4 | 88.813 / 88.484 | 4.793 / 4.927 |
| Coordinator owns the registered action | 1 / 1 | 85.187 / 84.578 | 3.107 / 3.218 |

The control's four same-key standard-library executions overlapped for 40.27 and 43.30 seconds across the two rounds. The dedicated producer executed the action in 23.54 and 24.43 seconds. In each coordinated arm, the clients received four verified hits and all four still executed the separate Go toolchain helper. The coordinated runs served about 1,440 CAS reads each, compared with three and one in the control. CAS read counts represent requests, not bytes transferred. [Coordinated round 1](../experiments/datadog_action_ownership_natural.json) · [Control round 1](../experiments/datadog_action_ownership_control.json) · [Control round 2](../experiments/datadog_action_ownership_control_r2.json) · [Coordinated round 2](../experiments/datadog_action_ownership_natural_r2.json)

Removing three overlapping executions reduced total batch time by **4.1% and 4.4%** on this workload. Peak aggregate commit fell by **1.69 and 1.71 GiB**. These two matched observations characterize this fixture on this host. The extra Bazel process, analysis, publication checks and local HTTP delivery are included in the coordinated timings. Both timing pairs preceded the staging-path memory fix.

For the next integration, Bazel can supply an actual command and input Merkle tree to a worker through its remote execution protocol. That would remove the hand-registered recipe and the extra Bazel analysis of the producer target from the common path. The measurable acceptance conditions are the same one-execution count, surviving subscribers, correct per-view outputs, and end-to-end time and memory under a shared budget. The separate graph experiment is to evict and restore analysis state while preserving the previously measured warm edit latency. [Bazel remote spawn runner](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/lib/remote/RemoteSpawnRunner.java) · [Worker execution service](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/tools/remote/src/main/java/com/google/devtools/build/remote/worker/ExecutionServer.java)

## Reproduction and scope

Run the standalone probes from the research workspace with Python 3.11 or later. They use fresh private stores and require no network services outside loopback.

```text
python -B experiments/shared_action_probe.py --output ownership_acceptance.json
python -B experiments/shared_action_residency.py --output ownership_residency.json
python -B experiments/shared_action_crash_probe.py --output ownership_crash.json
```

For the compiled Windows test, provide Git, `psutil`, Git Bash, Bazel 9.1.0, the pinned Agent revision and the existing private reference results. A prepopulated download cache can be supplied with `--repository-cache`. Use distinct output names for the three modes and run them sequentially on an otherwise comparable host.

```text
python -B experiments/datadog_action_ownership.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references experiments/datadog_compiled_stress_windows.json --mode cancel --output ownership_cancel.json
python -B experiments/datadog_action_ownership.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references experiments/datadog_compiled_stress_windows.json --mode natural --output ownership_natural.json
python -B experiments/datadog_action_ownership.py --repo PATH_TO_AGENT --bazel PATH_TO_BAZEL --references experiments/datadog_compiled_stress_windows.json --mode control --output ownership_control.json
```

The generic service ran on Windows and Linux/WSL. The compiled adapter covers one Windows action key, with a private result store and an HTTP CAS sharing a single trial's lifetime. A production backend must retain or revalidate the referenced blobs before offering completed results. Native macOS, eviction of Bazel analysis nodes and whole-Agent compilation remain open.
