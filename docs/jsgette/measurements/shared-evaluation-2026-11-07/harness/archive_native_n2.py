#!/usr/bin/env python3
"""Archive one finished native N2 role; no launch, mutation or cache/source walk."""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tarfile

R = Path('/mnt/shared-evaluation-20261107')
TASK = R / 'native-n2-probe01'
E = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary')
BINARY_SHA = 'b7071363aba091f922b91d73dc1f8db4169dd791c164aa10fe58f168a89e689e'
FIXTURE_SHA = '2de503bc8d4b5e7e1817b5a73e9bb78c1d7bacc56f5ae19847b8f686ebaff78a'
PHASE_FILES = ('command.json', 'stdout.log', 'stderr.log', 'process-result.json', 'verified-result.json', 'bep.jsonl')
HELPERS = ('native_comparison.py', 'm1_resources.py', 'fixture.py', 'baseline.py', 'output_bases.py')


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


def live_process(pid, ticks=None):
    require(type(pid) is int and pid > 0, 'Invalid PID')
    try:
        fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return False
    return fields[0] != 'Z' and (ticks is None or int(fields[19]) == ticks)


def collect_files(root):
    """Fixed N2 paths only. Never inspect state, repository or worktree entries."""
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
        for index in range(2):
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
    destination = E / ('native-n2-probe01-' + role)
    output_names = ('runtime.tar.gz', 'runtime.tar.gz.json', 'report.json')
    require(all(not (destination / name).exists() and not (destination / name).is_symlink()
                for name in output_names), 'Archive output exists; no overwrite')
    guard_path, identity_path = destination / 'guard/result.json', destination / 'guard/scope-identity.json'
    guard, identity = json.loads(guard_path.read_text()), json.loads(identity_path.read_text())
    cleanup, unit = guard['final_scope_cleanup'], guard['scope']['unit']
    require(guard['status'] in ('completed', 'command_failed') and cleanup['verified'] is True and
            cleanup['present'] is False and cleanup['signalled'] is False, 'Final guard absence is not verified/unsignalled')
    require(re.fullmatch('shared-eval-guard-native-n2-probe01-' + role + r'-[0-9a-f]{32}\.scope', unit) and
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
    require(config['run_root'] == report['run_root'] == str(root) and config['clients'] == report['clients'] == 2 and
            config['hosting'] == report['hosting'] == ('native' if role == 'native' else 'private') and
            report['reference_only'] is (role == 'ref') and config['scenario'] == report['scenario'] == 'identical',
            'Unexpected N2 role/fixture configuration')
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
    require(binary == R / 'native-cap2-requalification-v1/candidate/bazel-native-cap2' and binary.resolve() == binary,
            'Unexpected candidate path')
    actual_binary = sha(binary)
    require(actual_binary == config['binary']['sha256'] == report['binary_sha256'] == BINARY_SHA,
            'Pinned candidate digest mismatch')
    require(config['binary']['source_commit'] == '4862608d940b49293c68c014e413ecc9dc98aa8e' and
            config['binary']['source_git_tree_sha1'] == 'a28c113856ec48838041bba3b9591ab9ea67e4ac', 'Candidate base source mismatch')
    files = collect_files(root)
    harness_pins = config['preparation_provenance']['harness_sha256']
    for name in HELPERS:
        path = TASK / 'harness' / name
        require(path.resolve() == path and path.is_file() and path.stat().st_size <= 1024**2 and
                sha(path) == harness_pins[name], 'Frozen helper changed: ' + name)
        if name in report['harness_sha256']:
            require(report['harness_sha256'][name] == harness_pins[name], 'Reported helper mismatch')
        files['executed-harness/' + name] = path
    require(harness_pins['fixture.py'] == FIXTURE_SHA, 'Unsupported fixture verifier')
    spec = importlib.util.spec_from_file_location('archived_n2_fixture', TASK / 'harness/fixture.py')
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    if 'fixture' in report:
        manifest = report['fixture']
        require(json.loads((root / 'fixture/manifest.json').read_text()) == manifest, 'Fixture manifest mismatch')
        for row in manifest['worktrees']:
            workspace = Path(row['workspace'])
            require(row['index'] in (0, 1) and workspace == root / 'fixture' / ('w%02d' % row['index']) and
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
        bases = [Path(paths['control'])] if role == 'native' else [Path(paths['bases'][str(i)]) for i in range(2)]
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
                report['measured']['passed'] is True and len(report['measured']['results']) == 2 and
                len(source_checks) == 2 and len(daemon_checks) == (1 if role == 'native' else 2) and
                not verification_errors, 'Completed run lacks independent source/daemon verification')
        require(all(row['exit_code'] == 0 and row['behavior']['passed'] and row['policy_verified']
                    for row in report['measured']['results']), 'Measured correctness/policy failure')
    members = []
    for name, path in sorted(files.items()):
        require(path.is_file() and not path.is_symlink() and path.resolve() == path and
                path.stat().st_size <= 64 * 1024**2, 'Invalid/oversized evidence file')
        members.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path)})
    require(len(members) <= 180 and sum(row['bytes'] for row in members) <= 256 * 1024**2, 'Evidence budget exceeded')
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
                'guard_result_sha256': sha(guard_path), 'scope_identity_sha256': sha(identity_path),
                'guard_policy_sha256': sha(policy_path), 'command_sha256': sha(destination / 'command.json'),
                'final_scope_absent': True, 'final_scope_unsignalled': True,
                'helper_sha256': sha(Path(__file__).resolve()),
                'scope': 'Fixed N2 reports/manifests, command/log/BEP/accounting evidence, behavior files and executed helpers only; no caches, whole sources, binaries or raw environment dumps',
                'metric_note': 'Copied runtime report is unchanged. Archive success does not promote performance_eligible or declare an M1/M4 gate.'}
    write_new(destination / 'runtime.tar.gz.json', encoded(metadata))
    print(json.dumps({'role': role, 'passed': passed, 'archive_sha256': metadata['archive_sha256'],
                      'members': len(members), 'copied_report': str(destination / 'report.json')}))


if __name__ == '__main__':
    main()
