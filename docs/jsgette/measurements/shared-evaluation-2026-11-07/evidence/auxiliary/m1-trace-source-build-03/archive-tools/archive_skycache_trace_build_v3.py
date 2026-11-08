#!/usr/bin/env python3
"""Archive one ended trace-source qualification; never launches or mutates runtime."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

import build_skycache_trace_v3 as qualification

WORK = Path(__file__).resolve().parent
Q, S = qualification.ROOT, qualification.S
RUN_ID = 'm1-trace-source-build-03'
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary' / RUN_ID
BUNDLE = WORK / 'trace-build-preparation/bundle-v3/bundle.json'
SUCCESS = 'build_and_focused_tests_passed_no_gate_verdict'
require, sha = qualification.require, qualification.sha


def write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)


def json_bytes(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def bounded(path, limit=4 * 1024**2):
    require(path.resolve() == path and path.is_file() and not path.is_symlink()
            and path.stat().st_size <= limit, 'Invalid bounded evidence file: ' + str(path))
    return path.read_bytes()


def validate_guard(guard, identity):
    final = guard['final_scope_cleanup']
    require(guard['status'] in ('completed', 'command_failed') and final['verified'] is True
            and final['present'] is False and final['signalled'] is False,
            'Requires completed guard and verified unsignalled scope absence')
    require(re.fullmatch('shared-eval-guard-' + RUN_ID + r'-[0-9a-f]{32}\.scope', identity['Id'])
            and identity['Id'] == guard['scope']['unit']
            and identity['InvocationID'] == guard['scope']['invocation_id'], 'Guard identity mismatch')
    group = identity['ControlGroup']
    require(group.startswith('/') and '..' not in Path(group).parts and group.endswith('/' + identity['Id']),
            'Invalid guard cgroup')
    return Path('/sys/fs/cgroup') / group.lstrip('/')


def expected_commands():
    return qualification.commands()


def validate_commands(report, passed):
    expected, seen = expected_commands(), set()
    for row in report['commands']:
        name = row['name']
        require(name in expected and name not in seen and row['argv'] == expected[name]
                and row['cwd'] == str(S / 'source'),
                'Wrong/duplicate qualification command')
        seen.add(name)
        require(not passed or row.get('returncode') == 0, 'Qualified command did not succeed')
    require(not passed or seen == set(expected), 'Successful qualification lacks all four commands')
    require(not passed or [row['name'] for row in report['commands']] == ['version', 'build', 'tests', 'shutdown'],
            'Successful qualification commands are out of order')


def bep_evidence(path, *, tests=False):
    """Current BEP, not possibly cached log text, is test-result authority."""
    require(path.stat().st_size <= 64 * 1024**2, 'BEP exceeds archive bound')
    starts, finishes, summaries, outputs = [], [], {}, {}
    with path.open('rb') as stream:
        for index, line in enumerate(stream):
            require(index < 500000 and len(line) <= 1024**2, 'BEP record bound exceeded')
            row = json.loads(line)
            if 'started' in row:
                starts.append(row['started']['uuid'])
            if 'finished' in row:
                finishes.append(row['finished']['exitCode'])
            if 'testSummary' in row:
                label = row['id']['testSummary']['label']
                require(label in qualification.TESTS and label not in summaries, 'Unexpected/duplicate test summary')
                summaries[label] = row['testSummary']
            if 'testResult' in row:
                label = row['id']['testResult']['label']
                require(label in qualification.TESTS, 'Unexpected test-result target')
                target = label[2:].replace(':', '/')
                directory = S / 'build-output/execroot/_main/bazel-out/k8-fastbuild/testlogs' / target
                for output in row['testResult'].get('testActionOutput', []):
                    if output['name'] not in ('test.log', 'test.xml'):
                        continue
                    uri = urlparse(output.get('uri', ''))
                    require(uri.scheme == 'file' and not uri.netloc and not uri.query and not uri.fragment,
                            'Unexpected test-output URI')
                    file = Path(url2pathname(uri.path))
                    require(file.is_absolute() and file.is_relative_to(directory), 'Test output leaves named target')
                    relative = file.relative_to(directory)
                    require(relative.name == output['name'] and len(relative.parts) <= 4
                            and all(re.fullmatch(r'(?:shard_\d+_of_\d+|run_\d+_of_\d+|attempt_\d+)', part)
                                    for part in relative.parts[:-1]), 'Unexpected test-output path')
                    outputs[str(file)] = {'path': file, 'label': label}
                    require(len(outputs) <= 512, 'Test-output count exceeds bound')
    success = len(starts) == len(finishes) == 1 and finishes[0].get('code', 0) == 0 and finishes[0].get('name') == 'SUCCESS'
    if tests:
        success = success and set(summaries) == set(qualification.TESTS) and all(
            row.get('overallStatus') == 'PASSED' for row in summaries.values())
    return {'success': success, 'command_uuids': starts, 'finished': finishes,
            'test_summaries': summaries}, list(outputs.values())


def candidate_record(report):
    row = report['candidate']
    path, built = Path(row['destination']), Path(row['source'])
    require(path == Q / 'candidate/bazel-skycache-trace' and built.is_relative_to(S / 'build-output')
            and type(row['bytes']) is int and 0 < row['bytes'] <= 512 * 1024**2,
            'Wrong candidate location/size')
    require(re.fullmatch('[0-9a-f]{64}', row['sha256']) and row['sha256'] ==
            report['product_binary_sha256_after_build'] == report['product_binary_sha256_after_tests'],
            'Candidate bytes were not stable through focused tests')
    return path, built


def verify_generated_inputs(report):
    result = {}
    for name, field in (('build.bazelrc', 'generated_rc_sha256'), ('seed-copy.json', 'seed_copy_sha256')):
        value = sha(Q / name)
        require(value == report[field], 'Recorded generated input changed: ' + name)
        result[name] = value
    return result


def verify_reuse(report):
    prior, metadata, old_proof = qualification.read_prior()
    preserved = qualification.preserved_files(prior, metadata)
    require(report['prior_report'] == {'path': str(qualification.OLD_EVIDENCE / 'report.json'),
                                      'sha256': qualification.PRIOR_REPORT_SHA}
            and report['prior_archive_sha256'] == qualification.PRIOR_ARCHIVE_SHA
            and report['prior_metadata_sha256'] == qualification.PRIOR_METADATA_SHA
            and report['source_before_patch'] == old_proof['current']
            and report['preserved_prior_files'] == report['preserved_prior_files_after'] == preserved,
            'Prior source/evidence reuse receipt differs')
    require(all(report[key] == prior[key] for key in ('environment', 'generated_rc_sha256', 'seed_copy_sha256',
                                                     'configuration_sha256', 'bootstrap')),
            'Reused build configuration differs')
    require(sha(Q / 'prior-report.json') == qualification.PRIOR_REPORT_SHA
            and json.loads(bounded(Q / 'prior-source-verification.json')) == old_proof
            and sha(Q / 'bundle.json') == report['bundle_sha256'], 'Copied prior/bundle evidence differs')
    old = json.loads(bounded(S / 'source-members.json', 16 * 1024**2))
    new = json.loads(bounded(Q / 'source-members.json', 16 * 1024**2))
    changes = qualification.member_delta(old, new)
    qualification.validate_lineage(prior, report['bundle'])
    require(changes == report['repair'], 'Declared two-file repair lineage differs')
    for key in ('prior_server_absence', 'prior_server_absence_before_patch'):
        receipt = report[key]
        require(receipt['pid_file'] == str(S / 'build-output/server/server.pid.txt')
                and receipt['verified'] is True and receipt['live'] is False
                and (receipt['pid'] is None or type(receipt['pid']) is int and 0 < receipt['pid'] < 2**31),
                'Prior build-server absence proof differs')
    for row in changes:
        for folder, key in (('source-before', 'old'), ('source-after', 'new')):
            path = Q / folder / row['path']
            raw = bounded(path, 256 * 1024)
            require(len(raw) == row[key]['bytes'] and hashlib.sha256(raw).hexdigest() == row[key]['sha256']
                    and hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row[key]['git_blob'],
                    'Archived repair preimage/postimage differs')
    return {'verified': True, 'prior_report_sha256': qualification.PRIOR_REPORT_SHA,
            'prior_archive_sha256': qualification.PRIOR_ARCHIVE_SHA, 'preserved_prior_files': preserved,
            'build_state_root': str(S), 'source_root': str(S / 'source'), 'repair': changes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher-sha256', required=True)
    parser.add_argument('--bundle-sha256', required=True)
    args = parser.parse_args()
    require(all(re.fullmatch('[0-9a-f]{64}', value) for value in vars(args).values()), 'Invalid frozen input digest')
    for name in ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json', 'source-verification.json',
                 'candidate-verification.json', 'test-verification.json'):
        require(not (E / name).exists() and not (E / name).is_symlink(), 'Archive output already exists')
    guard_path, identity_path = E / 'guard/result.json', E / 'guard/scope-identity.json'
    guard, identity = json.loads(bounded(guard_path)), json.loads(bounded(identity_path))
    cgroup = validate_guard(guard, identity)
    require(not cgroup.exists(), 'Qualification scope still exists')
    command = ['python3', '-B', str(WORK / 'build_skycache_trace_v3.py'), '--bundle', str(BUNDLE),
               '--bundle-sha256', args.bundle_sha256]
    require(json.loads(bounded(E / 'command.json')) == command and
            json.loads(bounded(E / 'guard/policy.json'))['command_sha256'] == sha(E / 'command.json'),
            'Guard command provenance differs')
    require(sha(Path(qualification.__file__).resolve()) == args.launcher_sha256 and
            sha(Path(qualification.build.__file__).resolve()) == qualification.BUILD_HELPER and
            sha(Path(qualification.previous.__file__).resolve()) == qualification.PREVIOUS_HELPER_SHA and
            sha(BUNDLE) == args.bundle_sha256, 'Frozen qualification inputs changed')
    bundle = json.loads(bounded(BUNDLE))
    qualification.validate_bundle(bundle)
    raw = bounded(Q / 'report.json')
    report = json.loads(raw)
    require(report['root'] == str(Q) and report['build_state_root'] == str(S)
            and report['source_root'] == str(S / 'source') and report['reused_build_state'] is True
            and report['bundle'] == bundle and
            report['bundle_sha256'] == args.bundle_sha256 and report['status'] in ('failed', SUCCESS),
            'Report/bundle identity or completion differs')
    passed = report['status'] == SUCCESS
    require((guard['status'] == 'completed' and guard['exit_code'] == 0) if passed else
            (guard['status'] == 'command_failed' and guard['exit_code'] != 0), 'Report/guard outcome differs')
    require(report['scope_before']['name'] == identity['ControlGroup'] and
            (report.get('scope_after', {}).get('name') == identity['ControlGroup'] or not passed
             and 'scope_after' not in report), 'Qualification scope binding differs')
    expected_helpers = {str(WORK / 'build_skycache_trace_v3.py'): args.launcher_sha256,
                        str(Path(qualification.build.__file__)): qualification.BUILD_HELPER,
                        str(Path(qualification.previous.__file__)): qualification.PREVIOUS_HELPER_SHA}
    require(report['helper_sha256'] == expected_helpers, 'Qualification helper provenance differs')
    validate_commands(report, passed)
    files = {'report.json': Q / 'report.json', 'provenance/bundle.json': BUNDLE,
             'provenance/command.json': E / 'command.json', 'provenance/guard-result.json': guard_path,
             'provenance/scope-identity.json': identity_path, 'provenance/guard-policy.json': E / 'guard/policy.json',
             'provenance/build_skycache_trace_v3.py': Path(qualification.__file__).resolve(),
             'provenance/build_skycache_trace_v2.py': Path(qualification.previous.__file__).resolve()}
    missing = []
    for name in ('version.log', 'build.log', 'tests.log', 'shutdown.log', 'build.bep.jsonl', 'tests.bep.jsonl',
                 'source-members.json', 'instrumentation.patch', 'combined.patch', 'build.bazelrc', 'seed-copy.json',
                 'bundle.json', 'prior-report.json', 'prior-source-verification.json'):
        if (Q / name).is_file():
            files[name] = Q / name
        else:
            missing.append(name)
    for folder in ('source-before', 'source-after'):
        for relative in qualification.ALLOWED:
            name = folder + '/' + relative
            if (Q / name).is_file():
                files[name] = Q / name
            else:
                missing.append(name)
    require(not passed or not missing, 'Successful qualification lacks mandatory evidence')
    source_proof = {'verified': False, 'qualification_passed': passed,
                    'scope': 'Exact declared Git files; generated build extras are outside this proof.'}
    try:
        generated = verify_generated_inputs(report)
        reuse = verify_reuse(report)
        for key, name in (('source_members', 'source-members.json'),
                          ('instrumentation_patch', 'instrumentation.patch'), ('combined_patch', 'combined.patch')):
            require((Q / name).stat().st_size == bundle[key]['bytes'] and sha(Q / name) == bundle[key]['sha256'],
                    'Runtime copied source artifact differs: ' + name)
        members = json.loads(bounded(Q / 'source-members.json', 16 * 1024**2))
        current = qualification.verify_source(S / 'source', members)
        require(current == report['source_before'] == report['source_after'] == report['source_after_patch'],
                'Current/qualified source differs')
        source_proof.update(verified=True, current=current, generated_input_sha256=generated,
                            incremental_reuse=reuse,
                            source_archive=bundle['source_archive'],
                            instrumented_git_tree=bundle['instrumented_git_tree'])
    except Exception as error:
        source_proof['error'] = str(error)
        require(not passed, 'Successful qualification lacks source proof: ' + str(error))
    test_proof, named_counts = {'verified': False, 'qualification_passed': passed}, {}
    try:
        build_bep, _ = bep_evidence(Q / 'build.bep.jsonl')
        tests_bep, outputs = bep_evidence(Q / 'tests.bep.jsonl', tests=True)
        test_proof.update(build=build_bep, tests=tests_bep)
        for row in outputs:
            path = row['path']
            if path.is_file():
                files['testlogs/' + path.relative_to(S / 'build-output/execroot/_main/bazel-out/k8-fastbuild/testlogs').as_posix()] = path
                named_counts[row['label']] = named_counts.get(row['label'], 0) + 1
            else:
                missing.append(str(path))
        verified = build_bep['success'] and tests_bep['success'] and set(named_counts) == set(qualification.TESTS)
        require(not passed or verified and not missing, 'Missing successful BEP/test-result evidence')
        test_proof['verified'] = verified
    except Exception as error:
        test_proof['error'] = str(error)
        require(not passed, 'Successful qualification lacks test evidence: ' + str(error))
    candidate = {'verified': False, 'qualification_passed': passed}
    try:
        path, built = candidate_record(report)
        require(all(p.resolve() == p and p.is_file() and not p.is_symlink() for p in (path, built))
                and path.stat().st_size == built.stat().st_size == report['candidate']['bytes']
                and sha(path) == sha(built) == report['candidate']['sha256'], 'Current candidate bytes differ')
        candidate.update(verified=True, path=str(path), sha256=sha(path), bytes=path.stat().st_size)
    except Exception as error:
        candidate['error'] = str(error)
        require(not passed, 'Successful qualification lacks candidate proof: ' + str(error))
    members = []
    for name, path in sorted(files.items()):
        require(path.resolve() == path and path.is_file() and not path.is_symlink()
                and (path.is_relative_to(Q) or path.is_relative_to(S) or path.is_relative_to(WORK))
                and path.stat().st_size <= 64 * 1024**2, 'Unexpected/oversized archive member')
        members.append({'path': name, 'source': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(len(members) <= 560 and sum(row['bytes'] for row in members) <= 256 * 1024**2,
            'Evidence archive exceeds bound')
    archive_path = E / 'runtime.tar.gz'
    with archive_path.open('xb') as out, gzip.GzipFile(fileobj=out, mode='wb', filename='', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|') as archive:
            for row in members:
                archive.add(files[row['path']], arcname=row['path'], recursive=False)
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(archive.getnames() == [row['path'] for row in members], 'Archive membership differs')
        for row in members:
            with archive.extractfile(row['path']) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256'] and
                        archive.getmember(row['path']).size == row['bytes'], 'Archived evidence changed')
    report_sha = hashlib.sha256(raw).hexdigest()
    require(report_sha == sha(Q / 'report.json') and not cgroup.exists(), 'Runtime report/scope changed during archive')
    write_new(E / 'report.json', raw)
    write_new(E / 'source-verification.json', json_bytes(source_proof))
    write_new(E / 'test-verification.json', json_bytes(test_proof))
    candidate.update(report_sha256=report_sha, guard_result_sha256=sha(guard_path),
        scope_identity_sha256=sha(identity_path), final_scope_absent=True, final_scope_unsignalled=True,
        source_verification_sha256=sha(E / 'source-verification.json'),
        test_verification_sha256=sha(E / 'test-verification.json'))
    write_new(E / 'candidate-verification.json', json_bytes(candidate))
    metadata = {'schema_version': 1, 'passed': passed, 'report_status': report['status'],
        'archive_sha256': sha(archive_path), 'archive_bytes': archive_path.stat().st_size, 'members': members,
        'missing_files': missing, 'named_target_files': named_counts, 'candidate': candidate,
        'current_source_verification': source_proof, 'test_verification': test_proof,
        'bundle_sha256': args.bundle_sha256, 'launcher_sha256': args.launcher_sha256,
        'helper_sha256': sha(Path(__file__)), 'source_archive_excluded': bundle['source_archive'],
        'build_state_root': str(S), 'source_root': str(S / 'source'), 'reused_build_state': True,
        'candidate_binary_excluded': report.get('candidate'), 'runtime_preserved': True,
        'milestone_verdict': None}
    write_new(E / 'runtime.tar.gz.json', json_bytes(metadata))
    print(json.dumps({'passed': passed, 'archive_sha256': metadata['archive_sha256'], 'members': len(members)}))


if __name__ == '__main__':
    main()
