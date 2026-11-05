# Seekable discovery on the Datadog Agent

**Saving a byte offset paid off in a wide directory that was evicted repeatedly.** On Datadog's `releasenotes/notes` tree, two cursors reading the same warm directory index took median wall times of **176.7 ms with ordinal replay and 87.4 ms with byte seeking** at a 32-entry window. Both returned exactly the 3,954 paths found by Bazel. The other three workloads showed little difference between these two cursors. Eight concurrent overlay requests also showed that eliminating duplicate traversals does not necessarily shorten the time until the last caller finishes. [Single-request measurements](../experiments/datadog_seek_results_windows.json) · [Concurrent-overlay measurements](../experiments/datadog_overlay_results.json)

## What ran

The prototype stores each **visited** Git directory as an immutable, uncompressed tree object on disk when seeking is enabled. The cache is local and can be built on demand. A suspended reader retains the directory object ID and the byte offset of the next entry at each active depth. Reopening the file at that offset avoids decoding the prefix again. The index holds directory entries, not a build graph or a set of cached glob answers. Building the index still reads the whole first-seen directory, even if the first window needs only its first few entries. [Implementation](../experiments/seekable_tree.py)

Before the repository test, a small real Bazel 9.3.1 query supplied an independent list of paths. A worker stopped after 32 entries and exited mid-directory. A new process resumed at byte 741, after entry 19 of that directory. It returned the exact Bazel paths and recorded zero prefix entries replayed. In the timed trials, eviction closes directory readers and discards their buffers **within** a worker process. Git helpers, output digest and OS caches can remain live. The timed trials measure that cheaper form of eviction. [Gate and benchmark harness](../experiments/datadog_seek_benchmark.py)

