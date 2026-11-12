#!/usr/bin/env python3
"""C-only analysis of the complete fixed N1 trace matrix; no jobs or gate verdict."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import statistics
from urllib.parse import unquote, urlparse

import archive_real_trace_n1 as archive

runner, accounting = archive.runner, archive.accounting
WORK, E = runner.WORK, runner.E
require = runner.require
MIB = 1024**2
WINDOWS = ('build', 'prepared_request', 'tail', 'lifecycle')


def c_path(value):
    spelling = str(value)
    if spelling.startswith('/mnt/c/') and Path('C:/').drive:
        value = 'C:/' + spelling[len('/mnt/c/'):]
    elif re.match(r'^[cC]:[/\\]', spelling) and not Path('C:/').drive:
        value = '/mnt/c/' + spelling[3:].replace('\\', '/')
    path = Path(value).absolute()
    require(path.resolve() == path and not path.is_symlink(), 'C input/output path is redirected')
    require(path.drive.lower() == 'c:' or path.as_posix().startswith('/mnt/c/'), 'Only C-backed paths are allowed')
    return path


def linux_path(path):
    path = c_path(path)
    return str(PurePosixPath('/mnt/c', *path.parts[1:])) if path.drive else path.as_posix()


def read_json(path, provenance, limit=16 * MIB):
    path = c_path(path)
    raw = archive.read(path, limit)
    provenance[str(path)] = hashlib.sha256(raw).hexdigest()
    return json.loads(raw), raw


def check_helper_closure(pins, provenance):
    expected = {Path(module.__file__).resolve() for module in
                (runner, runner.smoke, runner.baseline, runner.fixture, runner.resources, runner.nc)}
    expected.update(E / 'harness' / name for name in ('output_bases.py', 'native_source_build.py'))
    expected.update(WORK / 'm1-trace-design' / name for name in ('replay.py', 'trace-v1.contract.json'))
    require(len(pins) == len(expected) and {c_path(name) for name in pins} == expected,
            'Complete imported helper closure required')
    for name, pinned_sha in pins.items():
        path = c_path(name)
        require(archive.sha(path) == pinned_sha, 'Imported helper changed: ' + str(path))
        provenance[str(path)] = pinned_sha


def bind_output(events, output, config, oracle):
    path = PurePosixPath(output['path'])
    require(path.is_absolute() and '..' not in path.parts and path.as_posix() == output['path']
            and path.is_relative_to(PurePosixPath(config['run_root']) / 'state/o00')
            and path.name == 'normalize_cmd' and type(output['bytes']) is int and output['bytes'] > 0
            and re.fullmatch(r'[0-9a-f]{64}', output['sha256']), 'Reported output identity is invalid')
    files = [entry for event in events for entry in event.get('namedSetOfFiles', {}).get('files', [])
             if PurePosixPath(entry.get('name', '')).name == 'normalize_cmd'
             or PurePosixPath(unquote(urlparse(entry.get('uri', '')).path)).name == 'normalize_cmd']
    require(len(files) == 1, 'Exactly one raw BEP normalizer output required')
    entry = files[0]
    uri = urlparse(entry.get('uri', ''))
    length = entry.get('length')
    require(uri.scheme == 'file' and not uri.netloc and not uri.query and not uri.fragment
            and unquote(uri.path) == output['path'] and entry.get('digest') == output['sha256']
            and (type(length) is int or isinstance(length, str) and re.fullmatch(r'[0-9]+', length))
            and int(length) == output['bytes'], 'Raw BEP URI/digest/length differs from reported output')
    require(config['reference_only'] or oracle is not None
            and all(output[key] == oracle[key] for key in ('sha256', 'bytes')),
            'Output differs from independently verified reference')


def reference_output(config, provenance):
    record = config['oracle_report']
    directory = c_path(E / 'evidence/auxiliary' / (runner.SERIES + '-reference-01'))
    def document(item, relative):
        require(c_path(item['path']) == directory / relative, 'Unexpected independent reference path')
        value, raw = read_json(item['path'], provenance)
        require(hashlib.sha256(raw).hexdigest() == item['sha256'], 'Pinned reference document changed')
        return value, raw
    reference, raw_report = document(record, 'report.json')
    guard, _ = document(record['guard_result'], 'guard/result.json')
    metadata, _ = document(record['archive_metadata'], 'runtime.tar.gz.json')
    verification, _ = document(record['verification'], 'verification/result.json')
    archive_path = c_path(record['archive']['path'])
    require(archive_path == directory / 'runtime.tar.gz' and archive_path.stat().st_size <= 512 * MIB,
            'Unexpected independent reference archive')
    archive_sha = archive.sha(archive_path)
    require(archive_sha == record['archive']['sha256'], 'Pinned reference archive changed')
    provenance[str(archive_path)] = archive_sha
    runner.validate_reference_verification(reference, record['sha256'], metadata, verification,
                                          archive_sha, config['postprocess_sha256'])
    require(reference['passed'] is True and reference['reference_only'] is True
            and reference['owned_daemons_absent'] is True and reference['source_and_binary_unchanged'] is True
            and all(reference['config'][key] == config[key] for key in
                    ('binary', 'source_identity', 'repository_seed', 'fixture_expected', 'helper_sha256'))
            and guard['status'] == 'completed' and guard['exit_code'] == 0
            and guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True}
            and guard['scope']['unit'] == reference['scope']['identity']['Id']
            and guard['scope']['invocation_id'] == reference['scope']['identity']['InvocationID']
            and len(reference['phases']) == 1 and reference['phases'][0]['passed'] is True,
            'Independent reference source/correctness/guard proof differs')
    result = reference['phases'][0]['result']
    require(result['behavior']['passed'] is True and result['policy_verified'] is True, 'Reference output proof missing')
    reader = archive.Archive(directory)
    try:
        require(reader.raw('report.json') == raw_report, 'Archived independent reference report differs')
        events = [json.loads(line) for line in reader.raw('reference/bep.jsonl').splitlines() if line.strip()]
        bind_output(events, result['output'], reference['config'], None)
    finally:
        reader.tar.close()
    return result['output']


def ratio(on, off):
    a, b = accounting.number(on), accounting.number(off)
    value = a / b if a is not None and b is not None and b > 0 else None
    return {'status': 'known', 'on': a, 'off': b, 'ratio': value} if accounting.number(value) is not None else {
            'status': 'unknown', 'on': a, 'off': b, 'ratio': None,
            'reason': 'Requires nonnegative finite costs and a positive off denominator'}


def cpu_ratio(on, off):
    value = accounting.ratio_bounds(on, off)
    if value['status'] == 'known' and accounting.interval(value['bounds']) is None:
        value = {'status': 'unknown', 'bounds': None, 'reason': 'Nonfinite ratio'}
    value['interpretation'] = 'Trace-on/trace-off diagnostic ratio; upper endpoint is conservative; no gate verdict'
    value['conservative_ratio'] = value['bounds'][1] if value['status'] == 'known' else None
    value.update(on_seconds_bounds=on, off_seconds_bounds=off)
    return value


def five_stats(values):
    require(len(values) == 5, 'Exactly five fixed pairs required')
    known = [value for value in values if accounting.number(value) is not None]
    return {'values': values, 'known_pairs': len(known), 'expected_pairs': 5,
            'status': 'complete' if len(known) == 5 else 'unknown',
            'median': statistics.median(known) if len(known) == 5 else None,
            'range': [min(known), max(known)] if len(known) == 5 else None}


def checked_window(start, end, reported):
    require(type(start['monotonic_ns']) is int and type(end['monotonic_ns']) is int
            and end['monotonic_ns'] >= start['monotonic_ns'], 'Invalid scope wall-clock interval')
    computed = runner.window(start, end)
    require(computed == reported, 'Reported window differs from raw scope/process CPU endpoints')
    bounds = computed['harness_cpu_bounds']
    non_harness = accounting.interval(bounds['non_harness_scope_cpu_ns_bounds']) if (
        bounds['non_harness_status'] == 'known') else None
    return {**computed, 'non_harness_seconds_bounds': [v / 1e9 for v in non_harness] if non_harness else None,
            'pressure': accounting.pressure_summary(start['snapshot'], end['snapshot'])}


def linked_verification(report, report_sha, metadata, verification, verification_sha, progress, manifest_sha):
    require(metadata['passed'] is True and metadata['kind'] == 'n1-trace-diagnostic'
            and metadata['run_id'] == report['config']['run_id']
            and metadata['report_sha256'] == report_sha and metadata['manifest_sha256'] == manifest_sha,
            'Archive report/manifest proof differs')
    require(verification.get('verified') is True and verification.get('status') == 'verified_diagnostic_no_gate_verdict'
            and verification.get('performance_eligible') is False and verification.get('milestone_verdict') is None
            and verification['run_id'] == report['config']['run_id']
            and verification['archive_sha256'] == metadata['archive_sha256']
            and verification['report_sha256'] == report_sha and verification['manifest_sha256'] == manifest_sha
            and verification['postprocess_sha256'] == report['config']['postprocess_sha256'],
            'Completed linked archive verification is required')
    require(progress['status'] == 'captured_and_verified_no_gate_verdict'
            and progress['verification_sha256'] == verification_sha
            and progress['launch_exit_code'] == progress['capture_exit_code'] == 0,
            'Coordinator did not record this successful verification')


def closed_wire(directory, progress, provenance):
    wire = directory.with_name(directory.name + '-wire')
    guard, guard_raw = read_json(wire / 'guard/result.json', provenance)
    identity, _ = read_json(wire / 'guard/scope-identity.json', provenance)
    policy, _ = read_json(wire / 'guard/policy.json', provenance)
    command, command_raw = read_json(wire / 'command.json', provenance)
    expected = ['python3', '-B', linux_path(WORK / 'archive_real_trace_n1.py'), 'verify', '--evidence', linux_path(directory)]
    require(command == expected and hashlib.sha256(command_raw).hexdigest() == progress['wire_command_sha256']
            == policy['command_sha256'] and hashlib.sha256(guard_raw).hexdigest() == progress['wire_guard_sha256'],
            'Wire guard command/result digest differs')
    require(guard['status'] == 'completed' and guard['exit_code'] == 0
            and guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True}
            and re.fullmatch('shared-eval-guard-' + re.escape(wire.name) + r'-[0-9a-f]{32}\.scope', identity['Id'])
            and guard['scope']['unit'] == policy['scope']['unit'] == identity['Id']
            and guard['scope']['invocation_id'] == identity['InvocationID']
            and policy['memory_max_mib'] == 10240 and policy['cpu_quota_percent'] == 600,
            'Actual trace replay guard did not complete cleanly')


def phase_summary(reader, phase, verified, config, fixture_row, oracle=None):
    name = phase['phase']
    require(reader.json(name + '/result.json') == phase, 'Archived phase report differs')
    samples = [json.loads(line) for line in reader.raw(name + '/samples.jsonl').splitlines() if line.strip()]
    coverage = accounting.sample_coverage(samples, 1, 'private')
    require(coverage == verified['coverage'] and not coverage['invalid_sample_indices']
            and coverage['complete_unique_process_samples'] == phase['complete_samples'] > 0
            and len(samples) == phase['samples'] and coverage['verified_peak_pss_bytes'] == phase['sampled_peak_daemon_pss_bytes']
            and (phase['lifecycle'] == 'restart' or coverage['pending_startup_samples'] == 0), 'PSS coverage differs')
    for label, key in (('initial', 'initial'), ('after-response', 'final'), ('drain-final', 'drain_final')):
        require([s for s in samples if s['label'] == label] == [phase[key]], 'Endpoint sample differs: ' + label)
    reads = {row['label']: row for row in phase['scope_reads']}
    require(len(reads) == len(phase['scope_reads']) and set(reads) == {
        'prepared-request-start', 'build-start', 'build-end', 'prepared-request-drained'}, 'Scope boundary set differs')
    times = [reads[label]['monotonic_ns'] for label in
             ('prepared-request-start', 'build-start', 'build-end', 'prepared-request-drained')]
    require(all(type(value) is int for value in times) and times == sorted(times), 'Scope boundaries are out of order')
    windows = {key: checked_window(reads[a], reads[b], phase[key]) for key, a, b in (
        ('build', 'build-start', 'build-end'), ('prepared_request', 'prepared-request-start', 'prepared-request-drained'),
        ('tail', 'build-end', 'prepared-request-drained'))}
    require(all(verified[key] == phase[key] for key in ('build', 'prepared_request', 'tail', 'daemon_cpu_seconds')),
            'Verified accounting summary differs')
    require(verified['pressure'] == windows['prepared_request']['pressure'], 'Verified request pressure differs')
    cpu = runner.nc.cpu_delta(phase['initial'], phase['final'], phase['lifecycle'])
    require(cpu == phase['daemon_cpu_seconds'], 'Daemon CPU endpoints differ')
    result = phase['result']
    oracle_flags = {'required': False, 'matches': None} if config['reference_only'] else {'required': True, 'matches': True}
    require(result['exit_code'] == 0 and result['timed_out'] is False and result['behavior']['passed'] is True
            and result['policy_verified'] is True and result['digest_oracle'] == oracle_flags,
            'Phase correctness/oracle proof differs')
    events = [json.loads(line) for line in reader.raw(name + '/bep.jsonl').splitlines() if line.strip()]
    bind_output(events, result['output'], config, oracle)
    mapping = runner.resources.EngineMapping(config['run_root'] + '/state/o00', Path('/unused'),
        runner.resources.ProcessIdentity(phase['final']['engines']['0']['pid'], phase['final']['engines']['0']['start_ticks']))
    require(runner.nc.bep_identity(events, fixture_row, mapping) == result['bep_identity'], 'BEP engine/UUID binding differs')
    metrics = [event['buildMetrics'] for event in events if 'buildMetrics' in event]
    require(len(metrics) == 1 and metrics[0] == result['bep']['build_metrics'], 'Actual BEP metrics differ')
    root = PurePosixPath(config['run_root'])
    paths = {'bases': {'0': root / 'state/o00'}, 'disk': root / 'state/action-cache', 'repository': root / 'state/repository-cache',
             'tmp': root / 'temp/tmp', 'skycache': root / 'state/skycache'}
    trace = root / name / 'trace.jsonl' if phase['trace_enabled'] else None
    context = root / name / 'context.json' if phase['trace_enabled'] else None
    require(runner.nc.audit_policy(events, runner.policy(paths, fixture_row, trace, context)) == result['trace_policy'],
            'Actual trace policy differs')
    wall = (result['exited_ns'] - result['started_ns']) / 1e9
    require(wall == phase['client_wall_seconds'] and wall >= 0, 'Client wall interval differs')
    normalized = {key: [value.replace(config['run_root'], '<run_root>') for value in values]
                  for key, values in result['trace_policy'].items() if not key.startswith('experimental_m1_trace')}
    return {'windows': windows, 'client_wall_seconds': wall, 'daemon_cpu_seconds': cpu,
            'daemon_cpu_note': phase['daemon_cpu_note'], 'sampled_peak_daemon_pss_bytes': coverage['verified_peak_pss_bytes'],
            'pss_coverage': coverage, 'uncovered_memory_roles': phase['uncovered_memory_roles'],
            'build_uuid': result['bep_identity']['build_uuid'], 'output': result['output'],
            'action_evidence': metrics[0].get('actionSummary'),
            'other_build_metrics': {key: value for key, value in metrics[0].items() if key != 'actionSummary'},
            'normalized_non_trace_policy': normalized, 'drain': phase['drain']}


def summarize_row(spec, progress, oracle, helper_pins):
    provenance = {}
    directory = c_path(E / 'evidence/auxiliary' / spec['run_id'])
    report, report_raw = read_json(directory / 'report.json', provenance)
    metadata, metadata_raw = read_json(directory / 'runtime.tar.gz.json', provenance)
    verification, verification_raw = read_json(directory / 'verification/result.json', provenance)
    require(hashlib.sha256(metadata_raw).hexdigest() == progress['archive_metadata_sha256'], 'Archive metadata changed')
    require(metadata['manifest_sha256'] == spec['manifest_sha256']
            and progress['manifest_sha256'] == spec['manifest_sha256'] and progress['command_sha256'] == spec['command_sha256'],
            'Order/coordinator input binding differs')
    linked_verification(report, hashlib.sha256(report_raw).hexdigest(), metadata, verification,
                        hashlib.sha256(verification_raw).hexdigest(), progress, spec['manifest_sha256'])
    require(sum(row['bytes'] for row in metadata['members']) <= 512 * MIB
            and (directory / 'runtime.tar.gz').stat().st_size <= 512 * MIB, 'Archive size budget exceeded')
    reader = archive.Archive(directory)
    try:
        provenance[str(directory / 'runtime.tar.gz')] = metadata['archive_sha256']
        require(reader.raw('report.json') == report_raw, 'Exact archived report changed')
        manifest_raw = reader.raw('manifest.json')
        _, original_manifest = read_json(spec['manifest'], provenance)
        _, original_command = read_json(spec['command'], provenance)
        require(manifest_raw == reader.raw('provenance/input-manifest.json')
                and manifest_raw == original_manifest
                and hashlib.sha256(manifest_raw).hexdigest() == spec['manifest_sha256'], 'Archived manifest differs')
        config = json.loads(manifest_raw)
        require(config['helper_sha256'] == helper_pins, 'Row imported helper closure differs')
        require(all(config[key] == spec[key] for key in ('run_id', 'pair', 'state', 'trace_enabled'))
                and config['reference_only'] is False and config['history'] == runner.HISTORY, 'Declared matrix row differs')
        archive.check_postprocess_helpers(config)
        require(verification['verifier_sha256'] == metadata['archiver_sha256']
                == config['postprocess_sha256']['archive_real_trace_n1.py'], 'Archive/verifier implementation differs')
        guard, guard_raw = read_json(directory / 'guard/result.json', provenance)
        policy, policy_raw = read_json(directory / 'guard/policy.json', provenance)
        identity, identity_raw = read_json(directory / 'guard/scope-identity.json', provenance)
        command, command_raw = read_json(directory / 'command.json', provenance)
        require(metadata['guard_result_sha256'] == hashlib.sha256(guard_raw).hexdigest()
                and metadata['scope_identity_sha256'] == hashlib.sha256(identity_raw).hexdigest(),
                'Archive guard/identity proof differs')
        for key, raw in (('guard', guard_raw), ('policy', policy_raw), ('identity', identity_raw), ('command', command_raw)):
            require(reader.raw('provenance/' + key + '.json') == raw, 'Copied control differs: ' + key)
        require(command_raw == original_command and hashlib.sha256(command_raw).hexdigest() == spec['command_sha256'],
                'Row command differs')
        phases = archive.check_report(report, config, guard, policy, identity, command_raw, spec['manifest_sha256'])
        require([p['phase'] for p in verification['phases']] == ['preparation', 'measured'], 'Verified phases differ')
        summaries = [phase_summary(reader, phase, proof, config, report['fixture']['worktrees'][0], oracle)
                     for phase, proof in zip(phases, verification['phases'])]
        require(summaries[0]['normalized_non_trace_policy'] == summaries[1]['normalized_non_trace_policy'],
                'Preparation/measured non-trace policy differs')
        lifecycle = checked_window(report['lifecycle_scope_start'], report['lifecycle_scope_end'], report['lifecycle_window'])
        require(verification['lifecycle_window'] == report['lifecycle_window'], 'Verified lifecycle window differs')
        require(report['lifecycle_scope_start']['monotonic_ns'] <= phases[0]['scope_reads'][0]['monotonic_ns']
                <= phases[0]['scope_reads'][-1]['monotonic_ns'] <= phases[1]['scope_reads'][0]['monotonic_ns']
                <= phases[1]['scope_reads'][-1]['monotonic_ns'] <= report['lifecycle_scope_end']['monotonic_ns'],
                'Lifecycle does not enclose sequential preparation/measurement')
        summaries[1]['windows']['lifecycle'] = lifecycle
        trace = None
        if spec['trace_enabled']:
            closed_wire(directory, progress, provenance)
            replay, replay_raw = read_json(directory / 'verification/replay-result.json', provenance)
            trace = verification['trace']
            declared = phases[1]['result']['trace']
            for member, field in (('trace.jsonl', 'sha256'), ('trace.jsonl.completion.json', 'completion_sha256'),
                                  ('context.json', 'context_sha256')):
                require(reader.rows['measured/' + member]['sha256'] == declared[field],
                        'Trace artifact inventory differs: ' + member)
            require(reader.rows['measured/trace.jsonl']['bytes'] == declared['bytes'], 'Trace size differs')
            attestations, attestations_raw = read_json(directory / 'verification/attestations.json', provenance)
            require(attestations == {'traces': {declared['sha256']: {'context': declared['context'],
                    'context_sha256': declared['context_sha256'], 'completion_sha256': declared['completion_sha256'],
                    'trace_path': declared['path']}}}, 'Replay attestation differs')
            require(hashlib.sha256(replay_raw).hexdigest() == trace['replay_result_sha256']
                    and trace['sha256'] == declared['sha256'] and trace['bytes'] == declared['bytes']
                    and replay['traces'] == [trace['replay_summary']] and replay['python_observer'] == trace['python_observer']
                    and trace['replay_summary']['trace_sha256'] == declared['sha256']
                    and trace['replay_summary']['completion_sha256'] == declared['completion_sha256']
                    and trace['replay_summary']['context_attested'] is True
                    and trace['replay_summary']['strict_cross_engine_observation_matches'] == 0
                    and trace['replay_summary']['projection_cross_engine_observation_matches'] == 0
                    and trace['replay_summary']['engine_id'] == config['run_id']
                    and trace['replay_summary']['snapshot_id'] == report['snapshot_id']
                    and replay['attestation_sha256'] == hashlib.sha256(attestations_raw).hexdigest()
                    and replay['replay_source_sha256'] == config['helper_sha256'][linux_path(WORK / 'm1-trace-design/replay.py')]
                    and replay['contract_sha256'] == config['helper_sha256'][linux_path(WORK / 'm1-trace-design/trace-v1.contract.json')]
                    and replay['milestone_verdict'] is None, 'Single-trace replay proof differs')
            trace = {**trace, **{key: replay[key] for key in ('table', 'interpretation', 'replay_limits',
                'attestation_sha256', 'replay_source_sha256', 'contract_sha256')}}
        else:
            require('trace' not in verification and not any(name.endswith(('/trace.jsonl', '/trace.jsonl.completion.json'))
                    for name in reader.rows), 'Trace-off row contains tracing evidence')
        contract_keys = ('binary', 'source_identity', 'history', 'hosting', 'clients', 'scenario', 'source_repository',
            'sample_interval_seconds', 'timeout_seconds', 'drain_deadline_seconds', 'fixture_expected', 'fixture_snapshot',
            'repository_seed', 'qualification', 'real_smoke_proof', 'helper_sha256', 'postprocess_sha256', 'oracle_report',
            'binding_provenance_sha256')
        contract = {key: config[key] for key in contract_keys}
        contract.update(scope_limits=report['scope']['limits'], snapshot_id=report['snapshot_id'],
                        non_trace_policy=summaries[1]['normalized_non_trace_policy'])
        require(runner.nc.digest(report['fixture_snapshot']) == report['snapshot_id'], 'Snapshot differs')
        return {**{key: spec[key] for key in ('run_id', 'pair', 'state', 'trace_enabled')},
                'verified': True, 'input_sha256': provenance, 'preparation': summaries[0], 'measured': summaries[1],
                'trace': trace, 'matching_contract': contract}
    finally:
        reader.tar.close()


def paired(rows):
    require([{key: row[key] for key in ('run_id', 'pair', 'state', 'trace_enabled')} for row in rows] == runner.schedule(),
            'Exactly the frozen twenty rows in order are required')
    require(all(row['matching_contract'] == rows[0]['matching_contract'] for row in rows), 'Matched source/policy/history differs')
    uuids = [row[phase]['build_uuid'] for row in rows for phase in ('preparation', 'measured')]
    require(len(set(uuids)) == 40, 'Repeated build UUID across sessions')
    result = {}
    for state in ('restart', 'warm'):
        pairs = []
        for number in range(1, 6):
            match = [row for row in rows if row['state'] == state and row['pair'] == number]
            off, on = (next(row for row in match if row['trace_enabled'] is enabled) for enabled in (False, True))
            a, b = on['measured'], off['measured']
            windows = {name: {'non_harness_cpu': cpu_ratio(a['windows'][name]['non_harness_seconds_bounds'],
                                                        b['windows'][name]['non_harness_seconds_bounds']),
                'raw_scope_cpu': ratio(a['windows'][name]['raw_scope_cpu_seconds'], b['windows'][name]['raw_scope_cpu_seconds']),
                'boundary_wall': ratio(a['windows'][name]['boundary_wall_seconds'], b['windows'][name]['boundary_wall_seconds'])}
                for name in WINDOWS}
            pairs.append({'pair': number, 'observed_order': [row['run_id'] for row in match], 'windows': windows,
                'client_wall': ratio(a['client_wall_seconds'], b['client_wall_seconds']),
                'daemon_cpu': ratio(a['daemon_cpu_seconds'], b['daemon_cpu_seconds']),
                'partial_daemon_pss': ratio(a['sampled_peak_daemon_pss_bytes'], b['sampled_peak_daemon_pss_bytes'])})
        statistics_by_window = {name: {metric: five_stats([p['windows'][name][metric][field] for p in pairs])
            for metric, field in (('non_harness_cpu', 'conservative_ratio'), ('raw_scope_cpu', 'ratio'), ('boundary_wall', 'ratio'))}
            for name in WINDOWS}
        result[state] = {'pairs': pairs, 'five_pair_statistics': {'windows': statistics_by_window,
            **{metric: five_stats([p[metric]['ratio'] for p in pairs]) for metric in ('client_wall', 'daemon_cpu', 'partial_daemon_pss')}}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--order', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    output = c_path(args.output)
    require(not output.exists(), 'Output must be new')
    provenance = {}
    order, order_raw = read_json(args.order, provenance)
    progress, _ = read_json(c_path(args.order).parent / 'execution-progress.json', provenance)
    require(order['schema_version'] == 1 and order['role'] == 'matrix' and order['diagnostic_only'] is True
            and order['milestone_verdict'] is None and order['runner_timeout_seconds'] == 2400
            and [{key: row[key] for key in ('run_id', 'pair', 'state', 'trace_enabled')} for row in order['rows']] == runner.schedule(),
            'Complete frozen matrix order required')
    require(progress['status'] == 'completed_diagnostic_no_gate_verdict' and progress['role'] == 'matrix'
            and progress['order_sha256'] == hashlib.sha256(order_raw).hexdigest()
            and [row['run_id'] for row in progress['rows']] == [row['run_id'] for row in order['rows']],
            'Matrix is incomplete; no active session evidence is read')
    expected_tools = {WORK / 'archive_real_trace_n1.py', WORK / 'run_real_trace_n1_matrix.ps1',
                      E / 'harness/run_auxiliary.ps1', E / 'harness/analyze_native_n2.py'}
    require(len(order['control_tools']) == 4 and {c_path(tool['path']) for tool in order['control_tools']} == expected_tools,
            'Complete frozen control helper set required')
    for tool in order['control_tools']:
        require(archive.sha(c_path(tool['path'])) == tool['sha256'], 'Frozen control helper changed')
        provenance[tool['path']] = tool['sha256']
    require(progress['driver_sha256'] == archive.sha(WORK / 'run_real_trace_n1_matrix.ps1'), 'Coordinator digest differs')
    config, config_raw = read_json(order['rows'][0]['manifest'], provenance)
    require(hashlib.sha256(config_raw).hexdigest() == order['rows'][0]['manifest_sha256'], 'First matrix manifest differs')
    check_helper_closure(config['helper_sha256'], provenance)
    oracle = reference_output(config, provenance)
    rows = [summarize_row(row, receipt, oracle, config['helper_sha256'])
            for row, receipt in zip(order['rows'], progress['rows'])]
    result = {'schema_version': 1, 'kind': 'n1-trace-on-off-diagnostic', 'verified_rows': 20,
        'diagnostic_only': True, 'performance_eligible': False, 'milestone_verdict': None,
        'input_sha256': provenance, 'analyzer_sha256': archive.sha(Path(__file__)), 'rows': rows, 'comparisons': paired(rows),
        'limitations': ['Same instrumented SkyCache binary; trace-on/off is observer coverage/cost, not architectural savings.',
            'No trace overhead subtraction. Whole-Python CPU bounds preserve raw scope totals; non-harness includes compiler descendants.',
            'Build, prepared request, tail and lifecycle are separate windows; lifecycle includes preparation and verification.',
            'Daemon CPU endpoints extend beyond build endpoints. Daemon PSS is partial sampled memory; other process roles are uncovered.',
            'Cgroup peaks include prior preparation and cannot be subtracted into a per-request peak.',
            'Single-trace replay supplies coverage and implemented parser/table costs, not cross-engine sharing credit.',
            'All five ratios are published; an unknown pair makes its full-five median/range unknown. No observer or M1/M4 gate verdict.']}
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    print(json.dumps({'output': str(output), 'verified_rows': 20, 'milestone_verdict': None}))


if __name__ == '__main__':
    main()
