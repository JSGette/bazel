# Organizing computation across workspace views

## What can two overlays actually share?

### Takeaway

An immutable source view identifies the answer to a source lookup. Two different views can give exactly the same answers to every question a particular computation asks. Build reuse follows those answers and any non-source inputs the computation observes. A common Git parent alone says little about this equivalence.

### Cited Findings

- Vesta built software using a functional modeling language. Its cache uses a primary key to find candidates and checks the fine-grained dependencies that a previous execution actually used. Its model cache even has both a coarse immutable-directory entry and a second entry that depends on the imports used. The authors identify overly broad dependencies and missing dependencies as separate failure modes. [Project cache documentation](https://vesta.sourceforge.net/doc/sdl-ref/caching.html), [Heydon, Levin and Yu, PLDI 2000](https://vesta.sourceforge.net/doc/pubs/pldi-00-04-20.pdf).
- Microsoft's BuildXL independently documents two-phase lookup using static information and the observed path sets of candidate executions. When too many candidates share a weak fingerprint, checking the path sets can degrade performance. Its graph reuse separately accounts for read files, directory memberships, queried environment variables and mount paths. These are two distinct levels of reuse. [Two-phase cache](https://github.com/microsoft/BuildXL/blob/main/Documentation/Wiki/Advanced-Features/Two-Phase-Cache-Lookup.md), [graph reuse](https://github.com/microsoft/BuildXL/blob/main/Documentation/Wiki/Advanced-Features/Graph-Reuse.md).
- Roslyn's workspace exposes a current solution, but each acquired solution is immutable. A caller can construct a new solution from an existing one and edits without applying it to the live workspace. Projects carry sources, compilation options and references. This is a useful analogy for pinning a source view while its editor continues changing its head. [Roslyn workspace model](https://github.com/dotnet/roslyn/blob/main/docs/wiki/Roslyn-Overview.md#solutions-projects-documents).
- Skyframe describes computations that register dependencies and can discover further dependencies in successive rounds. The pinned branch's glob implementation observes directory entries and package lookups. Its package lookup tests potential BUILD file names in priority order and can return the absence of a BUILD file. [Skyframe](https://bazel.build/reference/skyframe), [glob and subpackage check](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/lib/skyframe/GlobFunctionWithMultipleRecursiveFunctions.java#L74-L103), [glob directory read](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/lib/skyframe/GlobFunctionWithMultipleRecursiveFunctions.java#L128-L188), [package lookup](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/lib/skyframe/PackageLookupFunction.java#L150-L185).

### Inferences

- Use a pinned view root for selecting a consistent set of source answers. Use `query + implementation + known inputs + context` to identify an explicitly pure operation. Its explicit arguments can contain subtree or file identities when the operation needs the entire subtree or file. For example, a line-start table can depend on the source text alone. An operation rendering path-specific diagnostics also observes the source name. A build may separately observe configuration, a toolchain, external repos, environment or the Git commit through workspace stamping. Two identical source trees can still differ on these dimensions.
- For Linux, macOS and Windows, give the view a defined logical path namespace and identify the representation that a particular consumer observes. Git can turn LF into CRLF or apply a smudge filter when checking out a blob. A raw-byte operation can therefore see different inputs from the same Git object. Key it by the actual byte representation. A compiler may also observe compiler/toolchain, execution and target platform. A host-neutral query over the same content needs only its own semantic inputs. [Git checkout attributes](https://git-scm.com/docs/gitattributes), [Bazel platforms](https://bazel.build/concepts/platforms).
- For a deterministic pure query with dynamic dependencies, a prior result may be reused from another view if the new view satisfies the prior observations. Capture what was asked as well as its result. `read(p)` can observe bytes, `lstat(p)` can observe type, and `list(p)` can observe names and types without observing file bytes. A directory-tree digest can serve as a conservative fast proof of equality for a traversal contained within it. A mismatch only says to look closer. A recursive directory hash changes when file content changes, even when the observed directory membership stays equal. A symlink escaping the subtree requires separate observations along its resolution.
- Same-commit, same-label concurrent requests can discover different inputs. They cannot safely join one in-flight analysis on that pair alone. They can join an explicitly pure operation after deriving all its key inputs, or validate a completed candidate against the second view. A whole-view fast path also needs equal non-source context and a portable result. Opaque or side-effecting computations can remain independent.

### Gaps

- These precedents establish engineering choices. None quantifies the benefit of this architecture for the branch's target workloads or proves its Bazel values are portable. Candidate validation cost, path-bound values and non-source context all matter.

## How could computation follow sibling views?

### Takeaway

Four arrangements separate the identity of a source view from the lifetime of its evaluation. Only the last requires an evaluator to hold several divergent graph versions directly.

### Cited Findings

- The branch explicitly distinguishes sharing immutable directory and line-table payloads from sharing graph structure. A configured-target key contains label, configuration and related distinctions but does not encode an entire source view. The current in-memory evaluator specifies non-concurrent calls and a monotonic integer version. The abstract `Version` interface describes a partial order with incomparable versions. [Branch roadmap](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/docs/jsgette/plans/multi-worktree-completion-roadmap.md#L272-L300), [configured-target key](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/lib/skyframe/ConfiguredTargetKey.java#L65-L121), [concrete evaluator](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/skyframe/InMemoryMemoizingEvaluator.java#L25-L34), [version interface](https://github.com/JSGette/bazel/blob/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995/src/main/java/com/google/devtools/build/skyframe/Version.java#L18-L48).
- The build systems literature separates scheduling from deciding whether a result can be reused. A constructive trace stores the computed value together with dependency evidence. Multiple candidates for a request enable reuse after divergent input histories. [Build Systems à la Carte, §§4.1–4.3](https://www.microsoft.com/en-us/research/wp-content/uploads/2018/03/build-systems.pdf).

### Inferences

1. **Independent evaluations over shared snapshots.** Keep private graph state for each active view and share source blobs/trees by content. This tests whether giving work a first-class source identity already solves the desired organization. It offers isolation and shared representation, while analysis can still be repeated.
2. **One immutable base analysis with private shadows.** Freeze only the parts of a base evaluation that actually exist. When A changes a source observation, follow its base reverse dependencies to mark affected records in A. B retains its own marks. Recompute affected nodes on demand and keep their revised edges and values private. The graph's shadow contains the dependency closure of changed observations, potentially much larger than the source delta. A's new dependency absent from the base graph must be recorded in A for A's next edit. Unbuilt base nodes are ordinary misses. This model pays for a base warmup and is attractive when many siblings reuse it. It does less for computations first discovered in sibling views.
3. **Shared immutable computations discovered on demand.** Do not require a warm base. Either view can be the first producer of a cached result. Start with exact keys for operations with explicit inputs. For dynamic queries a two-stage lookup can compare a previously recorded trace against the requesting view before serving a successful, portable result. A mutable graph need not be shared to do this. Many distinct access patterns under one logical request, or validation nearly as expensive as recomputation, can make this choice unattractive.
4. **A graph with branching versions.** Store several immutable value/dependency records for a logical key and select the appropriate records under each pinned view. Siblings are incomparable. A later timestamp in B gives no validity guarantee about a value computed in A. Changed edges, missing results and in-flight work have to belong to the right view. Persisting the source tree does not supply these semantics. Generalizing Skyframe's graph storage, replacing it, or retaining private evaluators are independent research alternatives. Reconsider a branching evaluator after simpler arrangements demonstrably leave substantial repeated *derived* work whose observable dependencies can be described.

### Gaps

- No trace from the branch measures how often different views have the same observable inputs, how deep their affected closures are or which derived outputs can be shared unchanged. Those unknowns distinguish the alternatives better than raw graph-node counts.

## What small observation could distinguish the models?

### Takeaway

Replay a few read-only queries over pinned sibling source views, comparing each reused answer with a fresh evaluation. This is a model experiment. It needs neither a concurrent daemon nor a Bazel rewrite.

### Cited Findings

- BuildXL tracks both probes for absent files and directory enumerations because future additions may affect a computation even if all files it previously read remain identical. Its docs also describe the limitations when a process sees a different directory than the one fingerprinted. [Filesystem modes and enumerations](https://github.com/microsoft/BuildXL/blob/main/Documentation/Wiki/Advanced-Features/Filesystem-modes-and-Enumerations.md).

### Inferences

- Example: base C contains `lib/BUILD` and `lib/a.cc` and the BUILD selects `glob(["*.cc"])`. A edits only `docs/readme`. B adds untracked `lib/b.cc`. The base and A have different view roots and the same glob result. B shares the same Git parent and original `.cc` bytes, yet its glob result differs. If B instead adds a higher-priority `lib/BUILD.bazel`, all previously read file bytes may still match while the selected BUILD file changes. The observation that `lib/BUILD.bazel` used to be absent rules out the stale result. Reverting B's addition can recover the old result regardless of overlay name or generation. A second query that consumes file bytes rather than names distinguishes changing the contents of `a.cc` from changing directory membership.
- For a paper or small offline replay choose `lookupBuildFile(dir)`, `listMatching(dir, '*.cc')` and `read(path)`. Compare per-view keys, a warmed base record with per-view invalidation, and candidate trace validation. Count stale answers, avoidable recomputations, changed graph records and observations checked per recovered result. Unrelated-edit misses expose over-keying. An omitted absent probe, added directory member, or reading A's state when evaluating B exposes under-keying. Repeat after reconvergence and with a computation first discovered in a sibling. Defer targets, action execution and migration questions until these models yield different, credible predictions about real work.

### Gaps

- The example is a proposed discriminating experiment. It supplies no performance measurements and does not claim correctness for arbitrary Bazel operations.
