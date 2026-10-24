# Continue SkyCache locally

## Goal and authorization

Improve the user's personal Bazel fork, `https://github.com/JSGette/bazel`, using
the research on `jsgette/master` under `docs/jsgette`. Priorities are fast parallel
reads and writes, skipping already-validated dependency subtrees, and releasing
the resident analysis graph after a configurable idle period while recovering
eligible values from local SkyCache. Preserve correctness, publication durability,
cross-worktree sharing and normal fallback on cache misses.

The user asked for an independent, thorough implementation, comparison with
alternatives to Merkle trees, documentation of technical decisions, and repeated
benchmarks at each meaningful step. That superseded the original global limit of
three build attempts. Use about three repetitions per full-build condition and
report all setup failures and incomplete runs. The latest instruction is to hand
off now for another local session; the remaining cloud jobs were stopped.

**These measurements ran in Linux cloud, not on the user's PC.** The selected
tools never exposed a PC terminal. The GitHub connection could read the public
fork but rejected Git object creation with HTTP 403, “Resource not accessible by
integration.” Nothing was pushed, published or committed by this session.

## Apply the implementation

`implementation.patch` includes every tracked and untracked implementation,
test and benchmark file, including the new idle task. It applies to this exact
source base, the current `jsgette/skycache-concurrent` tip when checked:

```text
6f451b2dc6f783dda879ff3c7ed3fc8146d0f410
```

From an existing Bazel clone, create an isolated worktree so current local work
is preserved. Replace `/path/to/handoff` with the extracted archive directory:

```bash
git fetch https://github.com/JSGette/bazel.git jsgette/skycache-concurrent
git worktree add -b jsgette/skycache-v2-local ../bazel-skycache-v2 \
  6f451b2dc6f783dda879ff3c7ed3fc8146d0f410
cd ../bazel-skycache-v2
git apply --check /path/to/handoff/implementation.patch
git apply /path/to/handoff/implementation.patch
```

Both patches were checked against clean base trees using separate temporary Git
indexes. The implementation patch SHA-256 is:

```text
3aa181e60b7ff2c55a14f462031656ba1b444a580dfc2cca5c4fdcaa93201925
```

`research-notes.patch` is optional and **has a different base**:
`8f4ec5307f553018d00a2be926868c530678f5ef` on `jsgette/master`.
Apply it only in a separate checkout of that documentation branch. Do not apply
it to the source base above. Copies of all new research/design notes are also
available directly under this archive's `docs/`; applying the documentation
patch is not required to continue implementation.

Read the checkout's `AGENTS.md`. It calls for building `//src:bazel-dev`, copying
the binary, and using separate output bases for experiments. No sub-agents were
used. No additional skill is needed to resume local coding.

## What is implemented

1. **Parallel Merkle-DAG validation.** The cache already had a fingerprinted DAG
   and invocation-local positive subtree memoization. The new validator removes
   the whole-traversal monitor, coalesces identical root requests with 4,096 lock
   stripes, and assigns one owner to each in-flight dependency subtree. Other
   callers defer busy children and process available siblings before waiting.
   Every child and resolution parent must succeed before a parent is memoized.
   No extra executor or new persistent format is introduced.
2. **Wait-cycle and cancellation handling.** Short critical sections protect the
   ownership/wait graph. Edges refer to pending completions; completed edges are
   ignored to avoid false cycles. Actual cross-root cycles fail closed. Failed
   or interrupted owners release all pending nodes; negative results do not
   persist, so repository materialization can be retried. No lock covers I/O or
   a wait. Completed root hits use a fast concurrent-memo path.
3. **`--experimental_analysis_cache_idle_retention`.** Default `0` disables it;
   e.g. `5m` enables it. Positive values must be whole seconds. After a successful
   command with usable published backing, an idle task reacquires a publication
   lease, rechecks existence and calls the coherent evaluator reset. It clears
   retained root/client/session metadata and performs best-effort GC. It preserves
   the JVM, action-result cache and output files, but also drops loading state.
4. **Repeated and imported-only idle cycles.** The backing hint survives a
   no-write recovery. A successfully accepted shared analysis entry can provide
   backing in a new server. Merely reading a blob from a rejected candidate does
   not qualify. Changing cache directory or namespace clears the old hint.
5. **Benchmarks and tests.** See `tools/skycache/PROTOTYPE_BENCHMARKS.md` after
   applying the patch. An unchanged serial validator is included only in the
   benchmark target for comparison. Roaring is used only by the standalone
   structure experiment, never as a production dependency.

