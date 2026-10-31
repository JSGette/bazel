# Concurrent worktree MVP execution report

Date: 2026-10-31

The concurrent MVP is implemented as the plan's **private-worker-process fallback**.
One local supervisor retains and routes simultaneous build/test requests to
complete, isolated Bazel JVMs. A real build and test both reached an execution
barrier before either was released, and both subsequently completed successfully.
A separate overlap cohort verified cancellation, ordinary failure, and workspace
shutdown without terminating the other engine's running test.

This completes a usable operational MVP, **not the native in-process multi-runtime
or shared-computation design**. The C++/Java binary is unchanged from the previous
ownership commit. The existing native `--experimental_shared_server` flag still
admits one engine; the new wrapper allocates a distinct protected native name to
each private worker. No JVM-count reduction, memory saving, throughput improvement,
or DataDog performance qualification is claimed.

## Implementation and interface

[The launcher and usage contract](../../../tools/multiworktree/README.md) are in
`tools/multiworktree/`. Production code uses only the Python standard library.
The supported platform is Linux with Python 3.11 or newer; verification used
Python 3.12.3 on Ubuntu/WSL2. Ordinary Bazel startup remains unchanged.

The supervisor provides:

- Canonical workspace routing, retained warm engines, separate output/execroot,
  external-repository, graph/module, local-cache, sandbox/worker and cleanup state.
- A bounded engine pool (default two), per-engine serialization, FIFO admission
  among eligible requests, independent progress, bounded queued requests, and
  least-recently-used idle-engine eviction.
- Byte-preserving stdout/stderr streaming, exit status propagation, and queued,
  running, disconnected-client and supervisor-signal cancellation.
- Workspace-scoped shutdown and an explicit idle-only supervisor `stop` command.
  Normal shutdown waits for the JVM to exit and reaps it before the slot is reused.
- Final invocation policies for action jobs, test jobs, loading threads and
  CPU/memory resource scheduling. Direct command-line overrides cannot widen the
  configured budget; canonical BEP options verify the effective values.
- Fixed binary identity, workspace directory pins, sticky replacement fencing,
  same-user UNIX-socket admission, logical/physical supervisor reservations, and
  reuse of the native workers' v2 process-lifetime ownership implementation.
- A durable journal written before worker launch, without arguments/environment
  values. Unexpected worker exit quarantines its slot; an unclean supervisor
  journal refuses automatic restart. Existing workers retain their native ownership
  after supervisor death. No live output base is silently handed to a fallback.

The full invocation environment is preserved. A change drains and replaces only
that workspace's worker, rather than relaxing the previous native environment
compatibility gate. The MVP disables system/home/workspace RC files and accepts
explicit command flags. This is a material interface limitation; representative
DataDog qualification must preserve its RC contract and has not been repeated
through this wrapper. Noninteractive commands only; terminal resize forwarding is
not implemented.

The host budget is a bound on this supervisor's admitted engine count and Bazel
resource accounting. JVM heaps are capped. Action memory and the 256 MiB supervisor
allowance are estimates, not hard process RSS/cgroup limits; native/non-heap
allocations and arbitrary action subprocesses still require workload qualification.
Unrelated Bazel processes are outside the pool's accounting.

## Why use worker processes now?

The existing plan expressly permits private worker processes as an early routing
milestone/fallback. The runtime audit found concrete in-process blockers:

| Boundary | Evidence and remaining work |
| --- | --- |
| Runtime/crash identity | `BlazeRuntime.Builder.build` calls `BugReport.setRuntime`; a second non-test registration fails the one-runtime precondition. |
| Graph interners | `InMemoryGraphImpl` installs graph-owned global SkyKey/Label pools; evaluator cleanup clears global pools. Multiple retained graphs need explicit ownership or disabled pooling. |
| Profiling | `Profiler.instance()` is process-global, and runtime command completion stops it. Concurrent attribution/cleanup needs refactoring. |
| Source rendering and JNI callbacks | Runtime setup replaces the global EvalException source-reader supplier; modules register native callbacks during workspace initialization. These need explicit lifetime/owner review. |
| Resources | `ResourceManager` is already instance-based in this revision. The earlier shorthand describing it as a singleton was incorrect. Aggregate admission/integration across independent engine managers still needs design. |

