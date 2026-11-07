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

## D009 — Preserve the aborted ten-client attempt and repair storage (2026-11-07)

**Attempt:** `/mnt/shared-evaluation-20261107/identical-n10-v3/runs/vanilla-reference-restart`, scope `shared-eval-m0-n10-vanilla-ref`. The requested protocol was restarted-daemon measurement, but the run never completed preparation. There is no `report.json`, measured directory, or completed cohort resource report. The baseline harness SHA-256 was `84b5ed561a6b06da5593113d9b85c74a25608eca60469db57e9f9dc42599a24d`; fixture SHA-256 was `2de503bc8d4b5e7e1817b5a73e9bb78c1d7bacc56f5ae19847b8f686ebaff78a`.

**Action:** the coordinator deliberately terminated the scope with SIGTERM under host memory pressure, then reported stopping remaining Java processes before inspecting Linux memory. The exact abort timestamp and raw cgroup snapshot were not retained; the observations below are explicitly coordinator-reported, not reconstructed harness measurements.

**Observed at abort:** Windows free physical memory was 697,268 KiB, free virtual memory 1,261,428 KiB, and total physical memory 33,193,408 KiB. WSL was configured for 20 GiB. The scope's observed `memory.events` counters were all zero; no cgroup OOM or OOM-kill event was observed. After Java cleanup, rounded Linux figures were approximately 66 MiB anonymous memory, 3.8 GiB Buffers, 7.4 GiB Cached, and 2.3 GiB KReclaimable.

**Repair:** enable direct I/O on `/dev/loop0`, backed by `/mnt/d/Codex/shared-evaluation-20261107/work/runtime.ext4`. Independent read-only verification at 21:30:50 UTC reported `DIO=1`; `findmnt` reported ext4 with `rw,noatime`. After a separate flag probe terminated, unmount and remount only `/mnt/shared-evaluation-20261107` to release task filesystem caches. An attempted mount with a `direct-io` option failed; the coordinator corrected the mount and enabled direct I/O through `losetup`, again verifying `DIO=1`. No data loss was observed. No global cache purge or user-global configuration change was performed.

**Observed across the task-only remount:** Buffers fell from 3,944,548 to 3,444 KiB and Cached from 7,772,884 to 182,164 KiB; free Linux memory afterward was approximately 17.35 million KiB. This is evidence of a substantial change in cache residency, not proof of the unique cause of the original host pressure or a measured performance benefit from direct I/O. The default repository-contents cache was already shared; this repair does not assume a missing shared repository cache.

**Preservation:** add an explicit `abort.json` to the interrupted run and retain the original logs. Extend `archive_run.py` narrowly with `--abort-record`: absent `report.json` is accepted only with explicit `status=aborted` and `passed=false`; the archive manifest records `aborted_without_report`, no measured phase, and no performance eligibility. For an aborted run, also include bounded JVM logs without repository or action-output trees. Do not synthesize a completed report.

- [Abort metadata](evidence/aborted-vanilla-n10-restart-v3.json).
- [Aborted archive](evidence/aborted-vanilla-n10-restart-v3.tar.gz): 139,179 bytes; SHA-256 `5d4ff838a075e002dccfc3a8e4d70d23b8e42ae8a2ba8ec369408f35b489e054`; 62 verified members, including all 40 preparation files and ten JVM logs.
- [SkyCache v2 qualification archive](evidence/qualification-skycache-restart-v2.tar.gz): 191,893 bytes; SHA-256 `551bf2237ada70e643bc4127e80893c2e448c0d569423b33f39b9fd6ee569455`; 32 verified members. Its successful build result remains qualification evidence, not a performance or milestone verdict.

**Next protocol:** use fresh run IDs and the same repaired storage setup for all matched arms; retain the aborted run as a separate attempt. Do not combine pre-repair and post-repair timings automatically. M0 remains in progress. This incomplete infrastructure attempt is not a passed or failed architectural gate.

## D010 — Fix the bounded M0 matrix before the retry (2026-11-07)

**Decision:** use the following declared coverage to decide M0. Each pair compares the pinned vanilla and current SkyCache binaries on the same fixture and configuration, with an independent vanilla correctness reference. These are required future measurements, not completed results. Avoid an exhaustive cross-product of every scenario, client count and cache state.

