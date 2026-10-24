# SkyCache proof structure decision and experiment log — 2026-10-24

This continues the v2 design and idle-retention prototype. The latest user request
authorizes repeated measurements and supersedes the earlier three-attempt cap.
Execution is in the connected Linux cloud workspace (4 CPU quota, 16 GiB memory),
not the user's PC: no PC execution connection is exposed. Do not relabel these
results as desktop measurements.

## Decision before changing the validator

Keep content-addressed dependency DAGs as the durable proof representation. A DAG
shares dependencies across roots; a strict tree would duplicate them. The current
implementation already has this representation and invocation-local positive
subtree memoization. Adding another tree of the same hashes alone cannot help.
First remove avoidable serialization between independent roots. Keep the existing
format, checksums, witness semantics, publication checks and leases.

There is no universal fastest data structure. Flat witness arrays can be better
for disjoint or dense batch work. A compact DAG avoids repeated edges for heavily
shared closures and supports demand-driven reads. Use a batch representation as
an auxiliary execution plan only when its preparation, memory and total command
cost pay back; do not replace the proof with an unverified bitmap.

| Alternative | Advantage | Decision / limitation |
| --- | --- | --- |
| Compact Merkle DAG | Shared closures, immutable IDs, selective traversal, bounded positive subtree memo | Retain; improve concurrency first. Current per-record decoding is not compact enough. |
| Sorted flat witness arrays | Contiguous access, easy batching, low overhead for disjoint roots | Candidate auxiliary plan. Existing E3 reduced mechanism time but added preparation cost and did not demonstrate end-to-end improvement. |
| Dense bitsets | Fast vectorizable union/difference | Benchmark; sparse high IDs can waste considerable space. IDs identify exact witnesses including expected digest, not just paths. |
| Roaring compressed bitmaps | Adaptive sparse/dense/run containers | Benchmark separately from dense bitsets; still needs a witness dictionary and provenance/resolution-parent semantics. |
| Inverted dependency index + dirty bitmap | Fast affected-root lookup from a small known change set | Attractive with an authoritative, loss-detecting change feed. Current arbitrary worktrees do not supply one. A watcher/mtime/Git status alone cannot justify hits. |
| Prolly tree | History-independent ordered map, content-defined block sharing and fast version diffs | Merkle B-tree variant, not an escape from live-input validation. Potential publication index; extra rechunking and ordering do not match arbitrary dependency DAGs. |
| B-tree / ART / LSM | Efficient key lookup and/or write buffering | Orthogonal storage index choices. Current immutable packs already avoid a global writer. Benchmark the actual bottleneck before adding compaction or mutable shared state. |
| Bloom / XOR filters | Cheap definite-negative membership checks | Useful accelerator only; false positives cannot certify analysis hits. |
| Vector clocks / generation counters | Constant-time reuse in an authoritative versioned store | No authoritative filesystem version exists here. Epochs can scope memoization but cannot certify unchanged bytes. |

## Initial structure experiment

`tools/skycache/ProofLayoutBenchmark.java` compares equal witness sets and verifies
equal checksums across methods. It uses shared closures, disjoint closures and
sparse root requests; rotates method order; runs five warmups and seven measured
rounds, each averaging 100 passes, in multiple fresh JVMs. Measurements include
separate preparation and representation payload sizes. It is a CPU/layout
experiment with integer IDs: no filesystem I/O, digest computation, parent
resolution, serialization or end-to-end speedup claim. Array payload and bitmap
serialized sizes are not full retained heap measurements.

Initial two JVM runs (before adding Roaring) agree on the direction: shared DAG
traversal is substantially cheaper than scanning flattened shared closures;
flat arrays win modestly for disjoint roots; dense bitmaps are expensive for the
disjoint layout. Sparse measurements and DAG JIT timings vary between processes,
so individual microsecond ratios are not decision-grade production estimates.
Raw samples remain in the experiment artifacts, including earlier variants.

## Planned sequence and gates

1. Compare structures, including Roaring; record the decision above before editing
   the validator. No synthetic layout win alone changes cache correctness.
2. Establish working idle seed/evict/recover integration, then repeat with a
   retained-graph control and an uncached-analysis control. Preserve failed runs.
3. Replace the whole-traversal monitor with bounded root lock stripes and
   concurrent positive memo sets; hold no nested root locks. Benchmark actual
   dependency validation at 1/2/4/8 threads and full command behavior. Tests must
   show distinct roots enter I/O together, identical roots coalesce, cancellation
   remains interruptible, failures retry, and final revalidation still detects
   mutation. Only short memo insertion critical sections may remain.
