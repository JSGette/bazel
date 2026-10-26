# Remote Skycache preliminary findings

**Recommendation: prioritize publication preparation, especially exact reuse of inherited witness blocks and cheaper fresh validation. Keep authenticated reads second and upload batching conditional on network latency.** The new evidence changes the initial plan's ordering: on the measured loopback workload, almost six seconds pass between build completion and the first upload, while the entire upload-to-commit interval lasts only 0.36–0.40 seconds.

This preliminary investigation used the existing product binary and produced six valid builds, nine measured hashing replays, and a read-only witness inventory. No product source, protocol or configuration defaults changed. One additional instrumented build restored no cached entries and was excluded. [Evidence and provenance](../evidence/skycache-preliminary-2026-10-26/provenance.json)

## What the valid builds establish

All six builds restored the expected **17 configured-target and 11 execution entries**, and all six output digests matched the established baseline. The three remote builds, including one JFR diagnostic, downloaded zero blob bytes and successfully committed their child publications. The product checkout remains clean at `bf6b717195`; the unchanged binary has SHA-256 `73830e4d510a5e2b9e5dec0c19d2202b36c7ab8f63fae2a21dc77dbaff7f3626`.

The four unprofiled builds, in execution order, were remote **25.388 s**, local **21.941 s**, local **14.849 s**, remote **25.670 s**. The first local result drifted substantially, so these are diagnostic observations rather than a speedup estimate. The separate JFR runs took 53.496 s remotely and 16.323 s locally; they are excluded from latency comparisons. JVM, JIT and filesystem warming were not fully stabilized. [Full run records](../evidence/skycache-preliminary-2026-10-26/results.json)

Timing the output stream and a loopback gRPC forwarding proxy separated the final interval in both unprofiled remote runs:

- Build-complete message to first upload: **5.946 s / 5.932 s**.
- First upload through completed catalog commit: **0.357 s / 0.402 s**.
- Build-complete message to command exit: **6.346 s / 6.380 s**.
- Upload RPCs: **116 / 118**, carrying about **10.25 MB** of serialized requests.

The proxy records forwarded RPC duration and payload size without logging message contents. Message-arrival timestamps include output transport delay; these intervals are not exact Java method spans. Nevertheless, the six-second gap precedes upload, and the direct JFR capture independently locates work in command finalization. The proxy adds overhead, so its timings are not interchangeable with the earlier TCP-proxy benchmark. [Timing analysis](../evidence/skycache-preliminary-2026-10-26/diagnostic-analysis.json)

Reviewing all 24 historical profiles corroborates the boundary: retained-remote runs had **7.25–8.34 s outside the standard profile**, versus **0.12–0.18 s** for local runs. That difference alone was not causal attribution; it identified the interval to inspect. [Historical boundary analysis](../evidence/skycache-preliminary-2026-10-26/historical-profile-boundaries.json)

In the valid remote JFR recording, 253 execution/native-method samples from the command thread occurred after the build-complete message. Of these, 149 included `LocalInputSnapshot.isUnchanged`, 41 were in witness-table writing, 44 in other finish work including witness sorting/list preparation, and 16 in inherited-witness registration. **These are stack-sample counts, not percentages of wall time.** Background Netty native-wait samples were not treated as CPU work. The evidence supports final validation and witness processing as targets, but does not assign exact seconds to each method.

## Small new values trigger a large repeated witness publication

The two valid unprofiled remote runs produced only **505 and 587 bytes of new pack files**, respectively, across five and six private writes. They also wrote **103 witness blocks totaling 10,075,663 bytes**. All 103 compressed blocks were byte-identical to blocks in the frozen parent publications.

Those blocks encode **418,229 global witnesses**: 332,784 under external repositories and 85,445 absolute-path witnesses. This is a different counter scope from the 182,455 witnesses checked during entry validation; the two numbers must not be treated as a trend. [Witness inventory](../evidence/skycache-preliminary-2026-10-26/witness-inventory.json)

