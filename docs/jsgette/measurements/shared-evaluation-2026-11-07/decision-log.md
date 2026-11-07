# Decision and execution log

Dates use Europe/Berlin. Decisions below are proposals or actions, not evidence of a passed milestone.

## D001 — Isolate work on a new fork branch (2026-11-07)

**Decision:** create `jsgette/shared-evaluation-gates-20261107` at `c54d33d5b40c5711f3841f50ea1c2f950c959450`, the verified tip of `jsgette/native-multi-worktree-mvp`, in a separate Git worktree. Commit and push the plan first, then publish reproducible implementation and evidence incrementally.

**Reason:** this branch contains the user's existing multi-worktree architecture and shared-action experiments. A separate branch preserves prior work and avoids collisions with other active investigations. No existing source checkout is reset or cleaned.

**Command:** `git worktree add -b jsgette/shared-evaluation-gates-20261107 <task>/work/bazel c54d33d5b40c5711f3841f50ea1c2f950c959450`.

## D002 — Reuse evidence and fixtures, not their conclusions (2026-11-07)

**Decision:** use the pinned Datadog profile-normalizer slice from the existing experiments as the first real workload. Compare matched vanilla and SkyCache binaries, plus candidate-off where useful. Preserve source edits and BUILD-generated-source variants and their independent executable behavior oracle.

**Reason:** prior work already found duplicated graphs while action execution was coalesced. Tiny edge-array and syntax pools produced small or inconsistent whole-process savings. Those facts motivate measuring larger opportunities; neither proves a new evaluator will pay off.

**Constraint:** prior diagnostic graph names, even after rebinding paths, cannot authorize reuse. Replay must distinguish diagnostic structural overlap from validated semantic equivalence.

## D003 — Keep benchmark storage isolated (2026-11-07)

**Observed:** WSL Ubuntu ext4 root has approximately 1.4 GiB available; D: has approximately 893 GiB available. The existing SkyCache source checkout has uncommitted changes.

**Decision:** preserve all existing sources, output bases, and evidence. Provision task-owned Linux filesystem storage for new substantial measurements rather than deleting previous work or silently comparing ext4 against Windows 9p storage. Record the actual storage stack and setup commands before benchmarking.

**Action:** created a new 64 GiB image at `D:/Codex/shared-evaluation-20261107/work/runtime.ext4`, formatted ext4 with `mkfs.ext4 -F -m 0 -L shared-eval-1107`, and mounted it at `/mnt/shared-evaluation-20261107` using `mount -o loop,noatime`. Filesystem UUID: `05abc6fb-e13c-4e06-bd41-2cb6911dd802`. Set mount ownership to UID/GID 1000. No old evidence or source was deleted.

**Setup correction:** WSL `truncate` initially created a non-sparse NTFS file. During formatting, `fsutil sparse setflag D:\Codex\shared-evaluation-20261107\work\runtime.ext4` enabled sparse handling. The initial setup cost is excluded from benchmark timing. The mount disappeared when the last WSL invocation ended; a task-owned long-lived WSL mount keeper now keeps the filesystem available. Record this ext4-over-loop-over-9p/NTFS storage stack; do not claim direct Linux-block-device performance.

**Status:** mounted and writable, approximately 63 GiB initially available. Infrastructure repair is not an architectural milestone failure.

## D004 — Use recorded binary hashes, not version labels (2026-11-07)

**Decision:** identify prebuilt development Bazel binaries by SHA-256 because both report `bazel no_version`.

- Vanilla: `34a95ea73a865b8382e433ec616369fec24e51e69f7c02c148dd30097dd5863b`, built from `a5c50fcb65841b505ec72840d83381a704e70afa`.
- SkyCache safe subset: `11547ce20ac9edcaa14c48206ac14618012d898ef24d4bbf6e0ac229b44b1e25`, recorded by the successful 29 October toolchain validation. The preserved checkout is based on `a23ae68903f03f08ce83dcf1be85cdff54bb1eeb` with uncommitted changes; HEAD alone is not its provenance.
- Preserved safe-subset patch SHA-256: `238cac7dffac4ca6a89ca144d59643ecb60e465d0dbeab0b21da0c1a2a56eb7e`.

**Correction to historical assumptions:** the available source rejects type-5 cached execution envelopes. M0 must establish actual analysis-cache hits from invocation statistics. It cannot inherit the older October 25 execution-reuse claims. `experimental_active_directories` is a deprecated no-op in this source; it is not a tuning variable here.