The packed store already uses per-invocation append-only packs, disjoint positional
writes, compact indexes and publication leases. Its format, fsync protocol and
read/write implementation were retained after measuring useful parallel scaling.

## Structure decision and rejected implementations

Keep a **Merkle DAG**, not a strict tree. Dependencies share subgraphs. Hashes of
stored records do not prove that current filesystem inputs still match them.
Positive validation remains invocation-local under Bazel's stable-input
assumption; final publication still performs fresh content validation.

Compact DAG traversal, flat arrays, dense bitsets and Roaring were measured in
two fresh JVMs per variant, with repeated rounds and equal witness checksums.
Shared closures favored the DAG; disjoint closures slightly favored flat arrays.
Roaring saved space but was slower for these CPU workloads, even with bulk
aggregation. These are integer-ID layout tests, not build-speed measurements.

Rejected stages, all retained in the evidence:

- Root stripes alone: repeated shared work; four-thread record reads grew from
  8,832 to about 30,000 and validation did not improve.
- Futures per witness: exactly 8,192 witness checks, but repeated decoding and
  fine-grained waiting made validation substantially slower.
- Whole-subtree futures with immediate waiting: eliminated repeated reads but
  serialized overlapping work. Deferring busy children fixed this bottleneck.

Other alternatives considered: inverted dependency indexes plus dirty bitmaps
require an authoritative change feed; watcher/mtime/Git status alone is not proof.
Prolly trees are a Merkle B-tree variant useful for ordered indexes, not a shortcut
around input validation. B-trees/ART/LSM are storage-index choices. Bloom/XOR filters
cannot certify positive hits. A persistent decoded-record cache adds RAM; the
current ownership mechanism avoids retaining decoded payloads after traversal.

Read `docs/skycache-structure-decision-2026-10-24.md` for the decision log and
`docs/skycache-v2-design-2026-10-24.md` for the broader design, research evidence,
correctness requirements and work that remains beyond this prototype.

## Verified results and their limits

**Build/tests:** final source binary built successfully. All **190 test cases in
12 local SkyCache test targets passed**, including concurrent independent roots,
same-root coalescing, cross-root cycle rejection, valid overlapping ancestors,
interruptible waits, content mutations, codec behavior, leases and collection.
See `evidence/final-tests.log` and `evidence/tests.json`.

**Validator mechanism:** 512 roots sharing 8,192 actual file witnesses, 8,832
actual packed records. Two warmups, five measured rounds at each concurrency,
two fresh JVMs; implementation order and thread-count order alternate.

| Threads | Serial median, JVM 1 / 2, ms | Final validator median, JVM 1 / 2, ms |
| ---: | ---: | ---: |
| 1 | 69.00 / 75.50 | 70.10 / 80.18 |
| 2 | 75.22 / 77.04 | 43.48 / 43.02 |
| 4 | 72.02 / 71.29 | 32.05 / 36.74 |
| 8 | 72.91 / 69.76 | 33.69 / 35.36 |

All final runs retain the exact serial record count. Four-thread validation is
about 1.94–2.25× faster; one-thread overhead is about 2–6%. Initial workspace
snapshot construction and final publication validation are outside these timed
sections. **This is not a demonstrated whole-build speedup.** The staged JSON
label `root_stripes` means the parallel candidate for that filename; only
`dependency-step4-*` is the retained deferred-subtree implementation.

**Packed store:** five measured rounds after two warmups, 1/2/4/8 threads. At four
threads, 20,000 × 256-byte records took 31.84 ms to durably write versus 43.17 ms
at one thread; verified reads took 11.36 versus 20.53 ms. For 8,000 × 4 KiB records,
durable writes took 47.00 versus 65.11 ms and reads 14.53 versus 37.08 ms. Writes
include sealing/fsync; reads are page-cache warm; directory creation is excluded.

**Idle integration:** completed exploratory runs verified correct output, same
JVM, repeated eviction/recovery without new writes, and changed content with the
original mtime restored after reset. Heap dropped by roughly 129 MiB in the
synthetic provider-heavy fixture. The completed exploratory six-cycle comparison
observed roughly 0.32 s recovery with SkyCache versus 0.99 s rebuilding analysis,
but its process-wait timer could overstate short durations by up to about 50 ms.
Treat these numbers as exploratory, not the final A/B conclusion.

The initial successful runs also disabled the module lockfile. Profiles revealed
about 6.5 seconds of registry/module mapping work after reset. The corrected
harness prepares a lockfile and uses `--lockfile_mode=error` during measurement.
It now uses blocking waitpid with a separate timeout watchdog.

## Exact stopping point

