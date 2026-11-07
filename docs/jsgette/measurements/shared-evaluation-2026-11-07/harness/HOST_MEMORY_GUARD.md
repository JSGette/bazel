# External Windows memory guard

The Linux cgroup limit does not protect Windows from all WSL host-memory and
commit pressure. Run future cohorts through `run_guarded.ps1` on the Windows
host, outside WSL. This wrapper leaves `baseline.py` and frozen runtime copies
unchanged. It accepts any literal Linux argument array, including a command
that waits at a benchmark barrier before invoking the harness.

Write the intended Linux argv to a JSON file, for example:

```json
["python3", "-B", "/mnt/shared-evaluation-20261107/harness-v4/baseline.py", "--root", "/mnt/shared-evaluation-20261107/identical-n10", "--clients", "10", "--scenario", "identical", "--mode", "vanilla", "--binary", "/home/jgett/skycache-20261029/bin/bazel-vanilla", "--repository-cache", "/home/jgett/.cache/bazel/_bazel_jgett/cache/repos/v1", "--cache-state", "restart", "--run-id", "vanilla-restart-guarded-01", "--require-cgroup"]
```

Then run PowerShell 7 with a new evidence directory:

```powershell
pwsh -NoProfile -File ./run_guarded.ps1 `
  -CommandJson C:/task/commands/cohort.json `
  -EvidenceDirectory C:/task/evidence/guarded-cohort-01 `
  -RunId vanilla-restart-01 -MemoryMaxMiB 12288
```

`MemoryMaxMiB` is mandatory: the wrapper does not silently change the Linux
budget. It also sets CPUQuota=600% and MemorySwapMax=0 by default. The outer
guard samples Windows `GetPerformanceInfo` every 500 ms and aborts on the first
sample below either reserve: **3 GiB available physical memory** or **4 GiB
available commit**. These are conservative operational defaults, not measured
performance gates. The thresholds, polling interval, CPU quota, and one-hour
timeout are explicit CLI settings recorded in `policy.json`. Polling cannot
guarantee protection against an allocation burst between samples; choose a
scope budget with adequate host headroom before starting.

The wrapper creates a fresh random task scope. Before signalling it, it checks
the exact unit name, random description token, cgroup suffix, and previously
observed invocation ID. It never stops the WSL distribution or arbitrary
processes. A memory breach kills only this verified scope. A preflight breach
launches nothing. Cleanup ownership failures are recorded and do not authorize
signalling some other scope.

A small Python startup gate waits at most 15 seconds on a unique abstract Unix
socket. The guard releases the requested workload only after checking scope
ownership and host memory again. An abort during scope creation cannot release
a delayed workload. Cleanup checks the scope again after the launcher exits and
waits for an inactive scope after signalling it.

Evidence includes initial and periodic free physical/commit bytes in
`host-memory.jsonl`, exact command and policy, observed scope identity, streamed
stdout/stderr, and `result.json`. Status `infrastructure_abort`, exit code 75,
means the run is unsuitable as a completed architectural milestone measurement.
The interrupted harness may have partial evidence without its own final report.
Do not reinterpret such an abort as an evaluator performance or correctness
failure. `command_failed` preserves the command's exit code; `guard_error`
records watchdog/control errors separately. Do not put credentials in argv.

Run `pwsh -NoProfile -File ./test_host_memory_guard.ps1` for low-impact checks.
They sample native memory but launch no Linux command or benchmark.

The initial real smoke found ambiguous `wsl.exe` resolution and launched no Linux
command; resolution now selects one application path. Subsequent real Windows
tests completed a two-second Python sleep with verified ownership and cleanup
(2.66 seconds), and aborted a 30-second sleep at a two-second timeout with exit
75 and the owned scope verified inactive (2.41 seconds). These are control-path
checks, not benchmark measurements.

## Repository contents cache

Both the pinned comparator source and current fork implement
`--repo_contents_cache` (alias `--repository_contents_cache`). By default it is
`{--repository_cache}/contents`, so the harness's shared repository-cache path
already enables shared fetched repository contents when eligible. Adding the
flag alone does not establish a new optimization.

For controlled experiments an explicit **task-owned, shared** contents-cache
directory can be passed identically to every arm via
`--bazel-arg=--repo_contents_cache=/mnt/shared-evaluation-20261107/repo-contents`.
Keep it outside all workspaces and output bases. Prewarm and record it separately
from the download cache. Eligibility depends on repository-rule inputs and
validation; this cache does not guarantee deduplication of all simultaneous
repository setup. A cache population run is setup evidence, not a cold-cache
measurement.

`RepositoryFetchFunction.java` admits fetched contents only when the repository
reports reproducibility and its rule is non-local. Symlink eligibility is also
checked. Local repository rules and ineligible links can therefore still cause
per-output-base setup despite a shared contents cache.

Source: `RepositoryOptions.java` defines the cache and its defaults;
`LocalRepoContentsCache.java` describes predeclared-input hashes plus recorded
input validation; `BazelRepositoryModule.java` rejects unsafe cache placement.
