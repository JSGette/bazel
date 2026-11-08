#!/usr/bin/env python3
"""One guarded test-only requalification; coordinator archives old evidence first.

No launch outside run_auxiliary.ps1, no automatic resume, no admission-cap edit.
Base source commit/tree/archive remain explicit; the sole test patch is separate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tarfile
import time

import native_source_build as build

S = build.RUNTIME / 'm1-native-build-v2'
Q = build.RUNTIME / 'native-cap2-requalification-v1'
TEST = 'src/test/shell/integration/shared_server_test.sh'
ANCHOR = b'function test_shared_installation_mismatch() {\n  local pid\n'
INSERT = (b'  # Keep the original daemon alive while the alternate installation is extracted.\n'
          b'  shared_args=(--max_idle_secs=300)\n')


def verify_base_source(archive, source, test_override=None):
    """Compare every archived file/link with current source; ignore generated extras."""
    count, total = 0, 0
    identity = hashlib.sha256()
    test_bytes = None
    seen = set()
    with tarfile.open(archive, 'r:') as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            build.require(not name.is_absolute() and '..' not in name.parts and
                          member.name not in seen, 'Unsafe/duplicate source archive member')
            seen.add(member.name)
            path = source / member.name
            count += 1
            total += member.size
            build.require(count <= 100000 and total <= 8 * 1024**3, 'Source verification budget exceeded')
            actual = path.lstat()
            if member.isdir():
                build.require(stat.S_ISDIR(actual.st_mode) and path.resolve() == path,
                              'Source directory changed: ' + member.name)
                continue
            if member.issym():
                build.require(stat.S_ISLNK(actual.st_mode) and os.readlink(path) == member.linkname,
                              'Source symlink changed: ' + member.name)
                digest = hashlib.sha256(member.linkname.encode()).hexdigest()
            else:
                expected_size = len(test_override) if member.name == TEST and test_override is not None else member.size
                build.require(member.isfile() and stat.S_ISREG(actual.st_mode) and
                              path.resolve() == path and actual.st_size == expected_size,
                              'Source file changed: ' + member.name)
                with stream.extractfile(member) as archived:
                    digest = hashlib.file_digest(archived, 'sha256').hexdigest()
                if member.name == TEST and test_override is not None:
                    digest = hashlib.sha256(test_override).hexdigest()
                build.require(build.sha(path) == digest, 'Source bytes changed: ' + member.name)
                if os.name == 'posix':
                    build.require(actual.st_mode & 0o111 == member.mode & 0o111,
                                  'Source executable mode changed: ' + member.name)
                if member.name == TEST:
                    test_bytes = path.read_bytes()
            identity.update((member.name + '\0' + digest + '\0').encode())
    build.require(test_bytes is not None, 'Qualification test missing from source archive')
    return {'members_verified': count, 'archived_bytes': total,
            'tracked_content_sha256': identity.hexdigest()}, test_bytes


def validate_patch(original, patched, old_hash, new_hash):
    build.require(hashlib.sha256(original).hexdigest() == old_hash, 'Old test SHA mismatch')
    build.require(hashlib.sha256(patched).hexdigest() == new_hash, 'New test SHA mismatch')
    build.require(original.count(ANCHOR) == 1 and
                  patched == original.replace(ANCHOR, ANCHOR + INSERT, 1),
                  'Patch differs from the exact test-only idle-timeout repair')


def requalification_commands(state, destination):
    commands = build.commands(state)
    for name in ('build', 'tests'):
        commands[name] = [('--build_event_json_file=' + str(destination / (name + '.bep.jsonl')))
                          if arg.startswith('--build_event_json_file=') else
                          ('--test_tmpdir=' + str(destination / 'test-tmp'))
                          if arg.startswith('--test_tmpdir=') else arg
                          for arg in commands[name]]
    return commands


def validate_prior(report, state):
    build.require(report['status'] == 'failed' and report['root'] == str(state), 'Wrong prior run/status')
    build.require(report['source_commit'] == build.COMMIT and report['source_tree'] == build.TREE and
                  report['unchanged_admission_cap'] == 2, 'Wrong base source pins/cap')
    commands = build.commands(state)
    for name, expected in (('build', 0), ('tests', 3), ('shutdown', 0)):
        rows = [row for row in report['commands'] if row['name'] == name]
        build.require(len(rows) == 1 and rows[0]['returncode'] == expected and
                      rows[0]['argv'] == commands[name], 'Unexpected prior ' + name + ' command/result')
    env = report['environment']
    expected_env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(state / 'home'),
                    'TMP': str(state / 'tmp'), 'TMPDIR': str(state / 'tmp'), 'LANG': 'C.UTF-8',
                    'LC_ALL': 'C.UTF-8', 'XDG_CACHE_HOME': str(state / 'cache'),
                    'XDG_CONFIG_HOME': str(state / 'config'), 'XDG_DATA_HOME': str(state / 'data'),
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'JAVA_TOOL_OPTIONS': '-Djava.io.tmpdir=' + str(state / 'tmp')}
    build.require(env == expected_env, 'Prior sanitized environment changed')
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior-report-sha256', required=True)
    parser.add_argument('--test-patch-file', type=Path, required=True)
    parser.add_argument('--old-test-sha256', required=True)
    parser.add_argument('--new-test-sha256', required=True)
    args = parser.parse_args()
    for value in (args.prior_report_sha256, args.old_test_sha256, args.new_test_sha256):
        build.require(re.fullmatch('[0-9a-f]{64}', value), 'Expected lowercase SHA256')
    scope = build.guard_snapshot()
    build.require(os.path.ismount(build.RUNTIME) and S.resolve() == S and S.is_dir(), 'Build state unavailable')
    build.require(not Q.exists() and not Q.is_symlink(), 'Qualification root exists; refusing resume')
    original_report = S / 'report.json'
    build.require(build.sha(original_report) == args.prior_report_sha256, 'Prior report SHA changed')
    prior = json.loads(original_report.read_text())
    env = validate_prior(prior, S)
    build.require(build.sha(S / 'source.tar') == prior['source_archive_sha256'], 'Base archive SHA changed')
    build.require(build.sha(S / 'build.bazelrc') == prior['generated_rc_sha256'], 'Generated rc changed')
    build.require(build.sha(S / 'tools/bazel-9.3.1') == prior['bootstrap']['sha256'], 'Bootstrap changed')
    Q.mkdir()
    (Q / 'test-tmp').mkdir()
    (Q / 'candidate').mkdir()
    report = {key: prior[key] for key in ('source_commit', 'source_tree', 'source_archive_sha256',
              'unchanged_admission_cap', 'source_git_dir', 'bootstrap', 'configuration_sha256',
              'generated_rc_sha256', 'embedded_jdk_sha256', 'compilation_jdk', 'environment',
              'seed_manifest_sha256', 'tool_versions', 'rc_transformation')}
    report.update(schema_version=1, status='running', root=str(Q), build_state_root=str(S),
                  prior_report={'path': str(original_report), 'sha256': args.prior_report_sha256},
                  launcher_sha256=build.sha(Path(__file__).resolve()),
                  build_helper_sha256=build.sha(Path(build.__file__).resolve()),
                  source_is_base_plus_qualification_test_patch=True,
                  scope_before=scope, commands=[], started_unix=time.time(),
                  scope_note='Test-only requalification; base source identity plus declared patch, not a benchmark.')
    command = requalification_commands(S, Q)
    ready = False

    def run(name, timeout=None):
        row = {'name': name, 'argv': command[name], 'started_unix': time.time()}
        report['commands'].append(row)
        build.save(Q / 'report.json', report)
        with (Q / (name + '.log')).open('xb') as log:
            result = subprocess.run(command[name], cwd=S / 'source', env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        row.update(returncode=result.returncode, finished_unix=time.time())
        build.save(Q / 'report.json', report)
        build.require(result.returncode == 0, name + ' failed; preserve qualification evidence')

    try:
        report['base_source_verification'], original = verify_base_source(S / 'source.tar', S / 'source')
        patched = args.test_patch_file.read_bytes()
        validate_patch(original, patched, args.old_test_sha256, args.new_test_sha256)
        built = (S / 'source/bazel-bin/src/bazel-dev').resolve()
        build.require(built.is_relative_to(S / 'build-output'), 'Product binary outside owned build output')
        before = build.sha(built)
        report['product_binary_sha256_before'] = before
        report['qualification_test_patch'] = {'path': TEST, 'patch_file': str(args.test_patch_file),
             'old_sha256': args.old_test_sha256, 'new_sha256': args.new_test_sha256,
             'scope': 'Only original-daemon idle timeout extended to 300 seconds in installation mismatch test',
             'base_source_fields_exclude_this_patch': True}
        (Q / 'test-before.sh').write_bytes(original)
        (Q / 'test-after.sh').write_bytes(patched)
        build.save(Q / 'report.json', report)
        (S / 'source' / TEST).write_bytes(patched)
        ready = True
        run('build')
        build.require(build.sha(built) == before, 'Product binary changed after test-only build')
        run('tests')
        report['qualified_source_verification'], _ = verify_base_source(
            S / 'source.tar', S / 'source', test_override=patched)
        report['product_binary_sha256_after'] = build.sha(built)
        build.require(report['product_binary_sha256_after'] == before, 'Product binary changed during tests')
        build.require(build.sha(S / 'source' / TEST) == args.new_test_sha256, 'Qualification patch changed')
        report['candidate'] = build.copy_verified(built, Q / 'candidate/bazel-native-cap2', before, executable=True)
        report['status'] = 'build_and_focused_tests_passed_no_gate_verdict'
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if ready:
            try:
                run('shutdown', 120)
            except Exception as error:
                report['status'] = 'failed'
                report['shutdown_error'] = str(error)
        report['finished_unix'] = time.time()
        try:
            report['scope_after'] = build.guard_snapshot()
            build.require(build.sha(original_report) == args.prior_report_sha256, 'Original report changed')
        except Exception as error:
            report['status'] = 'failed'
            report['final_verification_error'] = str(error)
        build.save(Q / 'report.json', report)
    build.require(report['status'] == 'build_and_focused_tests_passed_no_gate_verdict', 'Qualification/cleanup failed')


if __name__ == '__main__':
    main()
