#!/usr/bin/env python3
"""One frozen C-only audit of measured sidecars from all ten N1 restart rows."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile

import summarize_real_trace_families as helpers

analysis = helpers.analysis
require = analysis.require
MIB = 1024**2
ANALYSIS_SHA256 = 'c286ce13bc68394c4228229bba09422b2504df60e638e5987aacbb38c89e438b'
PROTOCOL_SHA256 = 'b4bf447302e4e4a7cf39f6b7940026f26694d4cc6927d808bdd8aa315e2c66fa'
FRESH = ('measured/local-skycache.json', 'measured/local-skycache-ownership.json')
PHASE = ('measured/command.json', 'measured/process-result.json', 'measured/partial-accounting.json',
         'measured/result.json', 'measured/verified-result.json')
SELECTED = {*FRESH, *PHASE, 'measured/bep.jsonl'}
COUNTERS = ('cacheHits', 'cacheMisses', 'valueStoreValueBytesReceived', 'valueStoreValueBytesSent',
    'valueStoreKeyBytesSent', 'valueStoreWriteOps', 'valueStoreReadOpsSuccessful', 'valueStoreReadOpsNotFound',
    'valueStoreReadBatches', 'valueStoreWriteBatches', 'analysisCacheBytesReceived', 'analysisCacheKeyBytesSent',
    'analysisCacheOps', 'analysisCacheBatches', 'serializationExceptionCount')
DISTRIBUTIONS = ('valueStoreReadLatencyMicros', 'valueStoreReadBatchLatencyMicros', 'valueStoreWriteLatencyMicros',
    'valueStoreWriteBatchLatencyMicros', 'analysisCacheReadLatencyMicros', 'analysisCacheReadBatchLatencyMicros')
COUNT_MAPS = ('cacheMissesByReason', 'cacheMissesBySkyfunction', 'cacheHitsBySkyfunction')
OPTIONAL = ('metadataLookupResult', 'invalidationLookupMetrics', 'peers', 'uploadPhaseTimings')


def integer(value):
    require(type(value) is int and value >= 0 or isinstance(value, str) and re.fullmatch('[0-9]+', value),
            'Expected recorded nonnegative integer')
    return int(value)


def presence(document, key):
    return {'present': key in document, 'raw': document.get(key)}


def histogram(document, key):
    """Count recorded samples only; never sum wall buckets or nested phases."""
    result = {'present': key in document, 'unit': 'elapsed_wall_microseconds',
              'bucket_boundary': 'minimum inclusive; maximum exclusive; omitted fields preserved',
              'bucket_count': None, 'known_recorded_count_sum': None, 'complete_recorded_count_sum': None,
              'missing_bucket_fields': None}
    if key not in document:
        return result
    value = document[key]
    require(isinstance(value, dict), 'Distribution must be an object')
    result['histogram_bucket_field_present'] = 'histogramBucket' in value
    if 'histogramBucket' not in value:
        return result
    buckets = value['histogramBucket']
    require(isinstance(buckets, list), 'Histogram buckets must be a list')
    missing = {field: [] for field in ('min', 'max', 'count')}
    count = 0
    for index, bucket in enumerate(buckets):
        require(isinstance(bucket, dict), 'Histogram bucket must be an object')
        for field in missing:
            if field not in bucket:
                missing[field].append(index)
            else:
                integer(bucket[field])
        if 'min' in bucket and 'max' in bucket:
            require(integer(bucket['min']) < integer(bucket['max']), 'Invalid histogram bounds')
        if 'count' in bucket:
            count += integer(bucket['count'])
    result.update(bucket_count=len(buckets), known_recorded_count_sum=count,
                  complete_recorded_count_sum=None if missing['count'] else count, missing_bucket_fields=missing)
    return result


def cache_summary(metrics):
    if 'remoteAnalysisCacheStatistics' not in metrics:
        return {'present': False, 'raw': None}
    cache = metrics['remoteAnalysisCacheStatistics']
    require(isinstance(cache, dict), 'Cache statistics must be an object')
    counters = {key: {**presence(cache, key), 'integer': integer(cache[key]) if key in cache else None}
                for key in COUNTERS}
    maps = {}
    for key in COUNT_MAPS:
        rows = cache.get(key)
        require(rows is None or isinstance(rows, list), 'Counter map must be a list')
        maps[key] = {'present': key in cache, 'entries': None if rows is None else [
            {'key': presence(row, 'key'), 'value': presence(row, 'value'),
             'integer': integer(row['value']) if 'value' in row else None} for row in rows]}
    retrieval = []
    for row in cache.get('latencyBySkyfunction', []):
        retrieval.append({'skyfunction': presence(row, 'skyfunction'), 'outcome': presence(row, 'outcome'),
                          'phase': presence(row, 'phase'), 'latency': histogram(row, 'latency')})
    recognized = {*COUNTERS, *DISTRIBUTIONS, *COUNT_MAPS, *OPTIONAL, 'latencyBySkyfunction'}
    return {'present': True, 'raw': cache, 'counters': counters,
            'client_and_store_distributions': {key: histogram(cache, key) for key in DISTRIBUTIONS},
            'count_maps': maps, 'retrieval_distributions_present': 'latencyBySkyfunction' in cache,
            'retrieval_distributions': retrieval, 'optional_fields': {key: presence(cache, key) for key in OPTIONAL},
            'other_recorded_fields': sorted(set(cache) - recognized)}


def read_measured(directory, metadata):
    inventory = {row['path']: row for row in metadata['members']}
    require(len(inventory) == len(metadata['members']) <= 128
            and sum(row['bytes'] for row in inventory.values()) <= 512 * MIB, 'Archive inventory budget differs')
    require(SELECTED <= inventory.keys(), 'Required fresh measured evidence is missing')
    values, observed, members = {}, [], []
    with tarfile.open(directory / 'runtime.tar.gz', mode='r|gz') as archive:
        for member in archive:
            observed.append(member.name)
            require(member.name in inventory and member.isfile() and member.size == inventory[member.name]['bytes'],
                    'Unexpected archive membership')
            if member.name.startswith('measured/'):
                role = ('fresh_measured_cache' if member.name in FRESH else
                        'measured_phase' if member.name in PHASE else
                        'measured_bep' if member.name == 'measured/bep.jsonl' else
                        'excluded_prior_command_advisory' if member.name.startswith('measured/previous-') else
                        'not_used_for_cache_attribution')
                members.append({**inventory[member.name], 'audit_role': role, 'content_read': member.name in SELECTED})
            if member.name not in SELECTED:
                continue
            limit = 32 * MIB if member.name.endswith('bep.jsonl') else 2 * MIB
            require(member.size <= limit, 'Selected measured member exceeds budget')
            raw = archive.extractfile(member).read(limit + 1)
            require(len(raw) == member.size and hashlib.sha256(raw).hexdigest() == inventory[member.name]['sha256'],
                    'Selected measured member digest differs')
            if member.name.endswith('bep.jsonl'):
                values[member.name] = [json.loads(line) for line in raw.splitlines() if line.strip()]
            else:
                values[member.name] = json.loads(raw)
    require(observed == sorted(inventory) and set(values) == SELECTED, 'Complete selected member set required')
    return values, members


def summarize_phase(row, report, documents):
    phase = documents['measured/result.json']
    require(phase == report['phases'][1] and phase['phase'] == 'measured' and phase['lifecycle'] == 'restart'
            and phase['trace_enabled'] == row['trace_enabled'] and phase['passed'] is True,
            'Exact successful measured restart phase required')
    verified, process = documents['measured/verified-result.json'], documents['measured/process-result.json']
    require(all(phase['result'][key] == value for key, value in verified.items())
            and all(verified[key] == value for key, value in process.items())
            and process['exit_code'] == 0 and process['timed_out'] is False, 'Measured result/process proof differs')
    partial = documents['measured/partial-accounting.json']
    require(partial == {'sample_count': phase['samples'], 'scope_reads': phase['scope_reads']},
            'Measured partial accounting differs')
    command = documents['measured/command.json']
    require(phase['result']['bep_identity']['build_uuid'] == row['measured']['build_uuid']
            and command['cwd'] == report['fixture']['worktrees'][0]['workspace'], 'Measured command identity differs')
    metrics = [event['buildMetrics'] for event in documents['measured/bep.jsonl'] if 'buildMetrics' in event]
    require(len(metrics) == 1 and metrics[0] == verified['bep']['build_metrics']
            == phase['result']['bep']['build_metrics'], 'Raw measured BEP differs from verified metrics')
    require(metrics[0] == {**row['measured']['other_build_metrics'], 'actionSummary': row['measured']['action_evidence']},
            'Raw measured BEP differs from pinned analysis')
    reads = {read['label']: read for read in phase['scope_reads']}
    labels = ('prepared-request-start', 'build-start', 'build-end', 'prepared-request-drained')
    require(len(reads) == len(labels) and set(reads) == set(labels), 'Measured boundary labels differ')
    for window, start, end in (('build', labels[1], labels[2]), ('prepared_request', labels[0], labels[3]),
                                ('tail', labels[2], labels[3])):
        require(analysis.checked_window(reads[start], reads[end], phase[window]) == row['measured']['windows'][window],
                'Measured phase boundary differs from verified analysis')
    require((process['exited_ns'] - process['started_ns']) / 1e9 == phase['client_wall_seconds']
            == row['measured']['client_wall_seconds'], 'Client interval differs')
    stats, ownership = (documents[name] for name in FRESH)
    analysis.runner.check_drain_documents(ownership, stats)
    require(phase['drain'] == row['measured']['drain'], 'Measured drain proof differs')
    return {'build_uuid': row['measured']['build_uuid'], 'command': command, 'process': process,
        'boundaries_monotonic_ns': {label: reads[label]['monotonic_ns'] for label in labels},
        'client_wall_seconds': phase['client_wall_seconds'],
        'boundary_wall_seconds': {window: phase[window]['boundary_wall_seconds'] for window in ('build', 'prepared_request', 'tail')},
        'drain': phase['drain'], 'sampling_attempts': partial['sample_count'],
        'fresh_local_cache_statistics': stats, 'fresh_ownership_snapshots': ownership,
        'remote_analysis_cache': cache_summary(metrics[0]),
        'other_recorded_bep_metrics': {key: metrics[0][key] for key in ('timingMetrics', 'actionSummary', 'memoryMetrics') if key in metrics[0]},
        'other_bep_metric_field_presence': {key: key in metrics[0] for key in ('timingMetrics', 'actionSummary', 'memoryMetrics')}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', required=True, type=Path)
    parser.add_argument('--analysis-sha256', required=True)
    parser.add_argument('--protocol', required=True, type=Path)
    parser.add_argument('--protocol-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(args.analysis_sha256 == ANALYSIS_SHA256 and args.protocol_sha256 == PROTOCOL_SHA256,
            'Only the frozen residual-investigation inputs are allowed')
    output, provenance = analysis.c_path(args.output), {}
    require(not output.exists(), 'Output must be new')
    helpers.pinned_path(args.protocol, {str(args.protocol): PROTOCOL_SHA256}, provenance, MIB)
    data = helpers.pinned_json(args.analysis, {str(args.analysis): ANALYSIS_SHA256}, provenance)
    require(data['kind'] == 'n1-trace-on-off-diagnostic' and data['verified_rows'] == 20
            and data['diagnostic_only'] is True and data['performance_eligible'] is False and data['milestone_verdict'] is None,
            'Completed diagnostic N1 analysis required')
    helpers.pinned_path(Path(analysis.__file__), {str(Path(analysis.__file__)): data['analyzer_sha256']}, provenance)
    for path in data['input_sha256']:
        helpers.pinned_path(path, data['input_sha256'], provenance)
    preparation = analysis.WORK / 'm1-trace-n1-matrix-preparation-v1'
    progress = helpers.pinned_json(preparation / 'execution-progress.json', data['input_sha256'], provenance)
    order = helpers.pinned_json(preparation / 'order.json', data['input_sha256'], provenance)
    identities = [{key: row[key] for key in ('run_id', 'pair', 'state', 'trace_enabled')} for row in data['rows']]
    require(identities == analysis.runner.schedule() == [{key: row[key] for key in identities[0]} for row in order['rows']]
            and all(row['verified'] is True for row in data['rows'])
            and progress['status'] == 'completed_diagnostic_no_gate_verdict' and progress['role'] == 'matrix'
            and progress['order_sha256'] == provenance[str(preparation / 'order.json')]
            and [row['run_id'] for row in progress['rows']] == [row['run_id'] for row in data['rows']],
            'All twenty closed rows and frozen order required before selecting the fixed restart stratum')
    results = []
    for row, receipt in zip(data['rows'], progress['rows']):
        if row['state'] != 'restart':
            continue
        directory = analysis.E / 'evidence/auxiliary' / row['run_id']
        for path in row['input_sha256']:
            helpers.pinned_path(path, row['input_sha256'], provenance)
        metadata = helpers.pinned_json(directory / 'runtime.tar.gz.json', row['input_sha256'], provenance)
        verification = helpers.pinned_json(directory / 'verification/result.json', row['input_sha256'], provenance)
        report = helpers.pinned_json(directory / 'report.json', row['input_sha256'], provenance)
        require(receipt['archive_metadata_sha256'] == provenance[str(directory / 'runtime.tar.gz.json')]
                and metadata['archive_sha256'] == provenance[str(directory / 'runtime.tar.gz')], 'Archived evidence identity differs')
        analysis.linked_verification(report, provenance[str(directory / 'report.json')], metadata, verification,
            provenance[str(directory / 'verification/result.json')], receipt, metadata['manifest_sha256'])
        if row['trace_enabled']:
            wire = {}
            analysis.closed_wire(directory, receipt, wire)
            require(all(row['input_sha256'][path] == digest for path, digest in wire.items()), 'Closed replay proof changed')
            provenance.update(wire)
        documents, inventory = read_measured(directory, metadata)
        result = summarize_phase(row, report, documents)
        results.append({**{key: row[key] for key in ('run_id', 'pair', 'state', 'trace_enabled')}, **result,
            'archive_sha256': metadata['archive_sha256'], 'archive_metadata_sha256': receipt['archive_metadata_sha256'],
            'verification_sha256': receipt['verification_sha256'], 'measured_member_inventory': inventory})
    require(len(results) == 10 and {enabled: sorted(row['pair'] for row in results if row['trace_enabled'] is enabled)
            for enabled in (False, True)} == {False: list(range(1, 6)), True: list(range(1, 6))},
            'Exactly all five off/on restart pairs required')
    result = {'schema_version': 1, 'kind': 'n1-restart-residual-sidecar-audit', 'diagnostic_only': True,
        'performance_eligible': False, 'milestone_verdict': None, 'input_sha256': provenance,
        'helper_sha256': {str(Path(__file__).resolve()): analysis.archive.sha(Path(__file__)),
                         str(Path(helpers.__file__).resolve()): analysis.archive.sha(Path(helpers.__file__))},
        'rows': results, 'limitations': [
            'All ten existing restarted measured rows; preparation and previous-command sidecars excluded from attribution.',
            'Fresh sidecars are command-local counters and advisory component snapshots, not a CPU or retained-memory partition.',
            'BEP and fresh sidecars may observe different asynchronous boundaries; differences are retained, not forced equal.',
            'Latency buckets retain elapsed wall microseconds and raw optional-field presence; absent fields are not measured zeros.',
            'TOTAL and retrieval subphases overlap or nest; per-operation and per-batch distributions have different populations.',
            'Histogram counts are reported separately; no wall-duration summation, CPU conversion or cross-phase operation total.',
            'Metadata, serialized, allocation and garbage-collection bytes are not retained heap or peak PSS savings.',
            'No new runtime capture, profiling, economic comparison, savings estimate or architecture verdict.']}
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output': str(output), 'restart_rows': 10, 'milestone_verdict': None}))


if __name__ == '__main__':
    main()
