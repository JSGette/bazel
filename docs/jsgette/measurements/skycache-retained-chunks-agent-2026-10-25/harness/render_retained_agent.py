"""Insert validated retained-chunk measurements before Content-defined chunking."""

from pathlib import Path
import hashlib
import json


REPO = Path('/home/jgett/REPOS/bazel-skycache-remote-mvp')
EVIDENCE = REPO / 'docs/jsgette/measurements/skycache-retained-chunks-agent-2026-10-25'
HEADING = '### Agent repository measurements with retained remote chunks'
REMOTE_ARMS = ('remote', 'remote-warm')


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
    assert verification['passed'] and verification['test_cases'] == build['test_cases'] == 612
    assert verification['max_concurrency'] == 100
    assert verification['local_cache_max_bytes'] == 5 * 1024**3
    assert matrix['status'] == prime['status'] == 'passed'
    assert matrix['purpose'] == 'retained-chunk-matrix'
    assert matrix['measured_count'] == 24 and matrix['preparation_count'] == 4
    assert prime['measured_count'] == 0 and prime['preparation_count'] == 1
    assert verification['retained_warmup_samples'] == 2 and matrix['retained_seed_unchanged']
    assert len(comparison['stabilization']) == len(comparison['retained_warmups']) == 2
    seed = comparison['retained_seed']
    assert seed == verification['retained_seed'] == matrix['retained_seed']
    assert seed['produced_by_current_binary'] and seed['excluded_from_measurement']
    assert seed['lockfiles_excluded'] and seed['copied_parts'] == ['leased-v2/remote-chunks-v1/sha256']
    assert seed['frozen_catalog_receipts'] == matrix['primer_receipts']
    assert seed['inventory']['file_bytes'] > 0
    cells = {(row['mode'], row['arm']): row for row in comparison['summary']}
    assert len(cells) == 8
    labels = {'vanilla': 'vanilla', 'local': 'local SkyCache',
              'remote': 'remote, empty retained cache', 'remote-warm': 'remote, warm retained cache'}
    table = []
    within_table = []
    for mode in ['minimal', 'toplevel']:
        for arm in ['vanilla', 'local', *REMOTE_ARMS]:
            cell = cells[mode, arm]
            assert cell['samples'] == cell['prior_samples'] == 3
            assert cell['prior_arm'] == ('remote' if arm == 'remote-warm' else arm)
            table.append(f"| {mode} | {labels[arm]} | {cell['prior_arm']} | "
                         f"{cell['prior_median_seconds']:.2f} s ({interval(cell['prior_seconds'])}) | "
                         f"{cell['median_seconds']:.2f} s ({interval(cell['seconds'])}) | "
                         f"{cell['prior_over_current']:.2f}x |")
        warm = cells[mode, 'remote-warm']
        within_table.append(f"| {mode} | {warm['current_local_median_seconds']:.2f} s | "
                            f"{warm['current_cold_remote_median_seconds']:.2f} s | "
                            f"{warm['median_seconds']:.2f} s | {warm['current_local_over_current']:.2f}x | "
                            f"{warm['current_cold_remote_over_current']:.2f}x |")
    remote = comparison['remote_duration_samples']
    assert len(remote) == 12
    cold = [row for row in remote if row['arm'] == 'remote']
    warm = [row for row in remote if row['arm'] == 'remote-warm']
    assert len(cold) == len(warm) == 6
    assert all(row['remote']['downloaded_bytes'] > 0 and row['remote']['downloaded_chunks'] > 0
               and row['seeded_cache_files'] == row['seeded_cache_bytes'] == 0 for row in cold)
    assert all(row['remote']['downloaded_bytes'] == row['remote']['downloaded_chunks'] == 0
               and row['remote']['local_cache_hits'] > 0 and row['remote']['local_cache_bytes'] > 0
               and row['retained_seed_sha256'] == seed['inventory']['sha256'] for row in warm)
    assert all(row['remote']['local_cache_errors'] == 0
               and row['remote']['local_cache_max_bytes'] == 5 * 1024**3
               and row['remote']['max_concurrency'] == 100 for row in remote)
    duration_table = []
    for row in remote:
        duration_table.append(f"| {row['label']} | {row['seconds']:.2f} s | "
                              f"{row['cached_read_seconds']:.2f} s | {row['download_rpc_seconds']:.2f} s | "
                              f"{row['remote']['peak_cached_reads']} | {row['remote']['peak_chunk_downloads']} |")
    peaks = [row['remote']['peak_cached_reads'] for row in remote]
    download_peaks = [row['remote']['peak_chunk_downloads'] for row in cold]
    assert all(1 <= peak <= 100 for peak in peaks + download_peaks)
    assert all(row['remote']['peak_chunk_downloads'] == 0 for row in warm)
    auth = [row['remote']['authentication_cache_bytes'] for row in remote]
    evictions = [row['remote']['authentication_cache_evictions'] for row in remote]
    assert max(auth) <= 32 * 1024**2
    payload = [row['remote']['downloaded_bytes'] for row in cold]
    local_hits = [row['remote']['local_cache_hits'] for row in warm]
    local_bytes = [row['remote']['local_cache_bytes'] for row in warm]
    wire_down = [row['wire_download_bytes'] for row in warm]
    wire_up = [row['wire_upload_bytes'] for row in warm]
    writes = [row['local-skycache.json']['writes'] for row in remote]
    materialized = [row['remote']['materialized_publications'] for row in remote]
    reused = [row['remote']['reused_publications'] for row in remote]
    commits = sum(row['remote_committed'] for row in remote)
    memory_table = []
    for mode in ['minimal', 'toplevel']:
        for arm in REMOTE_ARMS:
            cell = cells[mode, arm]
            memory_table.append(f"| {mode} | {labels[arm]} | {cell['median_peak_pss_kib'] / 1024**2:.2f} GiB | "
                                f"{cell['median_worker_peak_pss_kib'] / 1024**2:.2f} GiB | "
                                f"{int(cell['median_rchar']):,} B |")
    text = f'''{HEADING}

Product commit `{comparison['source_commit']}` was built from tested tree
`{comparison['source_tree']}`; all **{verification['test_cases']} test cases** passed.
These measurements include shorter locks for sealed bucket reads and a persistent
cache for verified remote chunks, with the default **5 GiB** soft retained-file limit
and concurrency **100** for cached reads and downloads.
The comparison uses the same six Datadog targets, revision, output base, embedded tools,
output digests and pinned vanilla binary as the preceding concurrent-download matrix.
That historical baseline remains unchanged and has no retained-cache warm arm.

The new matrix completed **24 measured builds**, three samples for each of four arms
and two output policies. One deliberate cross-binary prime, two stabilization passes
and two retained-cache warmups are excluded from these samples. The prime succeeded
with zero old-seed hits and a committed new publication; the final stabilization wrote
zero records. Every measured cache reader restored **17 configured-target and
11 execution entries**, with clean graph/action state and no cache failures, transfer
skips or scan/read-limit failures. All six binary digests match the pinned vanilla
outputs in BEP; toplevel downloads also match by actual SHA-256.

| Output policy | Current arm | Historical arm | Previous median (range) | Current median (range) | Previous / current |
| --- | --- | --- | ---: | ---: | ---: |
{chr(10).join(table)}

The warm retained arm uses the previous **cold remote** result as its historical
comparison; there is no previous warm result. Ratios above 1 indicate lower current
wall time. These three-sample cells describe this run and do not establish statistical
significance. The comparison within the current matrix uses the same candidate binary:

| Output policy | Local median | Cold remote median | Warm remote median | Local / warm | Cold / warm |
| --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(within_table)}

Each binary has its own prepared publication seed because installation identity
versions analysis keys. Within this matrix, local and both remote arms use the same
frozen publication metadata. Cold remote samples begin with an empty client cache.
For warm remote samples, two excluded fresh-JVM reads, one per output policy, each
populate an empty private cache against the frozen catalog. Their verified chunk sets
are combined into a separate retained-chunk seed. Its
**{seed['inventory']['files']:,} files / {seed['inventory']['file_bytes']:,} B** are copied
into a new cache for each warm sample. Only the chunk subtree is copied: local publication
objects, leases and lockfiles are excluded. Source seed contents and catalog receipts
remain unchanged. Each warm read still verifies the length and SHA-256 of its private
chunk pin before use; persistent bytes are not implicitly trusted.

Arm order rotates and policy order alternates. Every sample has a fresh JVM and a clean
graph/action cache. Startup, clean, retained-seed copying and one backend warmup round
are excluded from build wall time; publication work is included. The metadata backend
is loopback with warmed CAS and OS caches. Thus the warm arm measures retained-byte
reuse under these conditions, not cold storage or a production network. Ordinary
action-cache connections retain the common override of 16 requests per connection.

Cold remote samples downloaded **{min(payload):,}–{max(payload):,} B** of blob payload.
All six warm samples downloaded **zero blobs and zero blob bytes**, with
**{min(local_hits):,}–{max(local_hits):,}** retained-cache hits and
**{min(local_bytes):,}–{max(local_bytes):,} B** reused per sample. This does not mean zero
network traffic: catalog discovery, protocol overhead and publication uploads remain.
The warm samples recorded **{min(wire_down):,}–{max(wire_down):,} B** received and
**{min(wire_up):,}–{max(wire_up):,} B** sent through the metadata proxy. Those wire counters
are separate from ordinary action-cache traffic. No persistent-cache errors were recorded.

The timing counters accumulate operation durations and are **not exclusive wall-time
components**. Parallel download calls contribute overlapping durations; cached reads
can overlap one another and downloads. Neither counter nor their sum is a wall-time
breakdown or CPU time. The RPC counter covers transport download calls, including
transport admission delay, after acquiring the client download permit; it excludes
waiting for that permit and does not cover all metadata RPCs. Local retained-chunk
verification is included in cached-read duration; it is not a separate exclusive timing category.

| Remote sample | Build wall | Cached-read accumulated duration | Download-RPC accumulated duration | Peak cached readers | Peak chunk downloads |
| --- | ---: | ---: | ---: | ---: | ---: |
{chr(10).join(duration_table)}

Observed cached-reader peaks span **{min(peaks)}–{max(peaks)}**. Cold chunk-download peaks
span **{min(download_peaks)}–{max(download_peaks)}**, within 100; warm download peaks are
zero. Authentication-cache weight reaches **{max(auth):,} B**, within 32 MiB, with
**{min(evictions):,}–{max(evictions):,}** evictions per remote sample. Remote writes span
**{min(writes):,}–{max(writes):,}**; **{commits} of {len(remote)}** remote samples report a
publication commit. Materialized-publication counts span {min(materialized)}–{max(materialized)}
and reused-publication counts span {min(reused)}–{max(reused)}. These observed counts
are recorded without a fixed payload or write-count assumption.

| Output policy | Arm | Median client peak PSS | Median worker peak PSS | Median process rchar |
| --- | --- | ---: | ---: | ---: |
{chr(10).join(memory_table)}

PSS is sampled process-tree memory; worker peaks are separate and filesystem page cache
is excluded. Process `rchar` is cumulative fresh-JVM read-like I/O including startup and
clean, not physical disk bytes or network traffic. The persistent limit is soft and
targets retained files and 65,536 entries; active private hardlink pins and
invocation scratch can retain additional bytes. Eviction under pressure and concurrent
process sharing were not exercised by this benchmark. Remaining costs include reading
and authenticating retained bytes, first-use downloads, and repeated uploads when a
new publication is exported. Results do not evaluate smaller chunks, CDC, upload
deduplication or production-network behavior.

All source, seed, CAS, catalog, binary/runtime preservation and cleanup checks passed.
Exact raw reports, prior report hashes, runtime JAR provenance, harnesses, build scripts,
legacy helpers, logs/profiles and test XML are frozen with SHA-256 checksums. Catalog
directories and signing keys are excluded from the archive.

[Eight-cell and per-sample comparison](measurements/skycache-retained-chunks-agent-2026-10-25/comparison.json),
[successful prime](measurements/skycache-retained-chunks-agent-2026-10-25/prime.json),
[raw matrix](measurements/skycache-retained-chunks-agent-2026-10-25/matrix.json),
[verification/provenance](measurements/skycache-retained-chunks-agent-2026-10-25/verification.json),
and [logs, profiles and test XML](measurements/skycache-retained-chunks-agent-2026-10-25/logs-profiles-tests.tar.gz).

'''
    path = REPO / 'docs/jsgette/skycache-remote-mvp.md'
    document = path.read_text()
    anchor = '## Content-defined chunking\n'
    assert HEADING not in document and document.count(anchor) == 1
    path.write_text(document.replace(anchor, text + anchor, 1))
    print(text)


if __name__ == '__main__':
    main()

