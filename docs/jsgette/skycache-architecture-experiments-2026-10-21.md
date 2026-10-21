# SkyCache architecture experiments — 2026-10-21

**Select compact input certificates as the primary next experiment. Keep selective publication as
a secondary design lead, but reject the current synchronous frontier path as a latency improvement.**
None of these variants demonstrated a reliable full-build speedup. Keep the concurrent immutable
store, per-server in-memory graph and existing evaluator; do not promote any experimental switch.

All 15 planned Datadog builds completed successfully with identical outputs and expected action-cache
reuse. Two protocol checks failed and remain failed: E1's inventory boundary included the excluded
preparation's Bazel-installation transition, and E3's wrapper lost every process-memory sample.
The qualifications below are part of the result, not erased by successful build/test outcomes.

## Measurements and selection

All values below are complete command seconds, including cache publication. Cache-off controls use
the same experimental binary with caching disabled, not a different vanilla executable.

| Experiment | Observed result | Decision |
|---|---|---|
| Controls | Initial off: 18.777 / 18.764 s; final off: 20.809 s. Default writer: 29.136 s; default readers: 21.447 / 20.204 s. | Final same-worktree control is 10.9% slower; fixed order and two reader samples do not support statistical confidence or drift correction. |
| E1: omit initial source scan, prohibit writes | Readers 22.277 / 22.386 s. Mean setup drops 0.451 to 0.016 s, but mean full time rises 20.825 to 22.332 s. | Do not select as a leading performance direction. Audit-qualified observations; async serialization before discarded writes remains. This tests scan avoidance, not a complete download-only implementation. |
| E2: bulk-load sealed packs, with E1 policy | Readers 34.018 / 20.774 s. Peak PSS 5.360 / 5.300 GiB versus E1 mean 5.092 GiB. | Inconclusive, not selected. The first run has an unexplained early-analysis stall; the second roughly matches default latency. No selective removal of the slow run. |
| E3: fixed-batch certificate | Validation mean 541.050 to 129.174 ms; allocations 776.224 to 153.002 MB. Common setup/admission plus validation 2.937 to 2.514 s. | Primary mechanism lead: 76.1% less validation time, 80.3% less allocation. Conditional evidence only; no full-build or peak-memory claim. |
| E4: synchronous analysis frontier | Writer 30.049 s; readers 26.301 / 24.149 s. Reader mean PSS 3.854 GiB versus default 4.574 GiB. | Secondary publication/size lead only. Current mode loses latency and must not be promoted. |

E4 reduces writer pack bytes from 410,017,786 to 172,263,010 (58.0%), and reader logical CAS payload
from 336.802 to 147.076 MB (56.3%). Initial writer cache size is about 458.431 to 213.819 MB.
However, the first frontier reader publishes four new records with another full witness manifest;
after both readers, total cache size is 240.937 MB (229.775 MiB), not the initial writer-only size.
These are logical traffic and persisted-size measurements, not physical disk I/O.

The default and E1/E2 readers all fetch 312,793 CAS values, traverse 163,377 proof records and perform
7,798 fresh witness checks. E4 still traverses 163,381 proof records and checks the same 7,798
witnesses. Smaller serialization has not removed broad admission or dependency validation.

See the [complete build table and comparison qualifications](measurements/skycache-concurrent-2026-10-20/architecture-experiment-builds-2026-10-21.md),
[curated build data](measurements/skycache-concurrent-2026-10-20/architecture-experiment-builds-2026-10-21.json)
and [audited certificate data](measurements/skycache-concurrent-2026-10-20/architecture-certificate-mechanism-2026-10-21.json).

## What explains the results

E1 saves only about 0.435 s of setup in this batch. The external pre-stage content inventory warms
source pages, so the prior report's 3.7 s setup assumption is not transferable here. Repository/module
timings vary; proof volume is unchanged. No named JIT measurement establishes a JIT explanation.

E2's first analysis takes 17.795 s versus 5.461 s on the second run. During much of the extra interval,
Bazel CPU use is low and there is little profiled work. Named repository extensions and GC pauses do
not explain it. Bulk-loading or locking is a hypothesis, not an identified cause. Its added heap and
unexplained stall prevent selecting it; a mean-only claim of intrinsic slowdown would also overstate
the evidence.

