# Stage 1 continuation: Linux process-lifetime ownership

Date: 2026-10-31

Status: implemented and verified for cooperating prototype processes on the local
Linux/WSL2 filesystem within one network namespace. Stage 1 remains incomplete.
This reserves a daemon process and its single engine; it is not a complete lease
covering orphaned actions/workers or a mechanism for evicting individual engines.

## Implementation and compatibility

Client locks previously protected startup only until the client handed off its
request. The daemon could then outlive those locks, and deleting control metadata
or lock files could remove the remaining evidence that it still owned the output.

`SharedServerOwnership` adds a separate process-lifetime reservation. Before a
disconnected launcher writes a startup profile, changes its installation symlink or
spawns a JVM, it reserves the control directory and engine output directory. Each
directory has a reservation keyed by its canonical path and another keyed by its
device/inode identity. An open directory descriptor pins the object. Both roles
use the same key domains, so another namespace cannot acquire an existing engine
directory as either its control directory or output directory. Partial acquisition
releases all reservations already taken.

Reservations use bound Linux abstract UNIX sockets. Their names are independent
of filesystem entries, and they remain reserved while an owning descriptor remains
open. This avoids the unlink/recreate loophole of an additional lock file.
Path keys use the repository's MD5 utility only to bound address length; a digest
collision would reject acquisition, not permit a second owner. These names are
coordination records, not authentication secrets. [Linux UNIX socket semantics](https://man7.org/linux/man-pages/man7/unix.7.html)

The launcher passes four socket descriptors and two directory descriptors through
the existing daemonize/JVM exec path, then closes its own copies after spawn. A
hidden Java startup argument carries the descriptor numbers; it is appended only
to the launch arguments, outside the persistent compatibility profile. Before
runtime/module initialization, JNI validates all six descriptors against the
expected paths, pinned objects and socket names. It sets `FD_CLOEXEC` so subsequent
executed children do not retain reservations. [Close-on-exec semantics](https://man7.org/linux/man-pages/man2/F_GETFD.2const.html)

The JVM retains the descriptors until process exit, including failed initialization,
runtime cleanup and shutdown hooks. Ending a Java resource scope does not certify
ownership transfer. The existing client locks continue to provide command waiting
and nonblocking behavior; the existing directory checks still fence admission and
cleanup against replacement directories. A conflicting process reservation rejects
with exit 36, including a shutdown request whose endpoint metadata is missing.

The new prototype uses **`shared_servers/v2/<name>`**. Older prototype launchers
do not participate in the new reservation protocol, so reusing their `v1` namespace
would not establish ownership. The version change creates separate control and
output state without migrating or deleting old state. Existing `v1` daemons remain
available through their original binary; a new binary's shutdown addresses `v2`.
Ordinary Bazel retains its existing namespace and behavior.

Shared mode now rejects `--run_in_user_cgroup` with exit 2 before namespace creation:
the systemd re-exec path is not qualified to preserve this descriptor handoff.
No fallback is attempted against a reserved output.

## Verification

The development binary built successfully. **151 cases passed, with no errors,
failures, duplicate identities or skips**, verified from retained XML:

| Target | Passed |
| --- | ---: |
| New native ownership tests | 9 |
| C++ startup options | 10 |
| Java server suite | 99 |
| Shared client integration | 21 |
| Selected legacy clients | 11 |
| Legacy server-mode SIGINT | 1 |

Native tests cover conflicting and independent reservations, partial acquisition
rollback, path and physical-object ownership after control/output renames,
unpinnable paths, descriptor adoption, missing/duplicate/wrong descriptors,
inherited ownership after launcher exit, abrupt owner death, and release across
exec while the executed child remains alive.

Five new shell cases cover absent control metadata while the original JVM is
suspended, another named server aliasing the live output, recovery after an idle
daemon is killed, failed JVM startup, and preservation of the older namespace.
Existing cases continue to cover startup compatibility, concurrent startup,
directory fencing, raw protocol rejection, Unicode paths, build/test and shutdown.

A separately copied candidate passed **12 CLI invocations**. The smoke inspected
all six live JVM descriptors, checked their close-on-exec flags and socket identities,
and executed a real local action whose descriptor inventory contained none of them.
It removed/restored control metadata around rejection, rejected another named
server's output alias, killed/reaped the idle owner, and started a fresh owner.
The new daemon then shut down and was reaped with status 0.

The same smoke ran the previous `v1` binary beside the candidate using the same
workspace, output-user-root and server name. Its PID/start ticks and five control
metadata files remained unchanged throughout the candidate's build, rejection,
crash recovery and shutdown. The previous daemon was also shut down/reaped with
status 0. This is separate-process compatibility evidence, not shared-JVM support.

- Base commit: `162a4d394739766a47d80f3fc8942733e9f9fae4`
- Candidate SHA-256: `bfa4248a617329a65e22d5f0843f1e7e56272931ff4ab0712278069c77e639f9`
- [Exact commands](../measurements/multi-worktree-stage1-ownership-2026-10-31/commands.json)
- [Validation and complete case inventory](../measurements/multi-worktree-stage1-ownership-2026-10-31/validation.json)
- [Smoke results](../measurements/multi-worktree-stage1-ownership-2026-10-31/smoke.json)
- [Re-runnable smoke script](../measurements/multi-worktree-stage1-ownership-2026-10-31/smoke.py)
- [Product/test patch](../measurements/multi-worktree-stage1-ownership-2026-10-31/implementation.patch)
- [Binary/tool provenance](../measurements/multi-worktree-stage1-ownership-2026-10-31/provenance.json)

## Remaining boundaries

Abstract socket names are isolated by Linux network namespace. This mechanism
does not coordinate shared output directories across network namespaces or hosts.
NFS/other remote filesystems, Windows/macOS, arbitrary custom JVM startup agents
and deliberate legacy-client access to the experimental private namespace remain
unqualified. [Linux network namespace semantics](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)

Reservations prevent another cooperating process from claiming the same names or
objects while their owning process remains alive. They do not make directory
mutation atomic with commands or protect every asynchronous callback and shutdown
hook from external path replacement. Recovery tests kill an idle engine. Orphaned
actions/workers that survive a server crash still require a separate cleanup and
ownership policy; process death alone does not prove all engine work has stopped.

The prototype still hosts one complete runtime. Full startup compatibility,
background-work leases, lifecycle fencing, engine-scoped disposal/transfer and
multi-engine shutdown remain open. No concurrency, memory-saving or speedup claim
is made.
