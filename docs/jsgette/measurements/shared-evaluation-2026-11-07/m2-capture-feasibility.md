# M2 capture feasibility: define the publication boundary

Preparatory design audit, 8 November 2026. M0/M1 still govern implementation; this note implements no product behavior and claims no milestone pass. It refines the capture question in the [plan](../../plans/shared-evaluation-gates-2026-11-07.md) and [Bonanza audit](bonanza-contract-notes.md).

**Recommended first contract:** evaluate a pinned, immutable, explicitly published revision. Admit either a supplied immutable tree or an atomic transaction over a service-owned tree. Treat importing an arbitrary live dirty directory as a separate adapter with its own correctness gate. A digest proves which bytes were admitted; it does not prove that independently scanned files coexisted.

## Three different guarantees

| Guarantee | Meaning | What it does not establish |
| --- | --- | --- |
| Immutable input identity | All reads resolve against one complete immutable manifest and verified objects. | How the caller obtained those bytes, or whether they describe a particular live-directory instant. |
| Filesystem point-in-time capture | The namespace and contents are those of one filesystem snapshot boundary within its declared domain. | That an editor/build generator had completed an intended multi-file transaction at that instant. |
| Published-revision consistency | A declared multi-file edit transaction becomes visible through one atomic root update; readers see a complete old or new revision. | Automatic inclusion of arbitrary writes made outside that transaction system. |

M2's phrase “never mixed input states” needs this distinction. A real filesystem snapshot can legitimately catch a writer between updating two files. That is a coherent filesystem instant, but not a complete application transaction. Microsoft's VSS protocol explicitly coordinates application writers as well as filesystem freezing; snapshot machinery alone does not supply every application's commit boundary. [VSS architecture](https://learn.microsoft.com/en-us/windows-server/storage/file-server/volume-shadow-copy-service)

Two equal stat/hash scans are insufficient against adversarial writers: an ABA sequence can restore a file between observations, and independently sampled files can form the same combination twice without that combination ever existing simultaneously. Opening file descriptors stabilizes object selection across rename, but does not make writable file contents or the whole namespace immutable. This is a reasoning limitation of the algorithm, not a claim that an observed benchmark suffered such a race.

## Feasible adapters and their honest limits

| Approach | Smallest enforceable contract | Cooperation/platform cost | Dirty-worktree implication |
| --- | --- | --- | --- |
| Explicit Git tree/object root | Resolve a supplied object ID once, admit its complete object closure, and evaluate those bytes. Never repeatedly resolve a moving branch name. | User-space object importer; define supported modes, symlink and repository-binding semantics. | Builds the selected tree, not unstaged/untracked files or unsaved editor buffers. |
| Transactional content-addressed overlay | Stage immutable blobs and a new manifest, validate closure, then atomically publish a branch head with an expected revision/generation. Readers pin the returned root. | Writers use the API or a controlled editing/session boundary; first prototype needs no filesystem mount or privileged provider. | Supports dirty edits supplied as explicit transactions, including additions/deletions; arbitrary shell/editor writes outside the API are not included. |
| Qualified filesystem snapshot | Read only a frozen provider snapshot, then import its complete supported namespace. | Provider-specific filesystem/service, permissions, scope and lifetime handling. Validate nested mounts/subvolumes, links and external repositories. | Can include dirty on-disk files at one instant; application transaction consistency needs additional coordination. |
| Coordinated directory capture | Hold a writer exclusion protocol covering every relevant mutation throughout enumeration and copying. | All writers must participate or a validated enforcement mechanism must exclude them, including existing writable handles. A convention-only lock is insufficient for arbitrary tools. | Useful for a service-owned edit session after its processes are stopped; not transparent support for an unrestricted local checkout. |
| Uncoordinated scans plus notifications | Best-effort observation with explicit uncertainty and fallback. | Portable scanning is easy; proving complete ordered mutation coverage is difficult. | Must not be admitted as proven coherent capture merely because repeated scans match or the event queue is empty. |

