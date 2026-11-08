#!/usr/bin/env python3
"""Archive one finished native N10 role; no launch, mutation or cache/source walk."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tarfile

R = Path('/mnt/shared-evaluation-20261107')
TASK = R / 'native-n10-probe01'
E = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary')
BINARY_SHA = '8b9195a536c4718cad937bbe70045db958f50b2dd43efb7aa742f91e284cb5d0'
FIXTURE_SHA = '2de503bc8d4b5e7e1817b5a73e9bb78c1d7bacc56f5ae19847b8f686ebaff78a'
PHASE_FILES = ('command.json', 'stdout.log', 'stderr.log', 'process-result.json', 'verified-result.json', 'bep.jsonl')
HELPERS = ('native_comparison.py', 'm1_resources.py', 'fixture.py', 'baseline.py', 'output_bases.py')
BUILD_REPORT_SHA = 'c58633b5467037d073733ef02b195696b9bc64f47aff46bac3daf23d46ac2e81'
BUILD_PROOF_SHA = 'bb2af67893cf36272240d221e869c4507bfac9fd086f6f7d0951526d108644a4'
SOURCE_PROOF_SHA = '98e6c74d854eefc62e64fdac694fbaf6b134b101a619eb369fd02a6bb7c21306'
HELPER_PINS = {
    'native_comparison.py': '02c722fd3ac7b01139adc9f0b239ab603cbd016efa4a0ee34c47118f64b4f217',
    'm1_resources.py': '27a4f79e6542d62bd3f117398255ea21161446b510782ec2ba53552926778dc3',
    'fixture.py': FIXTURE_SHA,
    'baseline.py': 'f418157ada895e181f75c43cd4c7c05e69077598013b9097420f650fa6c6f906',
    'output_bases.py': 'd63dd9b7ba66cb88b5abcf914e835a8a2bf0bad9e6aa94f6203ab884caa3bbae',
}
PROFILE = {'name': 'test-switch', 'description': 'Switch profile',
           'sysobjectid': ['1.3.6.1.4.1.9'], 'device': {}}


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def encoded(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def document(path):
    require(path.is_file() and not path.is_symlink() and path.resolve() == path and
            path.stat().st_size <= 8 * 1024**2, 'Invalid/oversized evidence document')
    return json.loads(path.read_text())


def successful_guard(guard, identity):
    require(guard['status'] == 'completed' and guard['exit_code'] == 0 and
            guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True} and
            guard['scope']['unit'] == identity['Id'] and
            guard['scope']['invocation_id'] == identity['InvocationID'], 'Prior guard proof differs')


def verify_qualification(config):
    """Read the exact C archives which authorized this candidate and cap10 cohort."""
    build = E / 'native-cap10-build-01'
    for name, expected in (('report.json', BUILD_REPORT_SHA), ('candidate-verification.json', BUILD_PROOF_SHA),
                           ('source-verification.json', SOURCE_PROOF_SHA)):
        require(sha(build / name) == expected, 'Pinned build proof changed: ' + name)
    report, proof, source = (document(build / name) for name in
                             ('report.json', 'candidate-verification.json', 'source-verification.json'))
    guard, identity = document(build / 'guard/result.json'), document(build / 'guard/scope-identity.json')
    successful_guard(guard, identity)
    require(proof['verified'] is True and proof['qualification_passed'] is True and
            proof['sha256'] == BINARY_SHA and proof['report_sha256'] == BUILD_REPORT_SHA and
            proof['guard_result_sha256'] == sha(build / 'guard/result.json') and
            proof['scope_identity_sha256'] == sha(build / 'guard/scope-identity.json') and
            proof['source_verification_sha256'] == SOURCE_PROOF_SHA and source['verified'] is True and
            source['current'] == report['source_final'], 'Build verification receipt differs')
    binary, provenance = config['binary'], config['preparation_provenance']
    require(provenance['candidate_report']['sha256'] == BUILD_REPORT_SHA and
            provenance['candidate_guard']['sha256'] == sha(build / 'guard/result.json') and
            binary['admission_cap'] == 10 and binary['base_admission_cap'] == 2 and
            binary['source_is_base_plus_qualification_test_and_cap10_patch'] is True and
            binary['source_tree_sha256'] == report['source_archive_sha256'] and
            binary['qualification_patch'] == report['qualification_test_patch'] and
            binary['cap10_patch'] == report['cap10_patch'] and
            binary['verified_source_content_sha256'] == report['source_final']['tracked_content_sha256'],
            'Runtime manifest does not bind qualified base-plus-patches source')
    acceptance = E / 'native-acceptance-cap10-01'
    accepted = provenance['full_cap10_acceptance']
    expected_names = {'results.json', 'runtime.tar.gz.json', 'control.json', 'guard/result.json',
                      'guard/policy.json', 'guard/scope-identity.json', 'command.json', 'runtime.tar.gz'}
    require(accepted['run_id'] == acceptance.name and accepted['candidate_sha256'] == BINARY_SHA and
            accepted['complete_checks'] == 21 and set(accepted['input_sha256']) == expected_names,
            'Wrong full-cap10 acceptance inputs')
    for name, expected in accepted['input_sha256'].items():
        path = acceptance / name
        require(path.is_file() and path.resolve() == path and path.stat().st_size <= 256 * 1024**2 and
                sha(path) == expected, 'Acceptance archive input changed: ' + name)
    result, meta = document(acceptance / 'results.json'), document(acceptance / 'runtime.tar.gz.json')
    ag, ai = document(acceptance / 'guard/result.json'), document(acceptance / 'guard/scope-identity.json')
    successful_guard(ag, ai)
    require(meta['passed'] is True and result['success'] is True and result['run_completed'] is True and
            not result['cleanup_errors'] and result['run_error'] is None and result['expected_engine_cap'] == 10 and
            result['binary_sha256'] == meta['candidate_sha256'] == BINARY_SHA and
            len(result['checks']) == len(set(result['checks'])) == 21 and
            set(result['checks']) == set(result['required_checks']) == set(meta['required_checks']) and
            meta['candidate_report_sha256'] == BUILD_REPORT_SHA and
            meta['candidate_verification_sha256'] == BUILD_PROOF_SHA and
            meta['archive_sha256'] == accepted['input_sha256']['runtime.tar.gz'], 'Cap10 acceptance not complete/bound')
    return {'build_report_sha256': BUILD_REPORT_SHA, 'build_candidate_verification_sha256': BUILD_PROOF_SHA,
            'source_verification_sha256': SOURCE_PROOF_SHA, 'full_cap10_acceptance': accepted}


def verify_endpoint(final, role, group, bases):
    expected = {str(index) for index in range(10)}
    require(final['eligible'] is True and final['identity_continuity_valid'] is True and
            final['membership_continuity_valid'] is True and final['expected_engine_count'] == 10 and
            final['valid_engine_count'] == 10 and set(final['engines']) == expected,
            'Measured endpoint lacks ten verified engines')
    processes = final['processes']
    identities = {(row['pid'], row['start_ticks']) for row in processes}
    require(len(identities) == len(processes) == len({row['pid'] for row in processes}) ==
            (1 if role == 'native' else 10), 'Wrong unique daemon count')
    covered = []
    for process in processes:
        require(type(process['pid']) is int and process['pid'] > 0 and
                type(process['start_ticks']) is int and process['start_ticks'] > 0 and
                len(process['engines']) == (10 if role == 'native' else 1),
                'Invalid process identity or engine-to-process cardinality')
        member = process['cgroup']
        require((member == group or member.startswith(group + '/')) and
                process['cgroup_before'] == member == process['cgroup_after'] and not process['errors'],
                'Measured daemon lacks stable owned membership')
        for key in process['engines']:
            require(key in expected and final['engines'][key]['pid'] == process['pid'] and
                    final['engines'][key]['start_ticks'] == process['start_ticks'] and
                    final['engines'][key]['output_base'] == bases[key] and not final['engines'][key]['errors'],
                    'Engine/process/output-base mapping differs')
            covered.append(key)
    require(len(covered) == 10 and set(covered) == expected, 'Duplicate/missing engine process coverage')
    scope = final['cgroup']
    require(scope['eligible'] is True and scope['binding_valid'] is True and scope['identity_continuity_valid'] is True and
            scope['expected_cgroup'] == group and scope['directory_identity'] == scope['directory_identity_after'] ==
            scope['expected_directory_identity'], 'Measured scope identity differs')
    return {'unique_daemon_count': len(identities), 'engine_count': 10, 'membership_verified': True}


def live_process(pid, ticks=None):
    require(type(pid) is int and pid > 0, 'Invalid PID')
    try:
        fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[0] != 'Z' and (ticks is None or int(fields[19]) == ticks)


def collect_files(root):
    """Fixed N10 paths only. Never inspect state, repository or worktree entries."""
    files = {}
    def add(relative):
        path = root / relative
        if path.exists():
            require(path.is_file() and not path.is_symlink() and path.resolve() == path,
                    'Redirected/non-file evidence: ' + relative)
            files[relative] = path
    for name in ('report.json', 'manifest.json', 'fixture/manifest.json',
                 'measured/samples.jsonl', 'measured/build-boundary.json', 'measured/partial-accounting.json'):
        add(name)
    for phase in ('preparation', 'measured', 'preparation-shutdown', 'final-shutdown',
                  'preparation-identity', 'measured-identity'):
        for index in range(10):
            suffixes = ('-server_pid', '-output_base') if phase.endswith('-identity') else ('',)
            for suffix in suffixes:
                directory = phase + '/c%02d' % index + suffix
                for name in PHASE_FILES:
                    add(directory + '/' + name)
                if phase in ('preparation', 'measured'):
                    add(directory + '/behavior/switch.yaml')
                    add(directory + '/behavior/out/switch.json')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('role', choices=('ref', 'private', 'native'))
    args = parser.parse_args()
    role, root = args.role, TASK / args.role
    destination = E / ('native-n10-probe01-' + role)
    output_names = ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json')
    require(all(not (destination / name).exists() and not (destination / name).is_symlink()
                for name in output_names), 'Archive output exists; no overwrite')
    guard_path, identity_path = destination / 'guard/result.json', destination / 'guard/scope-identity.json'
    guard, identity = json.loads(guard_path.read_text()), json.loads(identity_path.read_text())
    cleanup, unit = guard['final_scope_cleanup'], guard['scope']['unit']
    require(guard['status'] in ('completed', 'command_failed') and cleanup['verified'] is True and
            cleanup['present'] is False and cleanup['signalled'] is False, 'Final guard absence is not verified/unsignalled')
    require(re.fullmatch('shared-eval-guard-native-n10-probe01-' + role + r'-[0-9a-f]{32}\.scope', unit) and
            identity['Id'] == unit and identity['InvocationID'] == guard['scope']['invocation_id'], 'Guard identity mismatch')
    group = identity['ControlGroup']
    require(group.startswith('/') and '..' not in Path(group).parts and group.endswith('/' + unit), 'Unexpected scope path')
    require(not (Path('/sys/fs/cgroup') / group.lstrip('/')).exists(), 'Scope is still present')
    require(root.resolve() == root, 'Redirected run root')
    require((root / 'report.json').stat().st_size <= 8 * 1024**2 and
            (root / 'manifest.json').stat().st_size <= 2 * 1024**2, 'Report/manifest above bound')
    raw_report = (root / 'report.json').read_bytes()
    report = json.loads(raw_report)
    config = json.loads((root / 'manifest.json').read_text())
    policy_path = destination / 'guard/policy.json'
    policy = json.loads(policy_path.read_text())
    require(policy['command_sha256'] == config['guard_command_sha256'] == sha(destination / 'command.json'),
            'Guard command digest mismatch')
    command = ['python3', '-B', str(TASK / 'harness/native_comparison.py'), '--manifest', str(TASK / 'config' / (role + '.json'))]
    if role == 'ref':
        command.append('--reference-only')
    require(json.loads((destination / 'command.json').read_text()) == config['launch_argv'] == command,
            'Unexpected role command')
    require(config['run_root'] == report['run_root'] == str(root) and config['clients'] == report['clients'] == 10 and
            config['hosting'] == report['hosting'] == ('native' if role == 'native' else 'private') and
            report['reference_only'] is (role == 'ref') and config['scenario'] == report['scenario'] == 'identical',
            'Unexpected N10 role/fixture configuration')
    require(config['state'] == report['state'] == 'restart' and
            config['sample_interval_seconds'] == report['sample_interval_seconds'] == 0.5 and
            config['shared_name'] == 'native-n10-probe01-' + role, 'Wrong initial N10 restart protocol')
    require(report['manifest_sha256'] == sha(root / 'manifest.json') and report['binary_manifest'] == config['binary'],
            'Runtime manifest binding changed')
    scope = report['scope']
    require(scope['membership'] == group and scope['identity']['Id'] == unit and
            scope['identity']['InvocationID'] == identity['InvocationID'] and
            scope['guard_policy_sha256'] == sha(policy_path) and scope['scope_identity_sha256'] == sha(identity_path),
            'Report scope/guard binding mismatch')
    if 'final_scope_attestation' in report:
        final = report['final_scope_attestation']
        require(final['membership'] == group and final['identity']['Id'] == unit and
                final['identity']['InvocationID'] == identity['InvocationID'], 'Final report scope mismatch')
    passed = report['passed'] is True
    require((guard['status'] == 'completed' and guard['exit_code'] == 0) if passed else
            (guard['status'] == 'command_failed' and guard['exit_code'] != 0), 'Runtime/guard outcome mismatch')
    source_checks, verification_errors, daemon_checks = [], [], []
    binary = Path(config['binary']['path'])
    require(binary == R / 'native-cap10-build-v1/candidate/bazel-native-cap10' and binary.resolve() == binary,
            'Unexpected candidate path')
    actual_binary = sha(binary)
    require(actual_binary == config['binary']['sha256'] == report['binary_sha256'] == BINARY_SHA,
            'Pinned candidate digest mismatch')
    require(config['binary']['source_commit'] == '4862608d940b49293c68c014e413ecc9dc98aa8e' and
            config['binary']['source_git_tree_sha1'] == 'a28c113856ec48838041bba3b9591ab9ea67e4ac', 'Candidate base source mismatch')
    qualification_proof = verify_qualification(config)
    files = collect_files(root)
    harness_pins = config['preparation_provenance']['harness_sha256']
    require(harness_pins == HELPER_PINS, 'Measurement helpers differ from the frozen N2/N10 protocol')
    for name in HELPERS:
        path = TASK / 'harness' / name
        require(path.resolve() == path and path.is_file() and path.stat().st_size <= 1024**2 and
                sha(path) == harness_pins[name], 'Frozen helper changed: ' + name)
        if name in report['harness_sha256']:
            require(report['harness_sha256'][name] == harness_pins[name], 'Reported helper mismatch')
        files['executed-harness/' + name] = path
    require(harness_pins['fixture.py'] == FIXTURE_SHA, 'Unsupported fixture verifier')
    spec = importlib.util.spec_from_file_location('archived_n10_fixture', TASK / 'harness/fixture.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    if 'fixture' in report:
        manifest = report['fixture']
        require(json.loads((root / 'fixture/manifest.json').read_text()) == manifest, 'Fixture manifest mismatch')
        require(manifest['clients'] == 10 and manifest['scenario'] == 'identical' and
                sorted(row['index'] for row in manifest['worktrees']) == list(range(10)),
                'Wrong fixture cohort/index coverage')
        for row in manifest['worktrees']:
            workspace = Path(row['workspace'])
            require(row['index'] in range(10) and workspace == root / 'fixture' / ('w%02d' % row['index']) and
                    workspace.resolve() == workspace, 'Unexpected fixture worktree')
            try:
                tree = fixture.git(workspace, 'rev-parse', 'HEAD^{tree}').decode().strip()
                state = fixture.source_state(workspace)  # Frozen helper disables optional Git locks.
                require(tree == manifest['private_tree'] and state == row['source_state'], 'Fixture source changed')
                source_checks.append({'workspace': str(workspace), 'private_tree': tree, 'verified': True})
            except Exception as error:
                verification_errors.append(type(error).__name__ + ': ' + str(error))
    if 'paths' in report:
        paths = report['paths']
        control = root / 'state/user/shared_servers/v3' / config['shared_name'] / 'control'
        expected_bases = {str(index): str(control.parent / 'engines' /
            hashlib.md5(str(root / 'fixture' / ('w%02d' % index)).encode(), usedforsecurity=False).hexdigest()
            if role == 'native' else root / 'state' / ('o%02d' % index)) for index in range(10)}
        require(paths['control'] == str(control) and paths['bases'] == expected_bases,
                'Unexpected engine/control output views')
        bases = [Path(paths['control'])] if role == 'native' else [Path(paths['bases'][str(i)]) for i in range(10)]
        for base in bases:
            require(base.resolve() == base and base.is_relative_to(root / 'state'), 'Unexpected daemon base')
            pidfile = base / 'server/server.pid.txt'
            pid = int(pidfile.read_text().strip()) if pidfile.exists() else None
            alive = live_process(pid) if pid is not None else False
            daemon_checks.append({'pid_file': str(pidfile), 'pid': pid, 'live_pid': alive})
            if alive:
                verification_errors.append('Live PID remains in owned server PID file: ' + str(pidfile))
    for process in report.get('measured', {}).get('final', {}).get('processes', []):
        if live_process(process['pid'], process['start_ticks']):
            verification_errors.append('Measured daemon identity still alive: ' + str(process['pid']))
    if passed:
        require(report.get('owned_daemons_absent') is True and report.get('source_and_binary_unchanged') is True and
                report['measured']['passed'] is True and len(report['measured']['results']) == 10 and
                len(source_checks) == 10 and len(daemon_checks) == (1 if role == 'native' else 10) and
                not verification_errors, 'Completed run lacks independent source/daemon verification')
        require(all(row['exit_code'] == 0 and row['behavior']['passed'] and row['policy_verified']
                    for row in report['measured']['results']), 'Measured correctness/policy failure')
    endpoint, output_checks = None, []
    if passed:
        measured = report['measured']
        require(measured['engine_mappings_verified_after_window'] is True and
                sorted(row['index'] for row in measured['results']) == list(range(10)),
                'Missing measured client mapping/results')
        endpoint = verify_endpoint(measured['final'], role, group, report['paths']['bases'])
        oracle = None
        if role != 'ref':
            reference = E / 'native-n10-probe01-ref'
            require(config['oracle_report']['path'] == str(TASK / 'ref/report.json') and
                    config['oracle_report']['sha256'] == sha(reference / 'report.json') and
                    config['oracle_report']['guard_result']['sha256'] == sha(reference / 'guard/result.json'),
                    'Independent N10 oracle binding differs')
            ref = document(reference / 'report.json')
            successful_guard(document(reference / 'guard/result.json'), document(reference / 'guard/scope-identity.json'))
            require(ref['passed'] is True and ref['reference_only'] is True and ref['binary_sha256'] == BINARY_SHA and
                    sorted(row['index'] for row in ref['measured']['results']) == list(range(10)),
                    'Independent N10 oracle incomplete')
            digests = {row['output']['sha256'] for row in ref['measured']['results']}
            require(len(digests) == 1, 'Identical reference output digests differ')
            oracle = next(iter(digests))
        else:
            require('oracle_report' not in config, 'Reference unexpectedly consumes an oracle')
        for row in measured['results']:
            index, output = row['index'], row['output']
            path = Path(output['path'])
            base = Path(report['paths']['bases'][str(index)])
            require(path.resolve() == path and path.is_file() and path.is_relative_to(base) and
                    path.stat().st_size == output['bytes'] <= 64 * 1024**2 and sha(path) == output['sha256'],
                    'Current measured output digest/ownership differs')
            behavior = root / ('measured/c%02d/behavior/out/switch.json' % index)
            require(document(behavior) == row['behavior']['profile'] == PROFILE and
                    row['behavior']['filename'] == 'switch.json' and row['timed_out'] is False and
                    row['variant'] == 'base' and row['compilation_mode'] == 'fastbuild',
                    'Independent behavior/configuration check differs')
            require(row['bep_identity']['index'] == index and
                    row['bep_identity']['workspace'] == str(root / 'fixture' / ('w%02d' % index)) and
                    row['bep_identity']['server_pid'] == measured['final']['engines'][str(index)]['pid'],
                    'BEP client/daemon identity differs')
            require(row['digest_oracle'] == {'matches': None, 'required': False} if role == 'ref' else
                    row['digest_oracle'] == {'matches': True, 'required': True} and output['sha256'] == oracle,
                    'Measured digest oracle failed')
            output_checks.append({'index': index, 'path': str(path), 'bytes': output['bytes'],
                                  'sha256': output['sha256'], 'behavior_verified': True,
                                  'interpretation': 'Current final bytes; phase assertions remain the frozen harness records'})
    members = []
    for name, path in sorted(files.items()):
        require(path.is_file() and not path.is_symlink() and path.resolve() == path and
                path.stat().st_size <= 64 * 1024**2, 'Invalid/oversized evidence file')
        members.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(len(members) <= 700 and sum(row['bytes'] for row in members) <= 256 * 1024**2, 'Evidence budget exceeded')
    archive_path = destination / 'runtime.tar.gz'
    with archive_path.open('xb') as target:
        with gzip.GzipFile(fileobj=target, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as archive:
                for row in members:
                    archive.add(files[row['path']], arcname=row['path'], recursive=False)
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(archive.getnames() == [row['path'] for row in members], 'Archive member mismatch')
        for row in members:
            with archive.extractfile(row['path']) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256'] and
                        archive.getmember(row['path']).size == row['bytes'], 'Archive checksum mismatch')
    require(hashlib.sha256(raw_report).hexdigest() == next(row['sha256'] for row in members if row['path'] == 'report.json'),
            'Report changed while archiving')
    write_new(destination / 'report.json', raw_report)
    metadata = {'schema_version': 1, 'role': role, 'run_root': str(root), 'passed': passed, 'diagnostic_only': True,
                'archive_sha256': sha(archive_path), 'archive_bytes': archive_path.stat().st_size, 'members': members,
                'report_sha256': hashlib.sha256(raw_report).hexdigest(), 'binary_sha256': actual_binary,
                'source_checks': source_checks, 'daemon_checks': daemon_checks, 'verification_errors': verification_errors,
                'qualification_proof': qualification_proof, 'endpoint_verification': endpoint,
                'current_output_checks': output_checks,
                'guard_result_sha256': sha(guard_path), 'scope_identity_sha256': sha(identity_path),
                'guard_policy_sha256': sha(policy_path), 'command_sha256': sha(destination / 'command.json'),
                'final_scope_absent': True, 'final_scope_unsignalled': True,
                'helper_sha256': sha(Path(__file__).resolve()),
                'scope': 'Fixed N10 reports/manifests, command/log/BEP/accounting evidence, behavior files and executed helpers only; no caches, whole sources, binaries or raw environment dumps',
                'metric_note': 'Copied runtime report is unchanged. Archive success does not promote performance_eligible or declare an M1/M4 gate.'}
    write_new(destination / 'runtime.tar.gz.json', encoded(metadata))
    print(json.dumps({'role': role, 'passed': passed, 'archive_sha256': metadata['archive_sha256'],
                      'members': len(members), 'copied_report': str(destination / 'report.json')}))


if __name__ == '__main__':
    main()
