"""Offline captured-table model for m1-typed-trace-v1; never live cache admission.

Attestation JSON maps trace SHA256 to {context,context_sha256,completion_sha256,
trace_path} under "traces"; context contains every context_file_field.
The caller attests those inputs; this program cannot verify their runtime origin.
Only cross-engine captures can earn modeled CPU credit. Same-engine retrieval is
reported separately, and all complete A/B/A variants remain in the table.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import time
import tracemalloc

CONTRACT = json.loads(Path(__file__).with_name('trace-v1.contract.json').read_text(encoding='utf-8'))
KINDS = set(CONTRACT['record_types'])
STATES = {'CHECK_DIRTY', 'INITIALIZING_ENVIRONMENT', 'COMPUTE', 'COMMIT'}
LIMITS = CONTRACT['record_types']['header']['limits']
MAX_TABLE_BYTES = 64 * 1024**2
MAX_VARIANTS = 250000
MAX_MODEL_BYTES = 64 * 1024**2


def require(ok, message):
    if not ok:
        raise ValueError(message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def hex_value(value, digits=None):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]*', value) is not None and (
        len(value) == digits if digits is not None else len(value) % 2 == 0)


def units(value):
    require(hex_value(value) and len(value) % 4 == 0, 'Invalid exact UTF16 code units')
    return bytes.fromhex(value).decode('utf-16-be', errors='surrogatepass')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON: ' + value)))


def validate_projection(node, observation, header):
    value = observation['projection']
    if observation['value_status'] != 'exact_projection':
        require(value is None and isinstance(observation['reason'], str) and observation['reason'],
                'Unknown projection needs null bytes and an explicit reason')
        return
    require(isinstance(value, dict) and observation['reason'] is None, 'Malformed exact projection')
    kind = value.get('type')
    classes = {'REGULAR_FILE': ('RegularFileStateValueWithDigest', {'size', 'digest_algorithm', 'digest_hex'}),
               'DIRECTORY': ('DirectoryFileStateValue', set()),
               'NONEXISTENT': ('NonexistentFileStateValue', set()),
               'SYMLINK': ('SymlinkFileStateValue', {'target_utf16be_hex'}),
               'DIRECTORY_LISTING_STATE': ('DirectoryListingStateValue', {'entries'})}
    require(kind in classes and value.get('class') == classes[kind][0] and
            set(value) == {'class', 'type'} | classes[kind][1], 'Unsupported concrete class/typed fields')
    require(node['function'] == ('DIRECTORY_LISTING_STATE' if kind == 'DIRECTORY_LISTING_STATE' else 'FILE_STATE'),
            'Projection function differs')
    if kind == 'REGULAR_FILE':
        require(integer(value['size']) and hex_value(value['digest_hex']) and value['digest_hex'] and
                value['digest_algorithm'] == header['repository_bindings']['main']['filesystem_policy']['digest_algorithm'],
                'Regular digest identity differs from filesystem policy')
    elif kind == 'SYMLINK':
        units(value['target_utf16be_hex'])
    elif kind == 'DIRECTORY_LISTING_STATE':
        require(isinstance(value['entries'], list), 'Listing entries must be an array')
        names = []
        for row in value['entries']:
            require(isinstance(row, list) and len(row) == 2 and row[1] in {'FILE', 'DIRECTORY', 'SYMLINK', 'UNKNOWN'},
                    'Invalid typed directory entry')
            name = units(row[0])
            require(name and '/' not in name and name not in {'.', '..'}, 'Invalid listing name')
            names.append(row[0])
        require(names == sorted(set(names)), 'Listing must be unique and ordered by UTF16 code units')
    require(len(canonical(value)) <= header['limits']['max_projection_bytes'], 'Projection byte bound exceeded')


def exclusive_intervals(intervals, header):
    """Recompute raw same-thread nesting; unknown children never become zero."""
    children = defaultdict(list)
    occurrences = defaultdict(list)
    for row in intervals.values():
        require(row['state'] in STATES and integer(row['thread_id'], 1) and integer(row['occurrence'], 1) and
                type(row['virtual_thread']) is bool and integer(row['wall_start_ns']), 'Invalid interval identity')
        require(row['status'] in {'complete', 'unsupported_cpu', 'unbalanced'}, 'Invalid interval status')
        require(row['wall_end_ns'] is None or integer(row['wall_end_ns']) and row['wall_end_ns'] >= row['wall_start_ns'],
                'Wall clock regressed')
        occurrences[row['node_id'], row['state']].append((row['interval_id'], row['occurrence']))
        parent = row['parent_interval_id']
        if parent is not None:
            require(parent in intervals and parent < row['interval_id'], 'Invalid interval parent')
            outer = intervals[parent]
            require(outer['thread_id'] == row['thread_id'] and outer['seq'] > row['seq'] and
                    outer['wall_start_ns'] <= row['wall_start_ns'] and
                    (outer['wall_end_ns'] is None or row['wall_end_ns'] is not None and
                     row['wall_end_ns'] <= outer['wall_end_ns']), 'Invalid same-thread nesting')
            if all(value is not None for value in (outer['cpu_start_ns'], outer['cpu_end_ns'],
                                                    row['cpu_start_ns'], row['cpu_end_ns'])):
                require(outer['cpu_start_ns'] <= row['cpu_start_ns'] <= row['cpu_end_ns'] <= outer['cpu_end_ns'],
                        'Nested CPU counters leave parent interval')
        children[parent, row['thread_id']].append(row)
        for field in ('cpu_start_ns', 'cpu_end_ns', 'exclusive_cpu_ns'):
            require(row[field] is None or integer(row[field]), 'Invalid CPU counter')
        if row['cpu_start_ns'] is not None and row['cpu_end_ns'] is not None:
            require(row['cpu_end_ns'] >= row['cpu_start_ns'], 'CPU counter regressed')
        require((row['status'] == 'complete' and row['reason'] is None) or
                (row['status'] != 'complete' and isinstance(row['reason'], str) and row['reason']), 'Interval reason missing')
    for rows in occurrences.values():
        require([v for _, v in sorted(rows)] == list(range(1, len(rows) + 1)), 'Interval occurrence regression/gap')
    require(len({row['thread_id'] for row in intervals.values()}) <= LIMITS['max_threads'], 'Thread bound exceeded')
    depths = {}
    for index in sorted(intervals):
        parent = intervals[index]['parent_interval_id']
        depths[index] = 1 + (depths[parent] if parent is not None else 0)
        require(depths[index] <= LIMITS['max_stack_depth'], 'Interval stack depth exceeded')
    for siblings in children.values():
        siblings.sort(key=lambda row: row['interval_id'])
        for first, second in zip(siblings, siblings[1:]):
            require(first['wall_end_ns'] is not None and first['wall_end_ns'] <= second['wall_start_ns'],
                    'Sibling intervals overlap or are unbalanced')
            if first['cpu_end_ns'] is not None and second['cpu_start_ns'] is not None:
                require(first['cpu_end_ns'] <= second['cpu_start_ns'], 'Same-thread CPU counter regressed')
    computed, inclusive = {}, {}
    capability = header['cpu_capability']
    for index in sorted(intervals, reverse=True):
        row = intervals[index]
        known = (row['status'] == 'complete' and row['wall_end_ns'] is not None and
                 not row['virtual_thread'] and capability['current_thread_supported'] and capability['enabled'] and
                 row['cpu_start_ns'] is not None and row['cpu_end_ns'] is not None)
        inclusive[index] = row['cpu_end_ns'] - row['cpu_start_ns'] if known else None
        direct = children[index, row['thread_id']]
        known = known and all(computed[child['interval_id']] is not None for child in direct)
        value = inclusive[index] - sum(inclusive[child['interval_id']] for child in direct) if known else None
        require(value is None or value >= 0, 'Child CPU exceeds parent CPU')
        require(row['exclusive_cpu_ns'] == value, 'Derived exclusive CPU differs from raw interval hierarchy')
        computed[index] = value
    return computed


def read_trace(path):
    records, hashes, whole, total = [], hashlib.sha256(), hashlib.sha256(), 0
    before = path.stat()
    with path.open('rb') as stream:
        while raw := stream.readline(LIMITS['max_record_bytes'] + 1):
            total += len(raw)
            require(raw.endswith(b'\n') and len(raw) <= LIMITS['max_record_bytes'] and
                    total <= LIMITS['max_total_bytes'] and len(records) < 1000000, 'Trace byte/record bound exceeded')
            row = parse_json(raw.decode('utf-8'))
            require(isinstance(row, dict) and row.get('type') in KINDS and
                    integer(row.get('seq')) and row['seq'] == len(records), 'Unknown record/sequence regression')
            required = {'type', 'seq'} | set(CONTRACT['record_types'][row['type']])
            require(required <= set(row), 'Required trace fields missing: ' + row['type'])
            if row['type'] == 'footer':
                require(row['preceding_bytes_sha256'] == hashes.hexdigest(), 'Footer byte digest differs')
            else:
                hashes.update(raw)
            records.append(row)
            whole.update(raw)
    after = path.stat()
    require((before.st_size, before.st_mtime_ns, before.st_ino) ==
            (after.st_size, after.st_mtime_ns, after.st_ino), 'Trace changed during parse')
    require(len(records) >= 2 and records[0]['type'] == 'header' and records[-1]['type'] == 'footer' and
            all(row['type'] not in {'header', 'footer'} for row in records[1:-1]), 'Header/footer missing or duplicated')
    header, footer = records[0], records[-1]
    require(header.get('format') == 'm1-typed-trace-v1' and header['limits'] == LIMITS and
            header['hosting'] == 'private' and header['phase'] in {'restart', 'warm', 'fixture'}, 'Unsupported trace contract')
    require(hex_value(header['binary_sha256'], 64) and hex_value(header['snapshot_id'], 64) and
            hex_value(header['command_policy_sha256'], 64) and isinstance(header['engine_id'], str) and header['engine_id'],
            'Invalid header identity')
    require(all(isinstance(header[name], str) and header[name] for name in ('semantic_epoch', 'cohort_id')) and
            all(re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', header[name])
                for name in ('trace_id', 'command_id')) and
            set(header['source_identity']) == {'base_commit', 'patch_sha256', 'reconstructed_git_tree'} and
            hex_value(header['source_identity']['base_commit'], 40) and
            hex_value(header['source_identity']['patch_sha256'], 64) and
            hex_value(header['source_identity']['reconstructed_git_tree'], 40), 'Invalid source/context identity')
    bindings = header['repository_bindings']
    require(set(bindings) == {'main'} and bindings['main']['logical_repository_id'] and
            hex_value(bindings['main']['binding_provenance_sha256'], 64) and
            bindings['main']['filesystem_policy']['os'] == 'linux' and
            bindings['main']['filesystem_policy']['case_sensitive'] is True, 'Unsupported repository binding')
    require(header['actual_digest_algorithm'] == bindings['main']['filesystem_policy']['digest_algorithm'] and
            hex_value(header['context_sha256'], 64), 'Actual filesystem/context identity differs')
    require(set(header['cpu_capability']) == {'current_thread_supported', 'enabled', 'virtual_thread_supported'} and
            all(type(v) is bool for v in header['cpu_capability'].values()) and
            header['cpu_capability']['virtual_thread_supported'] is False, 'Unsupported CPU capability')
    nodes, observations, intervals, events = {}, {}, {}, []
    counts, observed, retained, edges = Counter(row['type'] for row in records[:-1]), set(), set(), 0
    for row in records[1:-1]:
        kind = row['type']
        target, field = ({'node': (nodes, 'node_id'), 'observation': (observations, 'observation_id'),
                          'interval': (intervals, 'interval_id')}.get(kind, (None, None)))
        if target is not None:
            require(integer(row[field], 1) and row[field] not in target, 'Duplicate/invalid ' + field)
            target[row[field]] = row
            bound = LIMITS['max_nodes'] if kind == 'node' else LIMITS['max_observations'] if kind == 'observation' else LIMITS['max_intervals']
            require(len(target) <= bound, kind + ' identity bound exceeded')
        if kind in {'event', 'interval'}:
            observed.add(row['node_id'])
        if kind == 'event':
            events.append(row)
        if kind == 'observation' and row['origin'] == 'retained_done':
            retained.add(row['node_id'])
    require(len(nodes) <= LIMITS['max_nodes'], 'Node bound exceeded')
    logical_keys = set()
    for node in nodes.values():
        require(node['key_status'] in {'supported', 'unsupported'}, 'Unknown key status')
        if node['key_status'] == 'supported':
            key = node['logical_key']
            require(isinstance(key, list) and len(key) == 2 and key[0] == 'main' and node['reason'] is None and
                    node['function'] in {'FILE_STATE', 'DIRECTORY_LISTING_STATE'}, 'Unsupported logical key')
            relative_path = units(key[1])
            require(not relative_path.startswith('/') and all(p not in {'.', '..'} for p in relative_path.split('/')), 'Nonrelative logical path')
            logical = (node['function'], tuple(key))
            require(logical not in logical_keys, 'Duplicate supported logical key')
            logical_keys.add(logical)
        else:
            require(node['logical_key'] is None and isinstance(node['reason'], str) and node['reason'], 'Unsupported key reason missing')
    projected_bytes = 0
    for row in records[1:-1]:
        if row['type'] != 'node':
            require(integer(row['node_id'], 1) and row['node_id'] in nodes, 'Dangling node reference')
        if row['type'] == 'observation':
            require(row['origin'] in {'evaluated_value', 'evaluated_current', 'before_child_drop', 'retained_done'} and
                    row['value_status'] in {'exact_projection', 'unsupported', 'unavailable', 'error'} and
                    row['deps_status'] in {'grouped', 'unavailable', 'edgeless', 'error'} and
                    row['deps_origin'] in {'evaluated_direct_deps', 'retained_done', 'unavailable'} and
                    row['dependency_read_contract'] == 'captured_state_only', 'Unsupported observation status')
            validate_projection(nodes[row['node_id']], row, header)
            projected_bytes += len(canonical(row['projection'])) if row['projection'] is not None else 0
            if row['deps_status'] == 'grouped':
                require(isinstance(row['groups'], list), 'Grouped dependencies require explicit groups')
                for group in row['groups']:
                    require(isinstance(group, list), 'Invalid dependency group')
                    for edge in group:
                        require(isinstance(edge, list) and len(edge) == 2 and integer(edge[0], 1) and edge[0] in nodes and
                                (edge[1] is None or integer(edge[1], 1) and edge[1] in observations and observations[edge[1]]['node_id'] == edge[0]),
                                'Dangling/mismatched captured dependency')
                        edges += 1
            else:
                require(row['groups'] is None, 'Unavailable dependencies cannot become empty groups')
        elif row['type'] in {'event', 'bzl_input'}:
            oid = row['observation_id']
            require(oid is None or oid in observations and observations[oid]['node_id'] == row['node_id'], 'Dangling observation event')
            if row['type'] == 'bzl_input':
                require(nodes[row['node_id']]['function'] == 'BZL_LOAD' and row['status'] == 'input_evidence_only', 'BZL evidence is input only')
            else:
                require(row['event'] in {'dirtied', 'deleted', 'enqueueing', 'change_pruned', 'evaluated'}, 'Unknown event')
                for name in ('new_value_supplied', 'new_error_supplied', 'direct_deps_supplied'):
                    require(type(row[name]) is bool if row['event'] == 'evaluated' else row[name] is None,
                            'Malformed evaluation availability flag')
                require(not row['new_error_supplied'] or oid is None or observations[oid]['value_status'] == 'error',
                        'Error callback cannot claim an exact value')
    require(projected_bytes <= LIMITS['max_projected_bytes'] and edges <= LIMITS['max_edges'] and
            footer['node_count'] == len(nodes) and footer['edge_count'] == edges,
            'Footer graph counts differ')
    require(footer['record_counts'] == {kind: counts[kind] for kind in KINDS}, 'Footer record counts differ')
    require(footer['observed_node_count'] == len(observed) and footer['retained_only_node_count'] == len(retained - observed) and
            footer['dependency_only_node_count'] == len(set(nodes) - observed - retained), 'Footer node coverage differs')
    require(footer['complete'] is True and footer['limits_exceeded'] == [] and footer['writer_error'] is None and
            footer['unbalanced_intervals'] == 0 and all(row['status'] != 'unbalanced' for row in intervals.values()),
            'Incomplete/overflowed/unbalanced capture')
    cpu = exclusive_intervals(intervals, header)
    completion_path = Path(str(path) + '.completion.json')
    require(completion_path.is_file() and completion_path.stat().st_size <= 4096, 'Completion receipt missing/oversized')
    completion_raw = completion_path.read_bytes()
    require(len(completion_raw) <= 4096, 'Completion receipt grew above bound')
    completion = parse_json(completion_raw.decode('utf-8'))
    require(set(completion) == {'schema_version', 'command_id', 'context_sha256', 'trace_path', 'completion'} and
            type(completion['schema_version']) is int and completion['schema_version'] == 1 and
            completion['completion'] is True and completion['command_id'] == header['command_id'] and
            completion['context_sha256'] == header['context_sha256'] and isinstance(completion['trace_path'], str) and
            (completion['trace_path'].startswith('/') or re.match(r'^[A-Za-z]:[/\\]', completion['trace_path'])),
            'Completion receipt identity mismatch')
    return {'path': str(path), 'sha256': whole.hexdigest(), 'header': header, 'footer': footer,
            'nodes': nodes, 'observations': observations, 'intervals': intervals, 'exclusive_cpu': cpu,
            'observed': observed, 'events': events, 'completion': completion,
            'completion_sha256': digest(completion_raw)}


def replay(traces, attestations):
    """Read-only captured variants, staged per trace; no within-trace self hits."""
    strict_table, projection_table, reports = defaultdict(dict), defaultdict(dict), []
    table_bytes, variants, associations = 0, 0, 0
    trace_ids, command_ids, trace_hashes = set(), set(), set()
    for trace in traces:
        h, observations = trace['header'], trace['observations']
        require(h['trace_id'] not in trace_ids and h['command_id'] not in command_ids and trace['sha256'] not in trace_hashes,
                'Duplicate captured trace/command')
        trace_ids.add(h['trace_id'])
        command_ids.add(h['command_id'])
        trace_hashes.add(trace['sha256'])
        expected = {key: h[key] for key in CONTRACT['context_file_fields']}
        attestation = attestations.get('traces', {}).get(trace['sha256'])
        attested = attestation is not None
        if attested:
            require(attestation == {'context': expected, 'context_sha256': h['context_sha256'],
                                    'completion_sha256': trace['completion_sha256'],
                                    'trace_path': trace['completion']['trace_path']}, 'Caller attestation differs from trace/receipt')
        main_binding = h['repository_bindings']['main']
        binding = {key: main_binding[key] for key in ('logical_repository_id', 'filesystem_policy')}
        # Root mapping proofs and command/output paths remain required provenance,
        # but do not describe FILE_STATE/listing value semantics.
        # Any relevant equality-policy change must instead change semantic_epoch.
        epoch = [h['semantic_epoch'], h['source_identity'], h['binary_sha256'], binding]
        epoch_id = digest(b'm1-equality-epoch\0' + canonical(epoch))
        model_bytes = 0

        def reserve_model(amount):
            nonlocal model_bytes
            require(model_bytes + amount <= MAX_MODEL_BYTES, 'Replay transient model payload bound exceeded')
            model_bytes += amount

        key_cache, projections, signatures, unknown = {}, {}, {}, Counter()
        for node_id, node in trace['nodes'].items():
            key = canonical([epoch_id, node['function'], node['logical_key']]) if (
                attested and node['key_status'] == 'supported') else None
            reserve_model(64 + (len(key) if key is not None else 0))
            key_cache[node_id] = key
        for oid, observation in observations.items():
            key = key_cache[observation['node_id']]
            projection = canonical(observation['projection']) if key is not None and observation['value_status'] == 'exact_projection' else None
            reserve_model(64 + (len(projection) if projection is not None else 0))
            projections[oid] = projection
        visiting = set()

        def signature(oid):
            if oid in signatures:
                return signatures[oid]
            if oid in visiting:
                return None, 'dependency_cycle'
            if len(visiting) >= 128:
                return None, 'dependency_depth_limit'
            visiting.add(oid)
            row, groups = observations[oid], []
            signature_budget = len(projections[oid] or b'') + 16
            value, reason = None, None
            if projections[oid] is None:
                reason = 'binding_unattested' if not attested else 'key_or_value_unsupported'
            elif row['deps_status'] != 'grouped':
                reason = 'dependencies_' + row['deps_status']
            else:
                for group in row['groups']:
                    reserve_model(64)
                    captured = []
                    for node, child in group:
                        if child is None:
                            reason = 'dependency_observation_unavailable'
                            break
                        child_value, child_reason = signature(child)
                        if child_value is None:
                            reason = 'dependency_unknown:' + child_reason
                            break
                        reserve_model(256)
                        signature_budget += 144
                        captured.append([digest(b'm1-logical-query\0' + key_cache[node]),
                                         digest(b'm1-captured-observation\0' + child_value)])
                    groups.append(captured)
                    signature_budget += 4
                    if reason:
                        break
                if reason is None:
                    reserve_model(signature_budget)
                    value = canonical([observations[oid]['projection'], groups])
            visiting.remove(oid)
            signatures[oid] = (value, reason)
            return value, reason

        outcomes, staged = {}, []
        for oid, row in observations.items():
            reserve_model(256)
            value, reason = signature(oid)
            key, projection = key_cache[row['node_id']], projections[oid]
            def match(table, candidate):
                owners = table.get(key, {}).get(candidate, set()) if candidate is not None else set()
                return bool(owners - {h['engine_id']}), h['engine_id'] in owners
            strict, same = match(strict_table, value)
            free, _ = match(projection_table, projection)
            outcomes[oid] = {'strict_cross_engine_match': strict, 'projection_cross_engine_match': free,
                             'same_engine_variant_match': same, 'unknown_reason': reason,
                             'strict_known': value is not None, 'projection_known': projection is not None}
            if reason:
                unknown[reason] += 1
            staged.append((key, value, projection))
        node_obs = defaultdict(list)
        for oid, row in observations.items():
            node_obs[row['node_id']].append(outcomes[oid])
        compute, state_cpu = defaultdict(list), defaultdict(lambda: {'known_exclusive_ns': 0, 'unknown_intervals': 0})
        for iid, interval in trace['intervals'].items():
            ns = trace['exclusive_cpu'][iid]
            state_cpu[interval['state']]['known_exclusive_ns'] += ns or 0
            state_cpu[interval['state']]['unknown_intervals'] += int(ns is None)
            if interval['state'] == 'COMPUTE':
                compute[interval['node_id']].append(ns)
        ceilings = {kind: {'known_exclusive_compute_ns': 0, 'unknown_cpu_nodes': 0,
                           'unknown_match_nodes': 0, 'matched_observed_nodes': 0}
                    for kind in ('strict_captured_table', 'projection_free_oracle')}
        unknown_compute = 0
        for node in trace['observed']:
            costs = compute.get(node, [])
            known = bool(costs) and all(ns is not None for ns in costs)
            unknown_compute += int(not known)
            for kind, field, known_field in (('strict_captured_table', 'strict_cross_engine_match', 'strict_known'),
                                ('projection_free_oracle', 'projection_cross_engine_match', 'projection_known')):
                if not node_obs[node] or any(not row[known_field] for row in node_obs[node]):
                    ceilings[kind]['unknown_match_nodes'] += 1
                if node_obs[node] and all(row[field] for row in node_obs[node]):
                    ceilings[kind]['matched_observed_nodes'] += 1
                    if known:
                        ceilings[kind]['known_exclusive_compute_ns'] += sum(costs)
                    else:
                        ceilings[kind]['unknown_cpu_nodes'] += 1
        for value in ceilings.values():
            value['complete_cpu_ceiling_ns'] = value['known_exclusive_compute_ns'] if (
                value['unknown_cpu_nodes'] == value['unknown_match_nodes'] == 0) else None
        for key, value, projection in staged:
            for table, candidate in ((strict_table, value), (projection_table, projection)):
                if candidate is None:
                    continue
                if key not in table:
                    table_bytes += len(key)
                if candidate not in table.get(key, {}):
                    variants += 1
                    table_bytes += len(candidate)
                if h['engine_id'] not in table.get(key, {}).get(candidate, set()):
                    associations += 1
                    table_bytes += len(h['engine_id'].encode())
                require(table_bytes <= MAX_TABLE_BYTES and variants <= MAX_VARIANTS and associations <= 1000000,
                        'Replay table byte/count bound exceeded')
                table[key].setdefault(candidate, set()).add(h['engine_id'])
        reports.append({'trace_sha256': trace['sha256'], 'trace_id': h['trace_id'], 'engine_id': h['engine_id'],
            'snapshot_id': h['snapshot_id'], 'context_attested': attested, 'node_count': len(trace['nodes']),
            'completion_sha256': trace['completion_sha256'],
            'observation_count': len(observations), 'observed_node_count': len(trace['observed']),
            'retained_only_node_count': trace['footer']['retained_only_node_count'],
            'dependency_only_node_count': trace['footer']['dependency_only_node_count'],
            'model_payload_bytes_charged': model_bytes,
            'strict_cross_engine_observation_matches': sum(v['strict_cross_engine_match'] for v in outcomes.values()),
            'projection_cross_engine_observation_matches': sum(v['projection_cross_engine_match'] for v in outcomes.values()),
            'same_engine_variant_matches_no_credit': sum(v['same_engine_variant_match'] for v in outcomes.values()),
            'unknown_observations_by_reason': dict(unknown), 'observed_nodes_missing_or_unknown_compute_cpu': unknown_compute,
            'exclusive_cpu_by_state': dict(state_cpu), 'captured_node_cpu_ceilings': ceilings,
            'bzl_input_value_hits': None, 'unobserved_target_demand_closure': None})
    return {'traces': reports, 'table': {'logical_keys': len(strict_table),
            'retained_complete_variants': sum(map(len, strict_table.values())),
            'projection_keys': len(projection_table), 'projection_variants': sum(map(len, projection_table.values())),
            'canonical_payload_bytes': table_bytes, 'owner_associations': associations,
            'memory_note': 'Payload count is not Python allocation, JVM heap or PSS; tracemalloc peak measured separately'},
            'interpretation': ['Read-only captured-table model, not a live correctness/admission protocol.',
                'Caller attestations bind exact trace bytes and declared contexts; runtime provenance is not reverified.',
                'CPU credit is a captured-node ceiling, not avoided CPU; attempts are not paired with observations.',
                'Only known exclusive COMPUTE CPU is credited; absent/unsupported CPU remains explicitly unknown.',
                'Retained-only nodes and same-engine matches earn no command CPU credit.',
                'Grouped dependency evidence is captured state, never an atomic historical filesystem snapshot.',
                'Dependency/epoch IDs use domain-separated SHA256 over full captured representations; collision resistance is a model assumption.',
                'BZL input equality does not establish module-value equality; direct SkyCache validation is unobserved.',
                'Trace closure and table bytes cannot be converted to retained JVM heap, PSS savings, or a gate verdict.'],
            'milestone_verdict': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('traces', nargs='+', type=Path)
    parser.add_argument('--attestations', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists() and len(args.traces) <= 32, 'New output and at most32 traces required')
    require(sum(path.stat().st_size for path in args.traces) <= LIMITS['max_total_bytes'], 'Aggregate input byte bound exceeded')
    require(args.attestations.stat().st_size <= 4 * 1024**2, 'Attestation exceeds4MiB')
    raw = args.attestations.read_bytes()
    attestations = parse_json(raw.decode('utf-8'))
    tracemalloc.start()
    start, wall = time.thread_time_ns(), time.monotonic_ns()
    traces = [read_trace(path) for path in args.traces]
    parse_cpu, parse_wall = time.thread_time_ns() - start, time.monotonic_ns() - wall
    parse_current, parse_peak = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    start, wall = time.thread_time_ns(), time.monotonic_ns()
    result = replay(traces, attestations)
    replay_cpu, replay_wall = time.thread_time_ns() - start, time.monotonic_ns() - wall
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result.update(format='m1-offline-typed-replay-v1', attestation_sha256=digest(raw),
                  replay_source_sha256=digest(Path(__file__).read_bytes()),
                  contract_sha256=digest(Path(__file__).with_name('trace-v1.contract.json').read_bytes()),
                  replay_limits={'max_traces': 32, 'max_aggregate_trace_bytes': LIMITS['max_total_bytes'],
                    'max_records_per_trace': 1000000, 'max_table_payload_bytes': MAX_TABLE_BYTES,
                    'max_total_variants': MAX_VARIANTS, 'max_owner_associations': 1000000,
                    'max_dependency_depth': 128, 'max_transient_model_payload_bytes_per_trace': MAX_MODEL_BYTES},
                  python_observer={'parse_validation_thread_cpu_ns': parse_cpu, 'parse_validation_wall_ns': parse_wall,
                    'lookup_validation_thread_cpu_ns': replay_cpu, 'lookup_validation_wall_ns': replay_wall,
                    'parse_traced_current_bytes': parse_current, 'parse_traced_peak_bytes': parse_peak,
                    'lookup_traced_peak_increment_bytes': max(0, peak - parse_current),
                    'post_replay_traced_current_bytes': current,
                    'tracemalloc_enabled_during_measurement': True,
                    'thread_cpu_clock_resolution_seconds': time.get_clock_info('thread_time').resolution,
                    'wall_clock_resolution_seconds': time.get_clock_info('monotonic').resolution,
                    'clock_note': 'Observed deltas may quantize to zero for small fixtures; this does not establish zero cost.',
                    'process_rss_bytes': None, 'memory_kind': 'Python tracemalloc allocations; excludes native buffers/RSS/JVM/PSS'})
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    main()