The source explains the asymmetry. A local reader with zero writes returns early from `LocalCacheSession.finish`. A remote reader with even a few private writes registers inherited witnesses, performs fresh final validation, sorts/encodes the global witness set, seals files and publishes. The current finalization contract is necessary evidence of freshness; byte-identical stored blocks do **not** authorize skipping it. [Finalization](https://github.com/JSGette/bazel/blob/bf6b717195/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheSession.java#L839-L903), [snapshot validation](https://github.com/JSGette/bazel/blob/bf6b717195/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalInputSnapshot.java#L143-L198)

## A promising hashing hypothesis produced only a modest isolated gain

A read-only filesystem inventory found 246,683 regular-content observations corresponding to 140,057 file versions. Below the current 1 MiB digest-reuse threshold, there were **106,464 alias observations**, representing approximately **789 MB** of repeated logical content. The inventory is a point-in-time stat snapshot, not a measurement of bytes actually avoided by a running validator. Directory, symlink, permission and missing-path checks remain necessary. [Alias inventory](../evidence/skycache-preliminary-2026-10-26/witness-alias-inventory.json)

I compiled the existing `LocalContentHasher` source unchanged into a standalone replay. Its existing constructor permits different thresholds and capacities. Every replay used a fresh hasher and fresh file-size observations; inode/change-time checks and hashing behavior remained in that source. One warmup was excluded, followed by three rotated repetitions per variant:

- Current 1 MiB threshold / 256 entries: **2.726 s median**, 4.812 GB hashed, 142 retained digests.
- 64 KiB / 16,384 entries: **2.614 s**, 4.547 GB hashed, 2,087 retained digests.
- 8 KiB / 65,536 entries: **2.527 s**, 4.218 GB hashed, 20,823 retained digests.

Every run produced the same aggregate digest sequence. The 8 KiB variant reduced this isolated hashing time by **7.3%, about 0.20 s**. It also retained roughly 147 times as many digest entries; retained heap was not measured. The replay excludes directory/negative-witness validation, source capture, serialization and build scheduling, and does not establish concurrent-mutation correctness. **This is a secondary tuning candidate, not a demonstrated build improvement.** [Replay results](../evidence/skycache-preliminary-2026-10-26/hash-replay-summary.json), [unchanged-source provenance](../evidence/skycache-preliminary-2026-10-26/hash-replay-source.json)

## The next implementation experiment

**First: exact inherited-witness reuse during publication preparation.** Detect when the child's complete global witness set equals a pinned parent's set and reuse its authenticated block bytes/descriptors rather than sorting, compressing and durably rewriting an identical table. Keep fresh final validation unchanged. Begin with a format-preserving experiment that gives the child the same logical files and authenticated descriptors. Fall back to current generation for any added, removed or conflicting witness, missing block, or uncertain ownership. Parent pins and child durability must remain valid through commit.

This is the lowest-risk concrete opportunity exposed by the duplicate-block evidence. It does not promise to remove the entire six-second interval. Measure witness preparation separately from fresh validation, plus total command latency, allocation and filesystem writes. Compare identical output digests and publication contents; exercise changed external inputs, missing paths, symlink changes, conflicting parent evidence, corruption, cancellation and parent collection. Require an end-to-end gain before combining it with other changes.

**Next within the same direction: accelerate the fresh validation pass.** Obtain uncontaminated method timing for namespace/source capture and global-witness checks, then evaluate bounded parallel checking and within-pass alias reuse. Preserve every typed witness, fresh validation lifetime, content verification, change detection and cancellation. The threshold replay supplies a useful conservative baseline; its 0.20 s saving alone is too small to justify a broad change. Do not persist “clean” decisions across commands or substitute mtime/size for content proof.

**Second overall: authenticated-read locality.** The warm build still has substantial work before completion. Revisit channel reuse, range coalescing and chunk lookup only against profiles with finalization separately accounted for. This investigation did not establish which read-path operation dominates.

**Conditional: upload batching and missing-blob checks.** Even eliminating the entire observed upload-to-commit interval would save only about **0.4 s, or 1.6% of these remote commands**. That is an opportunity bound for this interval, not a speedup forecast. Batching can become important with WAN RTT, many producers or larger new publications; those conditions were not tested. Presence checks would avoid retransmitting duplicate blocks but would not by themselves remove their preceding regeneration and validation.

Keep CDC, replacement-policy changes and new certificate protocols deferred. A value-aware policy that omits low-benefit child publications is a separate future experiment because it changes cache-population behavior; this investigation did not disable publication.

## Limits and reproducibility

The valid builds used a frozen two-publication catalog, separate caches, a fresh JVM/clean graph per invocation, the existing six-binary Datadog fixture, minimal outputs, 2 GiB transfer and 600-second budget overrides, and the existing action cache. This is a loopback diagnostic with two unprofiled observations per arm, not a production or WAN benchmark.

An additional Java-agent method-timing attempt discovered **zero publications**, restored zero cached entries, and failed the harness's 17/11-hit guard despite matching output digests. Its 51.928 s timing is excluded from all comparisons. The scope mismatch has not been diagnosed; neither those timings nor its method spans establish warm-reader costs. The source and binary preservation checks passed. [Excluded attempt](../evidence/skycache-preliminary-2026-10-26/excluded-instrumented-run.json)

The harness, analysis scripts and replay live in [work](../work/skycache_preliminary.py). Raw profiles, JFR recordings, RPC timing and logs remain in `\\wsl.localhost\Ubuntu\home\jgett\REPOS\.work\skycache-preliminary-2026-10-26`; the excluded attempt has its own sibling directory. The evidence directory contains hashes, compact analyses and full run records. All benchmark clients and metadata workers launched by the harness were stopped. No product optimization was applied or pushed.
