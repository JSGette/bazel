# Bounded M0 matrix runner

`matrix-v7.json` declares 29 fresh rows: seven correctness references, restart comparisons
for seven fixtures, candidate-off anchors for identical N1/N10, warm identical
N1/N10 pairs, and a fresh-cache N1 pair. The source reference/comparison triplet
runs first to qualify the scenario interrupted by the v6 storage failure. There
are no imported v6 references or performance results. This is an
initial matrix, not a noise estimate or milestone verdict. Counterbalanced
repetitions require an explicit follow-up manifest if needed for assessment.

The preserved `matrix-v6.json` retains its original 29-row protocol, including
its explicit external N1 reference. The runner's default manifest remains v6
for compatibility; select v7 explicitly.

Run from PowerShell 7 on Windows, with the task filesystem mounted:

```powershell
./run_matrix.ps1 -Manifest ./matrix-v7.json -MemoryMaxMiB 10240 -BatchId m0-v7-plan -DryRun
./run_matrix.ps1 -Manifest ./matrix-v7.json -MemoryMaxMiB 10240 -BatchId m0-v7-01
```

Every invocation requires a new batch ID. `-DryRun` verifies local tool hashes and
manifest dependencies, and saves literal command arrays without reading runtime
files, launching Linux commands, or resetting caches. The memory cap has no
default. Use the same chosen cap for all matched arms; 10240 MiB supersedes the
earlier 12 GiB attempts. CPU quota remains six CPUs, with zero cgroup swap and the
guard's physical/commit reserves unchanged.

Actual execution checks the selected frozen harness and binary hashes. Each new cohort
runs through `run_guarded.ps1` sequentially. Before launching it, the pinned
`reset_task_mount.py` resets only the task mount, from a C-backed script/report
path, after the previous row's evidence has been checked and archived. The helper
refuses active task workloads; any reset failure stops the matrix. V7 stores its
repository cache and temporary files on the task filesystem; the original v6
repository cache on the WSL root is preserved. Each cohort's preparation then establishes
its declared warm/restarted lifecycle. Cold rows use fresh named caches and no
output-base pool; installed files and OS cache are not claimed fully cold.

Restart/warm rows use an explicit pool per fixture and binary mode. Pool bindings
prevent reuse after failures or changes in fixture, flags, binary, harness, or
resource limits. Nothing automatically repairs a failed pool or retries a row.
References must precede their consumers. An imported reference needs both an
explicit report path and guard directory; there is no report discovery.

Optional manifest `pool_namespace` selects the pool/SkyCache suffix (default
`v6`). Optional `temporary_root` passes the harness's `--temporary-root`; when
absent, the runner preserves the prior temporary-file behavior. V7 selects `v7`
and `/mnt/shared-evaluation-20261107/tmp-v7`, with the task repository cache at
`/mnt/shared-evaluation-20261107/repository-cache-v7`. The harness records the
temporary policy in reports and pool bindings. Every arm uses the same storage
policy; v6 and v7 performance observations must remain separate.

`matrix-v7b.json` keeps the v7 storage/harness policy and all 29 fresh rows, but
selects new `v7b` pools after the interrupted v7 attempt. Its optional Boolean
`require_budget_headroom` is true (default false for older manifests). After each
task reset, admission polls Windows every two seconds for at most 120 seconds and
requires two consecutive samples covering the full cohort cap plus each existing
guard reserve plus 512 MiB: 13.5 GiB physical and 14.5 GiB commit at a 10 GiB cap.
Each row records `<id>-host-budget-preflight.json` and its hash; timeout or error
stops before launch. Dry-run does not sample the host or import the guard. Select
this retry explicitly with `-Manifest ./matrix-v7b.json` and a new batch ID.

Evidence is written beneath `../evidence/<batch-id>` (override with
`-EvidenceDirectory`). `matrix-progress.json` records every selected row, current
status, hashes and errors. Reports must pass the existing summarizer's functional,
source, command-evidence, and cgroup checks. Reference-only rows may lack an
independent digest oracle and remain performance-ineligible. All measured rows
must satisfy every eligibility check. Missing or unsupported metrics remain
missing; no gate verdict is inferred.

Candidate-off shares the candidate binary and disables its cache flags, isolating
the feature only for the identical restarted N1/N10 anchors. Other vanilla versus
SkyCache cells compare different binary versions and do not alone establish a
cache-feature effect. The generic summarizer's strata do not encode all effective
resource limits, preparation or pool history: inspect this matrix's guard policies,
pool bindings and command evidence before treating trials as matched.

Successful reports are archived through `archive_run.py` and every member is
verified against its manifest. Original guard files, including JSONL samples,
are preserved in a separate verified ZIP with a `kind: host_guard` manifest;
there is no fabricated Bazel report. Failures stop immediately for assessment,
retaining progress, runtime logs, and any guard/reset evidence already produced.
