# SkyCache optimization batch — 2026-10-21

The batch is implemented and all focused tests pass. The independent reader took **25.285 s**
against **19.348 s** averaged across matched cache-off controls: **30.7% slower**. The fresh
writer took **33.766 s**. This remains an opt-in prototype with no net speed benefit on this
workload. The earlier reader observation was 27.762 s, but changed preparation/action-cache state
means the apparent 8.9% improvement is not an isolated measurement of this code change.

Implementation: [`6f451b2dc6`](https://github.com/JSGette/bazel/commit/6f451b2dc6f783dda879ff3c7ed3fc8146d0f410),
pushed to `jsgette/skycache-concurrent`. [Curated measurements and evidence hashes](measurements/skycache-concurrent-2026-10-20/batch-optimization-2026-10-21.json).

## Changes in this batch

- **Packed records:** records up to 64 KiB use one positional read/write loop instead of three.
  Larger records combine header and key, using two loops while keeping large payloads separate.
  The extra small-record copy is bounded at 64 KiB. Length, key, checksum, duplicate, conflict,
  publication and durability checks remain; the wire format is unchanged.
- **Source metadata:** fresh POSIX type, size and permissions come from one observation. Ordinary
  traversal entries reuse that type instead of performing two additional metadata calls. Symlink
  referents and directory aliases still receive followed checks. Whole-tree start/end scans and
  content hashing remain; metadata is never reused across commands or substituted for content.
- **Allocation/parsing:** digest string-length prefixes are written without temporary buffers;
  dependency-key regular expressions are compiled once, preserving the accepted grammar.

The validator-wide lock, broad publication admission and full source guard remain. Parallel hashing
and dependency-validation redesign were deferred to keep this batch small and quickly measurable.
No Skyframe evaluator changes, graph paging, transport changes or cache format migration were made.

## Validation and measurement method

**284 Java cases across 18 targets passed**, with all 18 test targets executed rather than reused
from the test cache. Source build/tests used Cache&Carry, which reported three remote cache hits;
the combined command took 16.856 s. Four new cases cover record-size boundaries, corruption and
truncation across both read paths, digest-format reference fixtures, and changed external symlink
referent permissions. The golden digest constants were independently calculated from the previous
encoding; this is fixed-format reference coverage, not an executed old/new Java differential run.

Six fresh-server builds completed: two cache-off preparation builds, then measured cache-off A,
writer A, independent reader B and cache-off B. Preparations took 23.260 and 18.558 s and are
excluded from the means. This is one observation per enabled role and two controls, not repeated
randomized sampling. Both linked worktrees use Datadog Agent commit
`0cde96beb70963d0838119c308b9d0422ee66be0` and target `//cmd/cluster-agent:cluster-agent`.

All builds ran sequentially on native WSL ext4 on the E: WD Blue SN570 NVMe, using 32 jobs,
Xmx6g and 6144 MiB action memory. The graph/server starts fresh each time; existing outputs,
action disk cache and OS filesystem cache remain warm. The baseline is the same optimized fork
with analysis caching disabled, not a separately built upstream release. Source compilation
completed and its server was stopped before measurement.

Every measured build had **6,788 local action-cache hits, zero digest mismatches, and one
unconditional internal action**. This removes the action-reuse mismatch present in the earlier
comparison; it does not prove that all work in the execution phase is identical. All six outputs
have the same SHA-256, sources are unchanged and both servers are stopped. Writer publication
succeeded; the independent reader had 4,501 configured-target hits and shared reads, with no cache
failure or rejected publication. The reader correctly avoided redundant publication.

## Results

| Metric | Cache off A | Fresh writer | Independent reader | Cache off B |
|---|---:|---:|---:|---:|
| Full command, s | 19.388 | 33.766 | 25.285 | 19.307 |
| Cache setup, s | 0.001 | 3.876 | 3.723 | 0.001 |
| BEP analysis, s | 5.504 | 7.849 | 5.403 | 5.215 |
| BEP execution, s | 1.025 | 0.959 | 3.156 | 1.033 |
| Bazel CPU, CPU-s | 133.920 | 205.940 | 126.630 | 135.540 |
| Peak process-tree PSS, GiB | 2.550 | 5.314 | 5.004 | 3.976 |

Reader setup is 0.438 s lower than the prior 4.161 s observation (10.5%); writer setup is
0.485 s lower than its prior 4.361 s (11.1%). The writer's total time is effectively unchanged
from 33.901 s. Reader CPU is 6.0% below the current control mean, but elapsed time and memory
remain worse. Peak PSS remains sampled rather than a retained-heap measurement; no memory
improvement is established by this small sample.

The nearly equal analysis phase (5.403 versus 5.360 s control mean) is **not proof that import
has reached parity with recomputation**. Profiles show work shifting across phase boundaries:
the reader evaluates the rules_python pip module extension for 1.764 s inside execution, while
the controls evaluate it for 1.456/1.499 s before execution. Reader execution also includes a
0.734 s repository-mapping manifest dependency check. These spans overlap with other work and
must not be added as independent causes. Complete-command time remains the primary measure.

## Remaining costs and next priorities

The mandatory 3.723 s setup is still substantial. The reader still validates roughly 163,000
dependency records under the global validator monitor and imports hundreds of thousands of
records. Coalescing their I/O does not reduce the amount of validation or decoded data.
The writer has a 5.228 s tail after analysis/execution, followed by about 3.295 s beyond BEP's
wall-time counter. Those intervals need narrower serialization/drain/final-validation timers;
they do not isolate fsync or checksum time.

Next work should instrument those boundaries, safely reduce evidence to the requested dependency
closure, and investigate the serialized validator and deferred repository work. Full content,
symlink, absence and listing validation must remain. More jobs cannot remove these serial costs.
No further builds or source changes were added after this batch to chase a favorable result.

**Correction to the previous diagnosis:** command references were already cleared by the inherited
`SerializationModule.afterCommand()` through `resetCommandState()`. The earlier idle-retention
claim was wrong and has been corrected. No redundant lifecycle cleanup was implemented.

Raw profiles, BEP, logs, memory samples, harness and executed test XML are retained in a private,
readback-verified archive. Public JSON records their hashes, all six attempts, qualifications and
source/binary provenance. Implementation and docs remain on their separate requested branches.
