# Bounded reuse of remote chunk channels

26 October 2026. Implements the first experiment from the
[local/remote chain trace](../skycache-chain-2026-10-26/README.md).

Remote cached reads now borrow an open channel exclusively and return it to a
bounded pool after fresh path validation. The selected default retains **128 idle
channels**. The observed warm-command median improves from **22.498 s to 21.892 s
(2.7%)**, while avoiding about **55% of per-range channel opens**. Cold timing is
effectively unchanged within this small experiment's variability.

This is a modest improvement to one part of the remote gap. It does not remove
the covering-block reads, cryptographic verification or retained-chunk activation.

## Results and capacity choice

Three measured observations per implementation in each cohort. Both cohorts
connect directly to the loopback metadata worker. "Cold" means empty client
retained storage with warmed backend/OS caches.

| Reader | Warm median | Warm range | Cold median | Cold range |
| --- | ---: | ---: | ---: | ---: |
| Original | 22.498 s | 22.297–22.693 s | 30.506 s | 29.899–33.196 s |
| 128 idle channels, selected | **21.892 s** | 20.467–21.916 s | **30.284 s** | 29.928–30.848 s |
| 256 idle channels | 21.729 s | 21.668–22.255 s | 30.521 s | 29.487–31.526 s |

The selected reader saves 0.606 s / 2.7% in the warm median. Its cold difference
is -0.223 s / -0.7%, too small relative to run variation to claim a cold speedup.
The 256-channel warm median is only 0.162 s lower than 128, while their warm means
are 21.884 s and 21.425 s respectively. These samples do not establish a reliable
latency advantage for doubling the idle-descriptor allowance. Select 128 for
similar observed performance with the smaller resource budget.

| Runtime counter | 128 channels | 256 channels |
| --- | ---: | ---: |
| Warm channel reuse fraction | 54.8–55.0% | 76.2–76.4% |
| Warm opens per command | 357,418–359,673 | 187,877–189,159 |
| Largest observed live cached-read channel count, both cohorts | **150** | 281 |
| Configured idle bound | 128 | 256 |
| Maximum total at measured concurrency 100 | 228 | 356 |

These counts describe the chunk reader, not all JVM descriptors. The original
reader opens/closes one channel per cached byte-range read; the preceding trace
counted approximately 795,000 such calls. Candidate hit fractions are measured
as hits / (hits + opens), not inferred from the earlier LRU simulation. The
implementation borrows exclusively, so simultaneous readers of one digest may
open different channels and reuse order follows lease returns.

The matched local control had a median of 17.388 s (16.535–17.604 s). Relative to
that control, the remote median gap decreases from 5.109 s to 4.504 s, about
11.9% of the observed gap. Common work and host variability remain substantial.
The selected reader's mean middle interval falls from 18.057 s to 17.131 s;
this interval includes admission, validation, restoration, execution and overhead,
so it is not a pure I/O attribution. Do not add medians to reconstruct phases.

[Warm measurements](warm-comparison.json), [cold measurements](cold-comparison.json),
[complete warm records](warm/results.json.gz),
[complete cold records](cold/results.json.gz).

## Implementation and preserved checks

[RemoteSkyCache](../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/RemoteSkyCache.java)
owns one invocation-local pool. It retains resolved paths and channels only while
they are idle. Acquire removes a channel from the map; release returns it or
closes it if retention is disabled, a duplicate is already idle, the read failed,
the thread is interrupted, or the pool is closing. Eviction selects idle entries
only. Open, stat, read and close operations occur outside the map lock.

Exclusive ownership matters because interrupting Java FileChannel I/O can close
that channel. Sharing one live channel among readers would let one interrupted
request disrupt another. The existing cached-reader semaphore bounds active
leases; the pool bounds idle entries. Even a descriptor waiting to be closed
after eviction is covered by its returning reader's permit. The total bound is
128 + configured cached-read concurrency, independently of the authentication
hash budget.

Every read still checks the current pathname with NOFOLLOW_LINKS, regular-file
type, expected size and file identity. A different identity closes the old
descriptor and reopens the current path. The open channel's size is checked before
and after reading, and pathname attributes are checked again before the lease can
be reused. If the filesystem supplies no file key, the channel is not retained.
Missing, truncated, symlinked and replaced scratch files cannot be hidden by an
old open descriptor.

The returned covering blocks are still authenticated against hashes derived from
a verified whole CAS chunk, and packed records retain their own integrity checks.
No block hash, witness check or fresh final validation is skipped. Close drains
readers through the existing lifecycle lock, closes pooled channels, then removes
scratch files. Wire/publication formats, upload behavior and public options are
unchanged. Statistics now include capacity, hits, opens, closes, live channels and
peak channels.

The helper classes are excluded from dynamic serialization by a narrow prefix in
[AutoRegistry](../../../../src/main/java/com/google/devtools/build/lib/skyframe/serialization/AutoRegistry.java).
File handles are runtime state; adding their class names must not shift the tags
of existing serialized classes. The complete registry class inventory and checksum
match the frozen baseline for both variants: **8,821 classes** in the check's
shared diagnostic classpath. [Registry comparison](registry-comparison.json).

## Correctness and benchmark controls

