# CDC investigation for SkyCache remote MVP

Research date: 2026-10-25. Inspected source HEAD: `6a380e6eeba73d278d3e1b4a5625edb5fcefd7e5`. No production edits or commits were made by this investigation. Other agents were editing the shared source tree concurrently; code observations below describe the inspected baseline. All experiment sources, downloads and output are confined to this directory.

## Recommendation

Keep CDC optional and off by default for this MVP. The actual unchanged/changed workload shows **zero additional pack deduplication from CDC** beyond immutable-parent and fingerprint reuse. First finish parent-descriptor reuse, missing-blob preflight/batching, and bounded selective reads. If read amplification is the goal, benchmark smaller fixed chunks against CDC at the same size: a smaller transfer unit, rather than content-defined boundaries, creates that benefit here.

Revisit CDC for compaction/repacking, independently reconstructed publications, or large serialized records with localized byte insertions. Those cases need a fresh, representative benchmark. Stabilizing serialization and record ordering is likely a prerequisite for useful cross-publication pack reuse.

## Format and protocol fit

The existing `PublicationFile.chunks` ordered digest list already permits varying chunk sizes; `SkyCacheProtocol.validateBundle` checks each nonempty digest is <=1 MiB and the sum matches file size. `RemoteSkyCache.FileSource.read` walks cumulative lengths, so its reader does not require fixed boundaries. CDC can therefore be a writer policy without a wire-format change. Keep algorithm/version/seed/parameter policy stable across writers to preserve reuse; existing descriptors remain readable. More/smaller chunks consume the 32,768-chunk and 1 MiB encoded-bundle bounds sooner.

At the inspected baseline, `RemoteSkyCache.publish` slices each file into fixed 1 MiB units, and `SkyCacheTransport.upload` unconditionally sends singleton BatchUpdateBlobs. **CDC alone does not save upload wire bytes when the client still retransmits every chunk.** Batch FindMissingBlobs followed by uploads only for missing digests (or an equivalent server-aware transfer) is required for network savings. Exact immutable parent descriptor reuse avoids repeating chunking as well as uploading.

