# Concurrent builds over workspace overlays

The experiments use independent workspace views of the same Git commit with different uncommitted edits. Each Bazel client builds its own Skyframe analysis graph. A local remote execution worker shares eligible actions by digest and publishes their outputs for every subscribing client. [Problem statement](PROBLEM.md) · [Worker](experiments/reapi_worker.py)

On Datadog’s SNMP profile normalizer, four cold concurrent builds started 65 remote commands with independent execution and 23 with shared execution. Batch time fell from 189.67 to 149.64 seconds. At eight worktrees and a 12 GiB memory budget, the corresponding counts were 107 and 23, and batch time fell from 289.69 to 204.19 seconds. The completed binaries matched independent references and passed functional checks. The compiled comparison ran on Windows. The worker protocol was also exercised on Linux/WSL. [Direct execution measurements](<reports/Direct execution across Datadog worktrees.md>)

The earlier [lazy evaluation research](<reports/Lazy evaluation across workspace overlays.md>) and [Datadog discovery benchmark](<reports/Seekable discovery on the Datadog Agent.md>) investigate bounded discovery and reuse across overlays. The [ownership MVP](<reports/Shared action ownership MVP.md>) tests cancellation, failures and memory retained by a coordinator.

## Running the experiments

From the Bazel repository root, change to this directory before running the commands in the reports.

```sh
cd docs/jsgette/measurements/worktree-overlays
```

The reports link to their scripts and captured JSON results. Local user-directory prefixes in diagnostic text have been replaced with `<user-home>`. For reproducing the exact timed worker sources, follow the patch instructions in the direct execution report.