E3 validates 4,495 real unique roots and 150,062 deduplicated witnesses. Both paths use the same fresh
snapshot/session and admission root: 142,264 checks use the admission memo and 7,798 are fresh. The
certificate is 28,179,548 bytes (26.874 MiB). Loading, checksums, decoding and witness validation are
inside the flat validation timer. Two warmups and three measured rounds per path alternate order.
Common admission still costs about 2.13 s; eliminating proof replay alone cannot solve the whole
startup cost. Full certificate preparation plus initial fixture binding costs 6.248 s, approximately
16 future validations to amortize at the measured mean saving. That is a conservative fixed-batch
calculation, not a prediction for a production writer that already possesses the witnesses.

E3 pays graph integrity verification during preparation, pins its publication closure, and hashes
the exact manifests/indexes/packs before and after outside the validation timer. Fresh input checks
remain; the certificate is not a production per-entry format. Content edits with restored mtime,
directory additions and symlink retargets are rejected in isolated probes. Those probes do not prove
graph/certificate equivalence during concurrent mutations, cross-worktree edits or input ABA races.

E4 moves synchronous frontier serialization onto the writer's critical tail: serialize/upload takes
6.514 s; the post-analysis/execution tail is 6.537 s versus default 3.730 s. Reader Skyframe statistics
also show substantially more evaluated/materialized configured-target entries (54,064 versus 4,513), and a
`toolchains_msvc` module extension runs for 2.185/2.523 s during execution, absent from default readers.
Reader execution grows to 5.752/6.113 s from 2.809/2.564 s. The first reader additionally pays publication
work. The higher logged hit count (7,036 versus 4,501) is therefore not evidence of faster builds.
The built-value counts include evaluation bookkeeping; they do not mean 54,064 expensive rule analyses.
Profile spans nest; these durations must not be added as independent causal contributions.

## Protocol, timeboxes and provenance

The plan was registered before measurements and pushed in docs commit `04cdc2fcd5479f79f69c4e259f745d7c6b4b1b84`.
The preregistered plan is retained [as JSON](measurements/skycache-concurrent-2026-10-20/architecture-experiment-plan.json).

| Stage | Preparation cap | Execution cap | Execution performed |
|---|---:|---:|---|
| E1 | 20 min | 10 min | Two excluded preparations, two off controls, one default writer, two default readers, two E1 readers. |
| E2 | 20 min | 10 min | Two readers; no retries. |
| E3 | 25 min | 15 min | One standalone Java process, 61.888 s; all ten validation rounds completed. |
| E4 | 25 min | 15 min | One writer and two readers, then the final off control. |

Preparation/review was parallel; compilation/tests and all experiment executions were sequential.
Code was prepared and tested in about ten minutes, within every preparation cap. Each stage finished
well within its execution cap. Each build had a 180-second deadline and bounded shutdown; E3 had a
900-second deadline. There were exactly 15 Datadog build attempts, including the two preparations,
with no build retries. UTC journal times are provenance; full-build comparisons use monotonic timers.

Datadog commit: `0cde96beb70963d0838119c308b9d0422ee66be0`.
Target: `//cmd/cluster-agent:cluster-agent` in two existing linked worktrees, with independent output
bases. Every command starts a fresh server/graph, retains warm outputs and disk action cache, and runs
alone on native WSL ext4 on the E: WD Blue SN570 NVMe. No input edits or concurrent throughput tests
were added to this experiment set. The existing identical `MODULE.bazel.lock` delta was preserved.

Build settings: `--jobs=32 --local_resources=memory=6144`, JVM `-Xmx6g`,
`--config=no-remote-cache --lockfile_mode=off --experimental_strict_repo_env`, pinned JDK25,
the established native action cache, and `--experimental_active_directories=cmd/cluster-agent`.
E1 uses `-Dskycache.experiment.read_only=true`; E2 adds `-Dskycache.experiment.bulk_pack=true`.
E4 uses `-Dskycache.experiment.frontier=true` with normal source capture and a fresh publication cache.
The cap for sealed-pack snapshots is 512 MiB per process in 64 MiB segments; all record integrity
checks remain. Captured valid bytes may outlive later backing-file damage, intentionally documented
and tested. E3 runs directly without any E1/E2 JVM property.

Every measured build had 6,788 local action hits, zero digest-mismatch misses and one unconditional
internal action. All output SHA256 values are
`7f6e2b951113f9e05bbe14709175f872b7222090bd8e694159f9f5aa5cf45664`.
Source/binding inventories are outside timed builds. Source, binary and effective input exports are
hashed; process RSS/PSS is sampled every 200 ms for the full builds. All servers are stopped.

