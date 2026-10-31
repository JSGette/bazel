# Multi-worktree Stage 1: registration and request lifetime

31 October 2026. **Second Stage 1 slice implemented and tested.** This builds on
[request ownership](multi-worktree-stage1-2026-10-31.md). The real single-engine
server now uses an engine registration and request leases. Shared-daemon startup
and multiple complete runtimes in one process are still unimplemented.

## Implemented boundary

`EngineRegistry` reserves output bases within one registry, independently of the
workspace part of the key. Two different workspace keys cannot reserve the same
output base, including while initialization or cleanup is in progress. A
registration has an opaque UUID and transitions through `CREATING`, `READY`,
`DRAINING`, and `CLOSED`. Failed initialization can abort a creating reservation.
Activation is one-way. Closed handles cannot remove a later registration at the
same paths, and old IDs cannot acquire its dispatcher.

Acquiring a lease requires the matching workspace/output-base key, a ready
registration, and a matching nonempty instance ID. An empty ID preserves the
legacy single-engine protocol. Draining stops new admission and waits for active
leases; interrupted draining retains ownership. Cleanup happens while the output
base is still reserved. Closing releases the reservation only after cleanup.
Dispatcher references are cleared from released leases and closed registrations.

This registry does not replace filesystem locks. The current endpoint has one
registration and retains the existing startup/output-lock arrangement. Tests with
two registrations establish component isolation, not support for two live Bazel
runtimes. Reserving before construction of an entire runtime and rolling back its
partial initialization remain part of the ownership split in Stage 3.

## Connection and control protocol

An authenticated `PingResponse` now includes `engine_instance_id`. The C++ client
pins it when connecting and includes it on `RunRequest`, `CancelRequest`, and
`TerminalSizeRequest`, including the internal restart shutdown request. A stale
run receives exit 36 and `ENGINE_UNAVAILABLE` before command registration or
preemption. Stale cancellation/resize requests have no effect and receive no
acknowledgment, following the existing invalid-cookie control-request behavior.
Unauthenticated pings reveal no registration ID.

Paths still travel in `RunRequest.engine_identity` and retain the earlier
ownership check. The opaque ID distinguishes registrations at the same paths; it
does not detect deletion/recreation of the filesystem objects during one
registration. It is not a startup compatibility fingerprint or a daemon discovery
key. Legacy clients omit the new field; new clients talking to an old server get
an empty field. This compatibility path must not be used as fencing in shared mode.

## Shutdown ordering

`CommandServer` holds a lease throughout each admitted RPC callback, including
final response transmission. This matters because the gRPC transport hands work
to a separate callback executor: transport termination alone need not mean the
callback has finished. After transport termination, engine cleanup drains these
leases, interrupts and joins idle tasks, then invokes the existing runtime and
dispatcher shutdown operations. It releases the registration afterward.

Cleanup runs once. If cleanup throws, the registration stays draining/reserved,
new requests are rejected, and a later cleanup attempt fails instead of rerunning
partially completed disposal. The actual server still terminates as a whole;
engine-only shutdown inside a shared daemon is not yet wired. Module-owned
asynchronous work beyond the RPC and idle-task boundaries still needs the
later ownership/global-state audit. Abrupt process termination is unchanged.

## Validation

[Retained evidence](../measurements/multi-worktree-stage1-registry-2026-10-31/)
contains source/build provenance, per-shard test XML/logs, a source patch, and a
copied-candidate smoke run. The source build of `//src:bazel-dev` succeeds.

- All **78 Java server tests** pass, including 15 new cases. Registry tests cover
  concurrent creation, output collisions, creation abort, activation constraints,
  interrupted drain, active leases, stale registration replacement, and keeping B
  usable while A drains. RPC tests cover authenticated instance discovery,
  rejection without preemption, stale cancellation/resize versus current and
  legacy requests, final-response lifetime, and cleanup failure. Idle work is
  verified stopped before cleanup returns.
- All **9 selected client tests** pass: canonical aliases, nested workspace
  switching with an explicit output base, outside-workspace commands, explicit
  failure-report paths, server reuse, normal/different-options shutdown,
  nonblocking lock failure, and lock release before timeout.
- The **SIGINT server-mode integration test** passes through the C++ cancellation
  path. XML case names/counts are checked, not just target exit status.
- Buildifier checks both changed BUILD files. Changed Java server/test files
  pass google-java-format checks. Validation runs on Linux x86_64 in Ubuntu WSL.

During compilation, Error Prone rejected direct access to inner-registration
fields guarded by a different outer-instance expression. Access now goes through
methods that acquire the registration's own registry monitor. It also caught a
test timeout overflow: the repository's general wait constant is `Long.MAX_VALUE`;
the thread-state assertions now use a bounded ten-second deadline. These were
resolved before the successful retained run.

## Next gate

Stage 1 remains open. Next implement the separate shared-mode control directory
and startup lock, a conservative versioned startup compatibility profile, and
cross-process ownership/registration. Do not publish a shared daemon PID where a
legacy client's restart path can kill it. Filesystem object lifetime checks,
multi-runtime construction/rollback, shared-mode-required IDs, engine-scoped
cancellation/shutdown, and platform qualification remain necessary before shared
attachment can be enabled. No new public mode or performance claim is introduced.
