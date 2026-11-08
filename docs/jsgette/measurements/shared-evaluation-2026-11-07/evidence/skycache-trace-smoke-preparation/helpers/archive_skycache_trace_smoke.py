#!/usr/bin/env python3
"""Capture ended real trace interoperability evidence; never launches a build."""
import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tarfile

import qualify_skycache_trace as smoke
from archive_skycache_trace_build import bounded, json_bytes, write_new

WORK, Q = Path(__file__).resolve().parent, smoke.ROOT
RUN_ID = 'm1-trace-acceptance-01'
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary' / RUN_ID
SUCCESS = 'real_trace_interop_passed_no_gate_verdict'
require, sha, digest = smoke.require, smoke.sha, smoke.digest


def validate_guard(guard, identity):
    final = guard['final_scope_cleanup']
    require(guard['status'] in ('completed', 'command_failed') and final['verified'] is True
            and final['present'] is False and final['signalled'] is False, 'Scope absence must be verified and unsignalled')
    require(re.fullmatch('shared-eval-guard-' + RUN_ID + r'-[0-9a-f]{32}\.scope', identity['Id'])
            and identity['Id'] == guard['scope']['unit'] and identity['InvocationID'] == guard['scope']['invocation_id'],
            'Guard run identity differs')
    group = identity['ControlGroup']
    require(group.startswith('/') and '..' not in Path(group).parts and group.endswith('/' + identity['Id']), 'Invalid scope path')
    return Path('/sys/fs/cgroup') / group.lstrip('/')


def expected_fixture(variant):
    """Reconstruct fixed source and output bytes independently of recorded claims."""
    require(variant in ('A', 'B'), 'Unexpected fixture variant')
    content = {name: value.encode() for name, value in smoke.FIXED.items()}
    content['inputs/a.txt'] = b'alpha\n' if variant == 'A' else b'bravo\n'
    target = 'inputs/a.txt' if variant == 'A' else 'inputs/z.txt'
    inputs = ['inputs/a.txt', 'inputs/z.txt']
    if variant == 'B':
        content['optional/added.txt'] = b'extra\n'
        inputs.append('optional/added.txt')
    members = [{'path': name, 'type': 'file', 'sha256': digest(raw)} for name, raw in content.items()]
    members.extend({'path': name, 'type': 'directory'} for name in ('inputs', 'optional'))
    members.append({'path': 'link.txt', 'type': 'symlink', 'target': target})
    members.sort(key=lambda row: row['path'])
    output = b''.join(content[name] for name in inputs) + content[target]
    return {'variant': variant, 'members': members, 'snapshot_id': digest(smoke.canonical(members)),
            'expected_output_hex': output.hex()}, content


def validate_daemons(report, *, passed, probe=smoke.identity):
    identities = report.get('daemon_identities', {})
    require(set(identities).issubset({'e0', 'e1'}) and (not passed or set(identities) == {'e0', 'e1'}),
            'Private engine identity set differs')
    pids = set()
    for engine, pair in identities.items():
        require(isinstance(pair, list) and len(pair) == 2 and all(type(v) is int and v > 0 for v in pair)
                and pair[0] > 1 and pair[0] not in pids, 'Invalid/duplicate private daemon identity')
        pids.add(pair[0])
        require(probe(pair[0]) != pair, 'Recorded daemon remains alive')
    cleanups = report.get('cleanup', [])
    require(len({row['engine_id'] for row in cleanups}) == len(cleanups), 'Duplicate engine cleanup')
    if passed:
        require({row['engine_id'] for row in cleanups} == {'e0', 'e1'} and all(
            row.get('recorded_identity_absent') is True and 'error' not in row
            and row.get('identity', identities[row['engine_id']]) == identities[row['engine_id']]
            for row in cleanups), 'Daemon cleanup proof incomplete')
    return identities