| Scenario | Concurrent clients | Engine and cache lifecycle | Required coverage |
| --- | --- | --- | --- |
| Identical inputs | 1 and 10 | Restarted engines; prepared, retained named caches | One matched pair at each width; repeat counterbalanced pairs if results are noisy or an interpretation depends on a small difference |
| Distinct declared-source edits | 10 | Restarted engines; prepared, retained named caches | One matched pair with ten distinct source variants |
| Source behavior edit | 10 | Restarted engines; prepared, retained named caches | One matched pair; current fixture deliberately repeats one edited variant after a base client |
| Shared BUILD-file edit | 10 | Restarted engines; prepared, retained named caches | One matched pair; current fixture deliberately repeats one edited variant after a base client |
| Configuration changes | 10 | Restarted engines; prepared, retained named caches | One matched pair covering the fixture's fastbuild/opt variants |
| Glob additions | 10 | Restarted engines; prepared, retained named caches | One matched pair with each client's unique added input and expected manifest |
| Identical inputs | 1 and 10 | Warm engines and retained named caches | One matched pair at each width, preserving the prepared daemons |
| Identical inputs | 1 | Fresh engines and explicitly empty named caches | One matched pair, with installation/repository/network setup costs separated from evaluator conclusions |

**Matched resource and lifecycle rules:** use the same six-CPU aggregate quota, 12 GiB memory limit and zero cgroup swap for both arms, on the repaired task filesystem. Freeze harness identities, binary hashes and effective build options; persist source-tree and oracle provenance. For restarted cohorts, prepare clients serially and shut down each prepared daemon, then launch all measured clients concurrently. Ten-client measured phases must contain ten concurrent requests; serial preparation is not permission to serialize the measured workload. Warm cohorts must retain their prepared daemons. Record guard policy, host memory observations, accounting membership and actual cache inventories. A guard-triggered abort is preserved as an infrastructure attempt, never interpreted as a completed measurement.

**Cache meaning:** `--fresh-caches` supplies fresh private paths for the repository cache, disk action cache and SkyCache. In the pinned source and binary, the default repository-contents cache derives from `{--repository_cache}/contents`; a new private repository-cache path therefore also makes its default contents cache private unless explicitly overridden. Verify inventories and command flags rather than inferring a hit from a warm-looking elapsed time. Shared installed Bazel files and OS page-cache residency remain separately identified: fresh named caches are not a claim of a fully cold machine. Preserve the complete cold-run elapsed/CPU totals and report download, installation and repository setup evidence separately; do not count network setup savings as shared evaluation savings.

**Assessment rules:** keep each arm and trial visible, including failures; do not pool timings from different harness versions or the pre-repair storage setup. A single pair is exploratory evidence, not a variance estimate. If spread or order sensitivity prevents the M0 conclusion, repeat the affected matched pair with reversed order before assessing it. M0 cannot pass until the declared coverage has completed with verified cgroup accounting and independent correctness checks, and its cost breakdown demonstrates substantial addressable duplicated evaluation or retained state. No numeric gate is passed by this protocol entry.

**Later thresholds:** the proposed M4 targets remain at least 50% lower aggregate build-control memory, at least 30% lower build-control CPU per completed build, and at most 10% single-request latency regression, relative to isolated SkyCache. After M0 supplies the baseline and before implementing the new candidate, record the final acceptance metrics, scopes and thresholds explicitly. Once frozen, do not move them in response to candidate results. This sequencing does not authorize claiming current proposals as measured results or silently relaxing them.

## D011 — Guard host memory and reuse prepared comparator state (2026-11-07)

**Decision:** run cohorts through the Windows host guard in `harness/run_guarded.ps1`. Keep the established six-CPU, 12-GiB Linux budget and zero swap; sample Windows available physical memory and commit headroom every 500 ms, reserving 3 GiB and 4 GiB respectively. A breach is an infrastructure abort. The wrapper uses a fresh scope, verifies its ownership, and releases a finite startup gate before running the workload. Cleanup signals only that verified scope and checks that it becomes inactive.

**Validation:** focused guard checks passed. A real two-second sleep completed normally with scope ownership and cleanup verified. A thirty-second sleep with a two-second timeout produced infrastructure-abort exit 75 and verified scope termination. An earlier smoke test failed before launching Linux because command discovery returned two `wsl.exe` paths; selecting the first application fixed it. Guard implementation committed as `bac5ab10`; the normal/timeout smoke records remain in the task work directory and will accompany the cohort evidence.

**Comparator-state choice:** add optional `--output-base-root` for repeated warm/restart trials. Fresh run directories retain immutable commands, profiles, reports and correctness evidence. Mutable Bazel state lives in a separately locked pool, bound to binary, fixture/source state, flags, resource limits, cache paths and harness identity. Reuse requires a successful previous report, successful daemon shutdown and no live recorded daemon. Interrupted or failed pools have no automatic reset path. Cold/fresh-cache trials cannot use a pool. Copy the complete pool binding into each evidence directory.