Experimental source: [`e29c37affc2fdcbeb42c3beefcba5368ee0ac4af`](https://github.com/JSGette/bazel/commit/e29c37affc2fdcbeb42c3beefcba5368ee0ac4af),
pushed to [`jsgette/skycache-experiments-2026-10-21`](https://github.com/JSGette/bazel/tree/jsgette/skycache-experiments-2026-10-21).
Bazel binary SHA256: `53a3ddfdbec7b44e20d2190dbbef8bd5894af0eefc205dbb5f844d7865141b5b`.
Implementation branch remains `6f451b2dc6f783dda879ff3c7ed3fc8146d0f410`; no experimental changes
were promoted. The Cache&Carry source build used `--config=efcache --bes_backend=
--remote_upload_local_results=false`, 32 jobs, Xmx4g and 10240 MiB action memory. After one test-target
visibility repair, all 293 tests in 19 suites executed and passed; source build took 20.806 s with five
remote cache hits. Two Bazel test durations displayed negative values; no performance inference uses
test-run timing.

## Failures, corrections and limitations retained

1. The first source build failed before executing tests because the new frontier test could not see
   `remote_analysis_cache_mode`. Added only its test-package visibility, then rebuilt successfully.
2. E1's pre-inventory preceded the excluded preparations. Those changed only `bazel_tools` in both
   output bases to the new experimental installation. Source entries, Git differences and every other
   repository binding were unchanged. E1's original failed audit, receipt and results remain; an explicit
   amendment starts subsequent stages from the post-preparation identity. Within-E1 comparisons are
   qualified: all measured commands used the same binary after preparation, but no per-command binding
   snapshot proves the exact transition instant.
3. The journal writer used `sha256` for both an evidence payload and its chain checksum. Only events
   4 and 88 were affected. Restoring their payload hashes from the existing inventories reproduced the
   original chain exactly. The original journal/harness are retained; the repaired chain renames the
   payload field and records original event hashes. Two failed diagnostic probes made no changes.
4. An E2 launch was stopped before any build because the inherited environment fingerprint changed.
   The sole difference was WSL's temporary `WSL_INTEROP` socket. Restoring that value reproduced the
   original full environment hash exactly; later build stages use it. No benchmark attempt was consumed.
5. E3 Java completed successfully with all checks, rounds and probes, but its Python wrapper expected
   two memory counters from a helper returning three. All 290 memory samples failed. The wrapper and
   receipt remain **failed**, RSS/PSS are null, and no rerun was made. Independent offline audit verified
   source/input/asset identities, the certificate/root bindings, journal, rounds and zero writes.
   Allocation is thread allocation, not peak RAM. Sampling exceptions may add overhead; it was not
   separately measured. Timing/allocation evidence is conditional, not a passed complete experiment.
6. E2's unexplained first-run stall and the final control's 10.9% drift remain in the data. No outlier
   trimming, extra attempts, randomization claims or confidence intervals were added.

Exact commands, raw logs/BEP/profiles, exported effective environments, inventories, both journals,
binary identities, test XML and scripts are retained privately with checksums and archive readback.
Public evidence is allowlisted numeric/boolean data and provenance; private inputs are not published.
The [archive/build provenance receipt](measurements/skycache-concurrent-2026-10-20/architecture-experiment-provenance-2026-10-21.json) records 225 readback-verified members (89,375,698 compressed bytes), SHA256 `0afd781770a3ad3592f31a494a34fb25ff6525b3927f5d663397082c2febb8a0`. Native binaries and packs remain available.

## Next implementation boundary

1. Prototype certificates bound to the actual validated root/value/input closure, with bounded parsing,
   content checks, corruption fallback, retained-value validation and publication/lease semantics intact.
   Reduce broad admission only when the entry's relevant external/environment witnesses can be proven;
   this admission redesign is a hypothesis, not a measured result. Preserve the writer's source guard
   until evidence is bound to the inputs actually consumed during evaluation.
2. Explore selective publication without the measured synchronous re-evaluation and module-resolution
   penalty, and avoid copying a full global witness manifest for four added records. This is a secondary
   size/memory lead, not a selected production mode.
3. Before promotion, require repeated end-to-end improvement against matched cache-off controls, then
   two-server/worktree edits, restart/collection and corruption tests. This batch establishes none of
   those new production guarantees. Keep graphs in RAM and evaluator changes minimal.
