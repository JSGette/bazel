# Stage 1 continuation: shared endpoint admission

Date: 2026-10-31

Status: implemented and verified on the Linux/WSL2 single-engine prototype.
Stage 1 remains incomplete; this change does not add another engine or establish
durable cross-process ownership.

## Behavior

The shared endpoint previously retained ordinary Bazel's legacy omission rules:
an authenticated request could omit its workspace/output identity or registration
ID. Its command allowlist also existed only in the C++ launcher. The endpoint now
enforces the shared protocol itself, before preemption, command registration or
dispatch. Runtime startup explicitly selects this policy from the existing
`--experimental_shared_server` option, independently of filesystem guard objects.

| Request | Shared endpoint requirement |
| --- | --- |
| Run, including shutdown | Matching workspace/output identity and current, nonempty registration ID |
| Run command | Exactly `build`, `test`, `info` or `shutdown` |
| Cancel / terminal resize | Current, nonempty registration ID, in addition to the existing cookie and command ID |
| Ping | Existing authenticated discovery; returns the current registration ID |

Run rejection produces one authenticated final response, exit 36, no command ID,
and no expected server termination. Missing path identity uses
`ENGINE_IDENTITY_MISMATCH`; missing/stale registration uses `ENGINE_UNAVAILABLE`.
Missing or unsupported commands use the new
`SHARED_SERVER_COMMAND_UNSUPPORTED` failure detail. The launcher still rejects
unsupported command lines earlier with exit 2. Empty/stale cancellation and resize
requests have no effect and receive no acknowledgment value.

Ordinary Bazel retains legacy omission behavior, including internal restart
shutdown requests. Present identities and nonempty registration IDs must still
match in that mode. These fields protect routing/lifetime correctness; they are
not a security boundary against a user who can manipulate daemon metadata.

## Verification

The development binary built successfully. **127 test cases passed, with no
failures, errors, duplicate case identities or skips**, verified from test XML:

| Target | Passed |
| --- | ---: |
| Java `ServerTests` | 99 |
| Shared client integration | 16 |
| Selected legacy client integration | 11 |
| Legacy server-mode SIGINT | 1 |

Ten new Java cases cover omitted identities/IDs, fully legacy requests, stale
registrations, wrong-workspace shutdown, missing/unsupported commands, accepted
commands, cancellation and resize. Rejection tests hold a preemptible command at
a barrier and verify it completes successfully without another dispatcher call.
Cancellation/resize tests reject empty and stale registrations before accepting
the current registration. Existing ordinary-mode cases remain in the same suite.

The new live integration probe connects directly to the real gRPC endpoint,
bypassing launcher validation. Eight requests exercise incomplete shutdown
identities, a stale ID, wrong workspace/output, `clean --expunge`, and a missing
command. All reject before dispatch. Authenticated ping returns the same
registration afterward, a sentinel in the engine output survives, and a normal
client reports the original daemon PID. Existing shared integration cases cover
real build/test/shutdown, startup incompatibility and physical-directory fencing.

A separately copied binary passed seven CLI invocations plus that eight-request
probe. A real local action reached a readiness marker before the smoke runner
sent SIGINT to the C++ client. The client cancelled the build with exit 8 using
its current registration ID; a subsequent command observed the same PID/start
ticks. Shutdown then completed and the adopted daemon was reaped with status 0.

The first standalone smoke attempt lacked the probe launcher's runfiles because
the probe had only been built as test data. Building its explicit binary target
resolved the harness prerequisite; that attempt's daemon was shut down/reaped.
The retained smoke result is the subsequent successful run in a fresh namespace.

Formatting checks passed for changed Java sources and BUILD files, along with
shell syntax and product/plan diff whitespace checks.

- Base commit: `2774ccfe91b6422c3daf5e98f4aa44a682626603`
- Candidate: `/home/jgett/bazel-multiworktree-20261031/stage1-protocol/bazel-stage1-protocol`
- Candidate SHA-256: `d31fec18b1ccb8325b455d400de86278857293ebbade46b61c017cbc2e4f3c24`
- [Validation and complete case inventory](../measurements/multi-worktree-stage1-protocol-2026-10-31/validation.json)
- [Copied-binary smoke results](../measurements/multi-worktree-stage1-protocol-2026-10-31/smoke.json)
- [Smoke script](../measurements/multi-worktree-stage1-protocol-2026-10-31/smoke.py)
- [Product/test patch](../measurements/multi-worktree-stage1-protocol-2026-10-31/implementation.patch)
- [Binary provenance](../measurements/multi-worktree-stage1-protocol-2026-10-31/provenance.json)

For the standalone smoke, first build
`//src/test/java/com/google/devtools/build/lib/server:shared_server_protocol_probe`
explicitly. Pass its executable under `bazel-bin` with `--probe`, the copied
candidate with `--bazel`, the existing repository cache with `--repository-cache`,
a fresh directory with `--root`, and the output JSON path with `--result`.
The [exact invocations](../measurements/multi-worktree-stage1-protocol-2026-10-31/commands.json)
and validation logs are retained; the legacy suite
uses its shell test-name glob filter separately from the Java suite.

## Remaining gates

The prototype still owns one complete runtime per process. Durable owner locks,
control namespace/lock-object lifetime fencing, full startup compatibility,
background-work lifetime fencing and shutdown scoped to one of several engines
remain unfinished. A second workspace continues to reject. This increment makes
shared routing metadata mandatory; it does not transfer ownership, protect
against filesystem mutation during execution, or demonstrate memory/performance
improvements.
