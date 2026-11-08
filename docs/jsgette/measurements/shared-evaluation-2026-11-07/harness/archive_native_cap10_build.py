#!/usr/bin/env python3
"""Capture this cap-10 qualification after guard exit; no builds or runtime writes.

Fixed phase files, five declared source paths, and four named targets only.
No recursive source/cache walks, raw JVM logs, retries, or existing-output writes.
"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

import build_native_cap10 as qualification
from archive_native_requalification import TARGETS, json_bytes, require, sha, write_new

R, S, Q, Q2 = qualification.build.RUNTIME, qualification.S, qualification.Q, qualification.Q2
WORK = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work')
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary/native-cap10-build-01'
PRODUCT = '5c2d5bc56edbba9bdba7cc797658d2facb4d661e'
PARENT = '9526272f1a13ca5de8dbed33b1d22824bcf0e957'
PRODUCT_TREE = 'c94711de5a859024631bf7e2685ac1300fe7aaf6'
PATCH_SHA = '05eeccdc12ae4dfe08d350688dd4b4bc2701fe26a155cdda9f59faa422aa16c2'
LAUNCHER_SHA = 'b7a8f0461700c09e6d59e0421e4f2c4e0d4e31fc7a2812df2856e88bf4aa882e'
GIT_DIR = '/mnt/c/Users/jgett/Documents/Codex/2026-10-31/start-contemplating-a-new-design-based/bazel-source/.git'
FILE_PINS = dict(zip(qualification.FILES, (
    ('4df666a7a92a3f2041b998889fd9ca66da84f7fca3b2df091069f0beeb030d70',
     'bf9d93b170a319df772e4841187bd6a030a9e85b740abd2026a1c46cb4bcf166', 43449, 43689),
    ('4a62670aae20a454490d6a18d6236cf2bb2cd2073b05024db882d5e2dd886054',
     'bb4bce31cdb51ad191cba4ff81c68995244da9ff9187871774dabfcf2657703c', 31219, 31307),
    ('e24c226c20ef102642ff15d48077c765f424075b4efaff71dade2086e281df96',
     '2e190903cf355acdf0a1aaef3207effb69dbf60c24bf3b2d507da0e53d98d8d9', 70754, 74107),
    ('8e7b15ce6d19f1b26cc2c6b0cb97962027f2b3ceda8d8b9c9eedf0f920c4eef9',
     '4bd5bd8cbabbcf2fa33539ce896dd150ed3fda4619af1bbb231baf259db2c047', 4377, 5099),
)))


def validate_guard(guard, identity):
    require(guard['status'] in ('completed', 'command_failed'), 'Guard has not completed normally')
    final = guard['final_scope_cleanup']
    require(final['verified'] is True and final['present'] is False and final['signalled'] is False,
            'Requires verified unsignalled scope absence')
    require(re.fullmatch(r'shared-eval-guard-native-cap10-build-01-[0-9a-f]{32}\.scope', identity['Id']) and
            identity['Id'] == guard['scope']['unit'] and
            identity['InvocationID'] == guard['scope']['invocation_id'], 'Guard run identity mismatch')
    group = identity['ControlGroup']
    require(group.startswith('/') and '..' not in Path(group).parts and
            group.endswith('/' + identity['Id']), 'Unexpected scope path')
    return Path('/sys/fs/cgroup') / group.lstrip('/')


def validate_patch(record):
    require((record['commit'], record['parent'], record['commit_tree'], record['sha256']) ==
            (PRODUCT, PARENT, PRODUCT_TREE, PATCH_SHA), 'Wrong committed cap-10 patch')
    require(len(record['files']) == 4 and {row['path'] for row in record['files']} == set(FILE_PINS),
            'Wrong cap-10 file set')
    for row in record['files']:
        require(tuple(row[key] for key in ('old_sha256', 'new_sha256', 'old_bytes', 'new_bytes')) ==
                FILE_PINS[row['path']], 'Wrong committed file bytes: ' + row['path'])


def main():
    outputs = ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json', 'candidate-verification.json',
               'source-verification.json')
    require(all(not (E / name).exists() and not (E / name).is_symlink() for name in outputs),
            'Archive output exists; refusing overwrite')
    guard_path, identity_path = E / 'guard/result.json', E / 'guard/scope-identity.json'
    guard, identity = json.loads(guard_path.read_text()), json.loads(identity_path.read_text())
    cgroup = validate_guard(guard, identity)
    require(not cgroup.exists(), 'Scope remains present; do not read active runtime')
    policy_path = E / 'guard/policy.json'
    require(json.loads(policy_path.read_text())['command_sha256'] == sha(E / 'command.json'),
            'Executed command snapshot mismatch')
    expected_command = ['python3', '-B', str(WORK / 'build_native_cap10.py'), '--patch-file',
                        str(WORK / 'native-cap10-product.patch'), '--patch-sha256', PATCH_SHA,
                        '--product-commit', PRODUCT, '--source-git-dir', GIT_DIR]
    require(json.loads((E / 'command.json').read_text()) == expected_command, 'Wrong qualification command')
    require(sha(Path(qualification.__file__).resolve()) == LAUNCHER_SHA, 'Executed launcher changed')
    require(all(path.resolve() == path for path in (R, S, Q, Q2)), 'Runtime path alias')
    raw_report = (Q / 'report.json').read_bytes()
    require(len(raw_report) <= 2 * 1024**2, 'Report exceeds bound')
    report = json.loads(raw_report)
    helper_dir = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/harness'
    helper_pins = {str(WORK / 'build_native_cap10.py'): LAUNCHER_SHA,
                   str(helper_dir / 'native_source_build.py'):
                   'c1180cb697231a169ed55b0d8fa3d7d66165c842a3c66d41fc133c9046acfc5a',
                   str(helper_dir / 'requalify_native_cap2.py'):
                   '38cb8e55f08d8bb30137217dcb179417e5ad6cfa310cd57ed2a89deba4b0f7f9'}
    require(report['helper_sha256'] == helper_pins and
            all(sha(Path(path)) == value for path, value in helper_pins.items()), 'Qualification helper changed')
    require(sha(Q2 / 'report.json') == qualification.PRIOR_SHA, 'Prior qualification report changed')
    prior = json.loads((Q2 / 'report.json').read_text())
    require(sha(S / 'report.json') == prior['prior_report']['sha256'], 'Original build report changed')
    qualification.validate_prior(prior, json.loads((S / 'report.json').read_text()))
    require(report['root'] == str(Q) and report['build_state_root'] == str(S) and
            report['prior_report'] == {'path': str(Q2 / 'report.json'), 'sha256': qualification.PRIOR_SHA},
            'Wrong report roots/prior identity')
    require(report['source_commit'] == qualification.build.COMMIT and
            report['source_tree'] == qualification.build.TREE and
            report['source_archive_sha256'] == qualification.ARCHIVE_SHA == sha(S / 'source.tar') and
            report['source_is_base_plus_qualification_test_and_cap10_patch'] is True and
            report['base_admission_cap'] == 2 and report['admission_cap'] == 10,
            'Wrong base-plus-patches source identity')
    require(report['qualification_test_patch'] == prior['qualification_test_patch'], 'Idle-test patch changed')
    require(report['scope_before']['name'] == report['scope_after']['name'] == identity['ControlGroup'],
            'Report is not bound to this external scope')
    require(report['status'] in ('failed', qualification.SUCCESS), 'Incomplete runtime report')
    passed = report['status'] == qualification.SUCCESS
    require((guard['status'] == 'completed' and guard['exit_code'] == 0) if passed else
            (guard['status'] == 'command_failed' and guard['exit_code'] != 0), 'Report/guard outcome mismatch')
    if 'cap10_patch' in report:
        validate_patch(report['cap10_patch'])
    if passed:
        validate_patch(report['cap10_patch'])
        expected = qualification.previous.requalification_commands(S, Q)
        apply = ['git', '-c', 'core.autocrlf=false', 'apply', '--whitespace=error-all']
        expected.update({'patch-check': apply + ['--check', str(Q / 'product.patch')],
                         'patch-apply': apply + [str(Q / 'product.patch')]})
        for name in ('patch-check', 'patch-apply', 'build', 'tests', 'shutdown'):
            rows = [row for row in report['commands'] if row['name'] == name]
            require(len(rows) == 1 and rows[0]['returncode'] == 0 and rows[0]['argv'] == expected[name],
                    'Missing successful exact command: ' + name)
    files, missing = {'report.json': Q / 'report.json'}, []
    for name in ('patch-check.log', 'patch-apply.log', 'build.log', 'tests.log', 'shutdown.log',
                 'build.bep.jsonl', 'tests.bep.jsonl', 'product.patch'):
        path = Q / name
        if path.is_file():
            files[name] = path
        else:
            missing.append(str(path))
    if 'product.patch' in files:
        require(sha(files['product.patch']) == PATCH_SHA, 'Captured patch changed')
    pins = {name: (old, new) for name, (old, new, _, _) in FILE_PINS.items()}
    pins[qualification.previous.TEST] = (qualification.NEW_TEST, qualification.NEW_TEST)
    overrides = {}
    for name, (old, new) in pins.items():
        for directory, expected in (('source-before', old), ('source-after', new)):
            path = Q / directory / name
            if path.is_file():
                require(sha(path) == expected, 'Declared source copy changed: ' + str(path))
                files[directory + '/' + name] = path
                if directory == 'source-after':
                    overrides[name] = path.read_bytes()
            else:
                missing.append(str(path))
    if passed:
        require(not missing, 'Successful run lacks required phase/source evidence')
    receipt = {'verified': False, 'qualification_passed': passed,
               'scope': 'Current archived source members; generated extras are outside this proof'}
    try:
        require(set(overrides) == set(pins), 'Not all declared source copies are available')
        current = qualification.verify_source(S / 'source.tar', S / 'source', overrides)
        require(current == report['source_final'] == report['source_after_patch'],
                'Current source differs from final qualification receipt')
        require(report['source_before']['override_paths'] == [qualification.previous.TEST] and
                report['source_before']['tracked_content_sha256'] ==
                prior['qualified_source_verification']['tracked_content_sha256'] and
                current['tracked_content_sha256'] != report['source_before']['tracked_content_sha256'],
                'Source-before identity is not the preserved cap-2 source')
        receipt.update(verified=True, current=current)
    except Exception as error:
        receipt['error'] = str(error)
        require(not passed, 'Successful qualification lacks current full-source verification: ' + str(error))
    require(sha(Q2 / 'candidate/bazel-native-cap2') == qualification.OLD_BINARY,
            'Preserved cap-2 candidate changed')
    testlogs = S / 'build-output/execroot/_main/bazel-out/k8-fastbuild/testlogs'
    target_counts = {}
    for target in TARGETS:
        count = 0
        for suffix in ('', 'shard_1_of_3', 'shard_2_of_3', 'shard_3_of_3'):
            for name in ('test.log', 'test.xml'):
                path = testlogs / target / suffix / name
                if path.is_file():
                    files['testlogs/' + path.relative_to(testlogs).as_posix()] = path
                    count += 1
        target_counts[target] = count
    require(not passed or all(target_counts.values()), 'Successful run lacks named target logs')
    candidate = {'verified': False, 'qualification_passed': passed}
    if 'candidate' in report:
        path = Path(report['candidate']['destination'])
        built = Path(report['candidate']['source'])
        require(path == Q / 'candidate/bazel-native-cap10' and path.resolve() == path and path.is_file() and
                built.resolve() == built and built.is_relative_to(S / 'build-output') and built.is_file() and
                path.stat().st_size == report['candidate']['bytes'] <= 512 * 1024**2, 'Wrong candidate location/size')
        value = sha(path)
        require(value == sha(built) == report['candidate']['sha256'] ==
                report['product_binary_sha256_after_build'] == report['product_binary_sha256_after_tests'] and
                value != qualification.OLD_BINARY == report['product_binary_sha256_before'],
                'Candidate is not stable changed product bytes')
        candidate.update(verified=True, path=str(path), sha256=value, bytes=path.stat().st_size)
    require(not passed or candidate['verified'], 'Successful qualification lacks a verified candidate')
    members = []
    for name, path in sorted(files.items()):
        require(path.is_file() and not path.is_symlink() and path.resolve() == path and
                (path.is_relative_to(Q) or path.is_relative_to(S)), 'Unexpected archive source')
        require(path.stat().st_size <= 64 * 1024**2, 'Member exceeds64MiB')
        members.append({'path': name, 'source': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(len(members) <= 64 and sum(row['bytes'] for row in members) <= 256 * 1024**2,
            'Archive exceeds member/byte bound')
    archive_path = E / 'runtime.tar.gz'
    with archive_path.open('xb') as destination:
        with gzip.GzipFile(fileobj=destination, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as archive:
                for row in members:
                    archive.add(files[row['path']], arcname=row['path'], recursive=False)
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(archive.getnames() == [row['path'] for row in members], 'Archive member list changed')
        for row in members:
            with archive.extractfile(row['path']) as stream:
                value = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(value == row['sha256'] and archive.getmember(row['path']).size == row['bytes'],
                    'Archive member verification failed: ' + row['path'])
    report_sha = hashlib.sha256(raw_report).hexdigest()
    require(report_sha == next(row['sha256'] for row in members if row['path'] == 'report.json'),
            'Report changed during capture')
    require(not cgroup.exists(), 'Scope reappeared during capture')
    write_new(E / 'report.json', raw_report)
    write_new(E / 'source-verification.json', json_bytes(receipt))
    candidate.update(report_sha256=report_sha, guard_result_sha256=sha(guard_path),
                     scope_identity_sha256=sha(identity_path), final_scope_absent=True,
                     final_scope_unsignalled=True, source_verification_sha256=sha(E / 'source-verification.json'))
    write_new(E / 'candidate-verification.json', json_bytes(candidate))
    metadata = {'schema_version': 1, 'archive_sha256': sha(archive_path),
                'archive_bytes': archive_path.stat().st_size, 'passed': passed, 'report_status': report['status'],
                'members': members, 'missing_optional_files': missing, 'named_target_files': target_counts,
                'candidate': candidate, 'current_source_verification': receipt,
                'prior_report_sha256': qualification.PRIOR_SHA, 'preserved_cap2_candidate_sha256': qualification.OLD_BINARY,
                'source_commit': report['source_commit'], 'source_tree': report['source_tree'],
                'source_archive_sha256': report['source_archive_sha256'],
                'cap10_patch': report.get('cap10_patch'), 'qualification_test_patch': report['qualification_test_patch'],
                'command_sha256': sha(E / 'command.json'), 'guard_policy_sha256': sha(policy_path),
                'helper_sha256': sha(Path(__file__).resolve()), 'launcher_sha256': LAUNCHER_SHA,
                'scope': 'Fixed phase files, five explicit before/after source paths, four named targets test.log/XML only',
                'testlog_freshness': 'Reused build-state logs can be cached/preexisting; current tests BEP is authoritative.',
                'runtime_preserved': True}
    write_new(E / 'runtime.tar.gz.json', json_bytes(metadata))
    print(json.dumps({'archive_sha256': metadata['archive_sha256'], 'members': len(members),
                      'passed': passed, 'copied_report': str(E / 'report.json'), 'candidate': candidate}))


if __name__ == '__main__':
    main()
