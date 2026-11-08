#!/usr/bin/env python3
"""Archive the named requalification only after its external guard has finished.

No Bazel, process signals, runtime writes, recursive walks or raw JVM/environment
logs. Prior source-build evidence remains untouched. Run once; no overwrite.
"""
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

R = Path('/mnt/shared-evaluation-20261107')
S = R / 'm1-native-build-v2'
Q = R / 'native-cap2-requalification-v1'
E = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary/native-cap2-requalification-01')
COMMIT = '4862608d940b49293c68c014e413ecc9dc98aa8e'
TREE = 'a28c113856ec48838041bba3b9591ab9ea67e4ac'
PRIOR_REPORT = 'ea7ac7214de97aba8da7ee0f4fce489e1fd0fecc6f779775b53f5067c538b59f'
OLD_TEST = '905ed0efafc77775a50fe832c5cfe70cce809b7e5788ad28820afcb2392e44ee'
NEW_TEST = '575367272b56e63e92fdd7f56605b1764fc6085e51bd1802ebe1099fe13230c5'
TARGETS = (
    'src/test/cpp/shared_server_ownership_test',
    'src/test/java/com/google/devtools/build/lib/remote/SharedActionExecutionRegistryTest',
    'src/test/java/com/google/devtools/build/lib/server/ServerTests',
    'src/test/shell/integration/shared_server_test',
)


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_new(path, data):
    with path.open('xb') as stream:
        stream.write(data)


def json_bytes(value):
    return (json.dumps(value, indent=2) + '\n').encode()


def validate_guard(guard, identity):
    require(guard['status'] in ('completed', 'command_failed'), 'Guard did not finish normally')
    final = guard['final_scope_cleanup']
    require(final['verified'] is True and final['present'] is False and
            final['signalled'] is False, 'Requires unsignalled, verified final scope absence')
    require(identity['Id'] == guard['scope']['unit'] and
            identity['InvocationID'] == guard['scope']['invocation_id'], 'Guard identity mismatch')
    require(re.fullmatch(r'shared-eval-guard-native-cap2-requalification-01-[0-9a-f]{32}\.scope',
                         identity['Id']), 'Wrong guarded run')
    group = identity['ControlGroup']
    require(group.startswith('/') and '..' not in Path(group).parts and
            group.endswith('/' + identity['Id']), 'Unexpected cgroup path')
    return Path('/sys/fs/cgroup') / group.lstrip('/')


