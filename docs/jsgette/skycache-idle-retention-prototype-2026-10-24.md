# Idle-retention prototype: three-attempt result

The minimal implementation compiles and its four focused tests pass. **The requested A/B
measurement did not complete.** Exactly three workload builds were attempted: two failed,
and the third produced correct output but could not publish to SkyCache. The idle task
therefore did not evict the graph. No memory-saving or recovery-latency claim is supported.

Implementation is local on `jsgette/skycache-idle-retention-prototype`, based on
`6f451b2dc6f783dda879ff3c7ed3fc8146d0f410`; it has not been pushed. This prototypes the
idle-retention portion of the [v2 design](skycache-v2-design-2026-10-24.md), using the
existing v1 store and validator. It does not implement Merkle certificates or parallel v2 I/O.

## Behavior

`--experimental_analysis_cache_idle_retention=5m` schedules an idle task after a successful
local SkyCache build when this server has a prior successful publication in the current
namespace. Default `0` disables it; fractional-second and negative values are rejected.
This narrow prototype does not arm solely from another server's imported publication.

At expiry, it rechecks the backing manifest under catalog coordination and pins the publication.
If backing is gone or cancellation happens before reset, it preserves the graph. Otherwise it
uses `BlazeWorkspace.resetEvaluator()` for a coherent reset, clears retained local certificate
references, requests GC and writes `skycache-idle-retention.json`. Publication protection ends
after the reset. Missing or unselected data on a subsequent command must be recomputed.

The reset drops **loading state as well as analysis**. The JVM, persistent action-result cache
and action outputs remain. The separate proposed action-cache-metadata retention flag is not
implemented. Once reset begins, the next command waits for it to finish through the existing
idle-task manager; GC may also delay an arriving command. OS memory release is not guaranteed.

## Attempts and evidence

The workload has 2,048 Starlark provider targets in 64 packages, each containing 512 strings,
and one final FileWrite output. Settings: four jobs, 4 GiB JVM heap, 4096 MiB action memory.
The initially registered sequence was seed, warm retained graph, then post-idle recovery.
Failures consumed the cap; there was no fourth workload build.

| Attempt | Complete command | Result |
|---|---:|---|
| 1: seed | 6.547 s | Registry access failed because the custom JDK lacked the environment CA trust. No analysis or publication. |
| 2: seed with idle retention | 8.526 s | Fixture analysis failed: Starlark does not support `%04d`. No publication. |
| 3: corrected fixture, memory-only fallback | 10.564 s | Build exit 0 and independently expected output matched. Publication rejected with `inputs changed during the invocation`; no eviction. |

The last output SHA-256 is
`303e01a2e9f45f8723dca27f0b6da98b0696f7b5b112dab53fabce221d87b3c5`.
Its sampled peak server PSS was 598.191 MiB, with 606.998 MiB observed at command end;
sampling can miss the peak. These are build-memory observations, not before/after eviction data.

The publication check does not log which input mismatched. One plausible fixture issue is
switching output bases while retaining convenience symlinks inside the source workspace:
the source guard excludes only links to the current output base. The exact cause is not
independently established. The harness now puts convenience links outside the source tree,
uses supported formatting, and automatically selects the existing environment trust store
when unambiguous. The final harness corrections were syntax-checked, not rerun.

The source binary compiled in 850.867 seconds, with 6,423 actions, followed by four passing
cases covering flag parsing, missing backing, pre-reset interruption and a pinned reset callback.
These unit tests do not establish a successful real-server eviction/recovery cycle.
Binary SHA-256: `4e262a8b005e05141b5bfc67c02381d960d6f157fb5f4858b89ad3b0d0c332c0`.

The [curated evidence](measurements/skycache-idle-retention-2026-10-24.json) preserves all three
attempts, source/test provenance, hashes, qualifications and unavailable metrics as null.
Local raw results, logs and an implementation patch are in `/workspace/skycache-idle-results`.
All task JVMs exited; container zombie entries have zero RSS. The compilation server and
its workers were no longer running during the workload builds.

The next experiment must first establish a stable publishable fixture, then measure retained
memory versus post-idle memory and a real recovery build. The current run is inconclusive;
the implementation remains an experimental opt-in prototype.
