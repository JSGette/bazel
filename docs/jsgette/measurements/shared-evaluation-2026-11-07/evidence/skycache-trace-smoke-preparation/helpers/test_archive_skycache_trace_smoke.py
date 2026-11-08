"""Offline protocol fixtures only; these do not claim real Java interop."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import archive_skycache_trace_smoke as archive


class SmokeArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='trace-smoke-archive-test-', dir=archive.WORK)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.qpatch = patch.object(archive, 'Q', self.root)
        self.qpatch.start()
        self.addCleanup(self.qpatch.stop)
        self.candidate = {'destination': str(self.root / 'qualified/bazel'), 'sha256': 'a' * 64}

    def report(self):
        report = {'cases': [], 'commands': [], 'daemon_identities': {'e0': [101, 1001], 'e1': [102, 1002]},
                  'cleanup': [{'engine_id': engine, 'identity': pair, 'recorded_identity_absent': True}
                              for engine, pair in (('e0', [101, 1001]), ('e1', [102, 1002]))]}
        for index, (name, engine, variant, enabled) in enumerate(archive.smoke.CASES):
            report['cases'].append({'name': name, 'engine_id': engine, 'variant': variant, 'trace_enabled': enabled,
                'oracle_passed': True, 'command_id': '00000000-0000-0000-0000-' + format(index + 1, '012x'),
                'daemon_identity': report['daemon_identities'][engine]})
            trace = str(self.root / 'traces' / (name + '.jsonl')) if enabled else ''
            context = str(self.root / 'contexts' / (name + '.json')) if enabled else ''
            report['commands'].append({'name': name, 'returncode': 0, 'cwd': str(self.root / engine),
                'argv': [self.candidate['destination'], '--output_base=' + str(self.root / 'outputs' / engine),
                         '--experimental_m1_trace=' + trace, '--experimental_m1_trace_context=' + context]})
        for name in ('git-init', 'git-add', 'git-commit', 'git-worktree-e0', 'git-worktree-e1',
                     'replay', 'shutdown-e0', 'shutdown-e1'):
            report['commands'].append({'name': name, 'returncode': 0})
        return report

    def test_fixed_fixture_oracle_and_absent_path_membership(self):
        first, first_content = archive.expected_fixture('A')
        changed, changed_content = archive.expected_fixture('B')
        self.assertEqual(bytes.fromhex(first['expected_output_hex']), b'alpha\nomega\nalpha\n')
        self.assertEqual(bytes.fromhex(changed['expected_output_hex']), b'bravo\nomega\nextra\nomega\n')
        self.assertNotIn('optional/added.txt', first_content)
        self.assertEqual(changed_content['optional/added.txt'], b'extra\n')
        self.assertNotEqual(first['snapshot_id'], changed['snapshot_id'])
        self.assertEqual(first, archive.expected_fixture('A')[0])
        self.assertEqual(next(row['target'] for row in first['members'] if row['type'] == 'symlink'), 'inputs/a.txt')
        self.assertEqual({row['path'] for row in first['members'] if row['type'] == 'directory'}, {'inputs', 'optional'})

    def test_independent_oracle_matches_tiny_actual_fixture_writer(self):
        workspace = self.root / 'offline-fixture'
        workspace.mkdir()
        for variant in ('A', 'B', 'A'):
            try:
                observed = archive.smoke.fixture_state(workspace, variant)
            except OSError as error:
                if getattr(error, 'winerror', None) == 1314:
                    self.skipTest('Windows user lacks symlink privilege; runtime requires Linux')
                raise
            expected, _ = archive.expected_fixture(variant)
            self.assertEqual(observed, expected)

    def test_case_order_private_ownership_and_trace_arguments(self):
        good = self.report()
        archive.validate_cases(good, self.candidate, passed=True)
        mutations = [lambda r: r['cases'].reverse(),
            lambda r: r['cases'][1].update(command_id=r['cases'][0]['command_id']),
            lambda r: r['cases'][0].update(daemon_identity=[102, 1002]),
            lambda r: r['commands'][0]['argv'].append('--experimental_m1_trace=/other'),
            lambda r: r['commands'][0].update(returncode=1)]
        for mutate in mutations:
            bad = copy.deepcopy(good)
            mutate(bad)
            with self.assertRaises(RuntimeError):
                archive.validate_cases(bad, self.candidate, passed=True)
        partial = copy.deepcopy(good)
        partial['cases'] = partial['cases'][:2]
        archive.validate_cases(partial, self.candidate, passed=False)
        with self.assertRaises(RuntimeError):
            archive.validate_cases(partial, self.candidate, passed=True)

    def test_daemons_must_be_unique_and_recorded_identities_absent(self):
        good = self.report()
        archive.validate_daemons(good, passed=True, probe=lambda pid: None)
        with self.assertRaisesRegex(RuntimeError, 'remains alive'):
            archive.validate_daemons(good, passed=True, probe=lambda pid: [pid, 1001])
        bad = copy.deepcopy(good)
        bad['daemon_identities']['e1'] = [101, 2000]
        with self.assertRaisesRegex(RuntimeError, 'duplicate'):
            archive.validate_daemons(bad, passed=True, probe=lambda pid: None)
        # A daemon may have exited naturally before cleanup; its pinned identity
        # is independently absent, so no fabricated shutdown command is required.
        good['cleanup'][0].pop('identity')
        good['commands'] = [row for row in good['commands'] if row['name'] != 'shutdown-e0']
        archive.validate_daemons(good, passed=True, probe=lambda pid: None)
        archive.validate_cases(good, self.candidate, passed=True)

    def test_expected_context_binds_actual_command_source_and_binding_bytes(self):
        name, engine, variant, _ = archive.smoke.CASES[1]
        state, _ = archive.expected_fixture(variant)
        binding = {'logical_repository_id': 'm1-trace-smoke-fixture-v1', 'physical_root': str(self.root / engine),
                   'filesystem_policy': {'os': 'linux', 'case_sensitive': True, 'digest_algorithm': 'SHA-256'}}
        (self.root / 'bindings').mkdir()
        (self.root / 'bindings' / (engine + '.json')).write_text(json.dumps(binding))
        command = archive.validate_cases(self.report(), self.candidate, passed=True)[name]
        manifest = {'source_identity': {'base_commit': 'b' * 40, 'patch_sha256': 'c' * 64, 'reconstructed_git_tree': 'd' * 40}}
        result = archive.expected_context(name, engine, state, manifest, self.candidate, command, binding)
        self.assertEqual(result['command_policy_sha256'], archive.digest(archive.smoke.canonical(command['argv'])))
        self.assertEqual(result['snapshot_id'], state['snapshot_id'])
        self.assertEqual(result['source_identity'], manifest['source_identity'])
        with self.assertRaisesRegex(RuntimeError, 'binding differs'):
            archive.expected_context(name, engine, state, manifest, self.candidate, command,
                                     dict(binding, physical_root='/other/worktree'))

    def test_trace_requires_command_receipt_and_context_identity(self):
        context = {'engine_id': 'e0', 'binary_sha256': 'a' * 64}
        state, _ = archive.expected_fixture('A')
        trace = self.root / 'trace.jsonl'
        parsed = {'header': dict(context, command_id='command', trace_id='trace', context_sha256='b' * 64),
                  'sha256': 'c' * 64, 'completion_sha256': 'd' * 64,
                  'completion': {'trace_path': str(trace)}, 'nodes': {},
                  'observations': {}, 'footer': {'unknown_counts': {'unobserved': 1}}}
        row = {'command_id': 'command', 'trace_id': 'trace', 'context_sha256': 'b' * 64,
               'trace_sha256': 'c' * 64, 'completion_sha256': 'd' * 64, 'node_count': 0,
               'unknown_counts': {'unobserved': 1},
               'listing_truthfulness': archive.smoke.check_listing_projections(parsed, state)}
        archive.validate_trace(parsed, context, row, trace, state, 'b' * 64)
        for field in ('command_id', 'trace_id', 'context_sha256'):
            bad = copy.deepcopy(parsed)
            bad['header'][field] = 'different'
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'binding differs'):
                archive.validate_trace(bad, context, row, trace, state, 'b' * 64)
        bad = copy.deepcopy(parsed)
        bad['completion']['trace_path'] = '/another/trace'
        with self.assertRaises(RuntimeError):
            archive.validate_trace(bad, context, row, trace, state, 'b' * 64)

    def test_guard_is_exact_run_and_unsignalled(self):
        unit = 'shared-eval-guard-' + archive.RUN_ID + '-' + 'a' * 32 + '.scope'
        identity = {'Id': unit, 'InvocationID': 'b' * 32, 'ControlGroup': '/owned/' + unit}
        guard = {'status': 'completed', 'scope': {'unit': unit, 'invocation_id': 'b' * 32},
                 'final_scope_cleanup': {'verified': True, 'present': False, 'signalled': False}}
        archive.validate_guard(guard, identity)
        guard['final_scope_cleanup']['signalled'] = True
        with self.assertRaises(RuntimeError):
            archive.validate_guard(guard, identity)


if __name__ == '__main__':
    unittest.main()
