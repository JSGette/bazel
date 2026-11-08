#!/usr/bin/env python3
"""Capture one ended N1 session, then independently verify its bounded archive."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tarfile
import tempfile

import run_real_trace_n1 as runner
import analyze_native_n2 as accounting

WORK, E = runner.WORK, runner.E
require = runner.require
MIB = 1024**2


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path, limit=16 * MIB):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path
            and path.stat().st_size <= limit, 'Invalid bounded evidence: ' + str(path))
    return path.read_bytes()


def save(path, value):
    with path.open('xb') as stream:
        stream.write((json.dumps(value, indent=2, sort_keys=True) + '\n').encode())


def row_for(run_id):
    rows = runner.schedule() + [{'run_id': runner.SERIES + '-reference-01', 'pair': 0,
                                'state': 'restart', 'trace_enabled': False}]
    value = next((row for row in rows if row['run_id'] == run_id), None)
    require(value is not None, 'Not a declared N1 session')
    return value


def check_postprocess_helpers(config):
    actual = {'archive_real_trace_n1.py': sha(Path(__file__)),
              'analyze_native_n2.py': sha(Path(accounting.__file__))}
    require(config.get('postprocess_sha256') == actual, 'Pinned archive/accounting validator changed')
    return actual


def validate_control(config, manifest_sha, command_raw, guard, policy, identity):
    row_for(config['run_id'])
    argv, command_sha = runner.launch_binding(config['launch_argv_prefix'], manifest_sha)
    require(json.loads(command_raw) == argv and hashlib.sha256(command_raw).hexdigest() == command_sha
            and policy['command_sha256'] == command_sha, 'Literal guard command differs')
    final = guard['final_scope_cleanup']
    require(final.get('verified') is True and final.get('present') is False, 'Guard scope absence not verified')
    require(re.fullmatch('shared-eval-guard-' + re.escape(config['run_id']) + r'-[0-9a-f]{32}\.scope', identity['Id'])
            and identity['Id'] == guard['scope']['unit'] == policy['scope']['unit']
            and identity['InvocationID'] == guard['scope']['invocation_id']
            and identity['ControlGroup'].endswith('/' + identity['Id']), 'Scope identity differs')
    require(identity['ControlGroup'].startswith('/') and '..' not in PurePosixPath(identity['ControlGroup']).parts,
            'Invalid owned scope path')
    require(policy['memory_max_mib'] == 10240 and policy['cpu_quota_percent'] == 600
            and policy['minimum_free_physical_bytes'] >= 3072 * MIB
            and policy['minimum_free_commit_bytes'] >= 4096 * MIB, 'Fixed guard limits differ')
    return guard['status'] == 'completed' and guard.get('exit_code') == 0 and final.get('signalled') is False


def allowed_runtime_names():
    names = {'manifest.json', 'report.json', 'fixture/manifest.json'}
    suffixes = ('command.json', 'bep.jsonl', 'stdout.log', 'stderr.log', 'process-result.json',
                'result.json', 'verified-result.json', 'samples.jsonl', 'partial-accounting.json',
                'local-skycache.json', 'local-skycache-ownership.json', 'previous-local-skycache.json',
                'previous-local-skycache-ownership.json', 'context.json', 'trace.jsonl',
                'trace.jsonl.completion.json', 'behavior/switch.yaml', 'behavior/out/switch.json')
    for phase in ('reference', 'preparation', 'measured'):
        names.update(phase + '/' + suffix for suffix in suffixes)
        for key in ('server_pid', 'output_base'):
            names.update(phase + '-identity/' + key + '/' + suffix for suffix in
                         ('command.json', 'stdout.log', 'stderr.log', 'process-result.json'))
    for phase in ('preparation-shutdown', 'final-shutdown'):
        names.update(phase + '/' + suffix for suffix in ('command.json', 'stdout.log', 'stderr.log', 'process-result.json'))
    return sorted(names)


def member_limit(name):
    return 256 * MIB if name.endswith('/trace.jsonl') else 32 * MIB if name.endswith(('/bep.jsonl', '/samples.jsonl')) else 16 * MIB


def capture(args):
    """Linux only; exact R reads occur only after the external scope is gone."""
    require(sys.platform == 'linux', 'Capture requires Linux paths; verification can be C-only')
    raw_manifest = read(args.manifest)
    require(hashlib.sha256(raw_manifest).hexdigest() == args.manifest_sha256, 'Manifest changed')
    config = json.loads(raw_manifest)
    row_for(config['run_id'])
    root = runner.R / config['run_id']
    destination = E / 'evidence/auxiliary' / config['run_id']
    require(config['run_root'] == str(root) and destination.resolve() == destination,
            'Fixed runtime/evidence path differs')
    for name in ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json', 'abort-record.json'):
        require(not (destination / name).exists(), 'Evidence destination already populated')
    controls = {name: read(destination / path) for name, path in {
        'command': 'command.json', 'guard': 'guard/result.json', 'policy': 'guard/policy.json',
        'identity': 'guard/scope-identity.json', 'preflight': 'host-budget-preflight.json'}.items()}
    guard, policy, identity = (json.loads(controls[key]) for key in ('guard', 'policy', 'identity'))
    guard_ok = validate_control(config, args.manifest_sha256, controls['command'], guard, policy, identity)
    group = Path('/sys/fs/cgroup') / identity['ControlGroup'].lstrip('/')
    require(not group.exists(), 'Owned scope is still present')
    files = {}
    for name in allowed_runtime_names():
        path = root / name
        if path.exists() or path.is_symlink():
            require(path.is_file() and not path.is_symlink() and path.resolve() == path
                    and path.stat().st_size <= member_limit(name), 'Invalid bounded runtime member: ' + name)
            files[name] = path
    report = json.loads(read(root / 'report.json')) if 'report.json' in files else None
    if report is not None:
        require(report['manifest_sha256'] == args.manifest_sha256
                and report['config']['run_id'] == config['run_id'], 'Runtime report binding differs')
        for pair in report.get('daemon_identities', []):
            require(len(pair) == 2 and all(type(value) is int and value > 0 for value in pair)
                    and pair[0] > 1 and runner.smoke.identity(pair[0]) != pair, 'Recorded daemon remains alive or invalid')
    for key, path in {'command': 'command.json', 'guard': 'guard/result.json', 'policy': 'guard/policy.json',
                      'identity': 'guard/scope-identity.json', 'preflight': 'host-budget-preflight.json'}.items():
        files['provenance/' + key + '.json'] = destination / path
    files['provenance/input-manifest.json'] = args.manifest
    records = [{'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path)} for name, path in sorted(files.items())]
    require(len(records) <= 128 and sum(row['bytes'] for row in records) <= 512 * MIB, 'Archive budget exceeded')
    archive_path = destination / 'runtime.tar.gz'
    with archive_path.open('xb') as output, gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|') as archive:
            for record in records:
                archive.add(files[record['path']], arcname=record['path'], recursive=False)
    # Verify every captured byte before issuing the archive receipt.
    with tarfile.open(archive_path) as archive:
        require(archive.getnames() == [row['path'] for row in records], 'Archive membership changed')
        for record in records:
            stream = archive.extractfile(record['path'])
            require(hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256']
                    and archive.getmember(record['path']).size == record['bytes'], 'Archived bytes differ')
    require(not group.exists() and sha(args.manifest) == args.manifest_sha256, 'Scope/input changed during capture')
    passed = bool(guard_ok and report and report.get('passed'))
    if report is not None:
        with (destination / 'report.json').open('xb') as output:
            output.write(read(root / 'report.json'))
    else:
        save(destination / 'abort-record.json', {'schema_version': 1, 'kind': 'no_runtime_report', 'passed': False,
             'run_id': config['run_id'], 'guard_status': guard['status'], 'guard_result_sha256': sha(destination / 'guard/result.json'),
             'no_measured_success_claim': True, 'runtime_preserved': True})
    save(destination / 'runtime.tar.gz.json', {'schema_version': 1, 'kind': 'n1-trace-diagnostic', 'passed': passed,
         'run_id': config['run_id'], 'archive_sha256': sha(archive_path), 'members': records,
         'manifest_sha256': args.manifest_sha256, 'report_sha256': sha(root / 'report.json') if report else None,
         'guard_result_sha256': sha(destination / 'guard/result.json'), 'scope_identity_sha256': sha(destination / 'guard/scope-identity.json'),
         'archiver_sha256': sha(Path(__file__)), 'runtime_preserved': True, 'performance_eligible': False, 'milestone_verdict': None})
    print(json.dumps({'captured': True, 'passed': passed, 'members': len(records), 'run_id': config['run_id']}))
    return 0  # Capturing a failure is successful preservation, never a successful session.


class Archive:
    def __init__(self, directory):
        self.directory = directory
        self.metadata = json.loads(read(directory / 'runtime.tar.gz.json'))
        require(sha(directory / 'runtime.tar.gz') == self.metadata['archive_sha256'], 'Archive digest differs')
        self.tar = tarfile.open(directory / 'runtime.tar.gz')
        self.rows = {row['path']: row for row in self.metadata['members']}
        require(len(self.rows) == len(self.metadata['members']) <= 128
                and self.tar.getnames() == sorted(self.rows), 'Duplicate/unexpected archive members')

    def raw(self, name, limit=None):
        row, item = self.rows[name], self.tar.getmember(name)
        require(item.isfile() and item.size == row['bytes'] <= (limit or member_limit(name)), 'Invalid member size/type')
        raw = self.tar.extractfile(item).read()
        require(hashlib.sha256(raw).hexdigest() == row['sha256'], 'Member digest differs: ' + name)
        return raw

    def json(self, name):
        return json.loads(self.raw(name))


def check_report(report, config, guard, policy, identity, command_raw, manifest_sha):
    require(validate_control(config, manifest_sha, command_raw, guard, policy, identity), 'Successful unsignalled guard required')
    expected = {**config, 'launch_argv': runner.launch_binding(config['launch_argv_prefix'], manifest_sha)[0],
                'guard_command_sha256': runner.launch_binding(config['launch_argv_prefix'], manifest_sha)[1]}
    require(report['config'] == expected and report['manifest_sha256'] == manifest_sha
            and report['passed'] is True and report['performance_eligible'] is False and report['milestone_verdict'] is None
            and report.get('owned_daemons_absent') is True and report.get('source_and_binary_unchanged') is True,
            'Successful exact report/input/cleanup proof missing')
    scope = report['scope']
    require(scope['membership'] == identity['ControlGroup'] and all(scope['identity'].get(key) == identity.get(key)
            for key in ('Id', 'Description', 'InvocationID', 'ControlGroup', 'ActiveState'))
            and scope['limits']['memory.max'] == str(10240 * MIB) and scope['limits']['memory.swap.max'] == '0',
            'Report scope or memory policy differs')
    quota, period = map(int, scope['limits']['cpu.max'].split())
    require(period > 0 and quota == 6 * period, 'Report CPU policy differs')
    phases = report['phases']
    require([row['phase'] for row in phases] == (['reference'] if config['reference_only'] else ['preparation', 'measured']),
            'Exactly one off preparation plus one measurement required')
    require(not phases[0]['trace_enabled'] and all(row['passed'] for row in phases), 'Preparation/reference must succeed with tracing off')
    if not config['reference_only']:
        require(phases[1]['trace_enabled'] == config['trace_enabled'] and phases[1]['lifecycle'] == config['state'], 'Measured lifecycle differs')
        before, after = (row['final']['engines']['0'] for row in phases)
        same = (before['pid'], before['start_ticks']) == (after['pid'], after['start_ticks'])
        require(same == (config['state'] == 'warm'), 'Restart/warm daemon identity history differs')
        if not same:
            require(report['preparation_shutdown']['recorded_identity_absent'] is True, 'Prepared daemon not shut down')
    return phases


def verify(args):
    """C-backed inputs only. Trace-on invocation belongs in its own auxiliary guard."""
    directory = args.evidence.resolve()
    require(directory.parent == (E / 'evidence/auxiliary').resolve(), 'Evidence must be a named auxiliary directory')
    row = row_for(directory.name)
    destination = directory / 'verification'
    require(not destination.exists(), 'Verification destination exists')
    destination.mkdir()
    archive = Archive(directory)
    result = {'schema_version': 1, 'run_id': directory.name, 'verified': False, 'diagnostic_only': True,
              'performance_eligible': False, 'milestone_verdict': None, 'archive_sha256': archive.metadata['archive_sha256'],
              'verifier_sha256': sha(Path(__file__)), 'phases': []}
    try:
        config = archive.json('provenance/input-manifest.json')
        result['postprocess_sha256'] = check_postprocess_helpers(config)
        require(config['run_id'] == directory.name and archive.metadata['passed'] is True, 'Archived session did not succeed')
        report = archive.json('report.json')
        require(archive.raw('report.json') == read(directory / 'report.json'), 'Copied report differs')
        manifest_sha = hashlib.sha256(archive.raw('manifest.json')).hexdigest()
        require(archive.raw('manifest.json') == archive.raw('provenance/input-manifest.json'), 'Runtime manifest bytes differ')
        result.update(report_sha256=hashlib.sha256(archive.raw('report.json')).hexdigest(), manifest_sha256=manifest_sha)
        phases = check_report(report, config, archive.json('provenance/guard.json'), archive.json('provenance/policy.json'),
                              archive.json('provenance/identity.json'), archive.raw('provenance/command.json'), manifest_sha)
        for key, name in (('guard_policy_sha256', 'policy'), ('scope_identity_sha256', 'identity'),
                          ('host_preflight_sha256', 'preflight')):
            require(report['scope'][key] == hashlib.sha256(archive.raw('provenance/' + name + '.json')).hexdigest()
                    == report['final_scope_attestation'][key], 'Scope provenance digest changed')
        preflight = archive.json('provenance/preflight.json')
        require(preflight['status'] == 'ready' and preflight['consecutive_ready_samples'] >= 2
                and preflight['policy']['minimum_physical_bytes'] >= 13824 * MIB
                and preflight['policy']['minimum_commit_bytes'] >= 14848 * MIB
                and len(preflight['samples']) >= 2 and all(row['available_physical_bytes'] >= 13824 * MIB
                and row['available_commit_bytes'] >= 14848 * MIB for row in preflight['samples'][-2:]),
                'Required budget preflight missing')
        require(runner.nc.digest(report['fixture_snapshot']) == report['snapshot_id'], 'Snapshot digest differs')
        uuids = set()
        for phase in phases:
            name = phase['phase']
            require(archive.json(name + '/result.json') == phase, 'Phase report differs from archived exact result')
            samples = [json.loads(line) for line in archive.raw(name + '/samples.jsonl').splitlines() if line.strip()]
            coverage = accounting.sample_coverage(samples, 1, 'private')
            require(not coverage['invalid_sample_indices'] and coverage['complete_unique_process_samples'] > 0
                    and (phase['lifecycle'] == 'restart' or coverage['pending_startup_samples'] == 0), 'Accounting coverage failed')
            require(coverage['verified_peak_pss_bytes'] == phase['sampled_peak_daemon_pss_bytes'], 'PSS peak differs')
            events = [json.loads(line) for line in archive.raw(name + '/bep.jsonl').splitlines() if line.strip()]
            result_row = phase['result']
            root = PurePosixPath(config['run_root'])
            paths = {'bases': {'0': root / 'state/o00'}, 'disk': root / 'state/action-cache',
                     'repository': root / 'state/repository-cache', 'tmp': root / 'temp/tmp', 'skycache': root / 'state/skycache'}
            trace = root / name / 'trace.jsonl' if phase['trace_enabled'] else None
            context_path = root / name / 'context.json' if phase['trace_enabled'] else None
            require(runner.nc.audit_policy(events, runner.policy(paths, report['fixture']['worktrees'][0], trace, context_path))
                    == result_row['trace_policy'], 'Archived actual build policy differs')
            mapping = runner.resources.EngineMapping(str(config['run_root']) + '/state/o00', Path('/unused'),
                runner.resources.ProcessIdentity(phase['final']['engines']['0']['pid'], phase['final']['engines']['0']['start_ticks']))
            actual_bep = runner.nc.bep_identity(events, report['fixture']['worktrees'][0], mapping)
            require(actual_bep == result_row['bep_identity'] and actual_bep['build_uuid'] not in uuids,
                    'Actual BEP command/engine binding differs')
            uuids.add(actual_bep['build_uuid'])
            require(result_row['behavior']['passed'] and result_row['policy_verified']
                    and (config['reference_only'] or result_row['digest_oracle'] == {'required': True, 'matches': True}),
                    'Functional/oracle proof missing')
            behavior = archive.json(name + '/behavior/out/switch.json')
            require(all(behavior.get(k) == v for k, v in {'name': 'test-switch', 'description': 'Switch profile',
                'sysobjectid': ['1.3.6.1.4.1.9']}.items()), 'Archived independent behavior output differs')
            runner.check_drain_documents(archive.json(name + '/local-skycache-ownership.json'), archive.json(name + '/local-skycache.json'))
            summary = {'phase': name, 'coverage': coverage, 'build': phase['build'], 'prepared_request': phase['prepared_request'],
                       'tail': phase['tail'], 'daemon_cpu_seconds': phase['daemon_cpu_seconds'],
                       'pressure': accounting.pressure_summary(phase['scope_reads'][0]['snapshot'], phase['scope_reads'][-1]['snapshot'])}
            result['phases'].append(summary)
        if row['trace_enabled']:
            verify_trace(archive, config, report, destination, result)
        else:
            require(not any(name.endswith(('/trace.jsonl', '/trace.jsonl.completion.json')) for name in archive.rows),
                    'Trace-off archive contains trace artifacts')
        check_postprocess_helpers(config)
        result.update(verified=True, lifecycle_window=report['lifecycle_window'],
                      status='verified_diagnostic_no_gate_verdict')
    except Exception as error:
        result.update(status='stopped_for_assessment', error=type(error).__name__ + ': ' + str(error))
    finally:
        archive.tar.close()
        save(destination / 'result.json', result)
    print(json.dumps({'verified': result['verified'], 'result': str(destination / 'result.json'), 'milestone_verdict': None}))
    return 0 if result['verified'] else 1


def verify_trace(archive, config, report, destination, result):
    replay_path = WORK / 'm1-trace-design/replay.py'
    for path in (replay_path, replay_path.with_name('trace-v1.contract.json')):
        require(sha(path) == config['helper_sha256'][str(path)], 'Frozen replay/contract changed')
    traced = report['phases'][-1]['result']['trace']
    context_raw = archive.raw('measured/context.json', 65536)
    context = json.loads(context_raw)
    require(context == traced['context'] and hashlib.sha256(context_raw).hexdigest() == traced['context_sha256'],
            'Exact raw context binding differs')
    expected_context = {'engine_id': config['run_id'], 'cohort_id': runner.SERIES, 'phase': config['state'], 'hosting': 'private',
        'binary_sha256': config['binary']['sha256'], 'source_identity': config['source_identity'],
        'semantic_epoch': 'm1-trace-main-facts-v1', 'snapshot_id': report['snapshot_id'],
        'command_policy_sha256': runner.nc.digest(archive.json('measured/command.json')['argv']),
        'repository_bindings': {'main': {'logical_repository_id': 'datadog-normalizer-projection-v1',
            'physical_root': report['fixture']['worktrees'][0]['workspace'],
            'filesystem_policy': {'os': 'linux', 'case_sensitive': True, 'digest_algorithm': 'SHA-256'},
            'binding_provenance_sha256': config['binding_provenance_sha256']}}, 'bzl_inlining': 'unknown'}
    require(context == expected_context, 'Declared context differs from actual source/request/fixture')
    with tempfile.TemporaryDirectory(prefix='wire-', dir=destination) as temporary:
        path = Path(temporary) / 'trace.jsonl'
        path.write_bytes(archive.raw('measured/trace.jsonl', 256 * MIB))
        receipt = archive.raw('measured/trace.jsonl.completion.json', 4096)
        Path(str(path) + '.completion.json').write_bytes(receipt)
        require(sha(path) == traced['sha256'] and hashlib.sha256(receipt).hexdigest() == traced['completion_sha256'],
                'Trace/receipt bytes differ')
        attestation = {'traces': {traced['sha256']: {'context': context, 'context_sha256': traced['context_sha256'],
            'completion_sha256': traced['completion_sha256'], 'trace_path': traced['path']}}}
        save(destination / 'attestations.json', attestation)
        command = [sys.executable, '-B', str(replay_path), str(path), '--attestations', str(destination / 'attestations.json'),
                   '--output', str(destination / 'replay-result.json')]
        save(destination / 'replay-command.json', command)
        with (destination / 'replay.log').open('xb') as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
        require(completed.returncode == 0, 'Actual Java trace rejected by frozen replay')
        replay = json.loads(read(destination / 'replay-result.json'))
        require(len(replay['traces']) == 1 and replay['traces'][0]['context_attested'] is True
                and replay['traces'][0]['trace_sha256'] == traced['sha256']
                and replay['traces'][0]['strict_cross_engine_observation_matches'] == 0,
                'Single actual trace attestation/replay differs')
        # Inspect just the bounded header for UUID linkage; replay has validated its full record/footer structure.
        with path.open('rb') as stream:
            header_raw = stream.readline(65537)
        require(len(header_raw) <= 65536, 'Header exceeds context budget')
        header = json.loads(header_raw)
        require(header['command_id'] == report['phases'][-1]['result']['bep_identity']['build_uuid']
                and header['context_sha256'] == traced['context_sha256'], 'Actual Java header/BEP UUID differs')
        result['trace'] = {'sha256': traced['sha256'], 'bytes': path.stat().st_size,
                           'replay_result_sha256': sha(destination / 'replay-result.json'),
                           'replay_summary': replay['traces'][0], 'python_observer': replay['python_observer']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    capture_parser = commands.add_parser('capture')
    capture_parser.add_argument('--manifest', type=Path, required=True)
    capture_parser.add_argument('--manifest-sha256', required=True)
    verify_parser = commands.add_parser('verify')
    verify_parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    return capture(args) if args.mode == 'capture' else verify(args)


if __name__ == '__main__':
    sys.exit(main())