def validate_cases(report, candidate, *, passed):
    cases = report.get('cases', [])
    require(len(cases) <= len(smoke.CASES) and (not passed or len(cases) == len(smoke.CASES)), 'Case count differs')
    commands = {}
    allowed = {'git-init', 'git-add', 'git-commit', 'git-worktree-e0', 'git-worktree-e1',
               'replay', 'shutdown-e0', 'shutdown-e1'} | {row[0] for row in smoke.CASES}
    for row in report['commands']:
        require(row['name'] in allowed and row['name'] not in commands, 'Unexpected/duplicate smoke command')
        commands[row['name']] = row
    if passed:
        require(allowed - {'shutdown-e0', 'shutdown-e1'} <= set(commands)
                and all(row.get('returncode') == 0 for row in commands.values()),
                'Successful smoke lacks successful declared commands')
        for cleanup in report['cleanup']:
            if 'identity' in cleanup:
                require('shutdown-' + cleanup['engine_id'] in commands, 'Observed live daemon lacks shutdown command')
    uuids = set()
    for row, (name, engine, variant, enabled) in zip(cases, smoke.CASES):
        require((row['name'], row['engine_id'], row['variant'], row['trace_enabled']) ==
                (name, engine, variant, enabled) and row['oracle_passed'] is True,
                'Case order/engine/variant/oracle differs')
        require(re.fullmatch('[0-9a-f-]{36}', row['command_id']) and row['command_id'] not in uuids,
                'Duplicate/invalid build command UUID')
        uuids.add(row['command_id'])
        require(row['daemon_identity'] == report['daemon_identities'][engine], 'Case daemon changed')
        command = commands[name]
        require(command.get('returncode') == 0 and command['cwd'] == str(Q / engine)
                and command['argv'][0] == candidate['destination']
                and command['argv'].count('--output_base=' + str(Q / 'outputs' / engine)) == 1,
                'Case command ownership differs')
        for option, value in (('experimental_m1_trace', str(Q / 'traces' / (name + '.jsonl')) if enabled else ''),
                              ('experimental_m1_trace_context', str(Q / 'contexts' / (name + '.json')) if enabled else '')):
            require([arg for arg in command['argv'] if arg.startswith('--' + option + '=')] ==
                    ['--' + option + '=' + value], 'Case trace/context argument differs')
    return commands


def expected_context(name, engine, state, manifest, candidate, command, binding):
    logical = {'logical_repository_id': 'm1-trace-smoke-fixture-v1', 'physical_root': str(Q / engine),
               'filesystem_policy': {'os': 'linux', 'case_sensitive': True, 'digest_algorithm': 'SHA-256'}}
    require(binding == logical, 'Logical/physical repository binding differs')
    logical['binding_provenance_sha256'] = sha(Q / 'bindings' / (engine + '.json'))
    return {'engine_id': engine, 'cohort_id': 'm1-trace-acceptance-v1', 'phase': 'fixture', 'hosting': 'private',
            'binary_sha256': candidate['sha256'], 'source_identity': manifest['source_identity'],
            'semantic_epoch': 'm1-trace-main-facts-v1', 'snapshot_id': state['snapshot_id'],
            'command_policy_sha256': digest(smoke.canonical(command['argv'])),
            'repository_bindings': {'main': logical}, 'bzl_inlining': 'unknown'}