4. Benchmark existing positional packed reads and writes at 1/2/4/8 threads.
   Preserve checksums, immutable-value conflict detection, fsync and publication
   ordering. Change the storage path only with measured supporting evidence.
5. Repeat end-to-end controls, input mutation and idle recovery; publish results
   as prototype evidence, including regressions and remaining unimplemented v2
   work. A positive microbenchmark is not a claim of faster builds.

Root stripes are the initial concurrency choice because they coalesce identical
root requests with fixed memory and no futures ownership graph. Different roots
can redundantly walk a shared subtree while it is in progress, then share its
completed success. A future per subtree risks cross-root wait cycles on corrupt
graphs; a persistent decoded-node cache increases RAM, contrary to this task's
goal. Single-flight *record reads* without recursive ownership remain a later
option if redundant work is significant. Stripe collisions only serialize work.

## Experiment updates

The first root-stripe implementation is **rejected as a performance improvement**.
Two fresh JVM experiments, each with two warmup and five measured rounds per
thread count, found no useful validation speedup. In the first run, the four-thread
median was 74.69 ms versus 70.34 ms serial; record reads grew from 8,832 to 30,571
and witness checks from 8,192 to 25,306. The second run confirmed the lack of
benefit. Raw `dependency-run1.jsonl` and `dependency-run2.jsonl` retain all samples.
This fixture deliberately overlaps root closures, exposing work amplification.

The next candidate coalesces **exact witness checks** with invocation-local
in-flight futures. Every caller still resolves parents first. A witness check
cannot recursively acquire another witness owner, so it creates no dependency
wait cycle. Failures are removed immediately; waits are interruptible; a waiting
caller's interruption does not cancel the owner. Completed positives remain in
the existing bounded memo, and no decoded proof payload is retained. This is
measured before deciding whether record-read coalescing is also necessary.

The existing packed format already supports useful parallel throughput. Five
measured rounds after two warmups, with thread order reversed between rounds:

| Records | Threads | Durable write median, ms | Verified read median, ms |
| --- | ---: | ---: | ---: |
| 20,000 × 256 B | 1 | 43.17 | 20.53 |
| 20,000 × 256 B | 4 | 31.84 | 11.36 |
| 8,000 × 4 KiB | 1 | 65.11 | 37.08 |
| 8,000 × 4 KiB | 4 | 47.00 | 14.53 |

Writes include sealing/fsync, but exclude creating the pack directory. Reads
follow writes and are page-cache warm. Thread creation is outside the timed
sections. Every returned value is byte-compared. These are single-process store
measurements, not full publication or cross-server build measurements. Eight
threads do not consistently beat four on this four-CPU quota. There is no evidence
here for replacing positional packed storage with a global mutable tree or LSM.

Compressed bitmap follow-up uses `FastAggregation.or`, not only repeated `or`.
It reduces the disjoint Roaring median from roughly 7 ms to 0.50 ms, showing why
the naive variant is not a fair best-case comparison. Flat arrays remain about
0.19 ms and the compact DAG about 0.23 ms for that shape. Roaring is compact (about
61 KiB serialized versus 512 KiB array payload), but does not win this CPU task.
Both versions and both JVM repetitions are preserved. Preparation is incremental
from the DAG/flat representation and sizes omit object headers; they are not total
heap or complete production encoding costs.

The witness-future candidate is also **rejected**: it reduced filesystem witness
checks to 8,192 but left roughly 29,000 record reads at four threads. Four-thread
validation medians worsened to 133.97/145.40 ms versus 69.79/71.12 ms for the
serial baseline in the two JVMs. Coordinating individual leaves caused workers
to advance together through repeated decoding work.

Whole-subtree futures restore the exact serial work count (8,832 records), but
waiting immediately on the first busy child still produced essentially serial
throughput. The retained implementation therefore **defers busy children and
does available sibling work first**, then waits for every deferred dependency
before validating the parent's witnesses or publishing its success. This uses
the existing caller threads, not an unbounded executor or an extra per-record
disk format. Successful root hits return directly from a concurrent memo.

An owner tracks only its active DFS ancestry. Before a wait, a short monitor
protects insertion and cycle detection in the wait graph. Edges refer to pending
node completions, not just thread/owner IDs: completed edges must be ignored even
before a woken caller clears its old pointer. This prevents spurious cycles when
one caller finishes a shared child and then requests another caller's ancestor.
No monitor is held across I/O or waiting. Failed/interrupted owners complete and
remove every outstanding node, so waiters cannot hang and later lookups can
retry. Resolution parents, checksums, depth/byte/reference limits and fresh final
publication validation remain enforced.

