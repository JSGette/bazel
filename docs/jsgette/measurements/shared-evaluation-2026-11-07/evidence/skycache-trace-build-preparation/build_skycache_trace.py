#!/usr/bin/env python3
"""Qualify one frozen SkyCache trace source bundle under the existing host guard.

No benchmark, source modification, retry, cache inventory or existing-root reuse.
Run from work/ only after all three frozen N10 arms have ended and been assessed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import time

WORK = Path(__file__).resolve().parent
HELPERS = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/harness'
sys.path.insert(0, str(HELPERS))
import native_source_build as build

R = Path('/mnt/shared-evaluation-20261107')
ROOT = R / 'm1-skycache-trace-build-v1'
PRIOR = R / 'm1-native-build-v2'
BASE = 'a23ae68903f03f08ce83dcf1be85cdff54bb1eeb'
BASELINE_TREE = '9bcb73e357fe966c3f3442b012b5d8d8d148fd7d'
SAFE_PATCH = '238cac7dffac4ca6a89ca144d59643ecb60e465d0dbeab0b21da0c1a2a56eb7e'
BOOTSTRAP = '7f775b54bbec487efaf0f49ccda1d2ed02cd34f582edfdd6aeeec76e39ef585c'
BUILD_HELPER = 'c1180cb697231a169ed55b0d8fa3d7d66165c842a3c66d41fc133c9046acfc5a'
SEED_SHA = '4918d80e2c51b04eb2474ec1f6cc8f6371614f1df30a445e2d8ad01a5c3a8e8b'
TESTS = ['//src/test/java/com/google/devtools/build/lib/skyframe:' + name for name in (
    'M1TraceCollectorTest', 'M1TraceModuleTest', 'SequencedSkyframeExecutorTest',
    'FileStateValueTest', 'CompactSortedDirentsTest', 'BzlLoadFunctionTest')]
TESTS += ['//src/test/java/com/google/devtools/build/lib/skyframe/serialization/local:' + name
          for name in ('LocalAnalysisValuesTest', 'LocalStarlarkInfoCodecTest',
                       'LocalExecutionValuesTest', 'LocalDependencyValidatorTest',
                       'LocalCacheSessionTest', 'LocalInputSnapshotTest', 'LocalCacheGarbageCollectorTest')]
require, sha, save = build.require, build.sha, build.save


def read_document(path, limit):
    require(path.is_file() and path.resolve() == path and not path.is_symlink()
            and path.stat().st_size <= limit, 'Invalid/oversized input document')
    return json.loads(path.read_bytes())


def validate_bundle(bundle):
    require(bundle['schema_version'] == 1 and bundle['base_commit'] == BASE
            and bundle['baseline_git_tree'] == BASELINE_TREE
            and bundle['safe_subset_patch_sha256'] == SAFE_PATCH, 'Wrong trace baseline')
    require(re.fullmatch('[0-9a-f]{40}', bundle['instrumented_git_tree'])
            and bundle['instrumented_git_tree'] != BASELINE_TREE, 'Instrumented tree missing')
    require(bundle['tests'] == TESTS, 'Focused test contract differs')
    for key, maximum in (('source_archive', 1024**3), ('source_members', 16 * 1024**2),
                         ('instrumentation_patch', 16 * 1024**2), ('combined_patch', 16 * 1024**2)):
        item = bundle[key]
        path = Path(item['path'])
        require(path.is_absolute() and path.resolve() == path and path.is_relative_to(WORK)
                and path.is_file() and not path.is_symlink(), 'Input outside frozen work bundle: ' + key)
        require(type(item['bytes']) is int and 0 < item['bytes'] <= maximum
                and path.stat().st_size == item['bytes'] and sha(path) == item['sha256'],
                'Input size/digest differs: ' + key)


def verify_source(source, members):
    require(18000 < len(members) < 30000, 'Unexpected source member count')
    names, aggregate, total = set(), hashlib.sha256(), 0
    for row in sorted(members, key=lambda item: item['path']):
        relative = PurePosixPath(row['path'])
        require(not relative.is_absolute() and str(relative) == row['path'] and '..' not in relative.parts
                and '\\' not in row['path'] and row['path'] not in names, 'Invalid source member')
        names.add(row['path'])
        path = source / row['path']
        require(path.resolve() == path and path.is_file() and not path.is_symlink()
                and row['mode'] in ('100644', '100755'), 'Invalid source file/mode')
        data = path.read_bytes()
        require(len(data) == row['bytes'] and hashlib.sha256(data).hexdigest() == row['sha256']
                and hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == row['git_blob'],
                'Source member differs: ' + row['path'])
        require(bool(path.stat().st_mode & 0o111) == (row['mode'] == '100755'), 'Executable mode differs')
        aggregate.update(json.dumps(row, sort_keys=True, separators=(',', ':')).encode() + b'\n')
        total += len(data)
    return {'files_verified': len(names), 'bytes_verified': total,
            'member_records_sha256': aggregate.hexdigest(), 'generated_extras_not_verified': True}


def verify_archive_members(archive, members):
    expected = {row['path']: row for row in members}
    require(len(expected) == len(members), 'Duplicate source member records')
    expected_dirs = {str(parent) for name in expected for parent in PurePosixPath(name).parents
                     if str(parent) != '.'}
    observed, directories = set(), set()
    with tarfile.open(archive, 'r:') as stream:
        for entry in stream:
            name = entry.name.rstrip('/') if entry.isdir() else entry.name
            relative = PurePosixPath(name)
            require(name and not relative.is_absolute() and str(relative) == name
                    and '..' not in relative.parts and '\\' not in name,
                    'Noncanonical source archive member')
            if entry.isdir():
                require(name in expected_dirs and name not in directories, 'Unexpected/duplicate source directory')
                directories.add(name)
            else:
                require(entry.isfile() and name in expected and name not in observed,
                        'Unexpected/duplicate/nonregular source archive file')
                row = expected[name]
                require(row['mode'] in ('100644', '100755') and entry.size == row['bytes']
                        and bool(entry.mode & 0o111) == (row['mode'] == '100755'),
                        'Source archive member size/mode differs')
                observed.add(name)
    require(observed == set(expected) and directories == expected_dirs, 'Source archive membership differs')
    return {'files': len(observed), 'directories': len(directories), 'exact_initial_membership': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', required=True, type=Path)
    parser.add_argument('--bundle-sha256', required=True)
    args = parser.parse_args()
    before = build.guard_snapshot()
    require(sha(Path(build.__file__).resolve()) == BUILD_HELPER, 'Qualification helper changed')
    require(sha(args.bundle) == args.bundle_sha256, 'Frozen bundle digest differs')
    bundle = read_document(args.bundle, 2 * 1024**2)
    validate_bundle(bundle)
    seed_path = WORK / 'trace-build-preparation/seed-manifest.json'
    require(sha(seed_path) == SEED_SHA, 'Seed manifest differs')
    seed = read_document(seed_path, 2 * 1024**2)
    require(seed['complete'] is True and seed['files'] == len(seed['members']) == 878
            and seed['bytes'] == 804384491, 'Incomplete source-build seed')
    require(R.resolve() == R and os.path.ismount(R), 'Owned runtime volume not mounted')
    require(not ROOT.exists() and not ROOT.is_symlink(), 'Build root exists; no automatic reuse')
    require(shutil.disk_usage(R).free >= 60 * 1024**3, 'Insufficient runtime free space')
    ROOT.mkdir()
    for name in ('source', 'tools', 'tmp', 'home', 'cache', 'config', 'data', 'sandbox',
                 'download-cache', 'repo-contents', 'test-tmp', 'test-install', 'candidate'):
        (ROOT / name).mkdir()
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(ROOT / 'home'),
           'TMP': str(ROOT / 'tmp'), 'TMPDIR': str(ROOT / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'XDG_CACHE_HOME': str(ROOT / 'cache'), 'XDG_CONFIG_HOME': str(ROOT / 'config'),
           'XDG_DATA_HOME': str(ROOT / 'data'), 'PYTHONDONTWRITEBYTECODE': '1',
           'JAVA_TOOL_OPTIONS': '-Djava.io.tmpdir=' + str(ROOT / 'tmp')}
    report = {'schema_version': 1, 'status': 'running', 'root': str(ROOT), 'bundle': bundle,
              'bundle_sha256': args.bundle_sha256, 'helper_sha256': {str(Path(__file__)): sha(Path(__file__)),
                  str(Path(build.__file__)): BUILD_HELPER}, 'scope_before': before,
              'commands': [], 'environment': env, 'started_unix': time.time(),
              'scope_note': 'Source/unit qualification only. No trace or M1 performance verdict.'}
    ready = False
    command = None

    def run(name, argv, timeout):
        row = {'name': name, 'argv': argv, 'started_unix': time.time()}
        report['commands'].append(row)
        save(ROOT / 'report.json', report)
        with (ROOT / (name + '.log')).open('xb') as log:
            result = subprocess.run(argv, cwd=ROOT / 'source', env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        row.update(returncode=result.returncode, finished_unix=time.time())
        save(ROOT / 'report.json', report)
        require(result.returncode == 0, name + ' failed; preserve log before any repair')

    try:
        report['source_archive_copy'] = build.copy_verified(Path(bundle['source_archive']['path']),
            ROOT / 'source.tar', bundle['source_archive']['sha256'])
        members = read_document(Path(bundle['source_members']['path']), 16 * 1024**2)
        report['source_archive_membership'] = verify_archive_members(ROOT / 'source.tar', members)
        build.extract_archive(ROOT / 'source.tar', ROOT / 'source')
        report['source_before'] = verify_source(ROOT / 'source', members)
        report['source_members_copy'] = build.copy_verified(Path(bundle['source_members']['path']),
            ROOT / 'source-members.json', bundle['source_members']['sha256'])
        report['instrumentation_patch_copy'] = build.copy_verified(Path(bundle['instrumentation_patch']['path']),
            ROOT / 'instrumentation.patch', bundle['instrumentation_patch']['sha256'])
        report['combined_patch_copy'] = build.copy_verified(Path(bundle['combined_patch']['path']),
            ROOT / 'combined.patch', bundle['combined_patch']['sha256'])
        require((ROOT / 'source/.bazelversion').read_text().strip() == '9.3.1', 'Bootstrap version differs')
        require('zulu27.28.101-ca-jdk27.0.0-linux_x64.tar.gz' in
                (ROOT / 'source/repositories.bzl').read_text(), 'Embedded JDK source changed')
        (ROOT / 'build.bazelrc').write_text(build.make_rc((ROOT / 'source/.bazelrc').read_text()))
        report['generated_rc_sha256'] = sha(ROOT / 'build.bazelrc')
        report['configuration_sha256'] = {name: sha(ROOT / 'source' / name) for name in
            ('.bazelrc', '.bazelversion', 'MODULE.bazel', 'MODULE.bazel.lock', 'maven_install.json',
             'repositories.bzl', 'bazel_downloader.cfg')}
        report['bootstrap'] = build.copy_verified(PRIOR / 'tools/bazel-9.3.1',
            ROOT / 'tools/bazel-9.3.1', BOOTSTRAP, executable=True)
        copied = []
        for row in seed['members']:
            source = Path(row['destination'])
            require(source.is_relative_to(PRIOR / 'download-cache'), 'Unexpected seed source')
            target = ROOT / 'download-cache' / source.relative_to(PRIOR / 'download-cache')
            target.parent.mkdir(parents=True, exist_ok=True)
            copied.append(build.copy_verified(source, target, row['sha256']))
        save(ROOT / 'seed-copy.json', {'source_manifest_sha256': SEED_SHA, 'members': copied})
        report['seed_copy_sha256'] = sha(ROOT / 'seed-copy.json')
        build.TESTS = TESTS
        command = build.commands(ROOT)
        ready = True
        run('version', command['version'], 60)
        run('build', command['build'], 3600)
        built = (ROOT / 'source/bazel-bin/src/bazel-dev').resolve()
        require(built.is_relative_to(ROOT / 'build-output'), 'Candidate outside owned output')
        report['product_binary_sha256_after_build'] = sha(built)
        run('tests', command['tests'], 3600)
        report['product_binary_sha256_after_tests'] = sha(built)
        require(report['product_binary_sha256_after_build'] == report['product_binary_sha256_after_tests'],
                'Product bytes changed while qualifying focused tests')
        report['candidate'] = build.copy_verified(built, ROOT / 'candidate/bazel-skycache-trace',
                                                  sha(built), executable=True)
        report['status'] = 'build_and_focused_tests_passed_no_gate_verdict'
    except Exception as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
        raise
    finally:
        if ready:
            try:
                run('shutdown', command['shutdown'], 120)
            except Exception as error:
                report.update(status='failed', shutdown_error=str(error))
        try:
            if 'source_before' in report:
                report['source_after'] = verify_source(ROOT / 'source', members)
                require(report['source_after'] == report['source_before'], 'Source changed during qualification')
            require(sha(Path(build.__file__).resolve()) == BUILD_HELPER and
                    sha(args.bundle) == args.bundle_sha256 and
                    sha(Path(__file__)) == report['helper_sha256'][str(Path(__file__))],
                    'Qualification input changed during run')
            report['scope_after'] = build.guard_snapshot()
            report['runtime_free_after'] = shutil.disk_usage(R).free
        except Exception as error:
            report.update(status='failed', final_verification_error=str(error))
        report['finished_unix'] = time.time()
        save(ROOT / 'report.json', report)
    require(report['status'] == 'build_and_focused_tests_passed_no_gate_verdict', 'Qualification incomplete')


if __name__ == '__main__':
    main()