## D005 — Isolate full-cohort resource accounting (2026-11-07)

**Decision:** launch each cohort in a fresh systemd user scope with CPU and memory accounting, `CPUQuota=600%`, `MemoryMax=12G`, and `MemorySwapMax=0`. A successful probe verified readable cgroup-v2 `cpu.stat`, `memory.current`, and `memory.peak` from the scope.

**Measurement contract:** record cgroup CPU for harness, clients, daemons and their compiler descendants; sample daemon PSS and process CPU separately. Verify each daemon belongs to the measured scope. Scope memory includes charged filesystem cache, so report its anonymous/file components and do not equate it with evaluator memory. Scope-lifetime peak includes any preparation phase; phase samples are labeled separately. Counterbalance repeated arms.

## D006 — Treat first run as qualification, not a performance result (2026-11-07)

**Action:** prepared a fresh private Git repository and one detached worktree from Datadog commit `85ad968bca856fa3d4e07b4d8e11961dabe6866f`, preserving the real profile normalizer target. Started `vanilla-qualification-01` under scope `shared-eval-m0-vanilla-q1`, with an empty task-owned action cache and the pre-existing repository download cache.

**Reason:** the first run qualifies dependencies, toolchains and the executable behavior oracle, and warms installation/download infrastructure. Harness review concurrently found stricter accounting and oracle checks needed before final measurement. This qualification is excluded from milestone performance claims, whether it succeeds or fails. Preserve its logs rather than rerunning into the same output directory.

## Execution protocol

For each measurement record the source/binary identity, exact arguments, scenario, cache state, resource limits, return status, correctness comparison, and measurement scope. Preserve failed attempts separately. Update the plan's status only after its gate has been assessed against evidence. A material failed gate permits one bounded repair with a measured cause; if still failed, publish the failure and stop for a pivot. Routine architectural choices are autonomous and documented here.

## D007 — Qualify cache reuse before expanding concurrency (2026-11-07)

**Action:** ran vanilla and SkyCache restarted-daemon qualification on the same private one-worktree fixture, using pinned harness commit `510facd7`. The first warm-up phases included installation and repository setup. Both measured phases passed accounting, functional behavior and byte-for-byte executable checks: SHA-256 `a4e26fb81ac494ebb4a407809b8450510d4379613d19e9ad92485a4bbd963fd0`.

**Observation, single trials only:** vanilla measured 15.119 s wall / 22.989 s scope CPU / 22.53 s observed engine CPU; SkyCache measured 14.845 s / 24.924 s / 24.50 s respectively. Sampled engine peak PSS was 743,977,984 and 759,917,568 bytes. These do not establish a speed or memory improvement. SkyCache restored 3,272 configured-target roots, read 34,374 dependency-validation records, and checked 818 witnesses. Actual restoration is qualified; M0 is not passed.

**Harness correction:** v2 now fails on missing/escaped daemon accounting, missing expected oracle variants, or tracked source changes. It shares an installation directory across cohorts. v3 extends configuration, glob and genuinely disjoint-source scenarios, records oracle provenance and bounded cache inventories, and adds an explicit per-cohort SkyCache path. Final matrix runs will use a frozen version, not files being edited concurrently.

**Preserved setup failure:** an attempted Linux `git -C` read of the Windows-created worktree failed because `.git` contains a Windows path. Scope `shared-eval-m0-skycache-v2-q1` therefore ran no build. Corrected immutable harness extraction through the main repository's explicit `--git-dir`; the actual successful candidate scope was `shared-eval-m0-skycache-v2-q2`.

## D008 — Distinguish shared evaluation from JVM hosting savings (2026-11-07)

**Decision:** M1 must consider the existing one-JVM/private-engine implementation as a cheaper competing explanation for memory savings. Its current hard cap is two engines; the root implementation has not changed that cap. JVM/JIT/class-metadata amortization is not evidence of shared graph evaluation.

**Reason:** the fork constructs independent runtimes, modules, resource managers and graphs per engine. Raising the cap would also raise its forced per-engine admission budgets (two CPU slots and 1,920 MiB estimated action memory), so any higher-cap probe must retain a fixed external aggregate budget and match effective options. No engine-slot recycling or ownership weakening is authorized by this experiment's acceptance criteria.
