# Phase-zero worktree baseline smoke

This stdlib Python harness tests real simultaneous Bazel commands in separate Git
worktrees. The tiny fixture is a correctness/measurement-pipeline smoke workload,
not evidence about large-repository analysis performance. It runs `build`, not
`test`, and requires Linux `/proc`, `/bin/sh`, and `/bin/cat`.

```sh
python run.py --bazel /workspace/tools/bazel-9.3.1 \
  --java-trust-store /workspace/tools/bazel-environment-cacerts \
  --state-dir /workspace/scratch/phase0-state \
  --results-dir /workspace/scratch/phase0-results \
  --concurrency 1,2,4 --repetitions 2
```

State/results paths must be separate new empty directories. The harness marks
them, never deletes them, and shuts down only servers it started at explicit
owned output bases. Each worktree retains its output base throughout its group;
the groups share disk and repository caches. The initial prime is an ordinary
cold command. Each concurrency group then starts with new output bases and warmed
disk cache, followed by warm repeats and source/BUILD/`.bzl`/negative-glob/glob/
symlink and context changes. Every mutation is followed by a baseline restore
build on the same servers, including restoring command options. Warm and mutation
cohorts reject missing/restarted servers using both PID and process start time.
Warm cache status is a setup intention: inspect BEP/profile for
actual action-cache hits. Repetitions include the first invocation after an edit;
do not aggregate it blindly with its warmed repeat.

All Bazel rc files are ignored. Heap is 512 MiB per server by default (768 MiB is
optional), loading threads are one per command, and aggregate configured jobs
are four. Launches use a thread barrier. Recorded launch spread and overlapping
client-command intervals describe actual overlap; they do not prove simultaneous
CPU execution. The fixture uses one Starlark action and a small Starlark message
toolchain, with no compiler toolchain.
Source/dependency acquisition by Bazel may still need network access.

Context cases exercise separate inputs to loading, analysis, and execution:

| Case | Actual Bazel input varied | Expected effect |
| --- | --- | --- |
| `module_override` | Local module override selects `dep_a` or `dep_b`, with equal module name/version | Loaded module value differs |
| `repository_mapping` | Apparent `@selected` maps to a different module | Identical `.bzl` load text resolves to different values |
| `toolchain_divergence` | `--extra_toolchains` chooses a message toolchain | Resolved `ToolchainInfo` message differs |
| `environment_divergence` | Explicit `--action_env=PHASE0_MESSAGE=...` | Shell action reads a different environment value |
| `configuration_divergence` | `--define=phase0_message=...` | Rule reads a different configuration value |

Module/mapping/toolchain choices alternate between worktrees; environment and
configuration values are unique per worktree. The output oracle includes these
values independently of Bazel's output. These are synthetic context checks, not
coverage of arbitrary repository rules, production compiler toolchains, test
environments, or the full lifecycle matrix.

The optional `--java-trust-store` passes an existing Java trust store to the
bundled JVM and records its checksum; it never disables TLS verification. See
the parent README for the managed-environment CA setup. The fixture is not a
hermetic toolchain benchmark: `/bin/sh` and `/bin/cat` are host tools, recorded
with resolved paths and checksums in the manifest.

The runner enables Linux child-subreaper behavior before subprocess creation.
During shutdown it reaps only the known daemon PID after checking process start
identity. This handles containers whose PID 1 leaves exited JVMs as zombies,
without consuming unrelated client exit statuses. Failure to establish this
cleanup mechanism aborts the run before starting its build servers.

The output oracle is defined from the fixture's intended input state, never from
actual output. Every command must exit zero and produce exact expected bytes.
Distinct server PIDs are required within each cohort. The fixture commit and
template source digest are retained in the manifest; each result records its
expected source-state specification plus actual input-file hashes and symlink
targets from a fixed fixture allowlist. Missing optional paths are explicit.
Nonempty BEP/profile files and overlapping command intervals for every member
are required. Shutdown failures or remaining server processes set
`cleanup_failed` and cause a nonzero harness exit.

## Artifacts (schema version 1)

* `manifest.json`: binary path/version/SHA-256, fixture Git commit/source digest,
  harness digest, options, nonsecret environment allowlist and cgroup limits.