- `final2-idle-baseline.json`: completed three baseline cycles using the corrected
  timer and fixed lockfile.
- `final2-idle-parallel.json`: **interrupted for this handoff** with 13 recorded
  attempts. Last recorded role: `r1-skycache-recovery`; its measurement/output
  verification did not finish. Some cycles are incomplete;
  do not pool this as a completed three-repetition A/B result.
- The final drift control did not run.
- The final mixed-version/two-server worktree test did not run. Its first attempt
  stopped before any build because the generic storage guard required 30 GiB
  free on a 32 GiB cloud disk. A cloud-only copy was prepared with a 4 GiB reserve,
  still checking live free space, but was not executed before the handoff.
- `idle_import_probe.py` was added to verify a cold server's zero-write import
  can schedule idle eviction. It passed Python syntax checking but **has not yet
  run**. Its production support passed the session unit test, including rejection
  of a backing hint based only on a blob read.
- A prior accurate baseline was interrupted after a failed attempt to overwrite
  a read-only copied binary; it is explicitly marked incomplete. The final
  binary was then copied to a new path. Do not discard or silently promote this
  incomplete run.

The broader design remains partly unimplemented: compact proof blocks/batch I/O,
observation-bound provenance to remove full scans, selective publication before
encoding, and a global in-flight byte budget. Current traversal limits are per
lookup and positive/in-flight node counts are bounded; high caller concurrency
can still multiply transient memory. Do not claim a production rollout or an
end-to-end win over no SkyCache from the current evidence.

## Next local steps

1. Apply the patch to the exact base in an isolated worktree. Build with Bazel
   9.3.0 and rerun the local package tests. Copy `bazel-dev` to a fresh filename;
   avoid overwriting a read-only or running binary. Use a separate output base.
2. Complete the corrected idle benchmark on the PC, with three repetitions per
   condition, a fixed module lockfile, independent output checks and profiling.
   `--rounds=3 --with-off-control --repeated-import-test` executes **25 workload
   builds** including seed/setup roles and correctness checks, not three total
   builds. The separate cold-import probe adds two. Set any new budget explicitly.
3. Run `idle_import_probe.py` against the completed fixture. Confirm zero novel
   writes, successful import, actual eviction and same-PID recovery.
4. Run the repository's `local_worktree_probe.py` with independent servers and
   worktrees, then the mixed old/new binary variant if useful. Preserve its normal
   PC/WSL host-volume checks. The archived cloud adaptation has absolute cloud
   paths and is evidence, not a script to run unchanged on the PC.
5. Re-run validator and packed read/write scaling on the actual PC storage at
   1/2/4/8 threads. The cloud quota was four CPUs and 16 GiB RAM. Do not transfer
   its optimum thread count or latency directly to a desktop/NVMe setup.
6. Measure a representative real target with matched configuration, cache state,
   build outputs and action counts. Compare complete client duration, CPU, PSS,
   bytes/record counts and tail latency. Keep warm-graph performance separate from
   idle recovery, first-server startup and ordinary uncached analysis.
7. Preserve every correctness guard when improving the next bottleneck. In
   particular, don't remove the initial/final full scan or trust watcher/Git/mtime
   information without observation-bound input provenance. Keep symlink chains,
   absence/listing witnesses, repository bindings, retained-value identity and
   mixed live/imported provider identity intact.

## Local/cloud provenance

- Source checkout: `/workspace/bazel-impl`, local branch
  `jsgette/skycache-idle-retention-prototype`, base `6f451b2...`; changes uncommitted.
- Research checkout: `/workspace/bazel`, `jsgette/master`, base `8f4ec530...`.
- Full cloud artifacts: `/workspace/skycache-v2-results`.
- Original three-attempt experiment: `/workspace/skycache-idle-results`.
- Final tested binary: `/workspace/bin/bazel-skycache-final`, SHA-256
  `eb110d81030e7bb09e63f8bb6449013f1e56973ef9184fd37db2f418c4ee4c47`.
- Serial prototype control: `/workspace/bin/bazel-skycache-idle`, SHA-256
  `4e262a8b005e05141b5bfc67c02381d960d6f157fb5f4858b89ad3b0d0c332c0`.
- Source build output base: `/workspace/bazel-build-output`; JDK 25 was supplied
  by its `rules_java` remote toolchain. These paths are references, not PC paths.

The archive intentionally excludes downloaded JDKs, compiled binaries, generated
workspaces/packs and raw server environment logs. The patch contains everything
needed to rebuild; the evidence includes benchmark samples, test summaries,
failure records and final-run client logs.
