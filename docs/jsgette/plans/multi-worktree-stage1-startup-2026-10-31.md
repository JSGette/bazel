# Stage 1 continuation: isolated control namespace and startup admission

Date: 2026-10-31

Status: implemented and tested on Ubuntu/WSL2, **one engine per named server**.
Stage 1 remains incomplete. This is not multi-workspace runtime admission,
concurrent build support, shared computation, or performance qualification.

## Implemented behavior

`--experimental_shared_server=<name>` opts into a Linux prototype. Names contain
1–64 ASCII letters, digits, underscores or hyphens. Under `output_user_root`:

- `shared_servers/v1/<name>/control` owns PID/start-time/authenticated endpoint
  metadata, the startup profile, JVM logging, abrupt-exit status and default
  failure-detail/OOM files.
- `shared_servers/v1/<name>/engines/<workspace hash>` owns the private output base,
  evaluator state, execroot and command output state.

The launcher canonicalizes the selected directories. In this mode its existing
`output_base` field denotes the control base, while `engine_output_base` is sent
to Java and in request identity. Java receives a hidden client-selected server
directory. The legacy mode retains its existing layout and restart behavior.

Clients take locks in install → control/startup → private output order, releasing
private output before control. Startup remains serialized through first RPC
handoff. Both startup/control and private output locks retain blocking and
`--noblock_for_lock` behavior; the tests prove rejection while a real fcntl lock
is held and a waiting client resumes after release. These are client locks, not
a completed server-held filesystem ownership protocol for multiple engines.

A compatible named server is reused. Incompatible installation, effective startup
profile, workspace, or JVM environment returns exit 36 without invoking the
legacy restart/kill path. Missing profile, unavailable endpoint with a live PID,
and corrupt process start-time metadata also reject without replacing the live
process. Unknown process ownership fails closed. Workspace CWD changes/deletion
or inability to inspect it reject attachment rather than restart the process.

## Compatibility contract and limits

The versioned profile compares the selected server executable, installation
arguments, generated JVM/server arguments, logging formatter, scheduling/cgroup
settings and coredump policy. Argument order and duplicate arguments are retained.
Only argv[0]'s display name and `--option_sources` provenance are omitted: neither
selects server behavior. Workspace and engine-output paths remain included until
multiple complete runtimes can be admitted safely. Unknown JVM options remain
in the profile. Existing logging configuration is not overwritten by a mismatch.

The effective JVM launch environment is compared byte-for-byte as a set of
entries against Linux `/proc/<authenticated PID>/environ`. Sanitization follows
the existing JVM launch path. Environment values are neither persisted in the
new profile nor printed on rejection; the diagnostic reports changed variable
names. This is deliberately restrictive: changes to `PWD`, `OLDPWD`, `SHLVL`,
credentials, or other apparently harmless values can reject reuse. Test helpers
hold shell context constant; they do not weaken product checks. No environment
setting has yet been audited out of compatibility. Custom systemd/cgroup launch
wrappers that change environment can likewise cause conservative rejection.

The profile assumes immutable installed executables/JDK/classpath resources;
it does not content-certify arbitrary files behind custom paths. Inherited
process limits, mounts, externally replaced files and directory object generations
are not fully qualified. The prototype must not be described as complete startup
semantic identity or filesystem lifetime fencing.

Supported commands are `build`, `test`, `info`, and `shutdown`. A workspace is
required. Batch mode, `exec-server`, explicit `--output_base`, output deletion on
shutdown, and other commands reject with exit 2. Changing or omitting the flag
returns to ordinary Bazel's separate default output namespace. Do not manually
point a legacy client into the experimental control/engine directories. Arbitrary
symlink aliases or external deletion/replacement of these directories are not
qualified. There is no protection against a same-user client deliberately bypassing
this namespace contract.

Only the first workspace can attach while this server is alive; another workspace
(including its `shutdown`) rejects, preserving the first server. Shutdown from the
owning workspace with matching startup settings/environment ends its single engine
and daemon. Engine-scoped shutdown must replace this before multiple engines can
be admitted. Whole-process interrupts and fatal crashes still have the existing
single-engine failure scope.

## Verification

All **112 cases** in the selected targets passed, with XML checked for exact case
counts, uniqueness, failures, errors and skips:

| Target | Cases |
| --- | ---: |
| New `shared_server_test` | 12 |
| C++ `startup_options_test` | 10 |
| Java `ServerTests` | 78 |
| Selected legacy `client_test` | 11 |
| Legacy server-mode SIGINT | 1 |

New coverage checks name validation and namespace selection, default legacy
coexistence, reordered contradictory JVM properties, idle setting mismatch,
installation mismatch, full environment mismatch without printing values,
A→B shutdown rejection→A reuse, concurrent creation, both client locks, missing
profile, unavailable endpoint, corrupt start-time identity, unsupported options,
and an actual custom Starlark test target through build and test commands.

Legacy coverage includes canonical workspace/output aliases, explicit output-base
restart behavior, outside-workspace operation, explicit failure-detail paths,
normal startup-option restarts, legacy option-order reuse, shutdown, lock waiting,
server reuse and SIGINT. Buildifier and `git diff --check` pass. Java edits follow
Google Java Format; an unrelated pre-existing formatting hunk was preserved.

A copied `//src:bazel-dev` binary ran six commands in a fresh private root. The
smoke test verified matching workspace, engine output placement, no PID under the
engine output, rejection of a changed JVM property, unchanged PID and Linux start
ticks after rejection, then shutdown and adopted/reaped daemon status 0.

- Base commit: `8795448666e0e715afef32960cca9182a3864a1a`
- Candidate SHA-256: `b46cec2592b5873557d04bb019cdf4191f57db8f2fc316b852c16c6f59df3b99`
- [Validation, XML and logs](../measurements/multi-worktree-stage1-startup-2026-10-31/validation.json)
- [Smoke commands and results](../measurements/multi-worktree-stage1-startup-2026-10-31/smoke.json)
- [Re-runnable isolated smoke script](../measurements/multi-worktree-stage1-startup-2026-10-31/smoke.py)
- [Product/test diff](../measurements/multi-worktree-stage1-startup-2026-10-31/implementation.patch)
- [Binary provenance](../measurements/multi-worktree-stage1-startup-2026-10-31/provenance.json)

## Remaining gates

Next Stage 1 work is durable cross-process engine ownership, filesystem object
lifetime/recreation detection, stricter shared protocol admission, and the full
startup/environment compatibility audit. Multiple complete private runtimes and
engine-scoped cleanup/shutdown must follow before admitting a second workspace.
Process-global isolation, parallel admission, pure compilation sharing and matched
performance qualification remain later stages. No speedup or memory-saving claim
is made by this control-namespace increment.
