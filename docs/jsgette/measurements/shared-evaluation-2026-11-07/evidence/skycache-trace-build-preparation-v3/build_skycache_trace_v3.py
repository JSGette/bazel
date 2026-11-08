#!/usr/bin/env python3
"""One declared two-file repair and incremental source qualification, never a benchmark."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

import build_skycache_trace_v2 as previous

WORK = Path(__file__).resolve().parent
R, S = previous.R, previous.ROOT
ROOT = R / 'm1-skycache-trace-qualification-v3'
OLD_EVIDENCE = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary/m1-trace-source-build-02'
PRIOR_REPORT_SHA = '18a7323a9e740dd8f4c6e150d0ac9a303b78163e09f5c883769e89d80587e0f5'
PRIOR_METADATA_SHA = '487999d884358d7fe8f432b4d4e2822cf4ff95acdb9c22ba5f7e0c374a836b9a'
PRIOR_ARCHIVE_SHA = 'a248a9c322313f11f384e4f33bd9402ab66da7997e2b76cc401b4c2e1ee66c9a'
PREVIOUS_HELPER_SHA = '6b2fcf351d623044dec4b3238c248754760bee66d293032dfdc3ac1384653238'
PREVIOUS_TREE = 'd9ca5e8f8e38bdd6c18405f85e1b6d2edabe7889'
ALLOWED = ('src/main/java/com/google/devtools/build/lib/skyframe/M1TraceModule.java',
           'src/test/java/com/google/devtools/build/lib/skyframe/M1TraceModuleTest.java')
PRESERVED = ('report.json', 'version.log', 'build.log', 'shutdown.log', 'build.bep.jsonl',
             'source-members.json', 'instrumentation.patch', 'combined.patch', 'build.bazelrc', 'seed-copy.json')
SUCCESS = 'build_and_focused_tests_passed_no_gate_verdict'
build, TESTS, BUILD_HELPER = previous.build, previous.TESTS, previous.BUILD_HELPER
BASE, BASELINE_TREE, SAFE_PATCH = previous.BASE, previous.BASELINE_TREE, previous.SAFE_PATCH
require, sha, save = previous.require, previous.sha, previous.save
validate_bundle, verify_source, read_document = previous.validate_bundle, previous.verify_source, previous.read_document


def commands():
    saved = build.TESTS
    try:
        build.TESTS = TESTS
        result = build.commands(S)
    finally:
        build.TESTS = saved
    for name in ('build', 'tests'):
        result[name] = [('--build_event_json_file=' + str(ROOT / (name + '.bep.jsonl')))
                        if arg == '--build_event_json_file=' + str(S / (name + '.bep.jsonl')) else arg
                        for arg in result[name]]
    return result


def validate_prior_records(prior, metadata, source, guard, identity):
    require(prior['root'] == str(S) and prior['status'] == 'failed'
            and [(row['name'], row.get('returncode')) for row in prior['commands']] ==
                [('version', 0), ('build', 1), ('shutdown', 0)], 'Unexpected prior build failure state')
    require(source['verified'] is True and source['qualification_passed'] is False
            and source['current'] == prior['source_before'] == prior['source_after']
            and source['current']['files_verified'] == 18592
            and metadata['current_source_verification'] == source and metadata['passed'] is False
            and metadata['archive_sha256'] == PRIOR_ARCHIVE_SHA, 'Prior full-source/archive proof differs')
    require(guard['status'] == 'command_failed' and guard['exit_code'] != 0
            and guard['final_scope_cleanup']['verified'] is True
            and guard['final_scope_cleanup']['present'] is False and guard['final_scope_cleanup']['signalled'] is False
            and identity['Id'] == guard['scope']['unit'] and identity['InvocationID'] == guard['scope']['invocation_id']
            and identity['Id'].startswith('shared-eval-guard-m1-trace-source-build-02-')
            and prior['scope_before']['name'] == prior['scope_after']['name'] == identity['ControlGroup'],
            'Prior guard identity/completion differs')


def read_prior():
    require(sha(OLD_EVIDENCE / 'report.json') == PRIOR_REPORT_SHA
            and sha(OLD_EVIDENCE / 'runtime.tar.gz.json') == PRIOR_METADATA_SHA
            and sha(OLD_EVIDENCE / 'runtime.tar.gz') == PRIOR_ARCHIVE_SHA, 'Preserved failed build evidence differs')
    prior = read_document(OLD_EVIDENCE / 'report.json', 4 * 1024**2)
    metadata = read_document(OLD_EVIDENCE / 'runtime.tar.gz.json', 4 * 1024**2)
    source = read_document(OLD_EVIDENCE / 'source-verification.json', 4 * 1024**2)
    guard = read_document(OLD_EVIDENCE / 'guard/result.json', 4 * 1024**2)
    identity = read_document(OLD_EVIDENCE / 'guard/scope-identity.json', 4 * 1024**2)
    rows = {row['path']: row for row in metadata['members']}
    require(len(rows) == len(metadata['members']), 'Duplicate prior archive members')
    for archive_name, local in (('report.json', 'report.json'), ('provenance/guard-result.json', 'guard/result.json'),
                                ('provenance/scope-identity.json', 'guard/scope-identity.json')):
        require(sha(OLD_EVIDENCE / local) == rows[archive_name]['sha256'], 'Prior archived input differs')
    validate_prior_records(prior, metadata, source, guard, identity)
    require(not (Path('/sys/fs/cgroup') / identity['ControlGroup'].lstrip('/')).exists(), 'Prior guard scope still exists')
    return prior, metadata, source


def preserved_files(prior, metadata):
    archived = {row['path']: row for row in metadata['members']}
    expected = {name: archived[name]['sha256'] for name in PRESERVED}
    expected['source.tar'] = prior['bundle']['source_archive']['sha256']
    for name, value in expected.items():
        require((S / name).resolve() == S / name and sha(S / name) == value, 'Preserved prior file changed: ' + name)
    return expected


def prior_server_absence(proc_root=Path('/proc')):
    path = S / 'build-output/server/server.pid.txt'
    require(path.resolve() == path and not path.is_symlink(), 'Prior server PID path is aliased')
    result = {'pid_file': str(path), 'verified': True, 'live': False, 'pid': None}
    if path.exists():
        require(path.is_file() and path.stat().st_size <= 32, 'Invalid prior server PID file')
        text = path.read_text(encoding='ascii').strip()
        require(text.isdecimal() and 0 < int(text) < 2**31, 'Invalid prior server PID')
        result['pid'] = int(text)
        require(not (proc_root / str(int(text))).exists(), 'Prior build server is still live; source repair refused')
    return result


def validate_lineage(prior, bundle):
    require(prior['bundle']['instrumented_git_tree'] == PREVIOUS_TREE
            and bundle['previous_instrumented_git_tree'] == PREVIOUS_TREE
            and bundle['previous_trace_source_sha256'] == prior['bundle']['trace_source_sha256']
            and len(bundle['repair_paths']) == len(ALLOWED) and set(bundle['repair_paths']) == set(ALLOWED)
            and bundle['instrumented_git_tree'] != PREVIOUS_TREE
            and all(bundle[key] == prior['bundle'][key] for key in
                    ('base_commit', 'baseline_git_tree', 'safe_subset_patch_sha256')),
            'Pinned predecessor tree/base differs')


def member_delta(old_rows, new_rows):
    old, new = ({row['path']: row for row in rows} for rows in (old_rows, new_rows))
    require(len(old) == len(old_rows) == len(new) == len(new_rows) == 18592 and set(old) == set(new),
            'Source member set/count changed')
    changed = sorted(name for name in old if old[name] != new[name])
    require(changed == sorted(ALLOWED), 'Only the two declared module/test repairs are allowed')
    result = []
    for name in changed:
        require(old[name]['mode'] == new[name]['mode'] == '100644'
                and old[name]['sha256'] != new[name]['sha256']
                and 0 < old[name]['bytes'] <= 256 * 1024 and 0 < new[name]['bytes'] <= 256 * 1024,
                'Repair mode/content/size differs')
        result.append({'path': name, 'old': old[name], 'new': new[name]})
    return result


def replacement_bytes(archive_path, changes):
    result = {}
    with tarfile.open(archive_path, 'r:') as archive:
        for row in changes:
            member = archive.getmember(row['path'])
            require(member.isfile() and member.size == row['new']['bytes'], 'Repair archive member differs')
            with archive.extractfile(member) as stream:
                data = stream.read(256 * 1024 + 1)
            require(len(data) == member.size and hashlib.sha256(data).hexdigest() == row['new']['sha256']
                    and hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['new']['git_blob'],
                    'Repair source bytes/blob differ')
            result[row['path']] = data
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--bundle-sha256', required=True)
    args = parser.parse_args()
    before = build.guard_snapshot()
    require(sha(Path(previous.__file__).resolve()) == PREVIOUS_HELPER_SHA
            and sha(Path(build.__file__).resolve()) == BUILD_HELPER and sha(args.bundle) == args.bundle_sha256,
            'Qualification inputs differ')
    bundle = read_document(args.bundle, 2 * 1024**2)
    validate_bundle(bundle)
    prior, metadata, old_proof = read_prior()
    require(R.resolve() == R and os.path.ismount(R) and S.resolve() == S
            and not ROOT.exists() and not ROOT.is_symlink(), 'Fresh owned qualification root required')
    require(shutil.disk_usage(R).free >= 10 * 1024**3, 'Less than 10 GiB task-volume space before qualification')
    server_absence = prior_server_absence()
    preserved = preserved_files(prior, metadata)
    old_rows = read_document(S / 'source-members.json', 16 * 1024**2)
    new_rows = read_document(Path(bundle['source_members']['path']), 16 * 1024**2)
    changes = member_delta(old_rows, new_rows)
    validate_lineage(prior, bundle)
    old_current = verify_source(S / 'source', old_rows)
    require(old_current == old_proof['current'], 'Current full source differs before repair')
    previous.verify_archive_members(Path(bundle['source_archive']['path']), new_rows)
    replacements = replacement_bytes(Path(bundle['source_archive']['path']), changes)
    require(sha(S / 'tools/bazel-9.3.1') == prior['bootstrap']['sha256'] == previous.BOOTSTRAP,
            'Reused bootstrap differs')
    ROOT.mkdir()
    (ROOT / 'candidate').mkdir()
    report = {'schema_version': 1, 'status': 'running', 'root': str(ROOT), 'build_state_root': str(S),
        'source_root': str(S / 'source'), 'bundle': bundle, 'bundle_sha256': args.bundle_sha256,
        'scope_before': before, 'commands': [], 'environment': prior['environment'],
        'helper_sha256': {str(Path(__file__)): sha(Path(__file__)), str(Path(previous.__file__)): PREVIOUS_HELPER_SHA,
                          str(Path(build.__file__)): BUILD_HELPER},
        'prior_report': {'path': str(OLD_EVIDENCE / 'report.json'), 'sha256': PRIOR_REPORT_SHA},
        'prior_archive_sha256': PRIOR_ARCHIVE_SHA, 'prior_metadata_sha256': PRIOR_METADATA_SHA,
        'source_before_patch': old_current, 'repair': changes, 'preserved_prior_files': preserved,
        'prior_server_absence': server_absence,
        'generated_rc_sha256': prior['generated_rc_sha256'], 'seed_copy_sha256': prior['seed_copy_sha256'],
        'configuration_sha256': prior['configuration_sha256'], 'bootstrap': prior['bootstrap'],
        'reused_build_state': True, 'started_unix': time.time(),
        'scope_note': 'Declared two-file source repair and incremental qualification, not a benchmark or gate result.'}
    command, ready = commands(), False

    def run(name, argv, timeout):
        row = {'name': name, 'argv': argv, 'cwd': str(S / 'source'), 'started_unix': time.time()}
        report['commands'].append(row)
        save(ROOT / 'report.json', report)
        with (ROOT / (name + '.log')).open('xb') as log:
            completed = subprocess.run(argv, cwd=S / 'source', env=prior['environment'],
                stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        row.update(returncode=completed.returncode, finished_unix=time.time())
        save(ROOT / 'report.json', report)
        require(completed.returncode == 0, name + ' failed; preserve evidence before any repair')

    try:
        for source, name in ((OLD_EVIDENCE / 'report.json', 'prior-report.json'),
                             (OLD_EVIDENCE / 'source-verification.json', 'prior-source-verification.json'),
                             (args.bundle, 'bundle.json'), (S / 'build.bazelrc', 'build.bazelrc'), (S / 'seed-copy.json', 'seed-copy.json')):
            build.copy_verified(source, ROOT / name, sha(source))
        for key, name in (('source_members', 'source-members.json'), ('instrumentation_patch', 'instrumentation.patch'),
                          ('combined_patch', 'combined.patch')):
            build.copy_verified(Path(bundle[key]['path']), ROOT / name, bundle[key]['sha256'])
        for row in changes:
            path = S / 'source' / row['path']
            require(sha(path) == row['old']['sha256'], 'Repair preimage changed')
            for folder, data in (('source-before', path.read_bytes()), ('source-after', replacements[row['path']])):
                destination = ROOT / folder / row['path']
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as stream:
                    stream.write(data)
        report['prior_server_absence_before_patch'] = prior_server_absence()
        for row in changes:
            path = S / 'source' / row['path']
            require(sha(path) == row['old']['sha256'], 'Repair preimage changed before write')
            path.write_bytes(replacements[row['path']])
        report['source_before'] = verify_source(S / 'source', new_rows)
        report['source_after_patch'] = report['source_before']
        ready = True
        run('version', command['version'], 60)
        run('build', command['build'], 3600)
        built = (S / 'source/bazel-bin/src/bazel-dev').resolve()
        require(built.is_relative_to(S / 'build-output'), 'Candidate outside reused output')
        report['product_binary_sha256_after_build'] = sha(built)
        run('tests', command['tests'], 3600)
        report['product_binary_sha256_after_tests'] = sha(built)
        require(report['product_binary_sha256_after_build'] == report['product_binary_sha256_after_tests'], 'Product changed during tests')
        report['candidate'] = build.copy_verified(built, ROOT / 'candidate/bazel-skycache-trace', sha(built), executable=True)
        report['status'] = SUCCESS
    except Exception as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
    finally:
        if ready:
            try:
                run('shutdown', command['shutdown'], 120)
            except Exception as error:
                report.update(status='failed', shutdown_error=str(error))
        try:
            report['source_after'] = verify_source(S / 'source', new_rows)
            require(report.get('source_before') == report['source_after'], 'Repaired source changed during qualification')
            require(preserved_files(prior, metadata) == preserved, 'Original build evidence changed')
            require(sha(args.bundle) == args.bundle_sha256 and all(sha(Path(path)) == value
                    for path, value in report['helper_sha256'].items()), 'Frozen qualification inputs changed')
            report['preserved_prior_files_after'] = preserved
            report['scope_after'] = build.guard_snapshot()
            report['runtime_free_after'] = shutil.disk_usage(R).free
        except Exception as error:
            report.update(status='failed', final_verification_error=str(error))
        report['finished_unix'] = time.time()
        save(ROOT / 'report.json', report)
    require(report['status'] == SUCCESS, 'Incremental source qualification failed')


if __name__ == '__main__':
    main()
