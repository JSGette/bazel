# Multi-worktree Stage 1: request ownership

31 October 2026. **First implementation slice complete; Stage 1 remains open.**
The [registration and request-lifetime continuation](multi-worktree-stage1-registry-2026-10-31.md)
implements the next slice; this document records the earlier state.
The [bounded Phase 0 baseline](multi-worktree-phase0-2026-10-31.md) now meets the
original baseline exit gate. This increment adds an ownership check to the real
C++ client / protobuf / Java dispatch path. It does not enable shared daemons.

## Implemented behavior

`RunRequest.engine_identity` carries canonical workspace and output-base paths.
The C++ launcher obtains these after normal workspace discovery and directory
preparation. It uses the same platform byte representation as startup arguments;
the protobuf fields are `bytes`, not UTF-8 `string`. The Java `CommandServer`
compares normalized `PathFragment`s with its startup paths after authenticating
the request, before preempting commands, allocating a command ID or invoking the
dispatcher. A mismatch returns exit 36, `ENGINE_IDENTITY_MISMATCH`, an authenticated
response cookie and a diagnostic. It neither runs the command nor shuts down the
server. An empty workspace denotes an outside-workspace command; an empty or
relative output base cannot match the server's absolute output base.

The location pair is deliberately not a filesystem generation, daemon ID or
startup compatibility fingerprint. Directory recreation and shared registration
still need the additional lifecycle checks below.

The new alias regression exposed an existing startup-path problem: the default
`failure_detail_out` used the output-base spelling before canonicalization. The
default now derives from the canonical output base in `PrepareDirectories`.
Explicit failure-report paths retain their existing startup-option behavior.
This permits a canonical path and its symlink alias to reuse the same server.

## Compatibility in this increment

| Request / client | Behavior |
| --- | --- |
| New client, matching identity | Dispatch through the existing single-engine command lock |
| New client, mismatched identity | Reject before dispatch/preemption; existing work continues |
| Legacy request without identity | Preserve existing single-engine behavior |
| New client talking to old server | Additive protobuf field is ignored; existing single-engine behavior |
| Ordinary user `shutdown` | Carries identity like other commands; retains current single-server shutdown |
| Internal restart of an old workspace/server | Existing control request omits identity, since the server being replaced may belong to a different workspace |
| Batch mode | Existing path; no RPC routing change |
| Shared mode | Not implemented or advertised by this patch |

Legacy omission is compatibility, not a security or ownership fence for a future
shared daemon. In particular, the legacy restart/kill path must never reach a
shared daemon. No shared PID or endpoint is currently published anywhere.

## Remaining Stage 1 contract at the end of this first slice

These are implementation decisions for the next slices, not working features or
public flags:

- A daemon gets a fresh process-lifetime identifier and its own control directory
  and startup lock. Start with a separate shared-mode namespace under the output
  user root. Do not place its PID in a legacy output-base server directory.
- An engine registration binds the canonical workspace/output-base pair to one
  daemon and one registration generation. Reserve an output base atomically;
  reject a second live workspace owner. Never merge engines by Git commit or
  workspace name. Nested roots use the existing discovery result.
- Compare a conservative, versioned startup profile before attachment. Begin
  with exact effective settings for executable/install identity, JVM/runtime
  selection and arguments, filesystem/digest behavior, and module/service setup.
  Preserve order/multiplicity where the current startup comparison does. Classify
  output/workspace paths as engine-local only after auditing their consumers.
  Unknown or incompatible settings reject attachment; do not silently weaken
  compatibility or kill other engines.
- Verify workspace/output-directory lifetime at admission and refuse stale
  registrations after deletion/recreation. Canonical path equality alone is
  insufficient. Platform-specific file identity and replacement races need tests.
- Use explicit `CREATING -> READY -> DRAINING -> CLOSED` ownership transitions.
  Creation reserves ownership, and initialization failure releases it. A command
  holds its engine lease through completion and callbacks. Start with serialized
  command admission and private runtimes/modules; concurrency is a later gate.
- Retain per-output-base lock semantics independently of the daemon startup lock.
  Test simultaneous daemon/engine creation, blocking waits, nonblocking failure,
  cleanup after failed creation, and startup-profile mismatch before enabling
  shared-mode attachment.
- Commands get invocation IDs scoped to their engine registration. Cancellation
  and terminal updates must resolve through that ownership. Future shared
  `shutdown` drains only the selected engine; daemon shutdown needs an explicit
  separate control operation. The current legacy shutdown remains unchanged.

## Verification

Retained [test evidence](../measurements/multi-worktree-stage1-2026-10-31/) includes
the build logs and per-shard XML/logs. `//src:bazel-dev` builds. The full Java
`ServerTests` and C++ `startup_options_test` pass. New server tests exercise valid
identity on normal requests, mismatched workspace/output base, empty/relative
output base, legacy omission, and rejection without preempting a live command.

Four new client tests pass and are explicitly present in the XML: canonical
workspace/output aliases, A-to-nested-B-to-A with one explicit output base,
outside-workspace commands, and preservation of explicit failure-report paths.
The three existing client regression tests for server reuse, startup-option
restart, and same-output-base command locking also pass. Buildifier checks the
modified BUILD file; changed Java sections use google-java-format 1.30.0.

Two test-selection mistakes are retained in the logs: the first filter used a
regex-style pattern where the shell runner expects a glob and selected only the
three explicitly named existing tests. A subsequent shell glob applied to a
mixed-language test command selected no Java/C++ cases. Unit suites were rerun
without a filter, and actual XML case names/counts were checked. The alias test
failed before the canonical-default fix and passed afterward.

Validation is Linux x86_64 under WSL. Windows/macOS path encoding and filesystem
identity, daemon creation races, registration generations, shared shutdown scope,
and shared/legacy coexistence remain unqualified. This increment is an ownership
boundary for the existing single-engine server, not completion of Stage 1.
