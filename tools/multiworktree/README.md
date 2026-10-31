# Concurrent worktree MVP (Linux)

This is the implementation plan's **private-process fallback**: one local Python
supervisor routes simultaneous `build` and `test` invocations to retained,
workspace-private Bazel JVMs. It also supports `info` and workspace `shutdown`.
It does **not** implement multiple BlazeRuntimes in one JVM, share Skyframe graphs,
or demonstrate a memory/performance improvement. The native
`--experimental_shared_server` flag remains single-engine; this wrapper gives
each worker its own name in that protected namespace.

Requires Linux, Python 3.11+, and a copied binary built from this checkout (with
the v2 process-lifetime ownership implementation). No Python packages are needed.
Ordinary Bazel remains unchanged.

## Start and use

Build and copy the candidate, then run a supervisor in a dedicated terminal:

```sh
bazel build //src:bazel-dev
cp bazel-bin/src/bazel-dev /tmp/bazel-mw-candidate
python3 tools/multiworktree/bazel_multiworktree.py \
  --state /tmp/bazel-mw-state --bazel /tmp/bazel-mw-candidate \
  --max-engines 2 --cpus 4 --memory-mb 4096 --heap-mb 768 serve
```

Use the absolute path to the script from each worktree. For example, in two
terminals, these commands can execute at the same time:

```sh
cd /path/to/worktree-a
python3 /path/to/bazel/tools/multiworktree/bazel_multiworktree.py \
  --state /tmp/bazel-mw-state build //your/package:target
```

```sh
cd /path/to/worktree-b
python3 /path/to/bazel/tools/multiworktree/bazel_multiworktree.py \
  --state /tmp/bazel-mw-state test //your/package:test
```

The supervisor ignores system, home, and workspace `.bazelrc` files for this MVP.
Pass build/test options explicitly, including `--repository_cache` if desired.
Startup settings belong to `serve`; arbitrary client startup options are not
supported. Build arguments and the complete invocation environment are forwarded
without shell interpolation. Input is noninteractive; stdout/stderr are streamed
as bytes, exit codes propagate, and Ctrl-C or client disconnection cancels the
request. Terminal resize forwarding is not implemented.

```sh
# Process identities, active engines, queue length and budget (no environment values):
python3 /path/to/bazel/tools/multiworktree/bazel_multiworktree.py --state /tmp/bazel-mw-state status

# Exit 9 instead of waiting for this workspace or a free engine slot:
python3 /path/to/bazel/tools/multiworktree/bazel_multiworktree.py --state /tmp/bazel-mw-state --no-wait build //:target

# Shut down only the engine for the current workspace:
python3 /path/to/bazel/tools/multiworktree/bazel_multiworktree.py --state /tmp/bazel-mw-state shutdown

# Drain all idle engines and stop the supervisor; exits 9 if work is active/queued:
python3 /path/to/bazel/tools/multiworktree/bazel_multiworktree.py --state /tmp/bazel-mw-state stop
```

SIGINT/SIGTERM to the supervisor cancels active/queued requests and attempts a
clean worker drain. Normal `stop` reaps the worker JVMs and marks the session
closed. A subsequent `serve` uses a fresh namespace. Idle slots are evicted by
least recent use when another workspace needs capacity. Commands for one engine
serialize; requests for a different engine can make progress. The first eligible
queued request gets admission, and at most 64 requests wait.

## Ownership and resource contract

* The default pool retains at most two complete engines, each with a private
  evaluator, modules, output base, execroot, external dependencies, action cache,
  worker/sandbox state, logs, and native command endpoint.
* Canonical workspace aliases select the same engine. Nested workspace markers
  select distinct engines. Workspace replacement fences ordinary requests;
  restoring the original directory permits shutdown. These are admission checks,
  not filesystem snapshots or protection against edits during actions.
* Environment changes drain and replace only that workspace's engine. This keeps
  the native conservative full-environment startup contract; it can reduce warm
  reuse even for benign shell environment changes. Explicit per-command flags
  such as `--action_env=NAME=value` work normally.
* Binary identity is fixed for a supervisor session. Copy a new binary to a new
  path and stop the old supervisor before changing its configured executable.
* Each engine receives `floor(cpus / max_engines)` action slots, loading threads
  and JVM active processors. A final Bazel invocation policy enforces `jobs`,
  `local_test_jobs`, and CPU/memory `local_resources` even when a command tries
  to override them. Parallel analysis is bounded by the engine count and JVM heap
  limits. Resource limits apply to this supervisor's pool, not unrelated Bazel
  processes on the host.
* Estimated action memory per engine is
  `floor((memory_mb - 256) / max_engines) - heap_mb`. The 256 MiB allowance is
  supervisor headroom. These are scheduling estimates, **not hard RSS/cgroup
  limits**: JVM non-heap memory, native allocations, action resource declarations,
  and arbitrary multithreaded subprocesses still require workload qualification.
* State must be owned by the invoking user with mode `0700`. Local UNIX sockets
  verify the connecting UID. Logical and physical supervisor ownership are
  reserved separately; native workers retain the existing process-lifetime
  output/control reservations. This is cooperative local coordination, not a
  hostile-user security boundary or cross-host locking scheme.

## Failure and recovery limits

An unexpected worker exit quarantines its slot. Other engines continue; the
supervisor does not restart against potentially orphaned work. A failed shutdown
also retains the slot. An unclean supervisor journal blocks automatic restart
of that state directory, even when its listener is gone. The journal records
worker registrations/PIDs and output roots without command arguments or environment
values. Original native worker ownership remains effective after supervisor death.

Automatic crash recovery and orphan-action cleanup are deliberately not part of
this MVP. Account for the recorded worker processes and their descendants before
starting another pool with a fresh state directory. Do not delete the journal to
force reuse. Old generation directories are retained for inspection; disk garbage
collection is manual. The full in-process ownership/global-state refactor, shared
immutable compilation cache, and DataDog performance qualification remain later
plan work.

## Verification

```sh
bazel test //tools/multiworktree:bazel_multiworktree_test
python3 tools/multiworktree/integration_test.py \
  --bazel /tmp/bazel-mw-candidate --repository-cache /path/to/repository-cache \
  --root /tmp/new-mw-acceptance-directory --result /tmp/mw-acceptance.json
```

The real-binary test requires a **new** root directory. It checks A→B→A warm state,
conflicting sources/dependencies/configuration/environment, a simultaneous build
and test at an execution barrier, same-engine queueing, running/queued/disconnected
cancellation, isolated failures/shutdown, enforced resource policy through BEP,
canonical/non-ASCII paths, idle eviction, directory replacement, clean drain,
worker crash quarantine, and refusal to replace an unclean supervisor. It retains
command logs and machine-readable results. This is a correctness acceptance test,
not a benchmark.