REAPI's FastCDC profile requires normalization 2, minimum average/4, maximum average*4, shared seed, and recommends leaving files smaller than maximum unsplit. Its recommended 512 KiB average permits 2 MiB chunks and does not fit the MVP's current 1 MiB hard limit. Average 256 KiB with 64 KiB minimum/1 MiB maximum fits; average 64 KiB has 16 KiB minimum/256 KiB maximum. Altering maximum to 1 MiB while keeping 512 KiB average would be a different profile, not the standard FastCDC parameters. [REAPI FastCDC parameters](https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/remote_execution.proto#L2486-L2530)

There is no need to require REAPI Split/Splice or v2.13 Get/RegisterChunkMapping for client-defined chunks: the SkyCache manifest already authenticates the ordered references to ordinary CAS blobs. Server chunk-mapping RPCs are optional interoperability/deduplication features for a whole CAS blob, not a prerequisite for SkyCache's own manifest layout. Negotiate them only if useful. [REAPI chunk RPCs](https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/remote_execution.proto#L443-L625)

## Experiment and actual results

`experiment.py` examines all actual `cache-* / leased-v2 / objects / segmented-v1 / * / *.pack` files from `perf-demand-2`. It inventories snapshots, removes exact duplicate file contents for computation, and parses every pack record (magic, key/value lengths, payload SHA256). Corpus: **108 distinct packs, 38,361,013 bytes**. Output inventory preserves each original file path and hash.

Algorithms: current fixed 1 MiB, fixed 256 KiB and 64 KiB controls, FastCDC2020 averages 256 KiB and 64 KiB with min=avg/4, max=avg*4, normalization 2, seed 0. The REAPI small-file bypass is applied for CDC. SHA256 identifies actual chunk bytes. A simulated remote CAS starts with the respective mode's primer publication; results count unique new pack payload bytes, excluding metadata, RPC headers and compression. This is an offline content-reuse experiment, **not measured network transfer or live-build performance**.

The C boundary routine is an exact cut-function translation using tables from the pinned, REAPI-listed [BuildBuddy Go FastCDC2020 implementation](https://github.com/buildbuddy-io/fastcdc2020/blob/2f01aaf2dde317b9875eea63b0c1f3bade2355dd/fastcdc/fastcdc.go), compiled with gcc -O3. Before corpus use, it matches every offset, length, SHA256 and gear fingerprint in the [official REAPI test vectors](https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/fastcdc2020_test_vectors.txt) for both seeds 0 and 666. This is not a measurement of Java production code or of the original paper implementation.

| Actual case | New pack payload vs primer, fixed 1 MiB | FastCDC 256 KiB | FastCDC 64 KiB |
|---|---:|---:|---:|
| Full, unchanged | 0 B | 0 B | 0 B |
| Full, changed | 43,850 B | 43,850 B | 43,850 B |
| Demand, unchanged | 1,651 B | 1,651 B | 1,651 B |
| Demand, changed | 96,096 B | 96,096 B | 96,096 B |

All three repetitions give these exact totals, and the smaller fixed controls give the same totals. Existing primer packs remain byte-for-byte identical; newly added child packs are small. CDC neither improves nor harms deduplication for those deltas.

The independently generated full and demand primers share zero entire transport chunks across every tested algorithm. Exact key+value record intersection is 5,245/7,368 records but only 900,731/19,003,686 bytes (4.74% of demand bytes). The primers come from different binary/mode families, so this comparison **does not isolate pack-order nondeterminism** or establish how same-version independent rebuilds would behave.

A separate, explicitly synthetic control inserts one byte halfway into the largest actual 1,498,289-byte pack. Fixed 1 MiB must transfer 1,498,290 new bytes; fixed 256 KiB needs 974,002; FastCDC 256 KiB needs 319,696; fixed 64 KiB needs 777,394; FastCDC 64 KiB needs 76,435. This demonstrates the expected boundary-resynchronization effect. The mutated bytes are not a valid SkyCache publication and are **not an observed build workload**.

## Costs and implementation consequences

- Chunk+SHA256 CPU, warm-memory median of seven offline trials over 38,361,013 bytes: fixed 1 MiB **14.60 ms**, fixed 256 KiB **14.62 ms**, fixed 64 KiB **14.98 ms**, FastCDC256 **24.82 ms**, FastCDC64 **28.01 ms**. CDC adds about 10–13 ms in this harness. It includes Python/ctypes overhead and one input copy per CDC file; no disk/RPC/compression and no production-Java extrapolation. Raw trials are in `chunk_and_hash_cpu.json`.
- The demand primer's 16 files need 28 fixed1MiB chunks, 82 fixed256KiB, 298 fixed64KiB, 55 FastCDC256KiB, or 263 FastCDC64KiB. More chunks increase descriptor size, CAS lookups and RPC overhead; singleton RPCs would amplify this. Batch operations and bounded parallelism matter.
- A model that independently queries each of the 7,368 demand-primer records from a cold chunk cache gives weighted read amplification 362.68x (fixed1MiB), 99.24x (fixed256KiB), 25.98x (fixed64KiB), 189.02x (FastCDC256), 31.43x (FastCDC64). This is **not a real build read trace**: actual invocation caching amortizes shared chunk downloads. It shows smaller fixed chunks beat CDC with the same nominal average for this model; it does not predict wall-clock improvements. The FastCDC256 small-file bypass also leaves several sub-1MiB files whole.
- Variable chunks require prefix sums (and ideally binary search) for range lookup. The current reader scans a file's chunk list from the beginning per read; smaller chunks make that scan longer. Download each touched complete chunk and check its independent SHA256; CDC does not permit unauthenticated arbitrary subranges.
- `PackedFingerprintValueStore.put` reserves append offsets concurrently; sealing sorts the index, not the pack payload. `SegmentedFingerprintValueStore` buckets by key and appends by arrival order. Consequently equal record sets can have different byte order; CDC resynchronizes around localized insertions but cannot recover large common contiguous runs that reordering or serialization changes destroy.
- Existing fingerprint reuse avoids writing equal key/value records and parent descriptors avoid retransferring immutable parents. CDC attacks a different residual problem: similar bytes inside changed/repacked large objects. Apply it after those semantic forms of reuse.
- Current pack writes store serialized records verbatim. If compression is added, chunk before compression, then compress independently per chunk (REAPI compressed transport still identifies uncompressed bytes). CDC after whole-pack compression generally loses stable byte neighborhoods, and a whole compressed stream undermines selective reads. This is a design inference; no compressed-pack benchmark was run. Restic similarly packs independently authenticated and optionally compressed blobs. [Restic pack format](https://restic.readthedocs.io/en/stable/100_references.html#pack-format)

## Follow-up acceptance gate

Only propose a production CDC change after measuring changed large records or realistic compaction/repack output with stable serialization. Compare fixed256KiB versus FastCDC256KiB and fixed64KiB versus FastCDC64KiB, not just against fixed1MiB. Measure unique bytes after FindMissingBlobs, actual RPC count, bundle bytes, upload CPU, demand-read downloaded bytes, peak memory, and end-to-end time with representative RTT/bandwidth. Keep chunks bounded and preserve checksums, closure retention and failure handling. No format migration is needed solely to permit variable chunk sizes.

## Primary algorithm references

- [FastCDC 2016 paper and author presentation](https://www.usenix.org/conference/atc16/technical-sessions/presentation/xia): gear rolling hash, skipping sub-minimum cut points, normalized chunk distribution.
- [2020 journal paper, author's university-hosted PDF](https://ranger.uta.edu/~jiang/publication/Journals/2020/2020-IEEE-TPDS%28Wen%20Xia%29.pdf): two-byte rolling enhancement. Published performance claims are not substituted for this workload's measurements.
- [Pinned REAPI protocol](https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/remote_execution.proto) and [test vectors](https://github.com/bazelbuild/remote-apis/blob/c2c07e3d718d3d490a4958344d9c3e47de0bfaf3/build/bazel/remote/execution/v2/fastcdc2020_test_vectors.txt).
- [Pinned Go implementation used as translation source](https://github.com/buildbuddy-io/fastcdc2020/blob/2f01aaf2dde317b9875eea63b0c1f3bade2355dd/fastcdc/fastcdc.go).
- [Restic design](https://restic.readthedocs.io/en/stable/100_references.html#backups-and-deduplication): primary example of CDC for insertion/deletion tolerance. Restic uses Rabin, not the FastCDC algorithm measured here.