The larger test read Datadog Agent commit `85ad968bca856fa3d4e07b4d8e11961dabe6866f`, containing 21,677 tracked non-directory entries. Datadog's pinned Bazel 9.1.0 evaluated the **original eBPF header filegroup in a complete detached checkout with its original module**. For the three stress cases, temporary filegroups ran real Bazel globs over actual Datadog subtrees, preserving nested package boundaries. Those three labels were introduced for the experiment. The reference was the digest and count of the exact sorted paths. Every measured arm had to reproduce both. The uncommitted changes in the user's checkout were excluded. [Native target definition](https://github.com/DataDog/datadog-agent/blob/85ad968bca856fa3d4e07b4d8e11961dabe6866f/pkg/security/ebpf/c/include/BUILD.bazel) · [Bazel oracle and repository inventory](../experiments/datadog_bazel_oracle.json)

All **36 workload and strategy combinations** returned the Bazel reference paths on native Windows and Ubuntu under WSL. The restart gate and logical replay counts agreed across hosts. The Ubuntu run used one timing round for portability verification. The timings below come from a separate nine-round Windows session. macOS remains untested. The source checkout's index checksum and pre-existing working-tree status matched before and after the trials. [Ubuntu WSL verification](../experiments/datadog_seek_check_wsl.json)

## When seeking pays

Windows wall times below are medians of nine fresh, uninstrumented Python workers per arm. All windowed columns use 32 entries. “Warm” means the unpacked directory objects were already present. “Empty” includes building them during the timed traversal. OS and Git caches were not flushed. In each round the order rotated. Process startup, reading inputs, computing the output digest and worker exit are included.

| Datadog workload | Entries visited / matched | Uninterrupted Git | Git + ordinal | Seek, empty index | Warm index + ordinal | Warm index + seek |
|---|---:|---:|---:|---:|---:|---:|
| Native eBPF headers, `**/*.h` | 138 / 129 | 128.4 ms | 128.8 ms | 108.7 ms | 79.3 ms | 79.8 ms |
| Dynamic instrumentation, `**/*.json` | 805 / 471 | 165.0 ms | 167.6 ms | 171.8 ms | 90.7 ms | 88.8 ms |
| Cluster Agent release notes, `**/*.yaml` | 325 / 324 | 111.5 ms | 112.5 ms | 105.1 ms | 78.2 ms | 78.3 ms |
| Agent release notes, `**/*.yaml` | 3,958 / 3,954 | 134.8 ms | 232.2 ms | 133.3 ms | 176.7 ms | 87.4 ms |

The last two columns isolate the resume mechanism. On the widest workload, ordinal replay decoded **243,719 earlier entries**. Seeking decoded zero. Bytes returned by index reads fell from 20,018,429 to 1,981,242. The index contained only 302,625 payload bytes, so even seeking read more bytes than the index contained. A reopening fetches a 16 KiB buffer and the next eviction can discard unread bytes. These counters measure bytes delivered by the reader, not physical disk traffic.

With a larger **256-entry window**, the same wide directory needed 30,681 ordinal replays. Warm ordinal and warm seek took **95.7 and 84.5 ms**. Seeking won all nine paired rounds at each window size on this workload. A separate earlier Windows session also found a large 32-entry-window difference, 187.1 against 95.0 ms. On the 129-header native target the larger window never evicted a reader. On the deeper dynamic-instrumentation layout, the traversal pruned 13 subpackages, and building the index on first use was slightly slower than uninterrupted discovery. [Earlier session](../experiments/datadog_seek_results_windows_session1.json)

The direct Git and indexed arms also differ in subprocess use. A direct ordinal reader can have one Git payload helper per active depth and must drain a partially read batch response to reuse the helper. A warm index needs fewer Git helpers. Consequently, attributing the entire Git-to-warm-index difference to byte seeking would overstate its benefit.

## Eight simultaneous overlay requests

Eight callers requested the release-note glob from eight private, uncommitted trees over the same commit. Four changed files **outside** the release-note package and shared its immutable subtree. Two added the same filename with different contents, one changed an existing file's contents, and one removed a file. The workload produced five distinct package-tree identities and three distinct output manifests. A barrier ensured overlap, and at most four producer processes could run at once. All callers received exact manifest bytes checked against an independent base listing plus the known edits.

| Five paired Windows trials, warm index, 32-entry window | Independent producers | Shared producers |
|---|---:|---:|
| Producer traversals | 8 | 5 |
| Directory entries visited | 31,665 | 19,791 |
| Bytes returned by index reads | 15,850,462 | 9,906,736 |
| Total manifest bytes delivered to callers | 1,566,216 | 1,566,216 |
| Median time for all eight callers to finish | 336.3 ms | 331.6 ms |

The conservative key included the package tree and fixed query semantics. Three callers joined an identical producer. Changes to file bytes can change the tree ID while leaving a names-only glob unchanged, so five producers were needed even though there were three distinct answers. Sharing removed about **37.5% of traversal work**. The observed **1.4%** median wall-time difference was small, and one paired round went the other way. The group timings include worker-pool startup, eight view lookups, result delivery and validation. Every timed group began with the required index objects already present. [Cohort harness](../experiments/datadog_overlay_cohort.py)

Separate 20 ms process-tree samples observed roughly **172 MiB** with eight producers and **148 MiB** with five. Each figure comes from one instrumented group. Short-lived Git processes can fall between samples and shared pages can be counted in several working sets. These observations do not establish a memory bound or a reliable memory saving. [Measurement method](../experiments/measure_process.py)

## Design decision

Keep byte-offset resumption as an option for wide directories that spill or are shared repeatedly. Keep direct streaming as the baseline for small or one-shot queries. The evidence favors an on-demand, per-directory index over a requirement to commit a repository-wide index. It does not yet identify a universal width threshold. A useful next comparison is to build an index **at the first expensive eviction**, then measure how often it pays back on an actual build trace.

This benchmark exercises source discovery of known globs over regular files and directories with fixed query semantics. The unpacked tree cache was private, trusted scratch storage. Bazel supplied the reference answers. Agent binary compilation, arbitrary Starlark evaluation, full-build makespan and Skyframe heap usage remain unmeasured. The results do not justify replacing Skyframe.

## Reproduction

Use Python 3.11 or later, Git, an Agent checkout containing the pinned commit, and Bazel or Bazelisk. The small gate uses Bazel 9.3.1. Regenerating the Agent oracle uses Bazel 9.1.0 and the Datadog target's external dependencies. The captured oracle can be used directly. Substitute the path to the checkout below. Outputs go to standard output unless `--output` is supplied.

```text
python -B experiments/datadog_cases.py --repo PATH_TO_AGENT --original-module --output experiments/datadog_bazel_oracle.json
python -B experiments/datadog_seek_benchmark.py --repo PATH_TO_AGENT --rounds 9
python -B experiments/datadog_overlay_cohort.py --repo PATH_TO_AGENT --window 32 --reps 5
```