Final validator mechanism results, five measured rounds after two warmups in
each of two fresh JVMs, with alternating implementation and thread-count order:

| Threads | Serial median, JVM 1 / 2, ms | Parallel subtree median, JVM 1 / 2, ms |
| ---: | ---: | ---: |
| 1 | 69.00 / 75.50 | 70.10 / 80.18 |
| 2 | 75.22 / 77.04 | 43.48 / 43.02 |
| 4 | 72.02 / 71.29 | 32.05 / 36.74 |
| 8 | 72.91 / 69.76 | 33.69 / 35.36 |

All runs check the same 8,192 file witnesses through 8,832 packed dependency
records and 512 requested roots. The four-thread mechanism improvement is about
1.94–2.25×; one-thread overhead is about 2–6%. This comparison excludes initial
workspace snapshot construction and the final publication scan, which are common
to both validators. It is not a whole-build speedup. `root_stripes` in the staged
benchmark JSON labels whichever parallel candidate belongs to that filename;
`dependency-step4-*` is the retained subtree/deferred-work algorithm.

## Full-command measurement corrections

The first successful idle runs used `--lockfile_mode=off`. A recovery profile
showed about 6.5 seconds in module mapping, dominated by registry HTTP requests.
Those runs verify eviction/recovery, but do not isolate analysis-cache speed.
The repeated harness now prepares registry metadata using `mod deps`, freezes
the lockfile with `--lockfile_mode=error`, and preserves each command profile.

The first repeated harness also used Python `Popen.wait(timeout=300)`, whose
POSIX polling sleeps can overstate subsecond command duration by up to roughly
50 ms. Final timing uses blocking `waitpid` plus a separate timeout watchdog.
Earlier `idle-locked-*` measurements remain historical exploratory observations;
only `final-idle-*` uses the corrected timer. No failed or superseded sample is
silently discarded from the artifact record.

Idle lifecycle review also tightened the backing-publication hint: a shared blob
read may belong to a rejected analysis candidate, so only a successfully accepted
analysis import qualifies as new backing. Retention survives repeated no-write
recovery cycles, clears closed command clients/snapshots on eviction, and resets
its hint when switching cache directories or namespaces. Full graph eviction is
still opportunistic: one backing publication does not promise complete coverage
of every resident node; missing or stale entries are recomputed normally.

Further alternatives considered but not implemented in this prototype:

- Keeping a decoded record cache would add RAM and duplicate pack-index lifetime
  management. Subtree ownership eliminates duplicate reads without retaining
  decoded payloads after traversal.
- Reusing every workspace fingerprint from the initial global scan could reduce
  repeated hashing, but adds bounded-yet-substantial witness memory and changes
  the timing of first per-entry validation. Keep the existing freshness contract
  until observation provenance and whole-command costs are measured explicitly.
- More than four threads did not improve these four-CPU-quota results. Do not
  infer desktop/NVMe optimum concurrency from this machine.
- Selective analysis-only graph eviction could preserve module/loading state,
  but requires additional Skyframe ownership/lifecycle work. The prototype uses
  the coherent full evaluator reset and documents that loading state is dropped.

## Sources

Repository research: `skycache-v2-design-2026-10-24.md`,
`skycache-architecture-experiments-2026-10-21.md`, E3 mechanism/build JSON and the
current validator, snapshot, session, packed-store and idle-task code.

Primary external sources checked on 2026-10-24:

- [Bazel Skyframe](https://bazel.build/reference/skyframe): dependency graph,
  bottom-up versus top-down invalidation and stable evaluation semantics.
- [IPFS Merkle DAGs](https://docs.ipfs.tech/concepts/merkle-dag/): immutable
  content-addressed nodes, multiple parents and shared subgraphs.
- [Dolt Prolly Trees](https://www.dolthub.com/blog/2024-03-03-prolly-trees/): ordered
  map tradeoffs, content-defined chunking and version comparison.
- [Roaring](https://roaringbitmap.org/): adaptive array/bitmap/run containers and
  set operations. The experiment uses Maven RoaringBitmap 1.3.0, SHA-256
  `73443a71a2e2401c7d518a487983539fcaf5f9652efbd178f929e7b6d36881d0`;
  this adds no production dependency.
