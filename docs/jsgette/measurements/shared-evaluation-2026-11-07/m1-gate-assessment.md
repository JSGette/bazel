# M1 assessment: inconclusive, pivot required

Decision: 2026-11-12. **Stop the current dependency-trace route before M2.**
The bounded investigation did not establish a defensible CPU and memory savings
ceiling. It did not establish that shared evaluation is impossible. The frozen
50% memory, 30% CPU and 10% latency goals remain unchanged.

## What passed and what it means

| Evidence | Result | What it establishes |
| --- | --- | --- |
| M0 baseline | 29 matrix rows and two diagnostics completed | Repeated retained state warrants investigation; CPU attribution is partial |
| Native hosting | Qualified cap-ten implementation, 21 acceptance checks, completed private/native N10 diagnostic | A useful way to share process services while keeping private evaluator graphs |
| Typed collector | Product build, thirteen Java suites, eight-build/five-trace interoperability smoke passed | Functional capture/replay for the tested types and mutations |
| N1 trace controls | All twenty sessions, forty builds and ten wire replays passed | Correct outputs and reproducible coverage/perturbation observations |
| Residual audit | All ten restarted cache/BEP evidence sets verified; source boundaries reviewed | Restoration is active, but counts and wall histograms do not partition CPU or memory |
| Deeper historical CPU decode | Neither of two admission attempts launched the decoder | No new full-stack result is available; both infrastructure failures are preserved |

The [N1 report](m1-trace-n1-diagnostics.md) includes every fixed pair. Tracing
increased prepared-request CPU by a median 26.4% on restarted requests and
101.0% on warm requests; median wall increases were 17.1% and 96.8%. These are
observer comparisons, not candidate-architecture regressions against an
invented tracing threshold.

The [function summary](m1-trace-family-coverage.md) finds configured-target work
first in each restarted trace, with median captured exclusive COMPUTE CPU
1.679 seconds, then module loading at 1.255 seconds and Bzl loading at
0.565 seconds. These families' semantic identities remain unsupported. More
than 99% of observations are unsupported; the exact workload subset is
main-root directories and nonexistent FILE_STATE values. N1 repetitions do not
provide cross-engine sharing credit.

## The one residual investigation

The [frozen protocol](m1-residual-investigation-protocol.md) selected the combined
unattributed restart cost instead of assuming it was all validation or GC.
The [verified sidecar audit](m1-residual-sidecars.json) covers every one of the
five off/on restart pairs. Each measured request records:

- 3,272 configured-target cache hits and 114 misses;
- 59,136 CAS gets, all hits, and 3,272 restore-root admissions;
- 21,278,547–21,294,616 serialized bytes charged to the restore budget across the
  ten requests, a representation
  measurement that is not retained heap or saved PSS.

The restore byte budget also charges shared CAS reads and retains charges for
failed candidates; it is not solely successful root payload. The separate BEP
hit count supplies evidence of successful configured-target retrieval.

Client/store/retrieval histograms have different populations and overlapping
elapsed boundaries. They cannot be added or converted into cache CPU. Missing
fields remain explicit. Fresh command sidecars were separated from the
preparation files copied under `previous-*`. Three focused audit tests and
root review passed. [Implementation and evidence](evidence/m1-residual-investigation/).

The [source audit](m1-cache-cost-audit.md) establishes that synchronous retrieval
and witness validation can fall inside configured-target COMPUTE, while
shared decoding uses the common pool and final validation can use additional
workers. The built-in profiler stops before cache finalization, even though
that finalization occurs before client return. Its small post-response tail
cannot bound the omitted work.

The original JFR decoder displayed only five stack frames. Deeper decoding of
the two archived restart recordings was attempted without new capture. Attempt
01 exhausted the inherited 10-GiB job's host-memory admission timeout before
launch. The [decoder-only resource correction](m1-residual-decoder-budget-addendum.md)
retained the host reserves and reduced only the offline job's maximum to
1536 MiB. Attempt 02 stopped before launch because the unchanged mount-inspection
helper entered an unrelated sandbox namespace without `/proc/self/mountinfo`.
Neither attempt produced a recording, decoder result or workload measurement.
The prepared full-stack analyzer passed seventeen synthetic tests and static
review; it was **not run on historical full stacks**. No third attempt was made.

Startup CPU profiling and retained Java-heap capture are technically possible
without another Bazel source change. They could improve attribution; they
cannot alone establish cross-snapshot equivalence, live replacement costs or
reclaimable peak PSS. The absence of deeper stacks therefore adds an attribution
gap, rather than supplying the reason to declare the architecture impossible.

## Savings ceiling and gate outcome

| Requirement | Evidence still missing | Outcome |
| --- | --- | --- |
| CPU headroom | Compatible whole-request attribution, supported cross-snapshot equivalence and charged replacement work | Unresolved |
| Memory headroom | Complete control-process coverage, disjoint ownership at compatible times, reusable representations and service/index costs | Unresolved |
| Validation-specific pivot | Evidence that repeated validation consumes most of the prospective savings | Not established by counts or sampled/wall timing |
| Optimistic impossibility bound | A justified unavoidable remainder after allowing every unsupported cost to disappear | Not established |

All unknown CPU/state remains potentially removable in the optimistic case.
Cheap supported leaves do not cap the rest of the architecture. Conversely,
granting unknown state free perfect sharing is not evidence for continuation.
Projected bytes, Python allocations, post-GC payload and daemon PSS remain
different measures. Neither the CPU nor memory ceiling is decision-ready.

**M1 is inconclusive. M2–M6 remain not started.** This is the planned investment
stop after one bounded residual investigation; no threshold was softened and no
failed infrastructure attempt was relabeled as an architectural performance
failure. The task runtime was unmounted, its loop device detached and its
verified keeper stopped; the image and evidence remain preserved. Unrelated
workloads were left running.

## Recommended architectural pivot

Retain the qualified native-hosting implementation as a useful component.
Its preliminary N10 comparison reduced build-window non-harness CPU about
66.3% and sampled daemon PSS about 24.7%; whole recorded lifecycle CPU fell
20.1%. It retains private graphs, used this fork's private mode as comparator,
and ran under heavy reclaim/I/O pressure. It is neither a shared-graph result
nor an isolated-SkyCache M4 pass. [Native assessment](native-n10-assessment.md).

For a new architecture experiment, change the unit of investigation to a
**completed, immutable result bundle with its dependency closure**, following
the useful graphlet idea in the [pinned Bonanza source audit](bonanza-contract-notes.md).
Keep snapshot identity, logical request, result identity, invocation and output
view separate. Treat output base as a presentation/lifecycle adapter; deleting
it must not invalidate an immutable result. Do not begin by merging live
Skyframe graphs or adding projection types one by one.

The next proposed experiment would deliberately replace the inconclusive M1
route with a small end-to-end bundle test: one real rule family, explicit
immutable input/repository bindings, a fresh second worktree without its own
preparatory build, exact output/mutation oracles, and fully charged capture,
validation, transfer, decoding and materialization. Test portability and
conditional-dependency correctness before measuring ten-worktree economics.
Use a new recorded protocol and the same economic goals. This is a proposed
pivot, **not work started or a claim that M2 passed**.

Global phase-barrier removal remains a separate experiment. The evidence here
does not connect it to the measured memory/CPU gaps, so combining it with the
bundle experiment would obscure that experiment's outcome.
