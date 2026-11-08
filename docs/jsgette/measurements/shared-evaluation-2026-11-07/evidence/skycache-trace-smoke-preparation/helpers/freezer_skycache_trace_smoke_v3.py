#!/usr/bin/env python3
"""C-only smoke preparation after archived source build03 succeeds; never launch/read R."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tarfile

import build_skycache_trace_v3 as build
import qualify_skycache_trace as smoke

WORK = Path(__file__).resolve().parent
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
INPUT = E / 'evidence/auxiliary/m1-trace-source-build-03'
PRIOR_INPUT = E / 'evidence/auxiliary/m1-trace-source-build-02'
OUTPUT = E / 'evidence/skycache-trace-smoke-preparation'
R = PurePosixPath('/mnt/shared-evaluation-20261107')
BUILD_ROOT, ROOT = R / 'm1-skycache-trace-qualification-v3', R / 'm1-trace-acceptance-v1'
STATE_ROOT = R / 'm1-skycache-trace-build-v2'
TREE = 'e8aa57dfd71b302706f16f25389351ff6bb328ff'
PATCH = '026010487e7a049bfbed364803053729f64d1f2be7acd970d5a04132d3512a90'
BUNDLE_SHA = '96d2685e027cfe64620281ae0828e28069e17cfb8b73226411c9e61771b3add0'
BUILD_LAUNCHER_SHA = 'a0140aaf26e25196a3d3b833a0a1b38e3ba7e95f126e01b034f5cb6bd697f705'
RECORDS = {'report': 'report.json', 'candidate': 'candidate-verification.json',
    'source': 'source-verification.json', 'tests': 'test-verification.json',
    'archive': 'runtime.tar.gz.json', 'guard': 'guard/result.json',
    'identity': 'guard/scope-identity.json', 'policy': 'guard/policy.json', 'command': 'command.json'}
PRIOR_RECORDS = {'prior_report': 'report.json', 'prior_metadata': 'runtime.tar.gz.json',
                 'prior_source': 'source-verification.json'}
require, sha = build.require, build.sha


def encoded(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def linux_path(path):
    path = path.resolve()
    require(path.drive.lower() == 'c:', 'Preparation must remain on C:')
    return str(PurePosixPath('/mnt/c', *path.parts[1:]))


def read_json(path, limit=4 * 1024**2):
    require(path.resolve() == path and path.is_file() and not path.is_symlink()
            and path.stat().st_size <= limit, 'Invalid C-backed input: ' + str(path))
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest(), raw


def expected_commands():
    previous = build.build.TESTS
    try:
        build.build.TESTS = build.TESTS
        result = build.build.commands(STATE_ROOT)
    finally:
        build.build.TESTS = previous
    for name in ('build', 'tests'):
        result[name] = ['--build_event_json_file=' + str(BUILD_ROOT / (name + '.bep.jsonl'))
                        if arg == '--build_event_json_file=' + str(STATE_ROOT / (name + '.bep.jsonl')) else arg
                        for arg in result[name]]
    return result


def validate_reuse(records, hashes, bundle):
    prior, prior_metadata, prior_source = [records[key] for key in PRIOR_RECORDS]
    report, source = records['report'], records['source']
    require(hashes['prior_report'] == build.PRIOR_REPORT_SHA and hashes['prior_metadata'] == build.PRIOR_METADATA_SHA
            and prior_metadata['archive_sha256'] == build.PRIOR_ARCHIVE_SHA
            and prior_source == prior_metadata['current_source_verification']
            and prior_source['verified'] is True and prior_source['qualification_passed'] is False
            and prior_source['current'] == prior['source_before'] == prior['source_after']
            and prior['root'] == str(STATE_ROOT) and prior['status'] == 'failed', 'Preserved failed source2 proof differs')
    build.validate_lineage(prior, bundle)
    members = {row['path']: row for row in prior_metadata['members']}
    require(len(members) == len(prior_metadata['members']), 'Duplicate prior archive members')
    preserved = {name: members[name]['sha256'] for name in build.PRESERVED}
    preserved['source.tar'] = prior['bundle']['source_archive']['sha256']
    reuse = source['incremental_reuse']
    require(report['build_state_root'] == reuse['build_state_root'] == str(STATE_ROOT)
            and report['source_root'] == reuse['source_root'] == str(STATE_ROOT / 'source')
            and report['reused_build_state'] is True and reuse['verified'] is True
            and report['source_before_patch'] == prior_source['current']
            and report['source_before_patch'] != report['source_before']
            and report['prior_report'] == {'path': linux_path(PRIOR_INPUT / 'report.json'), 'sha256': build.PRIOR_REPORT_SHA}
            and report['prior_archive_sha256'] == reuse['prior_archive_sha256'] == build.PRIOR_ARCHIVE_SHA
            and report['prior_metadata_sha256'] == build.PRIOR_METADATA_SHA
            and reuse['prior_report_sha256'] == build.PRIOR_REPORT_SHA
            and report['preserved_prior_files'] == report['preserved_prior_files_after'] == reuse['preserved_prior_files'] == preserved,
            'Incremental reuse/source preservation proof differs')
    require(all(report[key] == prior[key] for key in ('environment', 'generated_rc_sha256', 'seed_copy_sha256',
                                                     'configuration_sha256', 'bootstrap')), 'Inherited build policy differs')
    repairs = report['repair']
    require(repairs == reuse['repair'] and len(repairs) == 2 and {row['path'] for row in repairs} == set(build.ALLOWED),
            'Declared repair paths differ')
    for row in repairs:
        name = row['path']
        require(row['old']['path'] == row['new']['path'] == name
                and row['old']['mode'] == row['new']['mode'] == '100644'
                and row['old']['sha256'] == prior['bundle']['trace_source_sha256'][name]
                and row['new']['sha256'] == bundle['trace_source_sha256'][name]
                and row['old']['sha256'] != row['new']['sha256'], 'Declared repair hashes differ')
    for key in ('prior_server_absence', 'prior_server_absence_before_patch'):
        row = report[key]
        require(row['pid_file'] == str(STATE_ROOT / 'build-output/server/server.pid.txt')
                and row['verified'] is True and row['live'] is False
                and (row['pid'] is None or type(row['pid']) is int and 0 < row['pid'] < 2**31),
                'Prior daemon absence receipt differs')


def validate_records(records, hashes, bundle):
    report, candidate, source, tests, metadata = [records[name] for name in ('report', 'candidate', 'source', 'tests', 'archive')]
    guard, identity, policy = [records[name] for name in ('guard', 'identity', 'policy')]
    require(guard['status'] == 'completed' and guard['exit_code'] == 0
            and guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True},
            'Build guard did not finish successfully and unsignalled')
    require(re.fullmatch(r'shared-eval-guard-m1-trace-source-build-03-[0-9a-f]{32}\.scope', identity['Id'])
            and guard['scope']['unit'] == identity['Id'] and guard['scope']['invocation_id'] == identity['InvocationID']
            and identity['ControlGroup'].startswith('/') and identity['ControlGroup'].endswith('/' + identity['Id'])
            and report['scope_before']['name'] == report['scope_after']['name'] == identity['ControlGroup'], 'Build scope binding differs')
    require(policy['command_sha256'] == hashes['command'] and policy['memory_max_mib'] == 10240
            and policy['cpu_quota_percent'] == 600, 'Build guard command/budget differs')
    expected_command = ['python3', '-B', linux_path(WORK / 'build_skycache_trace_v3.py'), '--bundle',
        linux_path(WORK / 'trace-build-preparation/bundle-v3/bundle.json'), '--bundle-sha256', BUNDLE_SHA]
    require(records['command'] == expected_command, 'Build invocation differs')
    require(report['root'] == str(BUILD_ROOT) and report['bundle'] == bundle and report['bundle_sha256'] == BUNDLE_SHA
            and report['status'] == 'build_and_focused_tests_passed_no_gate_verdict'
            and bundle['base_commit'] == build.BASE and bundle['baseline_git_tree'] == build.BASELINE_TREE
            and bundle['safe_subset_patch_sha256'] == build.SAFE_PATCH and bundle['instrumented_git_tree'] == TREE
            and bundle['combined_patch']['sha256'] == PATCH and bundle['tests'] == build.TESTS,
            'Qualified build/source identity differs')
    expected_helpers = {linux_path(WORK / 'build_skycache_trace_v3.py'): BUILD_LAUNCHER_SHA,
                        linux_path(WORK / 'build_skycache_trace_v2.py'): build.PREVIOUS_HELPER_SHA,
                        linux_path(E / 'harness/native_source_build.py'): build.BUILD_HELPER}
    require(report['helper_sha256'] == expected_helpers, 'Build helper provenance differs')
    expected = expected_commands()
    commands = report['commands']
    require([row['name'] for row in commands] == ['version', 'build', 'tests', 'shutdown']
            and all(row.get('returncode') == 0 and row['argv'] == expected[row['name']]
                    and row['cwd'] == str(STATE_ROOT / 'source') for row in commands),
            'Build did not complete exact version/build/13-tests/shutdown commands')
    require(candidate.get('verified') is True and candidate.get('qualification_passed') is True
            and candidate['report_sha256'] == hashes['report']
            and candidate['guard_result_sha256'] == hashes['guard']
            and candidate['scope_identity_sha256'] == hashes['identity']
            and candidate['source_verification_sha256'] == hashes['source']
            and candidate['test_verification_sha256'] == hashes['tests']
            and candidate['final_scope_absent'] is True and candidate['final_scope_unsignalled'] is True,
            'Archived candidate proof is missing or mismatched')
    product = report['candidate']
    require(candidate['path'] == product['destination'] == str(BUILD_ROOT / 'candidate/bazel-skycache-trace')
            and PurePosixPath(product['source']).is_relative_to(STATE_ROOT / 'build-output')
            and candidate['sha256'] == product['sha256'] == report['product_binary_sha256_after_build']
            == report['product_binary_sha256_after_tests'] and candidate['bytes'] == product['bytes']
            and type(product['bytes']) is int and 0 < product['bytes'] <= 512 * 1024**2
            and re.fullmatch('[0-9a-f]{64}', product['sha256']), 'Stable candidate identity differs')
    require(source['verified'] is True and source['qualification_passed'] is True
            and source['current'] == report['source_before'] == report['source_after'] == report['source_after_patch']
            and source['current']['files_verified'] == 18592 and source['instrumented_git_tree'] == TREE
            and source['source_archive'] == bundle['source_archive']
            and source['generated_input_sha256'] == {'build.bazelrc': report['generated_rc_sha256'],
                                                     'seed-copy.json': report['seed_copy_sha256']},
            'Full qualified source verification differs')
    validate_reuse(records, hashes, bundle)
    require(tests['verified'] is True and tests['qualification_passed'] is True
            and tests['build']['success'] is True and tests['tests']['success'] is True
            and set(tests['tests']['test_summaries']) == set(build.TESTS)
            and all(row['overallStatus'] == 'PASSED' for row in tests['tests']['test_summaries'].values()),
            'All13 focused suite results are required')
    require(metadata['passed'] is True and metadata['report_status'] == report['status']
            and metadata['candidate'] == candidate and metadata['current_source_verification'] == source
            and metadata['test_verification'] == tests and metadata['bundle_sha256'] == BUNDLE_SHA
            and metadata['build_state_root'] == str(STATE_ROOT) and metadata['source_root'] == str(STATE_ROOT / 'source')
            and metadata['reused_build_state'] is True
            and metadata['launcher_sha256'] == BUILD_LAUNCHER_SHA and not metadata['missing_files'],
            'Archive outcome/provenance differs')
    return product


def verify_archived_bep(path, metadata, tests):
    require(path.stat().st_size == metadata['archive_bytes'] <= 256 * 1024**2
            and sha(path) == metadata['archive_sha256'], 'Qualification archive digest differs')
    records = {row['path']: row for row in metadata['members']}
    require(len(records) == len(metadata['members']) <= 560, 'Duplicate/unbounded archive manifest')
    with tarfile.open(path, 'r:gz') as archive:
        require(archive.getnames() == [row['path'] for row in metadata['members']], 'Archive member set/order differs')
        for name, key in (('build.bep.jsonl', 'build'), ('tests.bep.jsonl', 'tests')):
            member = archive.getmember(name)
            require(member.isfile() and member.size == records[name]['bytes'] <= 64 * 1024**2, 'Invalid archived BEP member')
            with archive.extractfile(member) as stream:
                raw = stream.read()
            require(hashlib.sha256(raw).hexdigest() == records[name]['sha256'], 'Archived BEP hash differs')
            starts, finishes, summaries = [], [], {}
            for line in raw.splitlines():
                require(len(line) <= 1024**2, 'Oversized BEP event')
                row = json.loads(line)
                if 'started' in row:
                    starts.append(row['started']['uuid'])
                if 'finished' in row:
                    finishes.append(row['finished']['exitCode'])
                if 'testSummary' in row:
                    label = row['id']['testSummary']['label']
                    require(label not in summaries and label in build.TESTS, 'Unexpected/duplicate actual test suite')
                    summaries[label] = row['testSummary']
            require(len(starts) == len(finishes) == 1 and starts == tests[key]['command_uuids']
                    and finishes == tests[key]['finished'] and finishes[0].get('code', 0) == 0
                    and finishes[0].get('name') == 'SUCCESS' and summaries == tests[key]['test_summaries'],
                    'Actual archived BEP differs from successful proof')
            require(key != 'tests' or set(summaries) == set(build.TESTS)
                    and all(row['overallStatus'] == 'PASSED' for row in summaries.values()), 'Actual13-suite BEP proof incomplete')


def manifest_for(report_sha, proof_sha, helper_hashes):
    return {'schema_version': 1, 'root': str(ROOT), 'cases': [list(row) for row in smoke.CASES],
        'harness_sha256': helper_hashes['qualify_skycache_trace.py'],
        'replay_sha256': helper_hashes['m1-trace-design/replay.py'],
        'contract_sha256': helper_hashes['m1-trace-design/trace-v1.contract.json'],
        'build_report': {'path': linux_path(INPUT / 'report.json'), 'expected': report_sha},
        'candidate_verification': {'path': linux_path(INPUT / 'candidate-verification.json'), 'expected': proof_sha},
        'source_identity': {'base_commit': build.BASE, 'patch_sha256': PATCH, 'reconstructed_git_tree': TREE}}


def main():
    manifest_path, command_path = WORK / 'trace-smoke-manifest.json', WORK / 'skycache-trace-smoke-command.json'
    require(not OUTPUT.exists() and not OUTPUT.is_symlink() and all(not p.exists() and not p.is_symlink()
            for p in (manifest_path, command_path)), 'Smoke preparation outputs already exist')
    records, hashes, raw_records = {}, {}, {}
    for key, name in RECORDS.items():
        records[key], hashes[key], raw_records[key] = read_json(INPUT / name)
    for key, name in PRIOR_RECORDS.items():
        records[key], hashes[key], raw_records[key] = read_json(PRIOR_INPUT / name)
    require(sha(PRIOR_INPUT / 'runtime.tar.gz') == build.PRIOR_ARCHIVE_SHA, 'Preserved prior archive bytes differ')
    bundle_path = WORK / 'trace-build-preparation/bundle-v3/bundle.json'
    bundle, bundle_sha, bundle_raw = read_json(bundle_path)
    require(bundle_sha == BUNDLE_SHA, 'Frozen third source bundle differs')
    candidate = validate_records(records, hashes, bundle)
    verify_archived_bep(INPUT / 'runtime.tar.gz', records['archive'], records['tests'])
    require(PurePosixPath(str(smoke.BUILD_ROOT).replace('\\', '/')) == BUILD_ROOT,
            'Runtime smoke launcher has not been pinned to qualified build03')
    helper_names = ['qualify_skycache_trace.py', 'test_qualify_skycache_trace.py',
        'archive_skycache_trace_smoke.py', 'test_archive_skycache_trace_smoke.py',
        'm1-trace-design/replay.py', 'm1-trace-design/trace-v1.contract.json',
        'build_skycache_trace_v3.py', 'build_skycache_trace_v2.py',
        'skycache-trace-smoke-protocol.md', Path(__file__).name, 'test_freezer_skycache_trace_smoke_v3.py']
    helper_bytes = {name: (WORK / name).read_bytes() for name in helper_names}
    for name in ('native_source_build.py', 'run_auxiliary.ps1', 'run_guarded.ps1',
                 'host_memory_guard.psm1', 'reset_task_mount.py'):
        helper_bytes['control/' + name] = (E / 'harness' / name).read_bytes()
    helper_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in helper_bytes.items()}
    require(helper_hashes['control/native_source_build.py'] == smoke.GUARD_HELPER, 'Runtime guard helper changed')
    require(helper_hashes['build_skycache_trace_v3.py'] == BUILD_LAUNCHER_SHA
            and helper_hashes['build_skycache_trace_v2.py'] == build.PREVIOUS_HELPER_SHA,
            'Qualification helper closure changed')
    manifest = manifest_for(hashes['report'], hashes['candidate'], helper_hashes)
    # This validator only compares values. PurePosixPath avoids Windows separators;
    # no runtime path existence, binary, or /proc access occurs during preparation.
    previous = smoke.ROOT, smoke.BUILD_ROOT
    try:
        smoke.ROOT, smoke.BUILD_ROOT = ROOT, BUILD_ROOT
        smoke.validate_provenance(manifest, records['report'], records['candidate'], hashes['report'])
    finally:
        smoke.ROOT, smoke.BUILD_ROOT = previous
    manifest_raw = encoded(manifest)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    command = ['python3', '-B', linux_path(WORK / 'qualify_skycache_trace.py'), '--manifest',
               linux_path(manifest_path), '--manifest-sha256', manifest_sha]
    OUTPUT.mkdir()
    def preserve(path, raw):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
    for key, raw in raw_records.items():
        relative = RECORDS[key] if key in RECORDS else 'prior/' + PRIOR_RECORDS[key]
        preserve(OUTPUT / 'inputs' / relative, raw)
    preserve(OUTPUT / 'inputs/bundle-v3.json', bundle_raw)
    for name, raw in helper_bytes.items():
        preserve(OUTPUT / 'helpers' / name, raw)
    preserve(manifest_path, manifest_raw)
    preserve(command_path, encoded(command))
    preserve(OUTPUT / manifest_path.name, manifest_raw)
    preserve(OUTPUT / command_path.name, encoded(command))
    preparation = {'schema_version': 1, 'input_sha256': hashes, 'helper_sha256': helper_hashes,
        'manifest_sha256': manifest_sha, 'command_sha256': sha(command_path), 'candidate': candidate,
        'source_identity': manifest['source_identity'], 'run_id': 'm1-trace-acceptance-01', 'timeout_seconds': 1800,
        'runtime_read': False, 'launched': False, 'milestone_verdict': None,
        'archive_sha256': records['archive']['archive_sha256']}
    preserve(OUTPUT / 'preparation.json', encoded(preparation))
    wrapper = E / 'harness/run_auxiliary.ps1'
    readme = ('# Frozen real-trace smoke preparation\n\n'
        'Source build03 reused the declared build02 state after an exact two-file repair. Both source trees, preserved prior evidence, '
        'the stable candidate, complete13-suite BEP and unsignalled guard completion were verified from C-backed archives. '
        'The runtime launcher rehashes the candidate before use. This preparation did not read runtime storage or launch a command.\n\n'
        f'Manifest SHA256: `{manifest_sha}`. Candidate SHA256: `{candidate["sha256"]}`.\n\n'
        'The coordinator can launch the existing guarded wrapper once:\n\n```powershell\n'
        f"& '{wrapper}' -CommandJson '{command_path}' -RunId 'm1-trace-acceptance-01' -TimeoutSeconds 1800\n```\n\n"
        'Passing proves functional Java trace/replay interoperability and fixture isolation only; it is not a performance or M1 gate verdict.\n')
    preserve(OUTPUT / 'README.md', readme.encode())
    print(json.dumps({'manifest': str(manifest_path), 'manifest_sha256': manifest_sha,
                      'command': str(command_path), 'prepared': str(OUTPUT), 'launched': False}))


if __name__ == '__main__':
    main()