**632 cases across 23 targets passed** for each final prototype. Six new tests,
plus strengthened existing tests, cover descriptor reuse, replacement with the
same size/mtime, replacement during a lease, bounded eviction while a reader
holds a channel, interruption isolation, close with active leases, discard after
failed validation, zero retention, and exclusion from dynamic serialization.
Existing corruption, forged-checksum, authentication-eviction, cache-only and
blocked-download cancellation tests continue to pass. The final selected source
is checked against the hashes of the tested 128-channel build.

All **21 measured commands** (12 warm-cohort commands including three local
controls, plus nine cold commands) matched all six original binary outputs and
the fresh genrule output, restored 18 configured-target and 11 execution entries,
and successfully published. Every remote child reused the same 103 witness
blocks / 10,075,663 bytes, byte-identical across the measured runs in each cohort.
Warm runs downloaded zero metadata payload bytes; cold runs downloaded
1,463,742,717 bytes. Descriptor peaks stayed within their bounds. Source and
frozen binary states were unchanged throughout each completed cohort; transient
probe files and fixture servers were cleaned up.

### Why this comparison uses a startup-only agent

Bazel intentionally includes its installation checksum in FrontierNodeVersion.
Consequently, different compiled binaries miss each other's root keys even when
their codec inventories match. The two initial warmup attempts that compared
different installations stopped at zero hits and are **excluded**. The first
prototype also introduced dynamic codec tags; the runtime-class exclusion above
preserves the original registry independently of the installation guard.

The primary experiment runs the same frozen executable and installation identity
for every arm. A benchmark-only Java agent leaves the original reader untouched
for baseline runs, or supplies the **exact compiled RemoteSkyCache class bytes**
from the tested 128/256 prototype before class definition. Its three nested helper
classes are the same in both prototypes. All arms receive the same registry class,
whose only change is excluding those added runtime helper names.

This changes no installation checksum, namespace, witness or authentication guard.
It permits identical publication IDs, layout and bytes in every arm without
separately priming nondeterministically packed publications. The agent does no
per-read instrumentation or simulation; setup/class loading precedes the timed
build. Its resource hashes are checked against the compiled classes, and runtime
capacity counters verify the selected reader. Early reader-transform logging can
precede CLI attachment and land in the server's jvm.out; registry-transform logs
are captured in each build log. The agent is not part of the production feature.

Normal use of a newly built Bazel still respects its installation checksum and
requires compatible entries to be primed for that installation. The timing result
isolates the reader implementation; it is not a claim that an ordinary binary
upgrade reuses the old installation's root keys.

Both cohorts use fresh JVMs, cleared Skyframe/action state, private cache/catalog
directories, the exact seed from the preceding chain trace, fixed environment and
tool identities, minimal output download, and a fresh tiny genrule per command to
force publication. Server startup, cache copying and backend warmups are outside
the timed build-through-finalization interval. All warmups are excluded. Warm
order is L/B/128/256, 256/128/B/L, 128/L/256/B; cold order is B/128/256,
256/128/B, 128/B/256. Build/test jobs do not overlap measured cohorts.

Three observations per implementation and loopback service do not establish tail
latency, statistical significance or WAN behavior. Cold results include transfer
variation. The selected 2.7% warm reduction is an observed median, not a production
forecast. There is no claim that 55% fewer opens means 55% less latency.

## Research interpretation and next work

The capacity choice follows the distinction between cache hit ratio and system
performance. [Qiu, Yang and Harchol-Balter, Queueing Systems, April 2026](https://link.springer.com/article/10.1007/s11134-026-09986-1)
show through queueing models and experiments that hit-path work and contention
can offset a higher hit ratio. [Chen et al., PVLDB 19(4), August 2026 arXiv version](https://arxiv.org/html/2608.29993v1)
study reducing promotion/maintenance overhead. These motivate measuring whole
commands and resource use, and keeping file I/O outside cache locks. They do not
prove that this pool is contention-bound or that an alternative eviction policy
would be faster.

The next isolated experiment remains digest/buffer reuse on the authenticated
range path. Covering-byte demand remains around 3.57 GB in warm commands, plus
retained activation; channel reuse does not remove that work. Smaller wire chunks
or access-local packing remain separate cold-path experiments. No additional
algorithm or validation relaxation is included in this change.

## Evidence and reproduction

- [Selected 128-channel build and test results](build-128-v2/result.json),
  [256-channel build and test results](build-256-v2/result.json), with test XML.
- [Warm harness](warm/harness.py), [cold harness](cold/harness.py),
  [analysis script](analyze_channels.py), and raw monotonic timelines beside results.
- [Agent source](SkycacheChannelAgent.java), [agent builder](build_channel_agent.py),
  [agent/class hashes](agent-provenance.json), and [registry checker](ChannelRegistryCheck.java).
- [Warm witness hashes](warm-witnesses.json), [cold witness hashes](cold-witnesses.json),
  [provenance](provenance.json), [verification](verification.json), and
  [evidence index](evidence-index.json).

These are fixture-specific scripts with pinned paths, source and environment
identity. They require the existing private Datadog fixture and seed, JDK 25,
Python gRPC environment and reachable action cache. Large binaries/profiles/BEP
files remain at the native paths referenced by provenance. Do not run timed
cohorts concurrently or overwrite existing run directories. Metadata transfer
limits are 2 GiB and 600 seconds, as in the preceding chain comparison.
