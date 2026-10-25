from pathlib import Path
import json
import statistics

repo = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
evidence = repo / 'docs/jsgette/measurements/skycache-block-reads-agent-2026-10-25'
comparison = json.loads((evidence / 'comparison.json').read_text())
matrix = json.loads((evidence / 'matrix.json').read_text())
summary = {(row['mode'], row['arm']): row for row in comparison['summary']}
baseline = {(row['mode'], row['arm']): row for row in comparison['prior_single_samples']}
lines = []
for mode in ['minimal', 'toplevel']:
    vanilla, local, remote = [summary[mode, arm]['median_seconds'] for arm in ['vanilla', 'local', 'remote']]
    old = baseline[mode, 'remote']['seconds']
    lines.append(f'| {mode} | {vanilla:.2f} s | {local:.2f} s | {old:.2f} s | {remote:.2f} s | {old / remote:.2f}x |')
remote_rows = [row for row in matrix['runs'] if row['measured'] and row['arm'] == 'remote']
minimal = summary['minimal', 'remote']
old_io = baseline['minimal', 'remote']['server_proc_io']['rchar']
io = minimal['median_rchar']
cached = statistics.median(row['remote']['cached_read_bytes'] for row in remote_rows)
weight = max(row['remote']['authentication_cache_bytes'] for row in remote_rows)
downloads = sorted(set(row['remote']['downloaded_bytes'] for row in remote_rows))
assert len(downloads) == 1
assert all(row['remote']['authentication_cache_evictions'] == 0 for row in remote_rows)
stabilization_upload = matrix['runs'][0]['wire_upload_bytes']
text = f'''### Agent repository measurements after block authentication

Product commit `{comparison['source_commit']}` was built from the exact tested tree.
Three measured repetitions per arm/policy (18 builds), plus two excluded preparation passes,
used the same six Datadog targets and repository revision as the earlier comparison. Arm order
rotated and policy order alternated. All builds and output checks passed; every cache reader
restored 17 configured-target and 11 execution entries. The 578 test cases also pass.

| Output policy | Vanilla, median | Local, median | Remote before, one sample | Remote after, median | Before / after |
| --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(lines)}

Remote minimal samples span {min(minimal['seconds']):.2f}–{max(minimal['seconds']):.2f} seconds;
toplevel samples span {min(summary['toplevel', 'remote']['seconds']):.2f}–{max(summary['toplevel', 'remote']['seconds']):.2f} seconds.
The old binary has only one prepared sample per policy. Each binary has its own prepared seed
because installation identity versions analysis keys; these are equivalent workload comparisons,
not identical cache-file comparisons. The new local/remote arms share identical frozen metadata
within this matrix. Their binary outputs match the pinned vanilla outputs byte-for-byte where
downloaded and by BEP digest in both policies. Build wall time includes publication and excludes
startup/clean and backend warmup. The metadata backend is loopback; these results do not establish
performance over a production network.

Minimal remote process `rchar` fell from **{old_io:,} B** to a median **{int(io):,} B**
({100 * (1 - io / old_io):.2f}% lower). This is the same cumulative process counter before/after,
including startup/clean and all read-like I/O, not physical disk or network traffic. Separately,
the new cached-read counter records a median **{int(cached):,} B** across remote samples.
Hash-cache weight reaches **{weight:,} B**, below the 32 MiB budget, with **zero evictions**.
Minimal remote client process-tree PSS has median **{minimal['median_peak_pss_kib'] / 1024**2:.2f} GiB**;
backend PSS is recorded separately and filesystem page cache is excluded.

The remaining cost is still material: each fresh remote reader downloads **{downloads[0]:,} B**
of metadata/chunk payload, despite returning about 244.6 MB of shared values. Remote reads retain
the global lock and 1 MiB wire chunks. All six remote samples reuse both parent descriptors and
materialize zero publications, but still write 16 private records; cached-only put deduplication
is unchanged. The excluded first refresh uploads **{stabilization_upload:,} B**, confirming the
value of `FindMissingBlobs` before uploading local parents already present in CAS. Profile and
reduce serialized reads, deduplicate uploads, then compare fixed 256 KiB chunks before adopting CDC.

The first cross-binary pilot and an initial preparation run stopped on harness assertions and
remain explicitly failed in the evidence. The latter completed two correct preparation builds;
its first pass had 1,708 analysis hits, while the stable second pass had 17 and zero writes. The
corrected harness gates the final stable pass, and reran preparation in a new directory before
the 18 successful measurements. No input-validation or output-verification checks were disabled;
neither failed run enters the timing table. All original source/seed/CAS/catalog checks passed;
client and server services
were stopped at completion.

[Per-sample comparison](measurements/skycache-block-reads-agent-2026-10-25/comparison.json),
[raw matrix](measurements/skycache-block-reads-agent-2026-10-25/matrix.json),
[verification/provenance](measurements/skycache-block-reads-agent-2026-10-25/verification.json),
and [logs, profiles and test XML](measurements/skycache-block-reads-agent-2026-10-25/logs-profiles-tests.tar.gz)
include the exact harnesses and checksums. Catalog signing keys are excluded.

'''
path = repo / 'docs/jsgette/skycache-remote-mvp.md'
document = path.read_text()
assert '### Agent repository measurements after block authentication' not in document
document = document.replace('## Content-defined chunking\n', text + '## Content-defined chunking\n', 1)
path.write_text(document)
print(text)
