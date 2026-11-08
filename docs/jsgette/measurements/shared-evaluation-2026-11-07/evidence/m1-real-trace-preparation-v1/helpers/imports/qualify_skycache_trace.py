#!/usr/bin/env python3
"""Eight bounded live builds: actual Java traces, independent outputs, offline replay.

Requires an explicit manifest pinned after source/unit qualification. No retry,
performance conclusion, synthetic trace, ambient environment capture, or old-root reuse.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

WORK = Path(__file__).resolve().parent
HELPERS = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/harness'
sys.path.insert(0, str(HELPERS))
import native_source_build as build

R = Path('/mnt/shared-evaluation-20261107')
ROOT = R / 'm1-trace-acceptance-v1'
BUILD_ROOT = R / 'm1-skycache-trace-qualification-v3'
BASE = 'a23ae68903f03f08ce83dcf1be85cdff54bb1eeb'
GUARD_HELPER = 'c1180cb697231a169ed55b0d8fa3d7d66165c842a3c66d41fc133c9046acfc5a'
CASES = [('e0-a-off', 'e0', 'A', False), ('e0-a-on', 'e0', 'A', True),
         ('e1-a-on', 'e1', 'A', True), ('e0-b-off', 'e0', 'B', False),
         ('e0-b-on', 'e0', 'B', True), ('e1-a-off', 'e1', 'A', False),
         ('e0-a-restored-on', 'e0', 'A', True), ('e1-a-repeat-on', 'e1', 'A', True)]
DEFS = ('def fixture(name):\n'
        '    native.genrule(name = name, srcs = native.glob(["inputs/*.txt", "optional/*.txt"], allow_empty = True)'
        ' + ["link.txt"], outs = ["result.txt"], cmd = "cat $(SRCS) > $@")\n')
FIXED = {'MODULE.bazel': 'module(name = "trace_smoke")\n',
         'BUILD.bazel': 'load(":defs.bzl", "fixture")\nfixture(name = "proof")\n',
         'defs.bzl': DEFS, 'inputs/z.txt': 'omega\n'}
require, sha, save = build.require, build.sha, build.save


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def document(path, expected=None, limit=4 * 1024**2):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path and path.is_file()
            and not path.is_symlink() and path.stat().st_size <= limit, 'Invalid bounded document: ' + str(path))
    raw = path.read_bytes()
    require(expected is None or digest(raw) == expected, 'Input document digest differs: ' + str(path))
    return json.loads(raw), digest(raw)


def validate_provenance(manifest, report, proof, report_sha):
    require(manifest['schema_version'] == 1 and manifest['root'] == str(ROOT)
            and manifest['cases'] == [list(row) for row in CASES], 'Smoke protocol differs')
    require(report.get('status') == 'build_and_focused_tests_passed_no_gate_verdict'
            and report.get('root') == str(BUILD_ROOT), 'Source/unit qualification did not pass')
    for name in ('version', 'build', 'tests', 'shutdown'):
        rows = [row for row in report['commands'] if row['name'] == name]
        require(len(rows) == 1 and rows[0].get('returncode') == 0, 'Missing qualified command: ' + name)
    require(report['source_before'] == report['source_after'], 'Qualified source changed')
    candidate = report['candidate']
    require(candidate['destination'] == str(BUILD_ROOT / 'candidate/bazel-skycache-trace')
            and candidate['sha256'] == report['product_binary_sha256_after_build']
            == report['product_binary_sha256_after_tests'], 'Qualified candidate identity differs')
    require(proof.get('verified') is True and proof.get('qualification_passed') is True
            and proof.get('final_scope_absent') is True and proof.get('final_scope_unsignalled') is True
            and proof.get('report_sha256') == report_sha and proof.get('path') == candidate['destination']
            and proof.get('sha256') == candidate['sha256'] and proof.get('bytes') == candidate['bytes'],
            'Archived candidate proof differs or build scope remains active')
    source = manifest['source_identity']
    bundle = report['bundle']
    require(set(source) == {'base_commit', 'patch_sha256', 'reconstructed_git_tree'}
            and source['base_commit'] == bundle['base_commit'] == BASE
            and source['reconstructed_git_tree'] == bundle['instrumented_git_tree']
            and source['reconstructed_git_tree'] != bundle['baseline_git_tree']
            and source['patch_sha256'] == bundle['combined_patch']['sha256']
            and re.fullmatch('[0-9a-f]{64}', source['patch_sha256'])
            and re.fullmatch('[0-9a-f]{40}', source['reconstructed_git_tree']), 'Complete instrumented source identity differs')
    return candidate


def fixture_members(workspace, names):
    """Bind declared files, symlinks, and the two real fixture directories."""
    members = []
    for name in sorted([*names, 'inputs', 'optional']):
        path = workspace / name
        if name in ('inputs', 'optional'):
            require(not path.is_symlink() and path.is_dir(), 'Fixture directory changed')
            members.append({'path': name, 'type': 'directory'})
        elif path.is_symlink():
            members.append({'path': name, 'type': 'symlink', 'target': os.readlink(path)})
        else:
            members.append({'path': name, 'type': 'file', 'sha256': sha(path)})
    return members


def fixture_state(workspace, variant):
    """Only these declared fixture paths may change; returns independent expected bytes."""
    require(variant in ('A', 'B'), 'Unknown fixture variant')
    (workspace / 'inputs').mkdir(exist_ok=True)
    (workspace / 'optional').mkdir(exist_ok=True)
    for name, value in FIXED.items():
        (workspace / name).write_text(value, encoding='utf-8')
    (workspace / 'inputs/a.txt').write_text('alpha\n' if variant == 'A' else 'bravo\n', encoding='utf-8')
    optional = workspace / 'optional/added.txt'
    if variant == 'B':
        optional.write_text('extra\n', encoding='utf-8')
    elif optional.exists():
        optional.unlink()
    link = workspace / 'link.txt'
    if link.is_symlink():
        link.unlink()
    require(not link.exists(), 'Unexpected non-symlink fixture path')
    link.symlink_to('inputs/a.txt' if variant == 'A' else 'inputs/z.txt')
    names = sorted([*FIXED, 'inputs/a.txt', 'link.txt'] + (['optional/added.txt'] if variant == 'B' else []))
    members = fixture_members(workspace, names)
    expected = b'alpha\nomega\nalpha\n' if variant == 'A' else b'bravo\nomega\nextra\nomega\n'
    return {'variant': variant, 'members': members, 'snapshot_id': digest(canonical(members)),
            'expected_output_hex': expected.hex()}


def verify_fixture(workspace, state):
    for row in state['members']:
        path = workspace / row['path']
        if row['type'] == 'symlink':
            require(path.is_symlink() and os.readlink(path) == row['target'], 'Fixture symlink changed')
        elif row['type'] == 'directory':
            require(not path.is_symlink() and path.is_dir(), 'Fixture directory changed')
        else:
            require(not path.is_symlink() and path.is_file() and sha(path) == row['sha256'], 'Fixture file changed')
    for name, expected in (('inputs', ['a.txt', 'z.txt']),
                           ('optional', ['added.txt'] if state['variant'] == 'B' else [])):
        require(sorted(path.name for path in (workspace / name).iterdir()) == expected,
                'Fixture glob membership changed')


def check_listing_projections(parsed, state):
    """Check exact emitted facts; absent listing projections remain missing coverage."""
    expected = {'inputs': [['a.txt', 'FILE'], ['z.txt', 'FILE']],
                'optional': [['added.txt', 'FILE']] if state['variant'] == 'B' else []}
    captured = Counter()
    for observation in parsed['observations'].values():
        if observation['value_status'] != 'exact_projection':
            continue
        node = parsed['nodes'][observation['node_id']]
        path = bytes.fromhex(node['logical_key'][1]).decode('utf-16-be', errors='surrogatepass')
        if observation['projection']['type'] == 'DIRECTORY_LISTING_STATE' and path in expected:
            actual = [[bytes.fromhex(name).decode('utf-16-be', errors='surrogatepass'), kind]
                      for name, kind in observation['projection']['entries']]
            require(actual == expected[path], 'Exact Java listing differs from fixture: ' + path)
            captured[path] += 1
    return {'verified_exact_listing_observations': dict(captured),
            'paths_without_exact_listing_observations': sorted(set(expected) - set(captured))}


def identity(pid):
    try:
        fields = Path('/proc', str(pid), 'stat').read_text().rsplit(')', 1)[1].split()
    except FileNotFoundError:
        return None
    return None if fields[0] == 'Z' else [pid, int(fields[19])]


def owned_daemon(base, scope):
    pidfile = base / 'server/server.pid.txt'
    if not pidfile.exists():
        return None
    pid = int(pidfile.read_text().strip())
    require(pid > 1, 'Invalid daemon PID')
    observed = identity(pid)
    if observed is None:
        return None
    argv = Path('/proc', str(pid), 'cmdline').read_bytes().split(b'\0')
    require(('--output_base=' + str(base)).encode() in argv, 'Daemon output-base ownership differs')
    require('0::' + scope in Path('/proc', str(pid), 'cgroup').read_text().splitlines(),
            'Daemon escaped the owned guard scope')
    require(identity(pid) == observed, 'Daemon identity changed while checking ownership')
    return observed


def bep_identity(path):
    require(path.stat().st_size <= 16 * 1024**2, 'BEP exceeds smoke budget')
    events = [json.loads(line) for line in path.read_bytes().splitlines() if line.strip()]
    starts, finishes = ([row[key] for row in events if key in row] for key in ('started', 'finished'))
    require(len(starts) == len(finishes) == 1 and finishes[0]['exitCode'].get('code', 0) == 0
            and finishes[0]['exitCode'].get('name') == 'SUCCESS',
            'Build event success proof missing')
    return starts[0]['uuid']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    manifest, manifest_sha = document(args.manifest, args.manifest_sha256)
    require(manifest['harness_sha256'] == sha(Path(__file__))
            and sha(Path(build.__file__)) == GUARD_HELPER, 'Harness identity differs')
    prior, prior_sha = document(**manifest['build_report'])
    proof, _ = document(**manifest['candidate_verification'])
    candidate = validate_provenance(manifest, prior, proof, prior_sha)
    binary = Path(candidate['destination'])
    require(binary.resolve() == binary and not binary.is_symlink() and binary.stat().st_size == candidate['bytes']
            and sha(binary) == candidate['sha256'], 'Qualified candidate bytes differ')
    replay_path = WORK / 'm1-trace-design/replay.py'
    contract_path = replay_path.with_name('trace-v1.contract.json')
    require(sha(replay_path) == manifest['replay_sha256']
            and sha(contract_path) == manifest['contract_sha256'], 'Replay/contract bytes differ')
    before = build.guard_snapshot()
    require(R.resolve() == R and os.path.ismount(R) and not ROOT.exists() and not ROOT.is_symlink(),
            'Fresh owned runtime root required')
    ROOT.mkdir()
    for name in ('tmp', 'home', 'cache', 'config', 'data', 'traces', 'contexts', 'cases', 'links', 'bindings'):
        (ROOT / name).mkdir()
    env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(ROOT / 'home'),
           'TMP': str(ROOT / 'tmp'), 'TMPDIR': str(ROOT / 'tmp'), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
           'XDG_CACHE_HOME': str(ROOT / 'cache'), 'XDG_CONFIG_HOME': str(ROOT / 'config'),
           'XDG_DATA_HOME': str(ROOT / 'data'), 'PYTHONDONTWRITEBYTECODE': '1',
           'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
    report = {'schema_version': 1, 'status': 'running', 'root': str(ROOT), 'manifest_sha256': manifest_sha,
              'candidate': candidate, 'source_identity': manifest['source_identity'], 'scope_before': before,
              'cases': [], 'commands': [], 'daemon_identities': {}, 'cleanup': [], 'environment': env,
              'diagnostic_only': True, 'milestone_verdict': None, 'started_unix': time.time()}
    save(ROOT / 'manifest.json', manifest)
    daemon_ids, startup, traces, attestations, preserved, states = {}, {}, [], {'traces': {}}, {}, {}
    spec = importlib.util.spec_from_file_location('m1_smoke_replay', replay_path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)

    def run(name, argv, cwd, timeout=180):
        row = {'name': name, 'argv': argv, 'cwd': str(cwd), 'started_unix': time.time()}
        report['commands'].append(row)
        save(ROOT / 'report.json', report)
        with (ROOT / (name + '.log')).open('xb') as log:
            result = subprocess.run(argv, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        row.update(returncode=result.returncode, finished_unix=time.time())
        save(ROOT / 'report.json', report)
        return result.returncode

    def verify_preserved():
        require(all(sha(Path(path)) == expected for path, expected in preserved.items()),
                'An earlier command trace/context/receipt changed')

    try:
        seed = ROOT / 'fixture-seed'
        seed.mkdir()
        fixture_state(seed, 'A')
        git = ['git', '-c', 'core.autocrlf=false', '-c', 'core.symlinks=true',
               '-c', 'user.name=Trace qualification', '-c', 'user.email=trace-qualification.invalid']
        for label, command in [('git-init', ['init']), ('git-add', ['add', '.']), ('git-commit', ['commit', '-m', 'Fixed trace smoke A'])]:
            require(run(label, git + command, seed, 30) == 0, 'Fixture Git setup failed')
        for engine in ('e0', 'e1'):
            workspace = ROOT / engine
            require(run('git-worktree-' + engine, git + ['worktree', 'add', '--detach', str(workspace), 'HEAD'], seed, 30) == 0,
                    'Independent fixture worktree setup failed')
            # Git does not track empty directories; the A fixture intentionally owns this empty glob root.
            (workspace / 'optional').mkdir(exist_ok=True)
            states[engine] = fixture_state(workspace, 'A')
            (ROOT / 'links' / engine).mkdir()
            base = ROOT / 'outputs' / engine
            startup[engine] = [str(binary), '--ignore_all_rc_files', '--output_user_root=' + str(ROOT / 'state' / engine),
                '--output_base=' + str(base), '--install_base=' + str(ROOT / 'install'),
                '--digest_function=SHA-256', '--host_jvm_args=-Xmx768m', '--host_jvm_args=-XX:ActiveProcessorCount=2',
                '--host_jvm_args=-Djava.io.tmpdir=' + str(ROOT / 'tmp'), '--noidle_server_tasks', '--max_idle_secs=600']
            binding = {'logical_repository_id': 'm1-trace-smoke-fixture-v1', 'physical_root': str(workspace),
                       'filesystem_policy': {'os': 'linux', 'case_sensitive': True, 'digest_algorithm': 'SHA-256'}}
            save(ROOT / 'bindings' / (engine + '.json'), binding)
        inventory = Counter()
        for name, engine, variant, enabled in CASES:
            workspace, directory = ROOT / engine, ROOT / 'cases' / name
            directory.mkdir()
            for other, previous in states.items():
                verify_fixture(ROOT / other, previous)
            state = fixture_state(workspace, variant)
            states[engine] = state
            save(directory / 'fixture.json', state)
            base, bep = ROOT / 'outputs' / engine, directory / 'bep.jsonl'
            argv = startup[engine] + ['build', '//:proof', '--incompatible_autoload_externally=',
                '--jobs=2', '--loading_phase_threads=2', '--local_resources=cpu=2', '--local_resources=memory=512',
                '--repository_cache=' + str(ROOT / 'repository-cache'), '--repo_env=TMP=' + str(ROOT / 'tmp'),
                '--repo_env=TMPDIR=' + str(ROOT / 'tmp'), '--action_env=TMP=' + str(ROOT / 'tmp'),
                '--action_env=TMPDIR=' + str(ROOT / 'tmp'), '--remote_cache=', '--remote_executor=',
                '--bes_backend=', '--bes_results_url=', '--disk_cache=', '--lockfile_mode=off',
                '--experimental_remote_skycache=', '--experimental_local_skycache=' + str(ROOT / 'skycache'),
                '--experimental_analysis_cache_idle_retention=3600s', '--symlink_prefix=' + str(ROOT / 'links' / engine) + '/',
                '--noshow_progress', '--color=no', '--curses=no', '--build_event_json_file=' + str(bep),
                '--nobuild_event_json_file_path_conversion']
            trace, context_path = ROOT / 'traces' / (name + '.jsonl'), ROOT / 'contexts' / (name + '.json')
            if enabled:
                argv += ['--experimental_m1_trace=' + str(trace), '--experimental_m1_trace_context=' + str(context_path)]
                binding, _ = document(ROOT / 'bindings' / (engine + '.json'))
                binding['binding_provenance_sha256'] = sha(ROOT / 'bindings' / (engine + '.json'))
                context = {'engine_id': engine, 'cohort_id': 'm1-trace-acceptance-v1', 'phase': 'fixture', 'hosting': 'private',
                    'binary_sha256': candidate['sha256'], 'source_identity': manifest['source_identity'],
                    'semantic_epoch': 'm1-trace-main-facts-v1', 'snapshot_id': state['snapshot_id'],
                    'command_policy_sha256': digest(canonical(argv)), 'repository_bindings': {'main': binding},
                    'bzl_inlining': 'unknown'}
                save(context_path, context)
                context_sha = sha(context_path)
            else:
                argv += ['--experimental_m1_trace=', '--experimental_m1_trace_context=']
            before_names = set(path.name for path in (ROOT / 'traces').iterdir())
            code = run(name, argv, workspace)
            process = owned_daemon(base, before['name'])
            require(process is not None, 'Private daemon absent after build')
            require(engine not in daemon_ids or daemon_ids[engine] == process, 'Unexpected private daemon replacement')
            daemon_ids[engine] = process
            report['daemon_identities'][engine] = process
            require(len({value[0] for value in daemon_ids.values()}) == len(daemon_ids), 'Private engines share a PID')
            require(code == 0, 'Build failed: ' + name)
            for other, previous in states.items():
                verify_fixture(ROOT / other, previous)
            command_id = bep_identity(bep)
            result_path = (ROOT / 'links' / engine / 'bin/result.txt').resolve()
            require(result_path.is_relative_to(base) and result_path.is_file(), 'Output escaped its private output base')
            output = result_path.read_bytes()
            require(output == bytes.fromhex(state['expected_output_hex']), 'Independent output oracle differs: ' + name)
            (directory / 'result.txt').write_bytes(output)
            verify_preserved()
            row = {'name': name, 'engine_id': engine, 'variant': variant, 'trace_enabled': enabled,
                   'command_id': command_id, 'daemon_identity': process, 'snapshot_id': state['snapshot_id'],
                   'output_sha256': digest(output), 'oracle_passed': True}
            if enabled:
                parsed = replay.read_trace(trace)
                expected_context = {key: parsed['header'][key] for key in replay.CONTRACT['context_file_fields']}
                require(expected_context == context and parsed['header']['command_id'] == command_id
                        and parsed['header']['context_sha256'] == context_sha == sha(context_path),
                        'Java trace/command/context binding differs')
                receipt = Path(str(trace) + '.completion.json')
                require(parsed['completion']['trace_path'] == str(trace), 'Java receipt path differs')
                row['listing_truthfulness'] = check_listing_projections(parsed, state)
                evidence = {'context': context, 'context_sha256': context_sha,
                            'completion_sha256': sha(receipt), 'trace_path': str(trace)}
                attestations['traces'][sha(trace)] = evidence
                traces.append(trace)
                for observation in parsed['observations'].values():
                    projection = observation['projection']
                    if observation['value_status'] == 'exact_projection':
                        inventory[projection['type']] += 1
                        if observation['node_id'] in parsed['observed'] and projection['type'] in ('DIRECTORY', 'DIRECTORY_LISTING_STATE'):
                            inventory['observed_exact_directory_or_listing'] += 1
                row.update(trace_sha256=sha(trace), completion_sha256=sha(receipt), context_sha256=sha(context_path),
                           trace_id=parsed['header']['trace_id'], node_count=len(parsed['nodes']),
                           unknown_counts=parsed['footer']['unknown_counts'])
                require(set(path.name for path in (ROOT / 'traces').iterdir()) == before_names | {trace.name, receipt.name},
                        'Unexpected trace outputs')
                for path in (trace, receipt, context_path):
                    preserved[str(path)] = sha(path)
            else:
                require(set(path.name for path in (ROOT / 'traces').iterdir()) == before_names,
                        'Trace-disabled command created trace artifacts')
            report['cases'].append(row)
            save(ROOT / 'report.json', report)
        require(inventory['observed_exact_directory_or_listing'] > 0, 'No observed exact directory/listing fact captured')
        require(len(traces) == 5 and len({row['command_id'] for row in report['cases']}) == 8,
                'Command/trace count or identity differs')
        save(ROOT / 'attestations.json', attestations)
        require(run('replay', [sys.executable, '-B', str(replay_path), *map(str, traces),
            '--attestations', str(ROOT / 'attestations.json'), '--output', str(ROOT / 'replay-result.json')], ROOT, 180) == 0,
            'Real Java traces failed offline replay')
        replay_result, replay_sha = document(ROOT / 'replay-result.json')
        require(len(replay_result['traces']) == 5 and all(row['context_attested'] for row in replay_result['traces']),
                'Replay did not validate every actual trace')
        report.update(status='real_trace_interop_passed_no_gate_verdict', typed_projection_inventory=dict(inventory),
                      replay_result_sha256=replay_sha, interpretation='Functional smoke only; unsupported facts/CPU remain unknown.')
    except Exception as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
    finally:
        for engine in startup:
            base = ROOT / 'outputs' / engine
            cleanup = {'engine_id': engine}
            try:
                current = owned_daemon(base, before['name'])
                if current is not None:
                    require(engine not in daemon_ids or current == daemon_ids[engine], 'Refusing replacement-daemon shutdown')
                    daemon_ids[engine] = current
                    cleanup['identity'] = current
                    require(run('shutdown-' + engine, startup[engine] + ['shutdown'], ROOT / engine, 120) == 0,
                            'Owned shutdown failed')
                    deadline = time.monotonic() + 10
                    while identity(current[0]) == current and time.monotonic() < deadline:
                        time.sleep(0.1)
                    require(identity(current[0]) != current, 'Recorded daemon remains alive after shutdown')
                cleanup['recorded_identity_absent'] = current is None or identity(current[0]) != current
            except Exception as error:
                report['status'] = 'failed'
                cleanup['error'] = type(error).__name__ + ': ' + str(error)
            report['cleanup'].append(cleanup)
        try:
            verify_preserved()
            for other, previous in states.items():
                verify_fixture(ROOT / other, previous)
            require(sha(binary) == candidate['sha256'] and sha(args.manifest) == manifest_sha
                    and sha(Path(__file__)) == manifest['harness_sha256'] and sha(replay_path) == manifest['replay_sha256']
                    and sha(contract_path) == manifest['contract_sha256'], 'Frozen inputs changed during qualification')
            require(all(identity(pid) != [pid, ticks] for pid, ticks in daemon_ids.values()), 'Recorded daemon still exists')
            report['scope_after'] = build.guard_snapshot()
            report['daemon_identities'] = daemon_ids
        except Exception as error:
            report.update(status='failed', final_verification_error=str(error))
        report['finished_unix'] = time.time()
        save(ROOT / 'report.json', report)
    require(report['status'] == 'real_trace_interop_passed_no_gate_verdict', 'Trace smoke failed; preserve evidence before repair')


if __name__ == '__main__':
    main()
