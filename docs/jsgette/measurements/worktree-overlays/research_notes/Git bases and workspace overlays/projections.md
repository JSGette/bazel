# Ways to expose and capture workspace overlays

## How have other systems represented uncommitted work on a shared base?

### Takeaway

Two production precedents make a sparse overlay tangible. EdenFS keeps one backing repository and one overlay per editable checkout. Google's published CitC design also saves revisions of unsubmitted work and exposes them to build and review tools. The latter is the closer precedent if builds need stable handles to in-progress edits.

### Cited Findings

- Git can check out the same commit in separate detached worktrees. Each has a private working directory, HEAD and index, with shared repository storage. This is a ready editing surface. The commit and index describe different things from the live, unstaged filesystem. [git-worktree](https://git-scm.com/docs/git-worktree#_commands) [git-status](https://git-scm.com/docs/git-status#_description)
- EdenFS's design separates repository source-control data from an editable checkout. Each mount tracks inodes, its own overlay of locally modified files and directories, and a journal of recent changes. Reads of original data can fetch by source object ID. Its on-disk SNAPSHOT record identifies the checked-out source-control commit, while the local overlay holds modified files. [EdenFS process state](https://github.com/facebook/sapling/blob/main/eden/fs/docs/Process_State.md) [EdenFS terminology](https://github.com/facebook/sapling/blob/main/eden/fs/docs/Glossary.md)
- EdenFS differentiates whether an inode has private stored data from whether its final content is different. A rename can preserve its backing object and a rewrite to the original bytes can leave an inode materialized. A list of writes or materialized files is consequently a *candidate* for deriving semantic differences. [EdenFS inode storage](https://github.com/facebook/sapling/blob/main/eden/fs/docs/InodeStorage.md)
- The 2016 Potvin and Levenberg paper describes CitC workspaces with local edits overlaid on Piper through a FUSE presentation. The workspace stores changed files, and the system records revisions of uncommitted writes. Developers could identify, share and restore workspace snapshots, and Google's build and test tools could access in-progress work. The paper distinguishes this workflow from submitting source changes. [Google Research publication](https://research.google/pubs/why-google-stores-billions-of-lines-of-code-in-a-single-repository/) [Published paper, pp. 80–81](https://acm.cs.uic.edu/pipermail/acm-announce/attachments/20170228/68cff66e/attachment-0001.pdf#page=4)

### Inferences

- A workspace name such as A can persist while its edits evolve. A build can take A at revision 12 and an editor can advance A to 13. Cross-workspace comparison then concerns the contents of the pinned revisions. Two mutable directories over the same commit do not imply either pinned revisions or equal observations.
- Take the *concept* of separating the shared source store, workspace state and named immutable revisions from these precedents. No specific choice of filesystem protocol, repository service, checkout product or evaluator follows from that separation.

### Gaps

- CitC's paper does not specify the data structure, an atomic multi-file transaction protocol, or a way for stock Git or Bazel to use it. EdenFS's per-checkout journal description alone does not establish that it stores historical bytes or can reconstruct an arbitrary past dirty tree.

## Which storage or filesystem boundaries could deliver these views?

### Takeaway

For Linux, macOS and Windows together, the common representation can be a Git base, a platform-independent tree of edit contents and names, and ordinary private directories materialized from a pinned revision. A live union mount gives independent write namespaces. A filesystem snapshot additionally supplies a frozen local read view. A source-object virtual filesystem supplies lazy materialization. They can implement different parts of the same logical model.

### Cited Findings

- Linux OverlayFS can share one lower directory across mounts with a separate non-overlapping upper and work directory for each. Lookup gives upper entries precedence, directories can merge, whiteouts hide removed lower names and an opaque directory hides inherited children. Operations such as writing a lower file can copy it to the upper layer. The kernel forbids external changes to the underlying layers of an active mount. [Sharing layers](https://docs.kernel.org/filesystems/overlayfs.html#sharing-and-copying-layers) [Whiteouts and opaque directories](https://docs.kernel.org/filesystems/overlayfs.html#whiteouts-and-opaque-directories) [Copy-up and non-directory objects](https://docs.kernel.org/filesystems/overlayfs.html#non-directories) [Underlying filesystem changes](https://docs.kernel.org/filesystems/overlayfs.html#changes-to-underlying-filesystems)
- Btrfs subvolume snapshots share extents with the original tree. A writable snapshot can be edited independently, and a snapshot can be created read-only. Creating a snapshot may first have to flush dirty data and nested subvolumes are excluded. Its subvolume ID and UUID name instances of a filesystem object. [Btrfs subvolumes and snapshots](https://btrfs.readthedocs.io/en/latest/btrfs-subvolume.html)
- EdenFS can expose repository data through a filesystem while fetching source objects on demand. Its authors describe redirects for write-intensive output directories such as buck-out, bypassing the virtual filesystem for generated data. [EdenFS overview](https://github.com/facebook/sapling/blob/main/eden/fs/docs/Overview.md) [EdenFS redirections](https://github.com/facebook/sapling/blob/main/eden/fs/docs/Redirections.md)
- Git provides distributions or installation instructions for Linux, macOS and Windows. Detection of file modes, case sensitivity and symlink support depends on the local filesystem. Git may check out a recorded symlink as a small regular file when core.symlinks is false. Checkout policy can convert LF to CRLF or apply a content filter. [Git on all three systems](https://git-scm.com/install/) [Git configuration](https://git-scm.com/docs/git-config#Documentation/git-config.txt-coresymlinks) [Git attributes](https://git-scm.com/docs/gitattributes)
- Apple documents APFS copy-on-write cloning of files and directories and read-only snapshots of a **volume**. Cloning one file or copying several files individually is a distinct operation from taking a point-in-time view of one whole volume. [Apple Platform Security](https://support.apple.com/guide/security/role-of-apple-file-system-seca6147599e/web) [Apple snapshot documentation](https://support.apple.com/en-gb/guide/disk-utility/dskuf82354dc/mac)
- Windows ProjFS lets an application present its backing store as files under a virtualization root. It requires enabling an optional Windows component. EdenFS illustrates platform specialization with FUSE on Linux, NFS on macOS and ProjFS on Windows. [Microsoft ProjFS overview](https://learn.microsoft.com/en-us/windows/win32/projfs/projected-file-system) [Enabling ProjFS](https://learn.microsoft.com/en-us/windows/win32/projfs/enabling-windows-projected-file-system) [EdenFS daemon interfaces](https://github.com/facebook/sapling/blob/main/eden/fs/docs/Process_State.md)

### Inferences

- In a Linux setup, one could materialize the base once, expose one OverlayFS mount per editing surface, and make each edit through its own mount. The mount remains live as the writer continues, and its upper is a filesystem-specific encoding of changes. Capturing coherent stable contents is a separate operation.
- Another local approach is one clean source subvolume, writable clones A and B, and read-only snapshots of those clones for build requests. This pushes capture into the filesystem and allows ordinary tools to open files by path. A content identity portable to a second machine would still require a canonical comparison or hashing scheme. Cloning only source bytes avoids duplicating worktree control metadata or unintentionally capturing build outputs.
- EdenFS-style path virtualization is interesting when fetching or checking out the entire base is costly. If ordinary tools already write conventional worktrees, capturing the selected observable contents at submission might be simpler than owning every write through a new mount. A filesystem provider's journal can narrow the capture set, but durable old bytes and consistent multi-file capture require a separate guarantee.
- Portable reference path, proposed. A named overlay advances with an explicit edit batch supplied by a cooperating writer. Each batch records bytes, types, path removals and resulting directory contents in immutable storage before publishing the new root. Alternatively import a directory while its writers have stopped. Without one of these boundaries, a best-effort scan can preserve the resulting captured tree but cannot promise that it equaled the live directory at any one instant. Materialize or copy the pinned tree into a build-private ordinary directory when native tools need paths. Keep generated outputs elsewhere. This common path incurs I/O, but its contract can be compared across hosts without a privileged mount or filesystem snapshot provider.
- A universal spelling of names and bytes in a manifest does not imply that every host filesystem can represent them. Distinguish the logical snapshot ID from a projection policy. For a portable first study, use representable names and regular files with explicit bytes. For a counterexample, add both `Foo.h` and `foo.h` and ask a case-insensitive checkout to materialize them. Reject an unrepresentable view explicitly. Likewise decide whether a reader is promised Git object bytes or checkout bytes before equating content IDs across machines. A future evaluator can include a projection/context key if it observes host-dependent behavior.

| Host | Ordinary common path to compare | Optional local capability to study | Boundary to preserve |
| --- | --- | --- | --- |
| Linux | Private directory with files materialized from an immutable root | OverlayFS for writable views, Btrfs snapshots on appropriate volumes | OverlayFS upper remains live until separately captured. Btrfs snapshots exclude nested subvolumes. |
| macOS | Same immutable root and ordinary directory | APFS clones for cheap files, optional APFS volume snapshot for capture | A clone of one file describes that file. File-by-file copies do not establish a common capture point for the tree. |
| Windows | Same immutable root and ordinary directory | Optional ProjFS for lazy checkout projection | A provider and its write/capture semantics are still required. Name availability, executable intent and link representation need a defined projection policy. |

### Gaps

- These sources document different platforms and consistency guarantees. They establish precedent, not comparative overhead on this workload or a need to provide any particular mount. A per-file sequence of observed writes does not itself establish an all-files snapshot if processes can write concurrently.

## Where would Bazel consume an overlay, and what can a small study distinguish?

### Takeaway

The editor sees a workspace, a build pins an effective source tree, and an action consumes the input paths chosen for its command. A whole-workspace root is useful to pin an invocation. Replacing each action's input identity with that root would make unrelated edits defeat reuse.

### Cited Findings

- The Remote Execution API specifies an Action containing a command digest and an input root digest. Directory protos and blobs referred to by that root are stored separately in CAS. The action also has execution properties such as platform and timeout. Bazel's sandbox documentation describes the per-action execroot of inputs and generated outputs. [Remote Execution API, Action and Directory](https://github.com/bazelbuild/remote-apis/blob/main/build/bazel/remote/execution/v2/remote_execution.proto) [Bazel sandboxing](https://bazel.build/docs/sandboxing)
- Bazel describes package loading and analysis as the steps that determine targets, dependencies and actions. Source listings can matter before any action exists. A glob over a package changes when an untracked matching input appears even if every previously enumerated input blob is unchanged. [Bazel build phases](https://bazel.build/extending/concepts) [Bazel glob semantics](https://bazel.build/reference/be/functions#glob)

### Inferences

- These layers answer distinct questions. A mounted tree lets existing tools read paths. A pinned source root says what the input namespace looked like. An action digest can independently match between two such roots if the action's inputs and execution context match. Sharing backing blobs or a mount does not make prior package analysis valid in a second view.
- Proposed paper exercise. Use a base with `pkg/BUILD` declaring a glob of H files, one checked-in `pkg/base.h`, and an unrelated text file. Pin A1 after adding `pkg/bonus.h` and pin B1 after deleting `pkg/base.h`. Then edit A again while both views would be read. Trace the glob listing and a read of a common, untouched file in both pinned views. Ask each representation to name which bytes, listings, and missing paths the build sees, where they reside, and how it retrieves them after the edit. Extend the trace with two overlays containing the same final tree through different edit sequences. This distinguishes isolation, capture, equality and execution reuse without implementing a daemon or running a benchmark.

### Gaps

- The REAPI tree covers inputs to an already constructed action. Using it for the source namespace requires separate treatment of analysis-time lookup, filesystem visibility, source policy and external inputs. Neither provider latency nor a build-speed gain follows from the model. No build was implemented or executed for these notes.
