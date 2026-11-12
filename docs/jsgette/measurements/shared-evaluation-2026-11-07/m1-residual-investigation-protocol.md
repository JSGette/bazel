# One bounded M1 residual-cost investigation

Frozen 2026-11-12, after D034's existing-byte family ranking and before the
follow-up analysis. This consumes the one residual investigation permitted by
the N1 protocol. It does not authorize another measurement matrix.

## Question and choice

Can the unexplained restarted-request cost be attributed sufficiently to choose
between shared validation, shared evaluation, or stopping the current trace
route? Configured-target COMPUTE is the largest captured family, but neither it
nor another named family dominates the whole measured request. Select the
combined unattributed CPU/state question; do not manufacture a subtraction
between trace-on state CPU and trace-off scope CPU.

Hypothesis: deeper existing CPU stacks and actual cache counters distinguish
validation/restoration from JVM and other work sufficiently to guide the
investment decision. Counter-hypothesis: attribution or semantic/memory
coverage remains too incomplete to support an M1 savings ceiling.

Use existing recordings first. A new full-JVM profiler and retained-heap dump
could improve attribution, but neither demonstrates cross-snapshot equivalence,
replacement costs, or reclaimable peak PSS. Do not build another instrumentation
pipeline merely to obtain a more detailed version of that same missing proof.

## Exact scope and budget

- The completed twenty-row N1 analysis, SHA-256
  `c286ce13bc68394c4228229bba09422b2504df60e638e5987aacbb38c89e438b`, and
  ten-trace family summary, SHA-256
  `b1218b0c55cd36f120b596ac412d8335807a90ac3df2786d51b95b03259af68e`.
- All ten measured restarted N1 rows: five tracing off and five on. Read their
  hash-verified archived measured BEP and fresh cache statistics, ownership and
  optional phase sidecars. Preparation and `previous-*` advisory files are
  explicitly excluded from measured-cache attribution. Preserve omitted fields,
  elapsed units, nested/concurrent histograms and their different boundaries.
- Exactly the existing M0 **restart** CPU recordings from the on/off diagnostic
  archives. On archive SHA-256:
  `f0fa344da92e7eeffaf596909f50a9d8bd9b6aee5e0c972e71add156d8202676`;
  off archive SHA-256:
  `58a49f20aed91d0576e3780d8f8170cdc1b12d71e9a1b169c20c12c3c7061b87`.
  Revalidate every member size/digest before extracting only the declared
  recording, original decoder/summary and provenance. Raw recordings together
  are below 2 MiB. The historical binary/window stays separate from current N1.
- Decode those two archived recordings once with `jfr print --json --stack-depth
  2048 --events jdk.ExecutionSample,jdk.NativeMethodSample`, using the archived
  successful tool SHA-256
  `c313fd7830d5acd4ae73749bbbb48a5705e1c3bbb41957d4c3223eb636417d5a`.
  Use fresh owned destinations, maximum 128 MiB decoded JSON per recording,
  512 MiB decoder heap, 10 minutes total, under the existing task guard. No new
  build, JVM recording, heap dump, forced GC, profiler installation, source
  change, global cache purge or larger trace/replay limit.
- Classify full stacks with explicit disjoint rules; retain unknown/truncated
  stacks and compare event counts, timestamps and first five frames with the
  original decode. Inclusive caller views are separate, never added. Samples
  are not seconds and are not scaled into trace-off CPU.
- Reconcile source-defined timer/worker boundaries and the existing memory
  evidence. Heap payload, allocation, serialized bytes and daemon PSS remain
  distinct. Uncovered clients/services, native/JVM ownership, temporal peaks,
  semantic equivalence and replacement costs stay unknown where unmeasured.

The implementation may correct a parser defect with a preserved failure and a
focused regression. It may not add recordings, workloads or instrumentation
families. Publish the scripts, tests, literal commands, input/output hashes,
all results and independent review. A tooling failure is recorded, not repaired
by silently substituting a differently scoped experiment.

## Decision and stopping rule

Assess the frozen M1 criteria after this packet. Continue only if supported
equivalence and charged costs leave plausible headroom for both later gates.
Recommend shared validation only if compatible evidence establishes its
material repeated cost; sample frequency or wall histograms alone cannot do so.
Stop a modeled direction only if its generous optimistic bound misses a gate
even after permitting all unsupported cost to disappear.

If CPU **or** memory savings remains unresolved, mark **M1 inconclusive — pivot
required**, stop before M2, and present the useful proven components and the
next architectural choice. Do not claim shared evaluation is impossible. Do
not add another profiler/semantic family to postpone this investment decision.
The 50% memory, 30% CPU and 10% latency gates remain unchanged.
