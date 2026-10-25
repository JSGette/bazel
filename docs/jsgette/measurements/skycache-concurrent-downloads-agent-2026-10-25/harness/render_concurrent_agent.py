"""Insert the validated concurrent-download measurements before Content-defined chunking."""

from pathlib import Path
import hashlib
import json
import statistics


REPO = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
EVIDENCE = REPO / 'docs/jsgette/measurements/skycache-concurrent-downloads-agent-2026-10-25'
HEADING = '### Agent repository measurements with concurrent downloads'


def interval(values, unit='s', digits=2):
    return f'{min(values):.{digits}f}–{max(values):.{digits}f} {unit}'


def main():
    for line in (EVIDENCE / 'SHA256SUMS').read_text().splitlines():
        digest, relative = line.split('  ', 1)
        path = EVIDENCE / relative
        assert path.resolve().is_relative_to(EVIDENCE.resolve())
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, relative
    comparison = json.loads((EVIDENCE / 'comparison.json').read_text())
    verification = json.loads((EVIDENCE / 'verification.json').read_text())
    matrix = json.loads((EVIDENCE / 'matrix.json').read_text())
    prime = json.loads((EVIDENCE / 'prime.json').read_text())
    build = json.loads((EVIDENCE / 'build.json').read_text())
    assert verification['passed'] and verification['test_cases'] == build['test_cases'] > 0
    assert verification['max_concurrency'] == 100
    assert matrix['status'] == prime['status'] == 'passed'
    assert matrix['measured_count'] == 18 and prime['measured_count'] == 0
    cells = {(row['mode'], row['arm']): row for row in comparison['summary']}
    assert len(cells) == 6
    table = []
    for mode in ['minimal', 'toplevel']:
        for arm in ['vanilla', 'local', 'remote']:
            cell = cells[mode, arm]
            assert cell['samples'] == cell['prior_samples'] == 3
            table.append(f"| {mode} | {arm} | {cell['prior_median_seconds']:.2f} s "
                         f"({interval(cell['prior_seconds'])}) | {cell['median_seconds']:.2f} s "
                         f"({interval(cell['seconds'])}) | {cell['prior_over_current']:.2f}x |")
    remote = comparison['remote_duration_samples']
    assert len(remote) == 6
    duration_table = []
    for row in remote:
        duration_table.append(f"| {row['label']} | {row['seconds']:.2f} s | "
                              f"{row['cached_read_seconds']:.2f} s | {row['download_rpc_seconds']:.2f} s | "
                              f"{row['remote']['peak_cached_reads']} | {row['remote']['peak_chunk_downloads']} |")
    peaks = [row['remote']['peak_cached_reads'] for row in remote]
    download_peaks = [row['remote']['peak_chunk_downloads'] for row in remote]
    auth = [row['remote']['authentication_cache_bytes'] for row in remote]
    evictions = [row['remote']['authentication_cache_evictions'] for row in remote]
    payload = [row['remote']['downloaded_bytes'] for row in remote]
    cached = [row['remote']['cached_read_bytes'] for row in remote]
    writes = [row['local-skycache.json']['writes'] for row in remote]
    materialized = [row['remote']['materialized_publications'] for row in remote]
    reused = [row['remote']['reused_publications'] for row in remote]
    commits = sum(row['remote_committed'] for row in remote)
    assert all(row['remote']['max_concurrency'] == 100 for row in remote)
    assert all(1 <= peak <= 100 for peak in peaks + download_peaks)
    assert max(auth) <= 32 * 1024**2
    minimal, toplevel = [cells[mode, 'remote'] for mode in ['minimal', 'toplevel']]
    text = f'''{HEADING}

Product commit `{comparison['source_commit']}` was built from tested tree
`{comparison['source_tree']}`; all **{verification['test_cases']} test cases** passed.
The comparison uses the same six Datadog targets, revision, output base, embedded tools,
output digests, and pinned vanilla binary as the preceding matrix with overlapping cached
reads and serialized downloads. That historical baseline remains unchanged.
Both matrices contain three measured samples per arm/policy. The new run completed
18 measured builds after one deliberate cross-binary prime and two excluded stabilization
passes. The prime succeeded with zero old-seed hits and a committed new publication;
the final stabilization wrote zero records. Every measured cache reader restored
17 configured-target and 11 execution entries, with clean graph/action state and no
cache failures, transfer skips or scan/read-limit failures.

| Output policy | Arm | Previous median (range) | Concurrent downloads median (range) | Previous / current |
| --- | --- | ---: | ---: | ---: |
{chr(10).join(table)}

Ratios above 1 indicate lower current wall time; these small samples describe this run
and do not establish statistical significance. Each binary uses its own prepared seed
because installation identity versions analysis keys. Within the new matrix, local and
remote readers use identical frozen publication metadata. All six binary digests match
the pinned vanilla outputs in BEP; toplevel downloads also match by actual SHA-256.
Arm order rotates and policy order alternates. Each sample has a fresh JVM and client
metadata cache, with startup/clean and one backend warmup round excluded from build
wall time. Publication work is included. The metadata backend is loopback with warmed
CAS and OS caches, so production-network performance remains unmeasured.
The SkyCache flag is omitted to exercise its new default of 100. Ordinary action-cache
connections retain the common benchmark override of 16 requests per connection across all arms.

The counters report accumulated cached-read work and accumulated download-RPC duration.
They are **not exclusive wall-time components**: this implementation permits parallel
download calls, so the RPC counter sums their overlapping durations and can exceed build
wall time. Cached reads can also overlap one another and downloads. Neither counter, nor
their sum, is a wall-time breakdown or CPU time. Timing begins after acquiring the relevant
client slot; the RPC counter covers transport download calls, including any transport admission
delay. It excludes waiting for the client download permit and does not cover all metadata RPCs.

| Remote sample | Build wall | Cached-read accumulated duration | Download-RPC accumulated duration | Peak cached readers | Peak chunk downloads |
| --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(duration_table)}

The reported concurrency setting is **100**. Observed cached-reader peaks span
**{min(peaks)} to {max(peaks)}** and chunk-download peaks span
**{min(download_peaks)} to {max(download_peaks)}**, each within that bound. These are observed
peaks, not a claim that 100 operations remain active throughout a build.
Authentication-cache weight reaches **{max(auth):,} B**, within 32 MiB; recorded evictions
span **{min(evictions):,}–{max(evictions):,}** per sample. Fresh remote readers download
**{min(payload):,}–{max(payload):,} B** of metadata/chunk payload and record a median
**{int(statistics.median(cached)):,} B** of cached reads. Remote sample writes span
**{min(writes):,}–{max(writes):,}**; {commits} of six samples report a publication commit.
Materialized-publication counts span {min(materialized)}–{max(materialized)} and reused-publication
counts span {min(reused)}–{max(reused)}. These observed counts are recorded without a fixed
payload or remote-write assumption.

Remote client process-tree PSS medians are **{minimal['median_peak_pss_kib'] / 1024**2:.2f} GiB**
for minimal and **{toplevel['median_peak_pss_kib'] / 1024**2:.2f} GiB** for toplevel; worker
peaks are recorded separately and filesystem page cache is excluded. Corresponding median
process `rchar` values are **{int(minimal['median_rchar']):,} B** and
**{int(toplevel['median_rchar']):,} B**. These are cumulative fresh-JVM read-like counters,
including startup/clean, and are neither physical disk bytes nor network traffic.

Remaining limits include bounded download/cached-read concurrency, 1 MiB wire chunks,
repeated uploads when a new publication is exported, and work required to read and
authenticate local chunk ranges. This comparison changes both download overlap and the
cached-reader bound, so it does not isolate their individual effects. It does not evaluate
upload deduplication, smaller chunks, CDC, or a production-network deployment. All
source/seed/CAS/catalog preservation and cleanup checks passed. Exact raw reports,
the prior report hashes, binary/runtime JAR provenance, harnesses, build scripts,
legacy helpers, logs/profiles and test XML are frozen with SHA-256 checksums; catalog
directories and signing keys are excluded from the archive.

[Six-cell and per-sample comparison](measurements/skycache-concurrent-downloads-agent-2026-10-25/comparison.json),
[successful prime](measurements/skycache-concurrent-downloads-agent-2026-10-25/prime.json),
[raw matrix](measurements/skycache-concurrent-downloads-agent-2026-10-25/matrix.json),
[verification/provenance](measurements/skycache-concurrent-downloads-agent-2026-10-25/verification.json),
and [logs, profiles and test XML](measurements/skycache-concurrent-downloads-agent-2026-10-25/logs-profiles-tests.tar.gz).

'''
    path = REPO / 'docs/jsgette/skycache-remote-mvp.md'
    document = path.read_text()
    anchor = '## Content-defined chunking\n'
    assert HEADING not in document and document.count(anchor) == 1
    path.write_text(document.replace(anchor, text + anchor, 1))
    print(text)


if __name__ == '__main__':
    main()