The fallback isolates these mutable runtime assumptions by process. It does not
claim to resolve the native Stage 4 audit or permit arbitrary objects to be shared.
Stages 2/6 (immutable compilation sharing) and the full native stages 3–5 remain open.

## Verification

The [retained evidence](../measurements/multi-worktree-mvp-2026-10-31/) contains
commands, copied-binary checksums, unit XML/logs, full acceptance results, individual
command/server logs, and validation metadata.

| Gate | Result |
| --- | --- |
| Source build and packaged launcher | `//src:bazel-dev` and `//tools/multiworktree:bazel_multiworktree` build successfully; packaged launcher and Bazel match source build hashes. |
| Focused unit suite | 21 test methods; 29 XML entries including parent/subtest entries; zero failures, errors or skips. |
| Packaged real-binary acceptance | 14 checks; 46 logged client commands plus status polling and process-lifecycle operations. All expected exit statuses asserted. |
| Parallel success | Both build/test actions reached a barrier before release and both completed successfully. |
| Isolation | Different source, local dependency, configuration and environment values; A→B→A retains PIDs and performs zero new package loading/target configuration without rerunning the output action. |
| Lifecycle | Running/queued/disconnected cancellation, same-engine queueing, nonblocking rejection, failure/shutdown isolation, idle eviction, replacement fencing, clean stop and signal drain. |
| Faults | Worker crash fences only its slot; supervisor crash refuses restart without mutating retained worker metadata; original workers explicitly shut down/reaped by the fixture. |
| Resource policy | Attempts to request 999 jobs/CPUs/threads are overridden. BEP reports two jobs/loading threads/test jobs and CPU=2/memory=1408 MiB per engine for the tested 4-CPU/4096-MiB pool with 512-MiB heaps. |
| Formatting and hygiene | Repository Pyink settings and Buildifier pass; production formatting preserves its AST; no MVP worker processes remain after verification. |

The logged client exits are 34 successes and expected negative cases: two busy
rejections (9), three cancellations (8), one missing target (1), one failing test
(3), four lifecycle rejections (36), and one deliberately killed/disconnected
client (-9). These are assertions in the acceptance harness, not ignored failures.

Three fixture preflights are retained: the custom test rule needed a `_test`
suffix; warm Bazel reports one internal action rather than zero total processes;
and Starlark strings require literal UTF-8 rather than JSON `\u` escapes. These
were test-fixture corrections. The completed packaged acceptance includes an
additional both-successful overlap cohort before its cancellation cohort.

## Artifacts and boundaries

Implementation base: `7e6dc36a6fa1aaba1b837dbf112ae0e1bb0347b7`.

Packaged artifacts:

- `/home/jgett/bazel-multiworktree-20261031/mvp/bazel`
- `/home/jgett/bazel-multiworktree-20261031/mvp/bazel_multiworktree.py`
- `/home/jgett/bazel-multiworktree-20261031/mvp/integration_test.py`

Bazel SHA-256: `bfa4248a617329a65e22d5f0843f1e7e56272931ff4ab0712278069c77e639f9`.

Supervisor SHA-256: `a802d7572cecf2b110458460e98199b78533d161f3013e3fe920bae84d907eb9`.

Full [provenance](../measurements/multi-worktree-mvp-2026-10-31/provenance.json) and
[validation](../measurements/multi-worktree-mvp-2026-10-31/validation.json) are retained.

Crash recovery, orphan-action cleanup, output-generation garbage collection,
transparent RC/startup compatibility, hard memory enforcement, cross-host/network
namespace coordination, shared immutable compilation, and native in-process
concurrency remain outside this MVP. A process exit is not proof that arbitrary
orphaned actions have stopped. Failed lifecycle operations retain/quarantine
ownership instead of automatically reusing those outputs. Restoring original
workspace objects permits scoped shutdown; normal work requires a fresh engine.
