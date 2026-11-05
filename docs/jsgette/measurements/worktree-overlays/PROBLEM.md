# Concurrent, lazy builds over workspace overlays

Several developers or agents start from the same Git commit and make different uncommitted edits. They submit **N concurrent build requests**, potentially from different overlays or with different options on the same overlay. Much of the work may be identical. How can a build system discover what each request needs, execute shared work once when it can establish equivalence, and keep memory use tied to current demand rather than the size of every workspace graph?

This is a research problem. The [Bazel multi-worktree branch](https://github.com/JSGette/bazel/tree/4f4b44b5a2c1780cdbb0215c4556de39d0f0a995) gives us a concrete point of comparison. The choice of source representation, evaluator, and scheduling algorithm is open.

## A representative build

A and B are two of those requests. Their overlays start at the same commit. A edits the application. B leaves the application alone and adds a header matched by a glob. Both request the same target. Other builds may request the same library, request something unrelated, or target a different platform. A third edit arrives in A while those builds run.

Each request should see a coherent version of its own sources. Both builds should be able to make progress. A library compile can serve both if its inputs and execution conditions are equivalent. The glob must see the added header only in B. Work in an unrelated part of the repository should impose little cost on either request. Finishing or cancelling A should let B keep using work it still needs.

## Desired properties

- **Independent views.** An overlay represents uncommitted additions, edits, and deletions relative to a base. A running build should be able to keep its view while editing continues. Publishing a view should not require a user commit or disturb the user's staging area.
- **Demand-driven discovery.** Start from requested targets and discover prerequisites as needed, including dependencies that become known during evaluation. Avoid enumerating an entire source tree or constructing its entire build graph as an admission cost.
- **Bounded resident state.** Keep enough state to make progress on active work. A small request in a large repository should not require the graph of the whole repository in RAM. Account for waiting parents, dependency lists, ready work, and completed-result indexes as well as running jobs. A small worker count alone says little about this footprint.
- **Reuse with a defensible identity.** If two requests reach the same eligible operation, one may join the other's execution. If the second request arrives after the first has left memory, a retained result should still be discoverable and usable. Equality of target names or base commits alone is insufficient. For example, a previously absent file, a changed compiler option, or a different execution platform can change the answer.
- **Concurrent progress and isolation.** The number of build requests may exceed the number of active execution slots. Independent work can proceed within a shared budget while waiting requests retain a way to resume. Each request must receive its own correct outputs and diagnostics, including when many requests share one producer.

## Constraints and open boundaries

The design must admit implementations on **Linux, macOS, and Windows**. Its semantics cannot depend on a facility available on only one OS. A Git object and a checked-out file can contain different bytes. Path identity, file types, executable mode, and what a build can actually read must be explicit wherever they affect equality.

We need to define how a mutable overlay becomes a stable view. An agent that supplies an edit batch offers a different consistency boundary from arbitrary tools writing into an ordinary directory. We also need to define the scope of the promise to avoid duplicate work. Sharing an eligible operation across healthy concurrent builds, retaining its result after memory eviction, and recovering after a crash are three different guarantees. Operations with side effects or unknown dependencies need their own eligibility rules.

A Git-aware driver or a small, committed description of the repository may help reconstruct the requested part of a graph. Its contents, ownership, and maintenance cost are open questions. Missing or outdated metadata, including a base index invalidated by an overlay edit, must not silently change the build result.

## Questions the experiments should answer

1. Can two independently edited views share unchanged source data while preserving what each build reads, including a directory listing or a missing file?
2. Can a bounded execution **window** discover and complete a small request without a hidden in-memory backlog proportional to the graph? What happens with a long chain, a wide fan-in, and many parents asking for the same prerequisite?
3. What information must survive outside that window to recognize overlapping work and later reuse a completed result? When does looking it up or reconstructing it cost more than repeating the work?
4. Would a Git integration, a committed structural index, or a locally generated index save more discovery work than it costs to maintain and validate? Which information still has to come from the effective overlay?
5. Which of these properties can live outside Skyframe, and what observation would justify changing or replacing the evaluator?

The initial experiments should use small inputs with known answers. Compare output identities and count expansions, executions, and retained records before interpreting timings. A production implementation and representative Bazel benchmarks come after we know which model is worth testing at that scale.

The [research findings and reproduction commands](<reports/Lazy evaluation across workspace overlays.md>) cover the first three experiments.

The [package lookup and glob experiment](<reports/Resuming a Bazel package glob.md>) checks resumable discovery and committed hints against fresh Bazel answers for eight overlays.

The [Datadog Agent benchmark](<reports/Seekable discovery on the Datadog Agent.md>) measures where seekable directory indexes and concurrent work sharing pay off on real repository data.

The [concurrent build stress tests](<reports/Concurrent Datadog Agent build stress.md>) exercise overlapping builds, output isolation and cancellation on pinned Datadog Agent inputs.

The [shared action ownership MVP](<reports/Shared action ownership MVP.md>) tests bounded producer admission, cancellation and completed-result reuse across independent build clients.

The [direct execution experiment](<reports/Direct execution across Datadog worktrees.md>) uses Bazel's actual remote execution interface to compare shared and independent compilation across four and eight Datadog worktrees.
