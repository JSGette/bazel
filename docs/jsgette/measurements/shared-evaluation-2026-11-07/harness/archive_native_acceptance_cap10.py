#!/usr/bin/env python3
"""Archive one completed native acceptance run; never launch or modify runtime.

Only fixed case directories, fixed inputs and outputs for ten engines plus overflow.
No state/cache/tmp/home walk, raw JVM logs, or process environment capture.
"""
import ast
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tarfile

R = Path('/mnt/shared-evaluation-20261107')
ROOT = R / 'native-acceptance-cap10-01'
HARNESS = R / 'native-acceptance-harness-v2/native_integration_test.py'
HARNESS_SHA = '4e01ca597caea1d77543e0ec21e15f728305565245e746fcc5898a17df6cb7ef'
E = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary')
DEST = E / 'native-acceptance-cap10-01'
WORKSPACES = ('a', 'b') + tuple('engine-%02d' % i for i in range(2, 10))
CASES = ('identity', 'cap-wide-divergent', 'divergent', 'identical', 'glob-isolation',
         'environment', 'configuration', 'test-overlap', 'failure-isolation',
         'same-engine-lock', 'cancel-waiter', 'cancel-producer', 'shutdown-isolation', 'cleanup')


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


def live_identity(identity):
    pid, ticks = identity
    require(type(pid) is int and type(ticks) is int and pid > 0 and ticks > 0, 'Invalid daemon identity')
    try:
        fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[0] != 'Z' and int(fields[19]) == ticks


def required_checks(harness_bytes):
    require(hashlib.sha256(harness_bytes).hexdigest() == HARNESS_SHA, 'Frozen harness changed')
    assignments = [node for node in ast.parse(harness_bytes).body
                   if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
                     t.id == 'REQUIRED_CHECKS' for t in node.targets)]
    require(len(assignments) == 1, 'Expected one frozen check set')
    call = assignments[0].value
    require(isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and
            call.func.id == 'frozenset' and len(call.args) == 1, 'Unexpected check-set syntax')
    checks = ast.literal_eval(call.args[0])
    require(type(checks) is set and len(checks) == 21 and all(type(x) is str for x in checks),
            'Expected exactly21 named checks')
    return checks


