#!/usr/bin/env python3
"""One guarded incremental cap-10 qualification; no launch/resume/cleanup policy here.

Coordinator must preserve cap-2 build/requalification/acceptance/N2 evidence first.
The source identity is base486 + the pinned idle-test repair + four committed files;
it is deliberately not claimed to equal the complete product commit's tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tarfile
import time

HELPERS = Path(__file__).resolve().parent / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/harness'
sys.path.insert(0, str(HELPERS))
import native_source_build as build
import requalify_native_cap2 as previous

S = build.RUNTIME / 'm1-native-build-v2'
Q2 = build.RUNTIME / 'native-cap2-requalification-v1'
Q = build.RUNTIME / 'native-cap10-build-v1'
PRIOR_SHA = 'b92e756bdea41da0c30eb475a9cb7d347cd43eea3d13b044f539589e01e2f8f9'
ARCHIVE_SHA = 'a451b8353a29f92cb852785ff57ce5143b8a7ca37d561428926bee722353e18d'
OLD_BINARY = 'b7071363aba091f922b91d73dc1f8db4169dd791c164aa10fe58f168a89e689e'
OLD_TEST = '905ed0efafc77775a50fe832c5cfe70cce809b7e5788ad28820afcb2392e44ee'
NEW_TEST = '575367272b56e63e92fdd7f56605b1764fc6085e51bd1802ebe1099fe13230c5'
FILES = (
    'src/main/java/com/google/devtools/build/lib/server/CommandServer.java',
    'src/main/java/com/google/devtools/build/lib/runtime/BlazeServerStartupOptions.java',
    'src/test/java/com/google/devtools/build/lib/server/CommandServerTest.java',
    'tools/multiworktree/NATIVE.md',
)
SUCCESS = 'build_and_focused_tests_passed_no_gate_verdict'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify_source(archive, source, overrides):
    """Verify every archived member; generated extras are not treated as source."""
    seen, applied = set(), set()
    count, total = 0, 0
    identity = hashlib.sha256()
    with tarfile.open(archive, 'r:') as stream:
        for member in stream:
            name = PurePosixPath(member.name)
            build.require(not name.is_absolute() and '..' not in name.parts and
                          member.name not in seen, 'Unsafe/duplicate archive member')
            seen.add(member.name)
            count += 1
            total += member.size
            build.require(count <= 100000 and total <= 8 * 1024**3, 'Source verification budget exceeded')
            path = source / member.name
            actual = path.lstat()
            if member.isdir():
                build.require(stat.S_ISDIR(actual.st_mode) and path.resolve() == path,
                              'Source directory changed: ' + member.name)
                continue
            if member.issym():
                build.require(member.name not in overrides and stat.S_ISLNK(actual.st_mode) and
                              os.readlink(path) == member.linkname, 'Source symlink changed: ' + member.name)
                expected = digest(member.linkname.encode())
            else:
                size = len(overrides[member.name]) if member.name in overrides else member.size
                build.require(member.isfile() and stat.S_ISREG(actual.st_mode) and
                              path.resolve() == path and actual.st_size == size,
                              'Source file changed: ' + member.name)
                if member.name in overrides:
                    expected = digest(overrides[member.name])
                    applied.add(member.name)
                else:
                    with stream.extractfile(member) as reader:
                        expected = hashlib.file_digest(reader, 'sha256').hexdigest()
                build.require(build.sha(path) == expected, 'Source bytes changed: ' + member.name)
                if os.name == 'posix':
                    build.require(actual.st_mode & 0o111 == member.mode & 0o111,
                                  'Source executable mode changed: ' + member.name)
            identity.update((member.name + '\0' + expected + '\0').encode())
    build.require(applied == set(overrides), 'Override missing from archived regular files')
    return {'members_verified': count, 'archived_bytes': total,
            'tracked_content_sha256': identity.hexdigest(),
            'override_paths': sorted(overrides), 'generated_extras_not_source_verified': True}


def validate_prior(prior, original):
    build.require(prior['status'] == SUCCESS and prior['root'] == str(Q2) and
                  prior['build_state_root'] == str(S), 'Wrong prior qualification')
    build.require(prior['source_commit'] == build.COMMIT and prior['source_tree'] == build.TREE and
                  prior['source_archive_sha256'] == ARCHIVE_SHA and
                  prior['unchanged_admission_cap'] == 2 and
                  prior['source_is_base_plus_qualification_test_patch'] is True, 'Prior source identity changed')
    patch = prior['qualification_test_patch']
    build.require((patch['path'], patch['old_sha256'], patch['new_sha256']) ==
                  (previous.TEST, OLD_TEST, NEW_TEST), 'Prior idle-test patch changed')
    build.require(prior['candidate']['sha256'] == OLD_BINARY and
                  prior['product_binary_sha256_before'] == OLD_BINARY and
                  prior['product_binary_sha256_after'] == OLD_BINARY, 'Prior binary changed')
    expected = previous.requalification_commands(S, Q2)
    for name in ('build', 'tests', 'shutdown'):
        rows = [row for row in prior['commands'] if row['name'] == name]
        build.require(len(rows) == 1 and rows[0]['argv'] == expected[name] and
                      rows[0]['returncode'] == 0, 'Prior command not qualified: ' + name)
    env = previous.validate_prior(original, S)
    build.require(prior['environment'] == env, 'Sanitized environment changed')
    for key in ('source_archive_sha256', 'configuration_sha256', 'generated_rc_sha256',
                'bootstrap', 'embedded_jdk_sha256', 'compilation_jdk', 'seed_manifest_sha256',
                'tool_versions', 'rc_transformation', 'source_git_dir'):
        build.require(prior[key] == original[key], 'Prior build field changed: ' + key)
    return env


def committed_patch(git, commit, supplied, source, env):
    """Read only four committed files; require the supplied canonical exact diff."""
    def read(*argv):
        return subprocess.check_output(git + list(argv), env=env)

    parents = read('rev-list', '--parents', '-n', '1', commit).decode().split()
    build.require(len(parents) == 2 and parents[0] == commit, 'Product commit must have exactly one parent')
    parent = parents[1]
    canonical = read('diff', '--binary', '--full-index', '--no-ext-diff', '--no-renames',
                     parent, commit, '--', *FILES)
    build.require(canonical == supplied and len(supplied) <= 256 * 1024,
                  'Patch is not the exact selected committed diff')
    names = read('diff', '--name-only', '--no-renames', parent, commit, '--', *FILES).decode().splitlines()
    build.require(set(names) == set(FILES) and len(names) == len(FILES), 'Expected all four declared changed files')
    rows, overrides = [], {}
    for name in FILES:
        for revision in (parent, commit):
            metadata = read('ls-tree', revision, '--', name).decode().strip().split('\t')
            build.require(len(metadata) == 2 and metadata[1] == name and
                          metadata[0].startswith('100644 blob '), 'Only regular unchanged-mode files allowed')
        before, after = read('show', parent + ':' + name), read('show', commit + ':' + name)
        build.require(max(len(before), len(after)) <= 2 * 1024**2 and before != after,
                      'Unexpected product file size/identity')
        build.require((source / name).read_bytes() == before, 'Current source differs from patch parent: ' + name)
        overrides[name] = after
        rows.append({'path': name, 'old_sha256': digest(before), 'new_sha256': digest(after),
                     'old_bytes': len(before), 'new_bytes': len(after)})
    return {'commit': commit, 'parent': parent,
            'commit_tree': read('rev-parse', commit + '^{tree}').decode().strip(),
            'sha256': digest(supplied), 'files': rows,
            'scope': 'Only the four listed files from this commit; full commit tree is not the build tree'}, overrides


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--patch-file', type=Path, required=True)
    parser.add_argument('--patch-sha256', required=True)
    parser.add_argument('--product-commit', required=True)
    parser.add_argument('--source-git-dir', type=Path, required=True)
    args = parser.parse_args()
    build.require(re.fullmatch('[0-9a-f]{64}', args.patch_sha256) and
                  re.fullmatch('[0-9a-f]{40}', args.product_commit), 'Expected full lowercase commit/SHA256')
    scope = build.guard_snapshot()
    build.require(os.path.ismount(build.RUNTIME) and S.resolve() == S and S.is_dir(), 'Build state unavailable')
    build.require(not Q.exists() and not Q.is_symlink(), 'Qualification root exists; refusing resume')
    prior_path, original_path = Q2 / 'report.json', S / 'report.json'
    build.require(build.sha(prior_path) == PRIOR_SHA, 'Prior qualification report changed')
    prior = json.loads(prior_path.read_text())
    build.require(prior['prior_report']['path'] == str(original_path) and
                  build.sha(original_path) == prior['prior_report']['sha256'], 'Original build report changed')
    original = json.loads(original_path.read_text())
    env = validate_prior(prior, original)
    build.require(args.source_git_dir.resolve() == args.source_git_dir and
                  args.source_git_dir.is_dir() and not args.source_git_dir.is_relative_to(build.RUNTIME),
                  'Source Git directory must be non-aliased and outside R')
    build.require(args.patch_file.resolve() == args.patch_file and args.patch_file.is_file() and
                  args.patch_file.stat().st_size <= 256 * 1024, 'Invalid patch file')
    supplied = args.patch_file.read_bytes()
    build.require(digest(supplied) == args.patch_sha256, 'Patch SHA changed')
    Q.mkdir()
    for name in ('test-tmp', 'candidate', 'source-before', 'source-after'):
        (Q / name).mkdir()
    keys = ('source_commit', 'source_tree', 'source_archive_sha256', 'source_git_dir', 'bootstrap',
            'configuration_sha256', 'generated_rc_sha256', 'embedded_jdk_sha256', 'compilation_jdk',
            'environment', 'seed_manifest_sha256', 'tool_versions', 'rc_transformation',
            'qualification_test_patch')
    report = {key: prior[key] for key in keys}
    helpers = {str(path): build.sha(path) for path in
               (Path(__file__).resolve(), Path(build.__file__).resolve(), Path(previous.__file__).resolve())}
    report.update(schema_version=1, status='running', root=str(Q), build_state_root=str(S),
                  admission_cap=10, base_admission_cap=2,
                  prior_report={'path': str(prior_path), 'sha256': PRIOR_SHA},
                  helper_sha256=helpers, scope_before=scope, started_unix=time.time(), commands=[],
                  source_is_base_plus_qualification_test_and_cap10_patch=True,
                  source_identity_note='source_commit/tree/archive identify BASE only; declared patches below form actual source',
                  scope_note='Incremental functional qualification; not an M1 benchmark or gate verdict.')
    commands = previous.requalification_commands(S, Q)
    ready, overrides = False, None

    def run(name, argv, timeout=None):
        row = {'name': name, 'argv': argv, 'started_unix': time.time()}
        report['commands'].append(row)
        build.save(Q / 'report.json', report)
        with (Q / (name + '.log')).open('xb') as log:
            result = subprocess.run(argv, cwd=S / 'source', env=env, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=timeout)
        row.update(returncode=result.returncode, finished_unix=time.time())
        build.save(Q / 'report.json', report)
        build.require(result.returncode == 0, name + ' failed; preserve qualification evidence')

    try:
        build.require(build.sha(S / 'source.tar') == ARCHIVE_SHA, 'Base source archive changed')
        build.require(build.sha(S / 'build.bazelrc') == prior['generated_rc_sha256'], 'Generated rc changed')
        build.require(build.sha(S / 'tools/bazel-9.3.1') == prior['bootstrap']['sha256'], 'Bootstrap changed')
        build.require(build.sha(S / 'seed-manifest.json') == prior['seed_manifest_sha256'], 'Seed manifest changed')
        test = (S / 'source' / previous.TEST).read_bytes()
        build.require(digest(test) == NEW_TEST, 'Pinned idle-test repair changed')
        overrides = {previous.TEST: test}
        report['source_before'] = verify_source(S / 'source.tar', S / 'source', overrides)
        built = (S / 'source/bazel-bin/src/bazel-dev').resolve()
        build.require(built.is_relative_to(S / 'build-output') and build.sha(built) == OLD_BINARY and
                      build.sha(Q2 / 'candidate/bazel-native-cap2') == OLD_BINARY, 'Prior candidate changed')
        report['product_binary_sha256_before'] = OLD_BINARY
        git = build.source_git_command(args.source_git_dir)
        record, changed = committed_patch(git, args.product_commit, supplied, S / 'source', env)
        report['cap10_patch'] = record
        (Q / 'product.patch').write_bytes(supplied)
        for name, data in {previous.TEST: test, **changed}.items():
            for directory, content in (('source-before', (S / 'source' / name).read_bytes()),
                                       ('source-after', data)):
                destination = Q / directory / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        build.save(Q / 'report.json', report)
        apply = ['git', '-c', 'core.autocrlf=false', 'apply', '--whitespace=error-all']
        run('patch-check', apply + ['--check', str(Q / 'product.patch')], 60)
        run('patch-apply', apply + [str(Q / 'product.patch')], 60)
        overrides.update(changed)
        report['source_after_patch'] = verify_source(S / 'source.tar', S / 'source', overrides)
        ready = True
        run('build', commands['build'])
        report['product_binary_sha256_after_build'] = build.sha(built)
        build.require(report['product_binary_sha256_after_build'] != OLD_BINARY, 'Cap-10 binary did not change')
        run('tests', commands['tests'])
        report['product_binary_sha256_after_tests'] = build.sha(built)
        build.require(report['product_binary_sha256_after_tests'] == report['product_binary_sha256_after_build'],
                      'Candidate changed during focused tests')
        report['candidate'] = build.copy_verified(built, Q / 'candidate/bazel-native-cap10',
                                                  report['product_binary_sha256_after_tests'], executable=True)
        report['status'] = SUCCESS
    except Exception as error:
        report['status'] = 'failed'
        report['error'] = type(error).__name__ + ': ' + str(error)
        raise
    finally:
        if ready:
            try:
                run('shutdown', commands['shutdown'], 120)
            except Exception as error:
                report['status'], report['shutdown_error'] = 'failed', str(error)
        try:
            if overrides is not None:
                report['source_final'] = verify_source(S / 'source.tar', S / 'source', overrides)
            report['scope_after'] = build.guard_snapshot()
            build.require(build.sha(prior_path) == PRIOR_SHA and
                          build.sha(original_path) == prior['prior_report']['sha256'], 'Prior reports changed')
            build.require(build.sha(Q2 / 'candidate/bazel-native-cap2') == OLD_BINARY, 'Prior copied binary changed')
            build.require(all(build.sha(Path(path)) == value for path, value in helpers.items()), 'Helper changed')
        except Exception as error:
            report['status'], report['final_verification_error'] = 'failed', str(error)
        report['finished_unix'] = time.time()
        build.save(Q / 'report.json', report)
    build.require(report['status'] == SUCCESS, 'Qualification/cleanup failed')


if __name__ == '__main__':
    main()
