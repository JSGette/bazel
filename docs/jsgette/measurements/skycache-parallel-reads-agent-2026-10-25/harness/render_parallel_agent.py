"""Insert the validated overlapping-read measurements before Content-defined chunking."""

from pathlib import Path
import hashlib
import json
import statistics


REPO = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
EVIDENCE = REPO / 'docs/jsgette/measurements/skycache-parallel-reads-agent-2026-10-25'
HEADING = '### Agent repository measurements with overlapping cached reads'


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
    assert verification['passed'] and verification['test_cases'] == 582
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
                              f"{row['remote']['peak_cached_reads']} |")
    peaks = [row['remote']['peak_cached_reads'] for row in remote]
    auth = [row['remote']['authentication_cache_bytes'] for row in remote]
    evictions = [row['remote']['authentication_cache_evictions'] for row in remote]
    payload = [row['remote']['downloaded_bytes'] for row in remote]
    cached = [row['remote']['cached_read_bytes'] for row in remote]
    writes = [row['local-skycache.json']['writes'] for row in remote]
    materialized = [row['remote']['materialized_publications'] for row in remote]
    reused = [row['remote']['reused_publications'] for row in remote]
    commits = sum(row['remote_committed'] for row in remote)
    assert all(1 <= peak <= 8 for peak in peaks) and max(auth) <= 32 * 1024**2
    minimal, toplevel = [cells[mode, 'remote'] for mode in ['minimal', 'toplevel']]
    text = f'''{HEADING}

Product commit `{comparison['source_commit']}` was built from tested tree
`{comparison['source_tree']}`; all **{verification['test_cases']} test cases** passed.
The comparison uses the same six Datadog targets, revision, output base, embedded tools,
output digests, and pinned vanilla binary as the preceding block-authentication matrix.
Both matrices contain three measured samples per arm/policy. The new run completed
18 measured builds after one deliberate cross-binary prime and two excluded stabilization
passes. The prime succeeded with zero old-seed hits and a committed new publication;
the final stabilization wrote zero records. Every measured cache reader restored
17 configured-target and 11 execution entries, with clean graph/action state and no
cache failures, transfer skips or scan/read-limit failures.

| Output policy | Arm | Previous median (range) | Overlapping reads median (range) | Previous / current |
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

The new counters separate accumulated cached-read work from accumulated download-RPC
duration. They are **not exclusive wall-time components**: cached operations can overlap
each other and the RPC stream, so their sums must not be added into a wall-time breakdown
or interpreted as CPU time. Cached-read timing starts after acquiring one of eight slots;
the RPC counter measures the transport download call, not all metadata RPCs or queue waits.

| Remote sample | Build wall | Cached-read accumulated duration | Download-RPC accumulated duration | Peak cached readers |
| --- | ---: | ---: | ---: | ---: |
{chr(10).join(duration_table)}

Observed cached-reader peaks span **{min(peaks)}–{max(peaks)}**, within the limit of eight.
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

Remaining limits include one download-RPC stream, 1 MiB wire chunks, repeated uploads
when a new publication is exported, and work required to read and authenticate local
chunk ranges. The measurements isolate overlapping cached I/O; they do not evaluate
upload deduplication, smaller chunks, CDC, or a production-network deployment. All
source/seed/CAS/catalog preservation and cleanup checks passed. Exact raw reports,
the prior report hashes, binary/runtime JAR provenance, harnesses, build scripts,
legacy helpers, logs/profiles and test XML are frozen with SHA-256 checksums; catalog
directories and signing keys are excluded from the archive.

[Six-cell and per-sample comparison](measurements/skycache-parallel-reads-agent-2026-10-25/comparison.json),
[successful prime](measurements/skycache-parallel-reads-agent-2026-10-25/prime.json),
[raw matrix](measurements/skycache-parallel-reads-agent-2026-10-25/matrix.json),
[verification/provenance](measurements/skycache-parallel-reads-agent-2026-10-25/verification.json),
and [logs, profiles and test XML](measurements/skycache-parallel-reads-agent-2026-10-25/logs-profiles-tests.tar.gz).

'''
    path = REPO / 'docs/jsgette/skycache-remote-mvp.md'
    document = path.read_text()
    anchor = '## Content-defined chunking\n'
    assert HEADING not in document and document.count(anchor) == 1
    path.write_text(document.replace(anchor, text + anchor, 1))
    print(text)


if __name__ == '__main__':
    main()