def main():
    outputs = [DEST / name for name in ('runtime.tar.gz', 'runtime.tar.gz.json', 'results.json', 'final-outputs.json')]
    require(all(not path.exists() and not path.is_symlink() for path in outputs), 'Archive output exists; no overwrite')
    guard_path = DEST / 'guard/result.json'
    identity_path = DEST / 'guard/scope-identity.json'
    guard, identity = json.loads(guard_path.read_text()), json.loads(identity_path.read_text())
    final = guard['final_scope_cleanup']
    require(guard['status'] in ('completed', 'command_failed') and final['verified'] is True and
            final['present'] is False and final['signalled'] is False, 'Guard has not proved unsignalled absence')
    unit = guard['scope']['unit']
    require(re.fullmatch(r'shared-eval-guard-native-acceptance-cap10-01-[0-9a-f]{32}\.scope', unit) and
            identity['Id'] == unit and identity['InvocationID'] == guard['scope']['invocation_id'],
            'Guard run identity mismatch')
    group = identity['ControlGroup']
    require(group.startswith('/') and '..' not in Path(group).parts and group.endswith('/' + unit), 'Unexpected scope path')
    require(not (Path('/sys/fs/cgroup') / group.lstrip('/')).exists(), 'Acceptance scope remains present')
    policy_path = DEST / 'guard/policy.json'
    require(json.loads(policy_path.read_text())['command_sha256'] == sha(DEST / 'command.json'),
            'Guard command snapshot mismatch')
    qualification = E / 'native-cap10-build-01'
    candidate_report = json.loads((qualification / 'report.json').read_text())
    candidate_proof = json.loads((qualification / 'candidate-verification.json').read_text())
    require(candidate_report['status'] == 'build_and_focused_tests_passed_no_gate_verdict' and
            candidate_report['admission_cap'] == 10 and
            candidate_report['root'] == str(R / 'native-cap10-build-v1') and
            candidate_proof['verified'] is True and candidate_proof['qualification_passed'] is True and
            candidate_proof['report_sha256'] == sha(qualification / 'report.json'), 'Candidate qualification is not verified')
    candidate = Path(candidate_report['candidate']['destination'])
    require(candidate == R / 'native-cap10-build-v1/candidate/bazel-native-cap10' and
            candidate.resolve() == candidate and candidate.stat().st_size <= 512 * 1024**2,
            'Unexpected qualified candidate location/size')
    require(candidate_proof['path'] == str(candidate) and candidate_proof['bytes'] == candidate.stat().st_size,
            'Candidate proof location/size mismatch')
    candidate_sha = sha(candidate)
    require(candidate_sha == candidate_proof['sha256'] == candidate_report['candidate']['sha256'], 'Candidate digest changed')
    command = json.loads((DEST / 'command.json').read_text())
    expected_command = ['python3', '-B', str(HARNESS), '--bazel', str(candidate), '--root', str(ROOT),
                        '--expected-engine-cap', '10', '--heap-mib', '7680', '--processors', '2',
                        '--tmp-root', str(ROOT / 'tmp')]
    require(command == expected_command, 'Acceptance command differs from fixed cap10 protocol')
    require(ROOT.resolve() == ROOT and HARNESS.resolve() == HARNESS and HARNESS.stat().st_size <= 64 * 1024,
            'Unexpected runtime path/size')
    checks = required_checks(HARNESS.read_bytes())
    raw = (ROOT / 'results.json').read_bytes()
    require(len(raw) <= 2 * 1024**2, 'Results exceed bound')
    result = json.loads(raw)
    require(result['harness_sha256'] == HARNESS_SHA and result['binary_sha256'] == candidate_sha,
            'Result harness/candidate mismatch')
    require(result['expected_engine_cap'] == 10 and result['heap_mib'] == 7680 and result['processors'] == 2 and
            result['temporary_root'] == str(ROOT / 'tmp') and result['repository_cache'] == str(ROOT / 'repository-cache'),
            'Acceptance resource/storage policy mismatch')
    require(set(result['required_checks']) == checks and len(result['required_checks']) == 21 and
            set(result['checks']).issubset(checks) and len(result['checks']) == len(set(result['checks'])),
            'Required or observed checks changed')
    passed = (result['success'] is True and result['run_completed'] is True and
              set(result['checks']) == checks and not result['cleanup_errors'] and result['run_error'] is None)
    require(result['success'] is passed, 'Acceptance success field conflicts with complete protocol evidence')
    require((guard['status'] == 'completed' and guard['exit_code'] == 0) if passed else
            (guard['status'] == 'command_failed' and guard['exit_code'] != 0), 'Guard/result outcome mismatch')
    require(not any(live_identity(value) for value in result['daemon_identities']), 'Recorded acceptance daemon is still alive')
    if passed:
        require(len(result['daemon_identities']) == 2 and len({tuple(x) for x in result['daemon_identities']}) == 2,
                'Expected original and fresh restarted daemon identities')
        require(set(result['output_bases']) == set(WORKSPACES) and len(set(result['output_bases'].values())) == 10,
                'Missing distinct ten-engine output mapping')
    files = {'results.json': ROOT / 'results.json', 'executed-harness.py': HARNESS}
    missing_cases = []
    for case in CASES:
        directory = ROOT / case
        if not directory.exists():
            missing_cases.append(case)
            continue
        require(directory.resolve() == directory and directory.is_dir(), 'Unexpected case directory alias')
        entries = []
        with os.scandir(directory) as listing:
            for entry in listing:
                entries.append(entry)
                require(len(entries) <= 100, 'Case artifact count exceeded')
        for entry in entries:
            require(entry.is_file(follow_symlinks=False) and
                    ((case == 'shutdown-isolation' and entry.name == 'closed-reopen-proof.json') or
                     re.fullmatch(r'(?:[A-Za-z0-9-]+(?:\.log|-command\.json|-spawns\.json|-bep\.jsonl)|starts|release|ready-[A-Za-z0-9-]+)', entry.name)),
                    'Unexpected case artifact: ' + entry.name)
            files['cases/' + case + '/' + entry.name] = Path(entry.path)
    if passed:
        require(set(missing_cases).issubset({'cleanup'}), 'Completed run lacks a required case directory')
    for workspace in WORKSPACES + ('overflow',):
        for name in ('MODULE.bazel', 'proof.bzl', 'BUILD.bazel', 'input.txt', 'globcase/BUILD.bazel',
                     'globcase/common.txt', 'globcase/item.txt', 'globcase/extra.txt'):
            path = ROOT / workspace / name
            if path.is_file():
                files['source/' + workspace + '/' + name] = path
    observed_outputs = []
    for workspace in WORKSPACES + ('overflow',):
        for name in ('out.txt', 'globcase/listing.out'):
            path = ROOT / workspace / 'bazel-bin' / name
            if not path.exists():
                observed_outputs.append({'workspace': workspace, 'output': name, 'present': False})
                continue
            resolved = path.resolve()
            require(resolved.is_relative_to(ROOT / 'state') and resolved.is_file() and
                    not resolved.is_symlink() and resolved.stat().st_size <= 1024 * 1024,
                    'Final output outside owned state or above1MiB')
            archive_name = 'final-output/' + workspace + '/' + name
            files[archive_name] = resolved
            observed_outputs.append({'workspace': workspace, 'output': name, 'present': True,
                                     'resolved_path': str(resolved), 'archive_path': archive_name,
                                     'bytes': resolved.stat().st_size, 'sha256': sha(resolved)})
    members = []
    for name, path in sorted(files.items()):
        require(path.is_file() and not path.is_symlink() and path.resolve() == path and
                (path.is_relative_to(ROOT) or path == HARNESS), 'Unexpected archived file/alias')
        require(path.stat().st_size <= 32 * 1024**2, 'Archive member exceeds32MiB')
        members.append({'path': name, 'source': str(path), 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(len(members) <= 1500 and sum(row['bytes'] for row in members) <= 256 * 1024**2, 'Archive budget exceeded')
    archive_path = DEST / 'runtime.tar.gz'
    with archive_path.open('xb') as destination:
        with gzip.GzipFile(fileobj=destination, mode='wb', filename='', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|') as archive:
                for row in members:
                    archive.add(files[row['path']], arcname=row['path'], recursive=False)
    with tarfile.open(archive_path, 'r:gz') as archive:
        require(archive.getnames() == [row['path'] for row in members], 'Archive member mismatch')
        for row in members:
            with archive.extractfile(row['path']) as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256'] and
                        archive.getmember(row['path']).size == row['bytes'], 'Archive checksum mismatch')
    require(hashlib.sha256(raw).hexdigest() == next(x['sha256'] for x in members if x['path'] == 'results.json'),
            'Acceptance results changed during archive')
    output_report = {'outputs': observed_outputs,
                     'interpretation': 'Independent final filesystem bytes only. These reads do not reproduce historical per-case outputs; frozen harness checks record those assertions.'}
    write_new(DEST / 'results.json', raw)
    write_new(DEST / 'final-outputs.json', encoded(output_report))
    metadata = {'schema_version': 1, 'passed': passed, 'required_checks': sorted(checks),
                'completed_checks': result['checks'], 'archive_sha256': sha(archive_path),
                'archive_bytes': archive_path.stat().st_size, 'members': members,
                'candidate_sha256': candidate_sha, 'harness_sha256': HARNESS_SHA,
                'candidate_report_sha256': sha(qualification / 'report.json'),
                'candidate_verification_sha256': sha(qualification / 'candidate-verification.json'),
                'guard_result_sha256': sha(guard_path), 'scope_identity_sha256': sha(identity_path),
                'command_sha256': sha(DEST / 'command.json'), 'guard_policy_sha256': sha(policy_path),
                'final_scope_absent': True, 'final_scope_unsignalled': True, 'recorded_daemons_absent': True,
                'final_outputs_sha256': sha(DEST / 'final-outputs.json'), 'missing_case_directories': missing_cases,
                'helper_sha256': sha(Path(__file__).resolve()),
                'scope': 'Known immediate case artifacts, fixed source inputs and 22 exact final output paths for ten engines plus overflow only; no state/cache/tmp/home traversal or raw JVM/environment logs'}
    write_new(DEST / 'runtime.tar.gz.json', encoded(metadata))
    print(json.dumps({'passed': passed, 'checks': len(result['checks']), 'members': len(members),
                      'archive_sha256': metadata['archive_sha256'], 'candidate_sha256': candidate_sha}))


if __name__ == '__main__':
    main()
