# Remote SkyCache witness reuse

**The first optimization reduced the median post-build interval from 6.921 to 6.115 seconds (11.6%) in a controlled publication workload.** Median command time fell from 27.043 to 25.473 seconds (5.8%). These are three observations per binary, with a small extra target to ensure both sides publish; they are not a production speedup forecast.

| Paired observation | Baseline command | Candidate command | Baseline post-build | Candidate post-build |
| --- | ---: | ---: | ---: | ---: |
| 1, baseline first | 27.474 s | 25.374 s | 6.915 s | 6.077 s |
| 2, candidate first | 26.786 s | 26.762 s | 6.935 s | 6.131 s |
| 3, baseline first | 27.043 s | 25.473 s | 6.921 s | 6.115 s |

The post-build saving was **0.804–0.838 seconds in every pair**. Whole-command savings ranged from 0.024 to 2.100 seconds, illustrating why the stage result is stronger evidence than attributing the entire median command difference to this change. All six measured runs restored 18 configured-target and 11 execution entries, downloaded zero metadata blob bytes, matched the six original binaries and probe output, and committed successfully. Each candidate reused **103 blocks / 10,075,663 bytes**. Every measured child's witness table was byte-identical across both binaries.

The first implementation targets publication preparation. When a child's complete global witness set equals a used parent's canonical table, it reuses that table's compressed representation. A linear exact comparison replaces sorting and encoding. The child still validates all current inputs, rereads and authenticates the compressed blocks, and writes independent durable files. Missing or corrupt source blocks fall back to generation after removing only files created by the failed copy.

This preserves manifest and wire formats, parent references, leases and the final freshness boundary. It saves preparation work; it does not eliminate child disk writes or upload bytes. New or conflicting evidence cannot select the reuse path. Two statistics, `reused_witness_blocks` and `reused_witness_bytes`, expose whether it ran.

The selected suite passed **618 test cases across 22 targets**. Six new tests cover exact typed equality, duplicate/noncanonical evidence, additional input fallback, independent child ownership, corrupt/missing/symlink block fallback, partial-copy cleanup and protection of existing destination files. Existing tests also cover changed inherited inputs, directory membership, conflicting parents and parent retention.

The benchmark uses the existing six Datadog binaries plus a tiny fresh genrule to require publication on both sides. Every original binary digest and the probe's output digest are checked. Each invocation uses a fresh JVM and graph, independent cache files, a frozen catalog and retained chunks; metadata traffic passes through a loopback timing proxy. Primers, stabilization and warmups are excluded. Six measured runs alternate baseline/candidate in ABBAAB order.

The binaries require separate primers because the rebuilt binary does not restore the old binary's analysis/execution keys. Separate primers can also differ in pack layout, so total-command comparisons have that limitation. The post-build interval isolates the targeted finalization stage more closely. Three observations per binary do not establish tail latency or a production/WAN effect.

Earlier diagnostic attempts remain in the raw evidence. A changing temporary `PATH` changed the namespace; replaying the recorded repository environment fixed that. A one-publication primer needed a stabilization publication to reach the steady workload. One subsequent candidate run made zero new writes and correctly skipped publication; its shorter time is excluded from the optimization comparison. The fresh genrule prevents that confound in the final comparison. A baseline JVM later faulted in compiled `java.util.stream.Nodes$SpinedNodeBuilder.accept` during witness sorting; that incomplete cohort is excluded, its crash report preserved, and the complete retry is reported above. This change is not claimed to fix the JVM failure.

The host's wall clock stepped during several runs. Command and post-build durations use the same monotonic clock. Cross-clock pre-upload estimates were discarded. Individual proxy RPC durations also use a monotonic clock: summed upload forwarding took median 0.206 s in the baseline and 0.185 s in the candidate; commit forwarding took about 0.028 s in both. These sums are not the complete publication latency.

The next target remains **fresh final validation**, followed by authenticated-read overhead. This change removes repeated representation work while keeping that validation intact. It does not justify raising concurrency, changing validation lifetimes or skipping witnesses. Upload batching remains conditional on a measured network bottleneck.

[Comparison and controls](comparison.json), [test verification](verification.json), [provenance](provenance.json) and [preparation/exclusions](preparation-and-exclusions.json) accompany the [benchmark harness](benchmark.py). Raw data remains under `/home/jgett/REPOS/.work/skycache-witness-reuse-2026-10-26`. The fixture probe was removed and all benchmark clients/workers stopped. The baseline and candidate binaries and source states were unchanged during measurement.
