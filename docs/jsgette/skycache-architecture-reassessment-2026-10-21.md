# SkyCache architecture reassessment — 2026-10-21

## Judgment

The current performance architecture is a poor fit for the measured parallel-worktree use case.
The main mistaken assumption was that validating and importing generic serialized analysis would
be substantially cheaper than local recomputation, while requiring almost no integration with
Bazel's established input tracking. The latest measurements contradict that assumption for this
workload. This is a design-level problem, not merely a shortage of threads or SSD bandwidth.

Keep the concurrent immutable storage, integrity checks, atomic publication, leases and conservative
fallback. Keep each server's graph in RAM. Treat the current implementation as a concurrency and
correctness prototype. Pause broad tuning of the current freshness pipeline until a smaller
experiment demonstrates a cheaper input-identity and import path.

This is a recommendation, not an implemented redesign. No source changes or builds were made for
this assessment. The source remains `6f451b2dc6f783dda879ff3c7ed3fc8146d0f410`.

## Evidence and economics

The [matched optimization batch](skycache-optimization-batch-2026-10-21.md) measured cache-off
19.348 s, reader 25.285 s and writer 33.766 s, with equal action-cache hit counts and matching
outputs. The reader has 4,501 configured-target hits and configures only 14 targets, versus 57,369
in controls, yet fetches 312,793 CAS values (336.8 MB) and checks 163,377 dependency records.
Reuse succeeds technically; hit count does not establish value to the user.

Cache-off has approximately 12.632 s before analysis and 5.360 s of reported analysis. The
reader's mandatory setup alone costs 3.723 s, about 70% of that analysis budget. The existing
cache does not remove the common 8.7–8.9 s repository-mapping stage. Analysis/execution boundaries
also move: pip module-extension evaluation occurs during reader execution. Thus phase numbers
are not an exact partition of potential savings; full-command time is the acceptance metric.

For a fixed writer cost and identical subsequent readers, the extra sequential wall time over
ordinary builds is approximately:

`14.418 + 5.938 * number_of_readers` seconds.

No number of current-speed readers amortizes that wall-time deficit. Holding writer cost fixed,
four readers would need to average below 15.743 s; eight below 17.546 s. These are arithmetic
break-even thresholds, not predictions or confidence intervals. Selective publication should
reduce writer cost as well as improving the reader.

Reported BEP CPU tells a different, limited story: writer uses 71.21 extra CPU-seconds while each
reader saves 8.10. About nine readers would amortize that reported CPU under fixed-cost assumptions,
but BEP can omit after-command publication work. This does not prove concurrent throughput gains.
Reader peak PSS of 5.004 GiB exceeds both controls (2.550/3.976 GiB), which matters when many agents
share a machine. The current batch is sequential, fresh-server, warm-output evidence; it does not
establish simultaneous scaling or performance on every repository.

## Decisions that created the cost

### 1. Freshness is reconstructed beside Skyframe

