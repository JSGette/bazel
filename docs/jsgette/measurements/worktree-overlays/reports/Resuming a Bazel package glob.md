# Resuming a Bazel package glob across workspace overlays

**A build can pin a Git tree, pause partway through a directory, and recover the correct package and glob without keeping its traversal process alive.** In this experiment, eight overlay requests stopped with two saved directory positions each. All eight resumed in new processes and matched fresh Bazel queries. The cost appeared when the same directory was evicted repeatedly. Processing 141 entries in windows of 32 required rereading 276 entries from earlier windows.

Native Windows and Ubuntu under WSL produced identical recorded results apart from the host and tool versions. Both used Bazel 9.3.1, the version named by the source checkout. macOS is an intended platform for the same probe and remains untested. [Windows results](../experiments/bazel_glob_results_windows.json) · [Linux results](../experiments/bazel_glob_results_linux.json)

## What Bazel checked

One base commit and eight distinct uncommitted Git trees contain a real package, `//app`. Its filegroup uses `glob(["**/*.txt"])`. For each tree, the harness materialized a fresh workspace and asked a separate Bazel batch process for both the active BUILD file and the filegroup's source labels. The query used a fresh output base with repository downloads disabled. The Python cursor independently computed the same two answers from Git. The cursor processes were the ones paused and restarted. [Fixture and query](../experiments/bazel_glob_fixture.py) · [Bazel glob semantics](https://bazel.build/reference/be/functions#glob)

| Change relative to the base | Active BUILD | Matched files | Could reuse the base result? |
|---|---|---:|---|
| Base | BUILD | 9 | Yes |
| Add the higher-priority BUILD.bazel | BUILD.bazel | 9 | No |
| Add a matching source | BUILD | 10 | No |
| Delete a matching source | BUILD | 8 | No |
| Make an existing directory a subpackage | BUILD | 7 | No |
| Remove a subpackage's BUILD file | BUILD | 10 | No |
| Change a matching file's contents | BUILD | 9 | Yes |
| Edit below an existing subpackage | BUILD | 9 | Yes |
| Edit an unrelated file outside the package | BUILD | 9 | Yes |

The comparisons checked the exact sorted paths as well as the BUILD filename. For example, both implementations included `.dotdir/visible.txt` and excluded `.hidden.txt`. A byte change in a matching source can preserve this query's answer because the query asks for filenames. An action that reads the source contents needs a different key. A separate control changed the Starlark rule itself. The hint validator rejected it, the specialized cursor declined to interpret it, and Bazel returned only `root.txt`.

## What survives eviction

The cursor scans Git directories depth first and streams matches into SQLite. At a pause, it saves each open directory's path, immutable tree ID and next entry ordinal. The eight requests used at most two cursor workers at a time. Each process exited after its first 32 entries, inside a directory with at least 128 files. While all eight were parked, the harness changed an edit-session ref to a different tree. Resuming the old checkpoint against that new tree was rejected. Resuming against the original pinned tree returned its original answer. [Cursor](../experiments/bazel_glob_cursor.py) · [Restart and isolation checks](../experiments/bazel_glob_probe.py)

Each first pause held **two directory frames**. Encoding the pinned tree, query identity and two positions took **222 bytes**. The actual SQLite file was **36,864 bytes**, including its schema, partial output and validation observations. On the first resume, the eight requests replayed between 26 and 30 directory entries, including entries in a parent directory. The probe counts entries consumed by the scanner. Git may load whole tree objects internally, and the probe does not measure Git buffers, operating-system caches or process-tree RSS.

The repeated-eviction control made the tradeoff clearer. Five successive processes completed the base traversal in windows of 32, 32, 32, 32 and 13 new entries. To reach their saved positions, they replayed 0, 19, 51, 83 and 123 entries respectively. The final paths were correct, but the scanner consumed 417 entries to cover 141 distinct traversal positions. An ordinal is cheap to save and increasingly expensive to seek using a stream that starts at the beginning. This is rediscovery of directory entries. The experiment executed no compile actions.

## Local traces and committed hints

The probe saved the base result and its validation evidence in two places. One was a local SQLite row. The other was a **1,327-byte JSON file in a real Git commit** created with a private index. The source branch and the user's staging area were untouched. Both arms loaded the same evidence and applied the same checks. Storage location therefore tested how a result could be distributed without changing its validity rules. [Validation code](../experiments/bazel_glob_trace.py)

An unchanged directory tree ID proves the observed subtree is unchanged. When an ID differs, the validator can inspect just the facts that affected this glob, including matching names, directory membership, the selected BUILD definition and package boundaries. The base record served **three of eight overlays** and declined the other five. Blind reuse would have returned a wrong answer in all five. A new local record generated for the added-source overlay then served that overlay on its next request.

Validation itself can cost nearly as much as discovery. For the edit outside the package, one Git query returning one entry established a hit. For the source added to the wide directory, validation examined **139 entries before rejecting** the base record. Fresh fallback then visited **142 entries**. The two numbers count separate passes. The trace also contains the complete prior result and directory evidence, and the validator materializes that record. This experiment gives no reason to assume a committed hint reduces latency or bounds total resident memory.

## Decision and reproduction

The [Datadog Agent benchmark](<Seekable discovery on the Datadog Agent.md>) implements the seekable cursor and compares it with ordinal replay on a native Bazel target, three larger repository layouts and eight simultaneous overlay requests.

Keep the pinned-tree and resumable-query approach for the next benchmark. Compare the ordinal cursor against an uninterrupted traversal and a cursor that can seek to a chunk of an immutable directory. Vary directory width and eviction frequency on real package layouts. Measure prefix replay, bytes read, elapsed time and peak memory across Python, Git and storage processes. The main decision is whether building or loading a seekable index costs less than the replay it avoids. This slice establishes no need to replace Skyframe.

Run from the workspace root with Python 3.11 or later, Git and Bazel 9.3.1 on the path. Use `--bazel PATH` for a separately installed binary. The probe writes JSON to stdout by default and uses temporary repositories. Its tested scope is regular files and directories, fixed workspace options, one package and one known rule. Arbitrary Starlark, symlinks, ignored-package settings and full action scheduling need separate evidence.

```text
python -B experiments/bazel_glob_probe.py
```