**Reason:** repeatedly extracting non-shareable repositories into a fresh output base for every trial consumes storage and setup time without improving a restarted-engine comparison. Pooling establishes an explicit comparator lifecycle; it does not decide the proposed evaluator's ownership model. Full environment digests remain recorded per trial but are excluded from pool equality because launcher variables change. Bazel still validates its relevant environment inputs; performance comparisons must inspect recorded provenance.

**Active retry:** pinned serial-preparation harness `888dbbd8` at `harness-v5`, independent vanilla reference run `identical-n10-v3/runs/vanilla-reference-restart-guarded-v5`. Prepare and stop one engine at a time, then measure all ten concurrently. Its first preparation build completed with sixteen action-cache hits and eight internal actions; this setup observation is not a milestone result.

## D012 — Fit the host budget and reset only task cache residency (2026-11-07)

**Observed:** the guarded v5 ten-client reference aborted before the tenth preparation completed, with nine successful preparations and no measured phase. At 21:44:09 UTC the guard recorded 4,748,271,616 bytes available physical memory and 4,265,795,584 bytes available commit, below its 4 GiB commit reserve. It signalled only the owned scope and verified termination. Preserve the [aborted run](evidence/aborted-vanilla-n10-restart-guarded-v5.tar.gz) and [guard/smoke bundle](evidence/host-guard-v5-abort-and-smokes.tar.gz). This is an infrastructure limit, not an architectural gate result.

**Budget decision:** supersede the 12 GiB protocol cap with an explicit 10 GiB (`MemoryMaxMiB=10240`) cap for every future matched arm. Keep the six-CPU quota, zero cgroup swap, 768 MiB JVM heap setting, and 3 GiB physical/4 GiB commit guard reserves. The matrix runner has no default memory cap. Different resource limits form different comparison strata; do not pool earlier 12 GiB timings with the new matrix.

**Storage repair:** enlarge only the task-owned sparse image from 64 to 256 GiB, refresh loop capacity and resize its ext4 filesystem. The coordinator reported approximately 252 GiB filesystem capacity, 230 GiB free and 22 GiB used afterward. No user root disk was resized. After a normal task unmount retained substantial cache, inspection found the exact task mount still present in the private mount namespaces of `polkitd` (PID 19343) and `wsl-pro-service` (PID 26273). Unmounting only `/mnt/shared-evaluation-20261107` in those namespaces allowed loop release and reduced reported file cache from approximately 11 GiB to 1.4 GiB. Neither service was killed or reconfigured. This establishes namespace-held mounts as the reason that particular unmount retained the task filesystem; it does not establish a universal explanation for host memory pressure.

**Repeatable setup boundary:** before each new cohort, after the preceding report and guard evidence are archived, run the pinned `reset_task_mount.py` from a C-backed path outside the mount. It verifies the exact image, UUID and loop backing, refuses active task workloads, synchronizes and unmounts only the task path in the relevant namespaces, then remounts the same filesystem and verifies direct I/O and `noatime`. It never performs a global page-cache drop. Repository caches on the WSL root remain warm; per-cohort preparation warms the task inputs required by that cohort's declared lifecycle. Preserve each reset's commands, memory observations and outcome. Stop on failure rather than forcing an unmount or continuing with an unknown storage state.

**Validation:** the coordinator's guarded N1 v6 reference completed with the expected executable digest, 14.315 s measured wall time, 23.863 s scope CPU and approximately 751.3 MB peak sampled PSS. This is a reference/pilot, not a performance conclusion. The reset helper's read-only inspection and actual task-only reset succeeded; the actual reset took approximately 0.46 s, remounted with `DIO=1`, and reduced reported file cache from approximately 2.51 million KiB to 1.45 million KiB. Helper SHA-256: `da301d14b2e9916b03e106be86edd233488dbce1a935a160ce91e4451158382f`.

**Execution manifest:** [matrix-v6.json](harness/matrix-v6.json) fixes the bounded coverage and pins v6 harness, binary and control-tool hashes. [run_matrix.ps1](harness/run_matrix.ps1) imports only the explicitly named N1 reference, adds candidate-off anchors at N1/N10, runs the remaining cohorts sequentially, verifies independent oracles/accounting, and archives successful reports plus genuine guard bundles. Dry-run checks and guard ZIP round-trip verification passed without launching any benchmark. The runner stops on the first infrastructure, functional, accounting, provenance or archive failure for assessment. It cannot declare a milestone passed, repair a pool or retry a failed report automatically.