def main():
    outputs = [E / name for name in ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json',
                                     'candidate-verification.json')]
    require(all(not path.exists() and not path.is_symlink() for path in outputs),
            'Archive output already exists; refusing overwrite')
    guard_path, identity_path = E / 'guard/result.json', E / 'guard/scope-identity.json'
    guard = json.loads(guard_path.read_text())
    identity = json.loads(identity_path.read_text())
    cgroup = validate_guard(guard, identity)
    require(not cgroup.exists(), 'Guard scope still exists; do not read active runtime evidence')
    policy_path = E / 'guard/policy.json'
    policy = json.loads(policy_path.read_text())
    require(policy['command_sha256'] == sha(E / 'command.json'), 'Executed command snapshot mismatch')
    require(Q.resolve() == Q and S.resolve() == S, 'Unexpected runtime path alias')
    raw_report = (Q / 'report.json').read_bytes()
    require(len(raw_report) <= 2 * 1024**2, 'Report exceeds bound')
    report = json.loads(raw_report)
    prior = json.loads((S / 'report.json').read_text())
    require(sha(S / 'report.json') == PRIOR_REPORT, 'Preserved prior report changed')
    require(report['root'] == str(Q) and report['build_state_root'] == str(S), 'Wrong report roots')
    require(report['source_commit'] == COMMIT and report['source_tree'] == TREE and
            report['source_is_base_plus_qualification_test_patch'] is True,
            'Base source/qualification patch identity mismatch')
    require(report['source_archive_sha256'] == prior['source_archive_sha256'] == sha(S / 'source.tar'),
            'Original source archive changed')
    require(report['scope_before']['name'] == report['scope_after']['name'] == identity['ControlGroup'],
            'Report scope differs from final external guard')
    passed = report['status'] == 'build_and_focused_tests_passed_no_gate_verdict'
    require(report['status'] in ('failed', 'build_and_focused_tests_passed_no_gate_verdict'),
            'Runtime report incomplete')
    if passed:
        require(guard['status'] == 'completed' and guard['exit_code'] == 0, 'Successful report lacks successful guard')
        for name in ('build', 'tests', 'shutdown'):
            rows = [row for row in report['commands'] if row['name'] == name]
            require(len(rows) == 1 and rows[0]['returncode'] == 0, 'Missing successful ' + name)
        require(report['qualification_test_patch']['old_sha256'] == OLD_TEST and
                report['qualification_test_patch']['new_sha256'] == NEW_TEST and
                report['product_binary_sha256_before'] == report['product_binary_sha256_after'],
                'Test patch or unchanged-product proof mismatch')
    else:
        require(guard['status'] == 'command_failed' and guard['exit_code'] != 0,
                'Failed report lacks failed guard command')
    files, missing = {'report.json': Q / 'report.json'}, []
    for name in ('build.log', 'tests.log', 'shutdown.log', 'build.bep.jsonl', 'tests.bep.jsonl',
                 'test-before.sh', 'test-after.sh'):
        path = Q / name
        if path.is_file():
            files[name] = path
        else:
            missing.append(str(path))
    if passed:
        require(not missing, 'Successful run lacks required phase/patch evidence')
    for name, digest in (('test-before.sh', OLD_TEST), ('test-after.sh', NEW_TEST)):
        if name in files:
            require(sha(files[name]) == digest, 'Patch evidence changed: ' + name)
    current_test = S / 'source/src/test/shell/integration/shared_server_test.sh'
    require(sha(current_test) in ((NEW_TEST,) if passed else (OLD_TEST, NEW_TEST)), 'Unexpected runtime test source')
    files['source/shared-server-test.sh'] = current_test
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
    if passed:
        require(all(target_counts.values()), 'Successful run lacks a named target test log/XML')
    candidate = {'verified': False, 'qualification_passed': passed}
    if 'candidate' in report:
        path = Path(report['candidate']['destination'])
        require(path == Q / 'candidate/bazel-native-cap2' and path.resolve() == path and
                path.is_file() and path.stat().st_size <= 512 * 1024**2, 'Unexpected candidate file')
        digest = sha(path)
        require(digest == report['candidate']['sha256'] == report['product_binary_sha256_before'] ==
                report['product_binary_sha256_after'], 'Candidate product digest mismatch')
        candidate.update(verified=True, path=str(path), sha256=digest, bytes=path.stat().st_size)
    require(not passed or candidate['verified'], 'Successful run has no verified candidate')
    members = []
    for name, path in sorted(files.items()):
        require(path.is_file() and not path.is_symlink() and path.resolve() == path and
                (path.is_relative_to(Q) or path.is_relative_to(S)), 'Unexpected archive source path')
        require(path.stat().st_size <= 64 * 1024**2, 'Archive member exceeds64MiB')
        members.append({'path': name, 'source': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(sum(row['bytes'] for row in members) <= 256 * 1024**2, 'Archive exceeds256MiB')
    archive_path = E / 'runtime.tar.gz'
    with archive_path.open('xb') as destination:
        with gzip.GzipFile(fileobj=destination, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as archive:
                for row in members:
                    archive.add(files[row['path']], arcname=row['path'], recursive=False)
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(archive.getnames() == [row['path'] for row in members], 'Archive members changed')
        for row in members:
            with archive.extractfile(row['path']) as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(digest == row['sha256'] and archive.getmember(row['path']).size == row['bytes'],
                    'Archive verification failed: ' + row['path'])
    require(hashlib.sha256(raw_report).hexdigest() == next(row['sha256'] for row in members
                                                         if row['path'] == 'report.json'), 'Report changed during capture')
    write_new(E / 'report.json', raw_report)
    candidate.update(report_sha256=hashlib.sha256(raw_report).hexdigest(),
                     guard_result_sha256=sha(guard_path), scope_identity_sha256=sha(identity_path),
                     final_scope_absent=True, final_scope_unsignalled=True)
    write_new(E / 'candidate-verification.json', json_bytes(candidate))
    metadata = {'schema_version': 1, 'archive_sha256': sha(archive_path),
                'archive_bytes': archive_path.stat().st_size, 'passed': passed,
                'report_status': report['status'], 'members': members, 'missing_optional_files': missing,
                'named_target_files': target_counts, 'candidate': candidate,
                'prior_report_sha256': PRIOR_REPORT, 'source_commit': COMMIT, 'source_tree': TREE,
                'source_archive_sha256': report['source_archive_sha256'],
                'command_sha256': sha(E / 'command.json'), 'guard_policy_sha256': sha(policy_path),
                'helper_sha256': sha(Path(__file__).resolve()),
                'scope': 'Named reports, phase logs/BEP, test patch and four targets test.log/XML only; no raw JVM/environment dump',
                'testlog_freshness': 'Files from reused build state may be cached or preexisting; current tests BEP is authoritative for invocation results.',
                'original_runtime_preserved': True}
    write_new(E / 'runtime.tar.gz.json', json_bytes(metadata))
    print(json.dumps({'archive_sha256': metadata['archive_sha256'], 'members': len(members),
                      'passed': passed, 'copied_report': str(E / 'report.json'), 'candidate': candidate}))


if __name__ == '__main__':
    main()
