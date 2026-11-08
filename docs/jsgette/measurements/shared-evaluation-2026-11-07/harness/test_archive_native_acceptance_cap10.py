"""Offline C-backed archive fixtures; never access the real runtime."""
import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

WORK = Path(__file__).resolve().parent


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'cap10_archive', WORK / 'archive_native_acceptance_cap10.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.harness = (WORK / 'bazel/tools/multiworktree/native_integration_test.py').read_bytes()
        self.checks = self.module.required_checks(self.harness)
        self.temporary = tempfile.TemporaryDirectory(prefix='cap10-archive-offline-', dir=WORK)
        self.addCleanup(self.temporary.cleanup)

    def fixture(self, passed=True, duplicate_output=False):
        m = self.module
        d = Path(self.temporary.name)
        m.R = d / 'runtime'
        m.ROOT = m.R / 'native-acceptance-cap10-01'
        m.HARNESS = m.R / 'native-acceptance-harness-v2/native_integration_test.py'
        m.E = d / 'evidence'
        m.DEST = m.E / 'native-acceptance-cap10-01'
        m.ROOT.mkdir(parents=True)
        m.HARNESS.parent.mkdir(parents=True)
        m.HARNESS.write_bytes(self.harness)
        q = m.E / 'native-cap10-build-01'
        q.mkdir(parents=True)
        candidate = m.R / 'native-cap10-build-v1/candidate/bazel-native-cap10'
        candidate.parent.mkdir(parents=True)
        candidate.write_bytes(b'offline synthetic candidate; not executable')

        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))

        write(q / 'report.json', {
            'status': 'build_and_focused_tests_passed_no_gate_verdict', 'admission_cap': 10,
            'root': str(m.R / 'native-cap10-build-v1'),
            'candidate': {'destination': str(candidate), 'sha256': m.sha(candidate)}})
        write(q / 'candidate-verification.json', {
            'verified': True, 'qualification_passed': True, 'path': str(candidate),
            'bytes': candidate.stat().st_size, 'sha256': m.sha(candidate),
            'report_sha256': m.sha(q / 'report.json')})
        command = ['python3', '-B', str(m.HARNESS), '--bazel', str(candidate), '--root',
                   str(m.ROOT), '--expected-engine-cap', '10', '--heap-mib', '7680',
                   '--processors', '2', '--tmp-root', str(m.ROOT / 'tmp')]
        write(m.DEST / 'command.json', command)
        unit = 'shared-eval-guard-native-acceptance-cap10-01-' + '0' * 32 + '.scope'
        write(m.DEST / 'guard/result.json', {
            'status': 'completed' if passed else 'command_failed',
            'exit_code': 0 if passed else 1,
            'scope': {'unit': unit, 'invocation_id': 'synthetic'},
            'final_scope_cleanup': {'verified': True, 'present': False, 'signalled': False}})
        write(m.DEST / 'guard/scope-identity.json', {
            'Id': unit, 'InvocationID': 'synthetic', 'ControlGroup': '/synthetic-absent/' + unit})
        write(m.DEST / 'guard/policy.json', {'command_sha256': m.sha(m.DEST / 'command.json')})
        outputs = {name: str(m.ROOT / 'state' / name) for name in m.WORKSPACES}
        if duplicate_output:
            outputs['engine-09'] = outputs['a']
        write(m.ROOT / 'results.json', {
            'harness_sha256': m.HARNESS_SHA, 'binary_sha256': m.sha(candidate),
            'expected_engine_cap': 10, 'heap_mib': 7680, 'processors': 2,
            'temporary_root': str(m.ROOT / 'tmp'),
            'repository_cache': str(m.ROOT / 'repository-cache'),
            'required_checks': sorted(self.checks),
            'checks': sorted(self.checks) if passed else sorted(self.checks)[:16],
            'success': passed, 'run_completed': passed, 'cleanup_errors': [],
            'run_error': None if passed else 'synthetic failure',
            'daemon_identities': [[101, 201], [102, 202]] if passed else [[101, 201]],
            'output_bases': outputs})
        if passed:
            for case in m.CASES:
                if case != 'cleanup':
                    (m.ROOT / case).mkdir()
            write(m.ROOT / 'shutdown-isolation/closed-reopen-proof.json', {'synthetic': True})
        for name in m.WORKSPACES + ('overflow',):
            (m.ROOT / name).mkdir()
            (m.ROOT / name / 'input.txt').write_text(name + '\n')
        m.live_identity = lambda identity: False
        return m

    def run_archive(self, m):
        with contextlib.redirect_stdout(io.StringIO()):
            m.main()
        return json.loads((m.DEST / 'runtime.tar.gz.json').read_text())

    def test_literal_command_and_frozen_checks(self):
        m = self.module
        ast.parse((WORK / 'archive_native_acceptance_cap10.py').read_text())
        self.assertEqual(len(self.checks), 21)
        command = json.loads((WORK / 'native-acceptance-cap10-command.json').read_text())
        self.assertEqual(command, [
            'python3', '-B', m.HARNESS.as_posix(), '--bazel',
            (m.R / 'native-cap10-build-v1/candidate/bazel-native-cap10').as_posix(),
            '--root', m.ROOT.as_posix(), '--expected-engine-cap', '10', '--heap-mib',
            '7680', '--processors', '2', '--tmp-root', (m.ROOT / 'tmp').as_posix()])

    def test_success_contract_and_overwrite_rejection(self):
        m = self.fixture()
        meta = self.run_archive(m)
        self.assertTrue(meta['passed'])
        self.assertEqual(len(meta['completed_checks']), 21)
        self.assertEqual(len(json.loads((m.DEST / 'final-outputs.json').read_text())['outputs']), 22)
        self.assertEqual(len([x for x in meta['members'] if x['path'].startswith('source/')]), 11)
        self.assertIn('cases/shutdown-isolation/closed-reopen-proof.json',
                      [x['path'] for x in meta['members']])
        for key in ('candidate_report_sha256', 'candidate_verification_sha256',
                    'guard_result_sha256', 'scope_identity_sha256', 'command_sha256',
                    'guard_policy_sha256', 'final_scope_absent', 'final_scope_unsignalled',
                    'recorded_daemons_absent', 'members'):
            self.assertIn(key, meta)
        with self.assertRaisesRegex(RuntimeError, 'no overwrite'):
            m.main()

    def test_failed_run_preserved_as_failed(self):
        meta = self.run_archive(self.fixture(passed=False))
        self.assertIs(meta['passed'], False)
        self.assertEqual(len(meta['completed_checks']), 16)

    def test_duplicate_output_mapping_rejected(self):
        m = self.fixture(duplicate_output=True)
        with self.assertRaisesRegex(RuntimeError, 'ten-engine output mapping'):
            m.main()


if __name__ == '__main__':
    unittest.main()
