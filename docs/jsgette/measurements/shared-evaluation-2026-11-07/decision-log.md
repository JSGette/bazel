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

**Status:** storage setup pending. Infrastructure repair is not an architectural milestone failure.

## Execution protocol

For each measurement record the source/binary identity, exact arguments, scenario, cache state, resource limits, return status, correctness comparison, and measurement scope. Preserve failed attempts separately. Update the plan's status only after its gate has been assessed against evidence. A material failed gate permits one bounded repair with a measured cause; if still failed, publish the failure and stop for a pivot. Routine architectural choices are autonomous and documented here.