* `fixture/`, `harness-run.py`, `verify_results.py`: frozen copies of the runner,
  verifier and fixture used by this run, preserving symlink targets.
* `<cohort>/<worker>/command.json`, `command.log`, `bep.jsonl`,
  `profile.json.gz`, `result.json`: exact argv/cwd, raw Bazel output, native
  telemetry, start/end clocks, elapsed time, exit/timeout, read-only PID samples,
  source-state specification, actual input hashes/symlink targets and
  expected/actual content hashes and bytes.
* `<cohort>/server-rss.jsonl`, `cohort.json`: roughly 50 ms samples of only the
  owned Bazel server JVMs, aggregate sampled RSS, launch spread and overlap.
  Client/action child RSS is **excluded**; short peaks can be missed. PID process
  start ticks are included to identify PID reuse.
* `shutdown-*.log`, `summary.json`: shutdown outcomes, all cohort records, failure
  reason and limitations. Failed builds still preserve their evidence.

Focused harness tests: `python -m unittest -v test_harness.py`. These assert failure
detection, exact-output checking, state reset and preservation of existing files.
They do not replace the native run.

## Representative DataDog runner

`representative.py` runs the pinned three-target DataDog cohort through Bazelisk
and its unmodified `tools/bazel` wrapper. Supply a full checkout at the revision
in `../representative-workload.json`, the source-built control, and Bazelisk:

```sh
python3 representative.py \
  --repository /home/jgett/bazel-multiworktree-20261031/datadog-agent \
  --bazel /home/jgett/bazel-multiworktree-20261031/control/bazel-control \
  --bazelisk /home/jgett/.local/bin/bazelisk \
  --state-dir /path/to/new-state \
  --results-dir /path/to/new-results \
  --concurrency 1,2,4 --repetitions 30
python3 representative_results.py /path/to/new-results
```

It creates detached worktrees from the pin, retains the repository rc, rejects
an unexpected `user.bazelrc`, and ignores system/home rc files. A minimal explicit
environment selects the control with `USE_BAZEL_VERSION`, provides an owned HOME
and XDG cache, disables remote-cache auto-selection, and contains no CI/private
credentials. Command options explicitly clear the remote cache and executor.
The workload's `.bazelversion` is unchanged; the control override is intentional
and its checksum is recorded.

The runner pins itself and descendants to four available logical CPUs, allocates
four aggregate jobs and 6 GiB of aggregate estimated action-memory resources,
uses one loading thread per invocation, and sets each server's heap to 2 GiB.
These memory settings are estimates and JVM caps, not an OS-enforced group limit.

The matrix first primes dependencies and executes the three tests. For each
concurrency group it starts fresh output bases with the primed caches, executes
tests uncached, excludes one warmup, records the requested number of warm batches,
then exercises different target sets and a return to the full target set. A final
diagnostic build and live heap histograms precede owned-server shutdown.

`representative_results.py` requires successful BEP completion, every requested
test target, positive run counts, and local XML evidence with actual passing test
cases. Uncached execution phases reject cache-only passes. Test logs/XML are
copied before shutdown; cached warm runs remain labeled as cached. Case identities
must match across worktrees; only incidental XML timestamps/durations/order are
excluded from that comparison. Missing targets, all-skipped tests, mismatched
cases, changed server identities, incomplete cohorts, and failed cleanup reject
the run. Generated module lockfiles are retained after the run.

Diagnostics retain JSON traces, forced-GC memory profiles, BEP graph/phase metrics,
and approximately 200 ms PSS/RSS samples of observed clients, servers and their
descendants. Histograms record live objects' shallow sizes after GC, not deep
ownership or total process memory. PSS sampling can miss brief processes/spikes;
trace spans can nest or overlap and must not be summed as exclusive CPU time.

Warm latency batches disable JSON tracing, forced-GC profiling and the process
sampler. A blocking process wait plus a separate timeout watchdog avoids the
up-to-50 ms exit-detection delay of Python's `wait(timeout=...)`. BEP and wrapper
overhead remain included. Thirty warm batches expose variation but do not prove
JIT steady state, p95 performance, or a shared-daemon improvement. Groups run in
the specified order, with common caches; future comparisons need interleaved,
matched candidate/control arms. The first WSL pilot used the polling wait and is
retained for correctness evidence only.