def validate_trace(parsed, context, row, trace, state, context_sha):
    header = parsed['header']
    require(all(header[key] == value for key, value in context.items())
            and header['command_id'] == row['command_id'] and header['trace_id'] == row['trace_id']
            and header['context_sha256'] == context_sha == row['context_sha256']
            and parsed['sha256'] == row['trace_sha256']
            and parsed['completion_sha256'] == row['completion_sha256']
            and parsed['completion']['trace_path'] == str(trace)
            and len(parsed['nodes']) == row['node_count'] and parsed['footer']['unknown_counts'] == row['unknown_counts'],
            'Java trace/context/receipt/case binding differs')
    require(smoke.check_listing_projections(parsed, state) == row['listing_truthfulness'], 'Listing truthfulness proof differs')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--launcher-sha256', required=True)
    args = parser.parse_args()
    for name in ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json', 'interop-verification.json'):
        require(not (E / name).exists() and not (E / name).is_symlink(), 'Archive output exists')
    guard_path, identity_path = E / 'guard/result.json', E / 'guard/scope-identity.json'
    guard, identity = json.loads(bounded(guard_path)), json.loads(bounded(identity_path))
    cgroup = validate_guard(guard, identity)
    require(not cgroup.exists(), 'Guard scope remains active')
    require(args.manifest.resolve() == args.manifest and args.manifest.is_relative_to(WORK), 'Manifest outside task work')
    manifest, manifest_sha = smoke.document(args.manifest, args.manifest_sha256)
    launcher = Path(smoke.__file__).resolve()
    require(sha(launcher) == manifest['harness_sha256'] == args.launcher_sha256
            and sha(Path(smoke.build.__file__)) == smoke.GUARD_HELPER, 'Frozen smoke helper differs')
    command = ['python3', '-B', str(launcher), '--manifest', str(args.manifest), '--manifest-sha256', manifest_sha]
    require(json.loads(bounded(E / 'command.json')) == command and
            json.loads(bounded(E / 'guard/policy.json'))['command_sha256'] == sha(E / 'command.json'), 'Guard command binding differs')
    prior, prior_sha = smoke.document(**manifest['build_report'])
    prior_proof, _ = smoke.document(**manifest['candidate_verification'])
    candidate = smoke.validate_provenance(manifest, prior, prior_proof, prior_sha)
    binary = Path(candidate['destination'])
    require(binary.resolve() == binary and binary.is_file() and not binary.is_symlink()
            and binary.stat().st_size == candidate['bytes'] and sha(binary) == candidate['sha256'], 'Qualified binary changed')
    raw = bounded(Q / 'report.json')
    report = json.loads(raw)
    require(report['root'] == str(Q) and report['status'] in ('failed', SUCCESS)
            and report['manifest_sha256'] == manifest_sha and report['candidate'] == candidate
            and report['source_identity'] == manifest['source_identity'] and report['diagnostic_only'] is True
            and report['milestone_verdict'] is None and json.loads(bounded(Q / 'manifest.json')) == manifest,
            'Smoke report/input identity differs')
    passed = report['status'] == SUCCESS
    require((guard['status'] == 'completed' and guard['exit_code'] == 0) if passed else
            (guard['status'] == 'command_failed' and guard['exit_code'] != 0), 'Guard/report outcome differs')
    require(report['scope_before']['name'] == identity['ControlGroup'] and
            (report.get('scope_after', {}).get('name') == identity['ControlGroup'] or not passed
             and 'scope_after' not in report), 'Smoke report scope differs')
    daemon_ids = validate_daemons(report, passed=passed)
    commands = validate_cases(report, candidate, passed=passed)
    replay_path = WORK / 'm1-trace-design/replay.py'
    contract_path = replay_path.with_name('trace-v1.contract.json')
    require(sha(replay_path) == manifest['replay_sha256'] and sha(contract_path) == manifest['contract_sha256'],
            'Frozen parser/contract differs')
    spec = importlib.util.spec_from_file_location('archived_trace_parser', replay_path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    files = {'report.json': Q / 'report.json', 'manifest.json': Q / 'manifest.json',
        'provenance/command.json': E / 'command.json', 'provenance/guard-result.json': guard_path,
        'provenance/scope-identity.json': identity_path, 'provenance/guard-policy.json': E / 'guard/policy.json',
        'provenance/input-manifest.json': args.manifest, 'provenance/build-report.json': Path(manifest['build_report']['path']),
        'provenance/build-candidate-verification.json': Path(manifest['candidate_verification']['path']),
        'provenance/qualify_skycache_trace.py': launcher, 'provenance/replay.py': replay_path,
        'provenance/trace-v1.contract.json': contract_path}
    missing, errors, verified_cases, expected_attestations, inventory = [], [], [], {}, Counter()

    def capture(name, required=passed):
        path = Q / name
        if path.is_file():
            files[name] = path
            return True
        missing.append(name)
        require(not required, 'Successful smoke evidence missing: ' + name)
        return False

    for name in commands:
        capture(name + '.log')
    for engine in ('e0', 'e1'):
        capture('bindings/' + engine + '.json')
    enabled = {row[0] for row in smoke.CASES if row[3]}
    for directory, allowed in (('contexts', {name + '.json' for name in enabled}),
                               ('bindings', {'e0.json', 'e1.json'})):
        actual = set()
        if (Q / directory).is_dir():
            for count, path in enumerate((Q / directory).iterdir()):
                require(count < len(allowed) and path.name in allowed, 'Unexpected context/binding artifact')
                actual.add(path.name)
        require(not passed or actual == allowed, 'Successful smoke context/binding membership differs')
    trace_total = sum((Q / 'traces' / (name + '.jsonl')).stat().st_size for name in enabled
                      if (Q / 'traces' / (name + '.jsonl')).is_file())
    require(trace_total <= 256 * 1024**2, 'Aggregate trace bytes exceed frozen replay bound')
    declared_names = {name + suffix for name in enabled for suffix in ('.jsonl', '.jsonl.completion.json')}
    trace_names = set()
    if (Q / 'traces').is_dir():
        for count, path in enumerate((Q / 'traces').iterdir()):
            require(count < 20, 'Unexpected trace artifact count')
            trace_names.add(path.name)
            if path.name not in declared_names:
                require(not passed and any(re.fullmatch(re.escape(name) + r'\.jsonl\.completion\.pending-[0-9a-f-]{36}', path.name)
                                          for name in enabled), 'Undeclared trace artifact')
                require(path.stat().st_size <= 4096, 'Completion pending file exceeds bound')
                capture('traces/' + path.name, required=False)
    require(not passed or trace_names == declared_names, 'Successful smoke lacks exactly five trace/receipt pairs')
    case_rows = {row['name']: row for row in report.get('cases', [])}
    for name, engine, variant, traced in smoke.CASES:
        for suffix in ('fixture.json', 'bep.jsonl', 'result.txt'):
            capture('cases/' + name + '/' + suffix, required=passed)
        if traced:
            for path in ('traces/' + name + '.jsonl', 'traces/' + name + '.jsonl.completion.json', 'contexts/' + name + '.json'):
                capture(path, required=passed)
        if name not in case_rows:
            continue
        row = case_rows[name]
        try:
            state, _ = expected_fixture(variant)
            require(json.loads(bounded(Q / 'cases' / name / 'fixture.json')) == state
                    and row['snapshot_id'] == state['snapshot_id'], 'Recorded fixture/snapshot differs')
            output = bounded(Q / 'cases' / name / 'result.txt', 4096)
            require(output == bytes.fromhex(state['expected_output_hex']) and digest(output) == row['output_sha256'],
                    'Independent case output bytes differ')
            require(smoke.bep_identity(Q / 'cases' / name / 'bep.jsonl') == row['command_id'], 'Case BEP command differs')
            if traced:
                context_path, trace = Q / 'contexts' / (name + '.json'), Q / 'traces' / (name + '.jsonl')
                context = json.loads(bounded(context_path, 64 * 1024))
                binding = json.loads(bounded(Q / 'bindings' / (engine + '.json')))
                require(context == expected_context(name, engine, state, manifest, candidate, commands[name], binding),
                        'Actual context differs from the pinned command/source/fixture')
                parsed = replay.read_trace(trace)
                validate_trace(parsed, context, row, trace, state, sha(context_path))
                expected_attestations[parsed['sha256']] = {'context': context, 'context_sha256': sha(context_path),
                    'completion_sha256': parsed['completion_sha256'], 'trace_path': str(trace)}
                for observation in parsed['observations'].values():
                    if observation['value_status'] == 'exact_projection':
                        kind = observation['projection']['type']
                        inventory[kind] += 1
                        if observation['node_id'] in parsed['observed'] and kind in ('DIRECTORY', 'DIRECTORY_LISTING_STATE'):
                            inventory['observed_exact_directory_or_listing'] += 1
                del parsed
            verified_cases.append(name)
        except Exception as error:
            errors.append({'case': name, 'error': str(error)})
            require(not passed, 'Successful smoke case cannot be independently verified: ' + str(error))
    if capture('attestations.json', required=passed) and capture('replay-result.json', required=passed):
        try:
            attestations = json.loads(bounded(Q / 'attestations.json'))
            replay_result = json.loads(bounded(Q / 'replay-result.json', 16 * 1024**2))
            require(attestations == {'traces': expected_attestations} and len(expected_attestations) == 5,
                    'Actual trace attestation set differs')
            require(sha(Q / 'replay-result.json') == report['replay_result_sha256']
                    and replay_result['format'] == 'm1-offline-typed-replay-v1'
                    and replay_result['attestation_sha256'] == sha(Q / 'attestations.json')
                    and replay_result['replay_source_sha256'] == manifest['replay_sha256']
                    and replay_result['contract_sha256'] == manifest['contract_sha256']
                    and replay_result['milestone_verdict'] is None, 'Replay provenance differs')
            expected_rows = [row for row in report['cases'] if row['trace_enabled']]
            require(len(replay_result['traces']) == len(expected_rows) == 5
                and len({row['trace_id'] for row in expected_rows}) == 5 and all(
                value['context_attested'] is True and all(value[field] == case[field] for field in
                    ('trace_sha256', 'trace_id', 'engine_id', 'snapshot_id', 'completion_sha256', 'node_count'))
                for value, case in zip(replay_result['traces'], expected_rows)), 'Replay input identities/order differ')
            require(dict(inventory) == report['typed_projection_inventory']
                    and inventory['observed_exact_directory_or_listing'] > 0, 'Actual observed projection inventory differs')
        except Exception as error:
            errors.append({'replay': str(error)})
            require(not passed, 'Successful smoke replay proof differs: ' + str(error))
    final_fixtures = {}
    for engine in ('e0', 'e1', 'fixture-seed'):
        for name in (*smoke.FIXED, 'inputs/a.txt', 'optional/added.txt'):
            if (Q / engine / name).is_file():
                capture(engine + '/' + name, required=False)
        try:
            state, content = expected_fixture('A')
            smoke.verify_fixture(Q / engine, state)
            final_fixtures[engine] = state
            for name in content:
                capture(engine + '/' + name)
        except Exception as error:
            errors.append({'final_fixture': engine, 'error': str(error)})
            require(not passed, 'Successful smoke final fixture differs: ' + str(error))
    records = []
    for name, path in sorted(files.items()):
        limit = 256 * 1024**2 if name.startswith('traces/') and name.endswith('.jsonl') else 64 * 1024**2
        require(path.resolve() == path and path.is_file() and not path.is_symlink()
                and (path.is_relative_to(Q) or path.is_relative_to(WORK)) and path.stat().st_size <= limit,
                'Invalid/oversized archive member')
        records.append({'path': name, 'source': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(len(records) <= 128 and sum(row['bytes'] for row in records) <= 384 * 1024**2, 'Archive exceeds total bound')
    archive_path = E / 'runtime.tar.gz'
    with archive_path.open('xb') as out, gzip.GzipFile(fileobj=out, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|') as archive:
            for row in records:
                archive.add(files[row['path']], arcname=row['path'], recursive=False)
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(archive.getnames() == [row['path'] for row in records], 'Archive membership differs')
        for row in records:
            with archive.extractfile(row['path']) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256']
                        and archive.getmember(row['path']).size == row['bytes'], 'Archive content differs')
    require(sha(binary) == candidate['sha256'] and sha(Q / 'report.json') == digest(raw)
            and sha(args.manifest) == manifest_sha and not cgroup.exists(), 'Final artifact/scope changed')
    validate_daemons(report, passed=passed)
    proof = {'schema_version': 1, 'verified': passed and not errors, 'interop_passed': passed,
        'report_sha256': digest(raw), 'candidate_sha256': candidate['sha256'],
        'manifest_sha256': manifest_sha, 'verified_cases': verified_cases,
        'trace_sha256': list(expected_attestations), 'final_fixtures': final_fixtures,
        'recorded_daemon_identities': daemon_ids, 'recorded_daemons_absent': True,
        'final_scope_absent': True, 'final_scope_unsignalled': True,
        'guard_result_sha256': sha(guard_path), 'scope_identity_sha256': sha(identity_path),
        'errors': errors, 'diagnostic_only': True, 'milestone_verdict': None}
    write_new(E / 'report.json', raw)
    write_new(E / 'interop-verification.json', json_bytes(proof))
    metadata = {'schema_version': 1, 'passed': passed, 'archive_sha256': sha(archive_path),
        'archive_bytes': archive_path.stat().st_size, 'members': records, 'missing_files': missing,
        'interop_verification_sha256': sha(E / 'interop-verification.json'), 'launcher_sha256': args.launcher_sha256,
        'helper_sha256': sha(Path(__file__)), 'source_identity': manifest['source_identity'],
        'candidate_binary_excluded': candidate, 'runtime_preserved': True, 'milestone_verdict': None,
        'interpretation': 'Actual Java trace/replay and fixture isolation qualification only; no performance or M1 gate verdict.'}
    write_new(E / 'runtime.tar.gz.json', json_bytes(metadata))
    print(json.dumps({'passed': passed, 'archive_sha256': metadata['archive_sha256'], 'members': len(records)}))


if __name__ == '__main__':
    main()
