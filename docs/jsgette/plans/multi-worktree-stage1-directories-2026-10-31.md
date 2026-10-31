# Stage 1 continuation: physical directory identity at admission

Date: 2026-10-31

Status: implemented and tested on Linux/WSL2 for the single-engine prototype.
Stage 1 remains incomplete. This is directory identity checking at admission,
not durable cross-process ownership, snapshot isolation, or multi-engine support.

## Implementation

The shared-server startup path captures `EngineDirectories` before any runtime
or module initialization. It opens descriptor-backed `SecureDirectoryStream`
handles for the workspace and private output base, reads identities from the
handles, and verifies that the named paths still refer to those objects. On the
qualified local Linux filesystem, retained handles prevent deleted inode identities
from being reused while the old engine exists. Providers without secure directory
streams or non-null file keys fail closed. Final-component symlinks and relative
paths are rejected; the launcher supplies canonical paths. Bazel's internal path
strings are converted to the platform encoding before calling Java NIO; a real
client test covers non-ASCII workspace and output-user-root names.

The handles remain owned by the server-main resource scope through command drain
and runtime cleanup, and are closed when that scope exits. Partial capture failure
closes already-opened handles. This resource is a directory pin, not a filesystem
lock or a transferable engine-ownership lease.

The engine checks current directory identities:

1. Before exposing its RPC endpoint after runtime initialization.
2. On Run RPCs before command registration, preemption or dispatch.
3. After obtaining the dispatcher command lock, before consuming idle-task results,
   module callbacks or new command setup. This closes the queued-request gap.
4. After draining RPCs and stopping idle-task management, before invoking the
   runtime/dispatcher cleanup callback.

An identity mismatch, missing path or failed observation permanently fences ordinary
commands for that engine. RPC and dispatcher rejections report exit 36 with
`GrpcServer.ENGINE_DIRECTORIES_CHANGED`. Editing children of the same directory
continues to work. Restoring the original directory object does not revive ordinary
commands or reuse potentially stale graph state.

A shutdown request may proceed after both original directory objects have been
restored. Its normal runtime cleanup then operates on those objects, followed by a
fresh server on the next invocation. Shutdown against replacement objects rejects.
An idle/other server stop that reaches the cleanup callback while identities do not
match also refuses that callback and leaves registration cleanup failed, rather
than treating it as successful disposal. Cancellation remains available for commands
already in flight. Authenticated discovery remains available so clients receive the
specific rejection instead of treating the daemon as missing and replacing it.

Ordinary Bazel does not open these handles and retains its existing behavior. Startup
options are still parsed once; the runtime creation helper now accepts the already
parsed options so identity capture precedes initialization.

## Verification

The source build and **906 test cases passed**. One existing runtime test remains
skipped by its unchanged `@Ignore` annotation:
`SynchronizedOutputStreamTest.testHandlesArbitraryBinaryDataCorrectly`
(`b/154242266`, BES binary stdout/err handling).

| Target | Passed | Existing skips |
| --- | ---: | ---: |
| Java `ServerTests` | 89 | 0 |
| Java `RuntimeTests` | 790 | 1 |
| Shared-server client integration | 15 | 0 |
| Selected legacy client integration | 11 | 0 |
| Legacy server-mode SIGINT | 1 | 0 |

XML was inspected for exact counts, duplicate case identities, errors, failures and
skips. Added coverage includes workspace/output replacement, deletion/recreation,
missing paths, symlink substitution, child edits, sticky fencing, fresh-owner
creation, partial capture errors and idempotent close. RPC tests prove a rejected
request does not preempt an existing command, initialization against changed
objects does not expose an endpoint, and cleanup callbacks cannot run against
replacement objects.

The dispatcher test holds a real command lock, waits until a second request is
queued, replaces the output directory, then releases the first command. The second
request returns the fencing failure without executing its command or consuming
idle-task results. A following request proves the rejected command released the
lock.

Real client tests replace or remove the private output directory while the daemon
remains alive. The launcher may create a directory/lock at the old path, but the
engine rejects it. A replacement sentinel remains intact; command logs and execroot
are not initialized there. After restoring the original object, ordinary commands
remain fenced; shutdown and fresh startup succeed.

A separately copied development binary passed **11 smoke commands**, retaining the
initial PID/start ticks across a startup mismatch, rejecting work and shutdown against
a replacement output, maintaining the fence after restoration, and starting a fresh
engine after shutdown. Both daemons were adopted and reaped with status 0. The
replacement contained only the caller's sentinel and the client lock.

- Base commit: `416d65e7cc6e3f4b6936268cbeb355927d31ba95`
- Candidate SHA-256: `d02a4dd8925b7ded47d6d3affdd7688af6a24c94e1e741921bd53b1fe3823e0e`
- [Validation and test inventory](../measurements/multi-worktree-stage1-directories-2026-10-31/validation.json)
- [Retained smoke results](../measurements/multi-worktree-stage1-directories-2026-10-31/smoke.json)
- [Re-runnable smoke script](../measurements/multi-worktree-stage1-directories-2026-10-31/smoke.py)
- [Product/test patch](../measurements/multi-worktree-stage1-directories-2026-10-31/implementation.patch)
- [Binary provenance](../measurements/multi-worktree-stage1-directories-2026-10-31/provenance.json)

## Boundaries and next work

These checks do not make filesystem observation atomic with execution. Mutation
after admission, already-running commands (including their internal retries),
workers, asynchronous module callbacks and JVM shutdown hooks are not made safe
against arbitrary external directory replacement by this change. No background
watcher, snapshot protocol or cancellation-on-replacement policy is introduced.
If deleted original objects cannot be restored, normal shutdown is intentionally
unavailable; terminating the affected single-engine prototype is still an operator
recovery step. That lifecycle is not release-ready.

The control namespace, lock files and output-user-root object lifetimes are not
pinned by this class. A second process cannot rely on this check as ownership
transfer proof. Network filesystems, Windows/macOS, alternate Java filesystem
providers and arbitrary same-user manipulation of experimental metadata are not
qualified. Full filesystem lifetime fencing and persistent cross-process owner
coordination remain open, as do complete startup compatibility, strict shared
protocol admission, multiple complete runtimes and engine-scoped shutdown.
Later global-state isolation, concurrency and performance gates remain unchanged.
No memory or speedup claim is made.