Git's `write-tree` serializes the index; its manual explicitly distinguishes that from current working-directory contents. A disposable index avoids changing the user's staging area, but `update-index`/`add` over live files does not create an atomic directory-capture guarantee. Git tree modes also cover a narrower model than arbitrary filesystem metadata. The importer should reject unsupported semantics or encode them explicitly in the service manifest, rather than silently treating Git as the full input contract. [Git write-tree](https://git-scm.com/docs/git-write-tree), [Git update-index](https://git-scm.com/docs/git-update-index), [Git object model](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)

A Btrfs read-only subvolume snapshot provides a stable copy-on-write root, but snapshots do not recursively include nested subvolumes. Snapshot boundaries must include every declared source/repository domain or bind separately captured roots under an explicit consistency policy. The current task filesystem is ext4; no filesystem conversion or snapshot setup is proposed for the running benchmarks. [Btrfs design](https://btrfs.readthedocs.io/en/stable/dev/dev-btrfs-design.html), [Btrfs subvolumes](https://btrfs.readthedocs.io/en/latest/Subvolumes.html), [Oracle Btrfs snapshot scope](https://docs.oracle.com/en/operating-systems/oracle-linux/8/btrfs/btrfs-CreatingSubvolumes.html)

Notifications can accelerate invalidation, but using them as capture proof requires stronger guarantees: complete mutation coverage, ordering against data visibility, a trustworthy before/after barrier, treatment of in-progress writes, and fail-closed handling of gaps, overflow and watch changes. Inotify documents queue overflow, recursive-watch races, writes through aliases outside a watched directory, and limitations around mapped-file changes. A quiet notification interval is therefore not sufficient evidence for this unrestricted adapter. [Linux inotify manual](https://man7.org/linux/man-pages/man7/inotify.7.html)

## Bounded first experiment

Implement only the immutable input interface and a transaction-backed revision store after M0/M1 permit continuation:

1. Define a versioned manifest for file bytes/kinds, relevant modes, directory membership, missing-path observations derived from that manifest, symlinks, configuration, repository/toolchain roots and the supported environment inputs. Resolve reads from the admitted root only. Reject escapes, unavailable objects and unsupported ambient reads explicitly.
2. Import one pinned Git tree without consulting the live checkout. Define filter/line-ending, submodule and LFS behavior explicitly; raw object bytes are the default, with unsupported expansion requiring a separately identified adapter. The isolated oracle must consume the same admitted bytes and policy.
3. Add `Apply(base_revision, edits) -> new_revision`: edits contain already admitted immutable objects plus namespace operations. Validate the entire new closure before publishing one root. A monotonic branch generation distinguishes stale publishers even when content returns A→B→A; immutable content IDs can still reuse A's objects.
4. Pin a revision for every evaluation. Keep worktree/branch heads, invocation identity and output-view ownership separate. Deleting a view or advancing another branch cannot change the pinned input root.
5. Initially keep publication local and process-scoped. Explicitly defer durable acknowledgments, crash recovery and GC fault tolerance to M5; do not imply persistence guarantees from atomic in-memory publication. Basic reader pins must already prevent cleanup from removing objects needed by an active request.

This demonstrates a coherent **remote, service-owned worktree** model: a worktree is a branch head over an immutable tree, with edits published as transactions. It deliberately does not certify arbitrary existing dirty-directory capture. A local integration must either submit complete edit transactions, supply a qualified snapshot, or return an explicit unsupported/fallback outcome. Falling back to ordinary Bazel does not gain a coherence guarantee from this service, and such builds cannot count as successful implementation coverage.

## Falsifying checks and cost accounting

- Pause a two-file transaction after staging its first blob. Readers must remain on the complete old revision until publication, then obtain the complete new revision; staged partial state must be unreachable.
- Race two publishers against the same expected generation; require a conflict/retry, not a lost update. Repeat through A→B→A to test generation handling separately from content reuse.
- Rename/delete directories, add a formerly missing path, and change symlink/type/mode/configuration bindings within transactions. Pinned old readers must preserve old results; new roots must expose the exact new namespace and observations.
- Retain a writable source handle or external hard-link alias while publishing imported content; subsequent writes must not mutate admitted blobs. Never use mutable source hard links as the immutable store. Detect corrupt/missing stored objects rather than accepting altered bytes.
- Advance a branch and cancel another staging transaction while old readers are active. Cleanup must retain their complete object closure and must not expose abandoned staging objects as a published revision.
- Drive ABA edits, same-size rewrites, directory renames, watch overflow and unsupported mapped/alias writes against the live-directory adapter. Until an adapter has an adequate mechanism, the expected result is explicit unsupported/fallback, not a fabricated successful coherent snapshot. Separately test a qualified provider's declared snapshot domain and lifetime.

Passing finite mutation tests does not prove an unrestricted algorithm's atomicity; the implementation must also identify the mechanism that enforces its guarantee. Measure object ingestion/hashing, changed bytes, manifest construction, publication, read tracking, pin bookkeeping and materialization separately. Do not count only the constant-time root swap while omitting the cost of producing the edit transaction. Record live-directory fallback frequency and workflow restrictions before broader adoption; passing the service-owned subset cannot be reported as universal M2 dirty-worktree support.

Protocol: read the existing plan and Bonanza audit, consulted the primary documentation linked above, and obtained an independent adversarial design review. No runtime task-filesystem access, builds, source/product changes, snapshot creation, or benchmark/gate changes were performed.
