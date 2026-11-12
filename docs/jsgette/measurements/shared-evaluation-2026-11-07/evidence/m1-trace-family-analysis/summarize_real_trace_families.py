#!/usr/bin/env python3
"""Stream already verified C-backed N1 traces into diagnostic family counters."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import statistics
import tarfile

import analyze_real_trace_n1 as analysis

require = analysis.require
MIB = 1024**2
STATES = ('CHECK_DIRTY', 'INITIALIZING_ENVIRONMENT', 'COMPUTE', 'COMMIT')
KINDS = ('header', 'node', 'event', 'observation', 'bzl_input', 'interval', 'footer')
MAX_NODES, MAX_RECORDS = 250000, 1000000


def pinned_path(path, pins, provenance, limit=512 * MIB):
    path = analysis.c_path(path)
    normalized = {analysis.c_path(name): digest for name, digest in pins.items()}
    require(path in normalized and path.is_file() and path.stat().st_size <= limit,
            'Missing or oversized pinned C input: ' + str(path))
    actual = analysis.archive.sha(path)
    require(actual == normalized[path], 'Pinned input changed: ' + str(path))
    provenance[str(path)] = actual
    return path


def pinned_json(path, pins, provenance, limit=16 * MIB):
    path = pinned_path(path, pins, provenance, limit)
    raw = analysis.archive.read(path, limit)
    require(hashlib.sha256(raw).hexdigest() == provenance[str(path)], 'Input changed during read')
    return json.loads(raw)


def cpu_summary(counts):
    known, unknown, intervals = counts
    return {'known_exclusive_ns': known, 'unknown_intervals': unknown, 'intervals': intervals,
            'complete_exclusive_ns': known if intervals and not unknown else None}


def add_cpu(target, source):
    for state, values in source.items():
        current = target.setdefault(state, [0, 0, 0])
        for index, value in enumerate(values):
            current[index] += value


def stream_trace(stream, expected_sha, expected_bytes, replay):
    """Reuse prior hierarchy validation only after proving identical trace bytes."""
    require(type(expected_bytes) is int and 0 < expected_bytes <= 256 * MIB, 'Trace byte bound exceeded')
    nodes, record_counts, observations = {}, Counter(), defaultdict(Counter)
    whole, preceding, size, records = hashlib.sha256(), hashlib.sha256(), 0, 0
    header = footer = None

    def node(node_id):
        require(type(node_id) is int and node_id > 0, 'Invalid node ID')
        if node_id not in nodes:
            require(len(nodes) < MAX_NODES, 'Node identity bound exceeded')
            nodes[node_id] = {'info': None, 'cpu': {}, 'values': Counter(), 'observed': False, 'retained': False}
        return nodes[node_id]

    while raw := stream.readline(MIB + 1):
        require(len(raw) <= MIB and raw.endswith(b'\n'), 'Oversized or unterminated record')
        size += len(raw)
        require(size <= expected_bytes and records < MAX_RECORDS, 'Trace byte/record bound exceeded')
        whole.update(raw)
        row = json.loads(raw)
        kind = row['type']
        require(kind in KINDS and row['seq'] == records and footer is None, 'Record order/type differs')
        require(kind == 'header' if records == 0 else kind != 'header', 'Header must occur exactly once first')
        records += 1
        if kind == 'footer':
            footer = row
            require(row['preceding_bytes_sha256'] == preceding.hexdigest(), 'Footer prefix digest differs')
            continue
        preceding.update(raw)
        record_counts[kind] += 1
        if kind == 'header':
            header = row
            continue
        item = node(row['node_id'])
        if kind == 'node':
            require(item['info'] is None, 'Duplicate node declaration')
            key = row['logical_key']
            item['info'] = (row['function'], row['key_status'], row['reason'], key[0] if key else None)
        elif kind == 'interval':
            state, value = row['state'], row['exclusive_cpu_ns']
            require(state in STATES and (value is None or type(value) is int and value >= 0), 'Invalid exclusive CPU')
            add_cpu(item['cpu'], {state: [0 if value is None else value, int(value is None), 1]})
            item['observed'] = True
        elif kind == 'event':
            item['observed'] = True
        elif kind == 'observation':
            projection = row['projection']
            reason = row['reason']
            value_class = (reason[len('unsupported_value_'):] if isinstance(reason, str)
                           and reason.startswith('unsupported_value_') else None)
            item['values'][(row['value_status'], reason, value_class,
                            projection['type'] if projection else None,
                            projection['class'] if projection else None)] += 1
            item['retained'] |= row['origin'] == 'retained_done'
            counts = observations[(row['origin'], row['deps_status'], row['deps_origin'])]
            counts['observations'] += 1
            if row['deps_status'] == 'grouped':
                groups = row['groups']
                counts['grouped_observations'] += 1
                counts['empty_grouped_observations'] += int(groups == [])
                counts['groups'] += len(groups)
                counts['empty_groups'] += sum(not group for group in groups)
                counts['edges'] += sum(len(group) for group in groups)
                counts['null_observation_references'] += sum(edge[1] is None for group in groups for edge in group)

    require(size == expected_bytes and whole.hexdigest() == expected_sha, 'Streamed trace identity differs')
    require(header is not None and footer is not None and footer['complete'] is True
            and footer['limits_exceeded'] == [] and footer['writer_error'] is None
            and footer['unbalanced_intervals'] == 0, 'Completed trace footer required')
    require(footer['record_counts'] == {kind: record_counts[kind] for kind in KINDS}, 'Record counts differ')
    require(all(item['info'] is not None for item in nodes.values()), 'Undeclared node reference')
    functions, state_cpu = {}, {}
    for item in nodes.values():
        function, key_status, reason, root = item['info']
        family = functions.setdefault(function, {'nodes': 0, 'observed_nodes': 0, 'retained_only_nodes': 0,
            'dependency_only_nodes': 0, 'observed_nodes_without_compute_interval': 0,
            'key_status': Counter(), 'key_unsupported_reasons': Counter(), 'key_roots': Counter(),
            'value_status': Counter(), 'value_unknown_reasons': Counter(), 'unsupported_value_classes': Counter(),
            'exact_projections': Counter(), 'cpu': {}})
        family['nodes'] += 1
        family['observed_nodes'] += item['observed']
        family['retained_only_nodes'] += item['retained'] and not item['observed']
        family['dependency_only_nodes'] += not item['retained'] and not item['observed']
        family['observed_nodes_without_compute_interval'] += item['observed'] and 'COMPUTE' not in item['cpu']
        family['key_status'][key_status] += 1
        family['key_roots'][root if root is not None else 'unknown'] += 1
        if key_status == 'unsupported':
            family['key_unsupported_reasons'][reason] += 1
        for (status, value_reason, value_class, kind, concrete_class), count in item['values'].items():
            family['value_status'][status] += count
            if status != 'exact_projection':
                family['value_unknown_reasons'][value_reason] += count
            if value_class is not None:
                family['unsupported_value_classes'][value_class] += count
            if kind is not None:
                family['exact_projections'][(kind, concrete_class, root)] += count
        add_cpu(family['cpu'], item['cpu'])
        add_cpu(state_cpu, item['cpu'])
    raw_cpu = {state: {'known_exclusive_ns': values[0], 'unknown_intervals': values[1]}
               for state, values in state_cpu.items()}
    require(raw_cpu == replay['exclusive_cpu_by_state'], 'Raw exclusive CPU differs from prior hierarchy-verified replay')
    coverage = {'node_count': len(nodes), 'observation_count': record_counts['observation'],
        'observed_node_count': sum(item['observed'] for item in nodes.values()),
        'retained_only_node_count': sum(item['retained'] and not item['observed'] for item in nodes.values()),
        'dependency_only_node_count': sum(not item['retained'] and not item['observed'] for item in nodes.values())}
    require(all(replay[key] == value for key, value in coverage.items()), 'Prior replay coverage differs')
    require(all(footer[key] == value for key, value in coverage.items() if key != 'observation_count')
            and footer['edge_count'] == sum(counts['edges'] for counts in observations.values()), 'Footer coverage differs')
    for family in functions.values():
        family['exclusive_cpu_by_state'] = {state: cpu_summary(family['cpu'].get(state, [0, 0, 0])) for state in STATES}
        del family['cpu']
        family['exact_projections'] = [{'kind': kind, 'class': concrete, 'root': root, 'observations': count}
            for (kind, concrete, root), count in sorted(family['exact_projections'].items())]
    return {'header': header, 'coverage': coverage, 'record_counts': dict(record_counts),
        'exclusive_cpu_by_state': {state: cpu_summary(values) for state, values in state_cpu.items()},
        'functions': functions, 'observations_by_origin_and_dependencies': [
            {'origin': origin, 'deps_status': status, 'deps_origin': deps_origin,
             **{key: counts[key] for key in ('observations', 'grouped_observations', 'empty_grouped_observations',
                 'groups', 'empty_groups', 'edges', 'null_observation_references')}}
            for (origin, status, deps_origin), counts in sorted(observations.items())],
        'footer_unknown_counts': footer['unknown_counts']}


def trace_summary(row, metadata, verification, progress, provenance):
    directory = analysis.c_path(analysis.E / 'evidence/auxiliary' / row['run_id'])
    pins, declared = row['input_sha256'], row['trace']
    replay = pinned_json(directory / 'verification/replay-result.json', pins, provenance)
    attestations = pinned_json(directory / 'verification/attestations.json', pins, provenance)
    proof = {}
    analysis.closed_wire(directory, progress, proof)
    require(all(pins[name] == digest for name, digest in proof.items()), 'Wire proof differs from pinned analysis')
    provenance.update(proof)
    require(provenance[str(directory / 'verification/replay-result.json')] == declared['replay_result_sha256']
            and replay['traces'] == [declared['replay_summary']]
            and replay['attestation_sha256'] == provenance[str(directory / 'verification/attestations.json')]
            == declared['attestation_sha256'] and replay['milestone_verdict'] is None
            and replay['replay_source_sha256'] == declared['replay_source_sha256']
            and replay['contract_sha256'] == declared['contract_sha256'], 'Prior successful replay binding differs')
    summary = declared['replay_summary']
    require(summary['context_attested'] is True and summary['engine_id'] == row['run_id']
            and summary['strict_cross_engine_observation_matches'] == summary['projection_cross_engine_observation_matches'] == 0
            and verification['trace']['sha256'] == declared['sha256'] == summary['trace_sha256'], 'Single-trace replay identity differs')
    require(set(attestations['traces']) == {declared['sha256']}, 'Exactly one trace attestation required')
    attestation = attestations['traces'][declared['sha256']]
    inventory = {member['path']: member for member in metadata['members']}
    require(len(inventory) == len(metadata['members']) <= 128
            and sum(member['bytes'] for member in inventory.values()) <= 512 * MIB, 'Archive inventory budget differs')
    require(inventory['measured/trace.jsonl']['sha256'] == declared['sha256']
            and inventory['measured/trace.jsonl']['bytes'] == declared['bytes']
            and inventory['measured/context.json']['sha256'] == attestation['context_sha256']
            and inventory['measured/trace.jsonl.completion.json']['sha256'] == attestation['completion_sha256']
            == summary['completion_sha256'], 'Archived trace/context/receipt binding differs')
    found, names = {}, []
    with tarfile.open(directory / 'runtime.tar.gz', mode='r|gz') as archive:
        for member in archive:
            names.append(member.name)
            require(member.name in inventory and member.isfile() and member.size == inventory[member.name]['bytes'],
                    'Unexpected archive member')
            if member.name == 'measured/trace.jsonl':
                result = stream_trace(archive.extractfile(member), declared['sha256'], declared['bytes'], summary)
                found[member.name] = True
            elif member.name in ('measured/context.json', 'measured/trace.jsonl.completion.json'):
                require(member.size <= 4096, 'Context/receipt exceeds bound')
                raw = archive.extractfile(member).read(4097)
                require(len(raw) == member.size and hashlib.sha256(raw).hexdigest() == inventory[member.name]['sha256'],
                        'Archived context/receipt bytes differ')
                found[member.name] = json.loads(raw)
    require(names == sorted(inventory) and len(found) == 3, 'Archived membership differs')
    header, context, receipt = result.pop('header'), found['measured/context.json'], found['measured/trace.jsonl.completion.json']
    require(context == attestation['context'] and all(header[key] == value for key, value in context.items())
            and header['context_sha256'] == attestation['context_sha256']
            and header['trace_id'] == summary['trace_id'] and header['engine_id'] == row['run_id']
            and header['snapshot_id'] == summary['snapshot_id'] and header['phase'] == row['state']
            and receipt == {'schema_version': 1, 'completion': True, 'command_id': header['command_id'],
                'context_sha256': attestation['context_sha256'], 'trace_path': attestation['trace_path']},
            'Context/completion receipt identity differs')
    return {key: row[key] for key in ('run_id', 'pair', 'state')} | result | {
        'trace_sha256': declared['sha256'], 'trace_bytes': declared['bytes'], 'context': context, 'receipt': receipt,
        'context_sha256': attestation['context_sha256'], 'completion_sha256': attestation['completion_sha256'],
        'prior_hierarchy_validation': {'replay_result_sha256': declared['replay_result_sha256'],
            'replay_source_sha256': declared['replay_source_sha256'], 'wire_guard_sha256': progress['wire_guard_sha256'],
            'archive_sha256': metadata['archive_sha256'], 'verification_sha256': progress['verification_sha256']}}


def rank_functions(rows):
    result = {}
    for state in ('restart', 'warm'):
        matches = [row for row in rows if row['state'] == state]
        require(sorted(row['pair'] for row in matches) == list(range(1, 6)), 'Exactly five trace-on rows per lifecycle required')
        matches.sort(key=lambda row: row['pair'])
        functions = sorted({function for row in matches for function in row['functions']})
        ranks = []
        for function in functions:
            costs = [row['functions'].get(function, {}).get('exclusive_cpu_by_state', {}).get('COMPUTE') for row in matches]
            values = [cost['known_exclusive_ns'] if cost and cost['intervals'] else None for cost in costs]
            ranks.append({'function': function, 'pairs': [row['pair'] for row in matches],
                'known_exclusive_compute_ns': values,
                'unknown_compute_intervals': [cost['unknown_intervals'] if cost else None for cost in costs],
                'complete_exclusive_compute_ns': [cost['complete_exclusive_ns'] if cost else None for cost in costs],
                'median_known_exclusive_compute_ns': statistics.median(values) if all(value is not None for value in values) else None})
        ranks.sort(key=lambda row: (-(row['median_known_exclusive_compute_ns'] or 0), row['function']))
        result[state] = ranks
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis', required=True, type=Path)
    parser.add_argument('--analysis-sha256', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(re.fullmatch('[0-9a-f]{64}', args.analysis_sha256), 'Expected analysis SHA-256 required')
    output, provenance = analysis.c_path(args.output), {}
    require(not output.exists(), 'Output must be new')
    data = pinned_json(args.analysis, {str(args.analysis): args.analysis_sha256}, provenance)
    require(data['kind'] == 'n1-trace-on-off-diagnostic' and data['verified_rows'] == 20
            and data['diagnostic_only'] is True and data['performance_eligible'] is False and data['milestone_verdict'] is None,
            'Completed diagnostic analysis required')
    pins = data['input_sha256']
    pinned_path(Path(analysis.__file__), {str(Path(analysis.__file__)): data['analyzer_sha256']}, provenance)
    for name in pins:
        pinned_path(name, pins, provenance)
    preparation = analysis.WORK / 'm1-trace-n1-matrix-preparation-v1'
    order = pinned_json(preparation / 'order.json', pins, provenance)
    progress = pinned_json(preparation / 'execution-progress.json', pins, provenance)
    identities = [{key: row[key] for key in ('run_id', 'pair', 'state', 'trace_enabled')} for row in data['rows']]
    require(identities == analysis.runner.schedule() == [{key: row[key] for key in identities[0]} for row in order['rows']]
            and all(row['verified'] is True for row in data['rows'])
            and progress['status'] == 'completed_diagnostic_no_gate_verdict' and progress['role'] == 'matrix'
            and progress['order_sha256'] == provenance[str(preparation / 'order.json')]
            and [row['run_id'] for row in progress['rows']] == [row['run_id'] for row in data['rows']],
            'All twenty closed, verified frozen rows required')
    results = []
    for row, receipt in zip(data['rows'], progress['rows']):
        directory = analysis.E / 'evidence/auxiliary' / row['run_id']
        for name in row['input_sha256']:
            pinned_path(name, row['input_sha256'], provenance)
        metadata = pinned_json(directory / 'runtime.tar.gz.json', row['input_sha256'], provenance)
        verification = pinned_json(directory / 'verification/result.json', row['input_sha256'], provenance)
        require(receipt['status'] == 'captured_and_verified_no_gate_verdict'
                and receipt['launch_exit_code'] == receipt['capture_exit_code'] == 0
                and receipt['archive_metadata_sha256'] == provenance[str(directory / 'runtime.tar.gz.json')]
                and receipt['verification_sha256'] == provenance[str(directory / 'verification/result.json')]
                and metadata['archive_sha256'] == verification['archive_sha256'] == provenance[str(directory / 'runtime.tar.gz')]
                and metadata['passed'] is True and verification['verified'] is True
                and verification['status'] == 'verified_diagnostic_no_gate_verdict'
                and verification['run_id'] == metadata['run_id'] == row['run_id'], 'Closed archive/verification chain differs')
        if row['trace_enabled']:
            results.append(trace_summary(row, metadata, verification, receipt, provenance))
    require(len(results) == 10, 'Exactly ten existing trace archives required')
    result = {'schema_version': 1, 'kind': 'n1-trace-family-coverage', 'diagnostic_only': True,
        'performance_eligible': False, 'milestone_verdict': None,
        'helper_sha256': analysis.archive.sha(Path(__file__)), 'input_sha256': provenance,
        'rows': results, 'function_compute_rank_by_lifecycle': rank_functions(results),
        'limitations': ['Existing ten N1 traces only; no new capture, benchmark, replay or cross-engine reuse credit.',
            'Raw exclusive CPU is reused only by exact byte identity with prior successful hierarchy validation.',
            'Known CPU subtotals retain unknown interval counts; missing or incomplete totals stay null.',
            'Rank medians use all five known subtotals in one lifecycle; unknown CPU is not inferred or scaled.',
            'Grouped dependency counts describe captured observations, not atomic or complete demand closure.',
            'Unsupported key roots/classes are unrecorded; unsupported value classes use only explicit reason suffixes.',
            'Observation versions are counted separately; retained-only nodes earn no avoided command CPU.',
            'No trace overhead subtraction, whole-build CPU attribution, PSS conversion, savings estimate or gate verdict.']}
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output': str(output), 'traces': 10, 'milestone_verdict': None}))


if __name__ == '__main__':
    main()