[LocalInputSnapshot](https://github.com/JSGette/bazel/blob/6f451b2dc6f783dda879ff3c7ed3fc8146d0f410/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalInputSnapshot.java)
scans the whole workspace during setup and again for successful publication. The source digest
is a mutation guard, not a direct revision lookup key. Independently,
[LocalDependencyValidator](https://github.com/JSGette/bazel/blob/6f451b2dc6f783dda879ff3c7ed3fc8146d0f410/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalDependencyValidator.java)
replays the saved transitive dependency proof under one monitor. Each consumer therefore pays
for another dependency-validation system alongside its in-memory graph.

Bazel already models files, symlink resolution, directory listings and dependencies in Skyframe.
Its documented incremental model invalidates affected nodes from changed inputs. That supports
investigating a narrow integration with those observations rather than maintaining independent
filesystem semantics indefinitely. It does not prove that the existing APIs expose everything
needed for cross-worktree reuse. [Official Skyframe model](https://bazel.build/reference/skyframe).

### 2. Admission is broader than the requested unit

`LocalInputSnapshot.globalWitnesses()` promotes all non-workspace witnesses to publication guards.
`LocalCacheSession.refresh()` validates those guards before opening the pack, then per-entry
validation follows. Memoization avoids repeated hashing within the command, but admission can
still inspect external inputs irrelevant to the requested entry. A publication is a storage and
visibility unit; it should not automatically become the validity unit for all its contents.

The required property is that the chosen result's actual inputs match, including configuration,
environment, repository-name bindings, listings, absent paths and symlink chains. Concurrent safe
publication does not itself require scanning unrelated source files or every external witness.

### 3. Object sharing was optimized before reuse economics

The cache is already content-addressed and packed. Recommending CAS as a replacement would miss
the problem. Fine-grained object sharing produces hundreds of thousands of lookups, integrity
checks, dependency records and reconstructed objects to save a few seconds of analysis. Coalescing
record I/O improves one factor; it does not change the volume of proof or import work.

Physical transfer units can be coarser while logical keys remain selective. A reusable analysis
unit could reference compact, deduplicated evidence tables and immutable transfer segments.
Flattening a full transitive witness list into every target would risk another size explosion;
shared tables or chunks are essential. No bundle size or unit boundary is selected without data.

### 4. Permission to publish became a broad publication policy

Local mode forces `async_bidi`. Eligible entries flow to serialization and command completion waits
for upload work. Every server may safely write, but it need not serialize every eligible result.
Any worker should be able to publish missing, expensive, likely reusable analysis units; cheap
units can be recomputed. Selection must happen before expensive serialization. Dedupe afterward
does not recover the writer's CPU and allocation cost. Async overlap does not remove that cost.

### 5. Transport abstraction does not yet imply portable identity

Project namespace identity contains the absolute Git common-directory path, and external absolute
paths can remain in payloads. These are defensible local constraints. Adding gRPC alone would not
make entries portable across machines. Future transport needs logical project/repository/toolchain
identities, a compatibility contract, batched transfer and a remote retention protocol. This is a
separate future design concern, not the immediate reason for the local regression.

## Recommended design direction

1. **Retain the tested storage machinery and service boundary.** All workers remain capable of
   reading and publishing. No designated global writer, shared mutable graph or graph paging.
2. **Bind reusable analysis to the input observations that produced it.** Obtain observations from
   Bazel's input/dependency tracking where practical, with narrowly scoped hooks if necessary.
   A compact certificate should identify the required files/listings/absences/symlinks, logical
   repository bindings, configuration and relevant environment. Validate shared observations once
   per command and only for the requested closure.
3. **Separate immutable shared input identities from mutable worktree overlays.** A verified
   immutable base can be shared; each worktree's actual dirty state must still be accounted for.
   Git commit/tree IDs can find candidates, but cannot establish live filesystem identity alone.
   Watcher gaps, untracked/ignored inputs, mode or symlink changes, generated/external inputs and
   repository rebinding require sound handling and conservative fallback.
4. **Select reusable units for measurable benefit and transfer them in batches.** Preserve existing
   codecs initially; change evidence and read policy before attempting a wholesale format rewrite.
   Choose units using measured computation, serialization, validation and import costs.
5. **Prove the design with a constrained comparison before expanding it.** Start with the same
   Datadog target and a deliberately analysis-heavy case. Measure empty-cache write, independent
   reader and small-edit reader against feature-off, with matching outputs and action-cache state.
   Evaluate complete command time, CPU, peak memory and ultimately two-server throughput. A new
   design must beat recomputation by a useful margin; more hits alone are not a success criterion.

The hardest part is a cheap, trustworthy input identity. Content addressing or a Merkle root does
not make obtaining that identity free. A first-time mutable worktree still needs its relevant
inputs checked unless a trustworthy version/change mechanism supplies the evidence. A compact
certificate reduces metadata/I/O overhead, but cannot simply eliminate that requirement.

The current full-tree guard cannot safely be deleted as a cleanup. File evidence is captured
during serialization, potentially after analysis consumed the inputs. Replacing the guard requires
binding certificates to evaluated observations or a stable/versioned input view. This is the
correctness question for the proposed narrow prototype, not a reason to weaken checks for speed.
A limited input-tracking integration may be necessary; a rewrite of Skyframe's evaluator is not
justified by the evidence. Its feasibility and performance remain to be demonstrated.

## Process correction

The previous work established safe concurrency and broad mutation coverage before establishing
that import could beat recomputation. I should have set a reader/writer performance gate earlier,
before investing further in protocol breadth. Keep the resulting correctness tests and storage
work, but let measured reuse economics determine whether and where the cache is enabled.
The earlier idle-retention claim was already retracted after tracing inherited cleanup; it is
not evidence for this architectural judgment.
