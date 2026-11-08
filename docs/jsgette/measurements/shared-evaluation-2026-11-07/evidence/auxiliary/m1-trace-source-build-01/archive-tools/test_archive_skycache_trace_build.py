"""Offline guard, command, candidate and real-BEP-shape rejection tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import archive_skycache_trace_build as archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='trace-archive-test-', dir=archive.WORK)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.qpatch = patch.object(archive, 'Q', self.root)
        self.qpatch.start()
        self.addCleanup(self.qpatch.stop)

    def guard(self):
        unit = 'shared-eval-guard-' + archive.RUN_ID + '-' + 'a' * 32 + '.scope'
        identity = {'Id': unit, 'InvocationID': 'b' * 32, 'ControlGroup': '/user.slice/owned/' + unit}
        guard = {'status': 'completed', 'scope': {'unit': unit, 'invocation_id': 'b' * 32},
                 'final_scope_cleanup': {'verified': True, 'present': False, 'signalled': False}}
        return guard, identity

    def test_guard_requires_exact_run_identity_and_unsignalled_absence(self):
        guard, identity = self.guard()
        self.assertEqual(archive.validate_guard(guard, identity).name, identity['Id'])
        for field, value in (('verified', False), ('present', True), ('signalled', True)):
            bad = copy.deepcopy(guard)
            bad['final_scope_cleanup'][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                archive.validate_guard(bad, identity)
        with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
            archive.validate_guard(guard, dict(identity, InvocationID='c' * 32))

    def test_success_requires_all_exact_commands_and_thirteen_test_labels(self):
        commands = archive.expected_commands()
        self.assertEqual(len(archive.qualification.TESTS), 13)
        self.assertTrue(all(label in commands['tests'] for label in archive.qualification.TESTS))
        report = {'commands': [{'name': name, 'argv': argv, 'returncode': 0} for name, argv in commands.items()]}
        archive.validate_commands(report, True)
        for mutation in ('missing', 'duplicate', 'changed', 'failed'):
            bad = copy.deepcopy(report)
            if mutation == 'missing':
                bad['commands'].pop()
            elif mutation == 'duplicate':
                bad['commands'].append(bad['commands'][0])
            elif mutation == 'changed':
                bad['commands'][2]['argv'].append('//unexpected:test')
            else:
                bad['commands'][2]['returncode'] = 1
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                archive.validate_commands(bad, True)
        archive.validate_commands({'commands': report['commands'][:1]}, False)

    def events(self):
        rows = [{'started': {'uuid': '00000000-0000-0000-0000-000000000001'}}]
        for label in archive.qualification.TESTS:
            directory = self.root / 'build-output/execroot/_main/bazel-out/k8-fastbuild/testlogs' / label[2:].replace(':', '/')
            rows += [{'id': {'testResult': {'label': label, 'run': 1, 'shard': 1, 'attempt': 1}},
                      'testResult': {'status': 'PASSED', 'testActionOutput': [
                          {'name': name, 'uri': (directory / name).as_uri()} for name in ('test.log', 'test.xml')]}},
                     {'id': {'testSummary': {'label': label}}, 'testSummary': {'overallStatus': 'PASSED'}}]
        rows.append({'finished': {'exitCode': {'name': 'SUCCESS', 'code': 0}}})
        return rows

    def bep(self, rows):
        path = self.root / 'tests.bep.jsonl'
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return path

    def test_bep_requires_successful_full_suite_results(self):
        rows = self.events()
        result, outputs = archive.bep_evidence(self.bep(rows), tests=True)
        self.assertTrue(result['success'])
        self.assertEqual(len(outputs), 26)
        for mutation in ('missing', 'failed', 'unfinished'):
            bad = copy.deepcopy(rows)
            if mutation == 'missing':
                del bad[2]
            elif mutation == 'failed':
                bad[2]['testSummary']['overallStatus'] = 'FAILED'
            else:
                bad.pop()
            result, _ = archive.bep_evidence(self.bep(bad), tests=True)
            self.assertFalse(result['success'])

    def test_bep_rejects_duplicate_summary_and_outside_target_output(self):
        rows = self.events()
        rows.insert(-1, rows[2])
        with self.assertRaisesRegex(RuntimeError, 'duplicate test summary'):
            archive.bep_evidence(self.bep(rows), tests=True)
        rows = self.events()
        rows[1]['testResult']['testActionOutput'][0]['uri'] = (self.root / 'outside/test.log').as_uri()
        with self.assertRaisesRegex(RuntimeError, 'leaves named target'):
            archive.bep_evidence(self.bep(rows), tests=True)

    def test_bep_allows_named_shards_but_not_arbitrary_descendants(self):
        rows = self.events()
        output = rows[1]['testResult']['testActionOutput'][0]
        output['uri'] = output['uri'].replace('/test.log', '/shard_2_of_3/test.log')
        _, outputs = archive.bep_evidence(self.bep(rows), tests=True)
        self.assertIn('shard_2_of_3', str(outputs[0]['path']))
        output['uri'] = output['uri'].replace('shard_2_of_3', 'secret-environment')
        with self.assertRaisesRegex(RuntimeError, 'test-output path'):
            archive.bep_evidence(self.bep(rows), tests=True)

    def candidate(self):
        return {'candidate': {'source': str(self.root / 'build-output/execroot/_main/bazel-out/k8-fastbuild/bin/src/bazel-dev'),
                    'destination': str(self.root / 'candidate/bazel-skycache-trace'), 'bytes': 100, 'sha256': 'a' * 64},
                'product_binary_sha256_after_build': 'a' * 64, 'product_binary_sha256_after_tests': 'a' * 64}

    def test_generated_configuration_and_seed_provenance_must_match_recorded_hashes(self):
        files = {'build.bazelrc': 'generated_rc_sha256', 'seed-copy.json': 'seed_copy_sha256'}
        report = {}
        for name, field in files.items():
            (self.root / name).write_bytes(name.encode())
            report[field] = archive.sha(self.root / name)
        self.assertEqual(len(archive.verify_generated_inputs(report)), 2)
        for name in files:
            (self.root / name).write_bytes(b'post-run mutation')
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'generated input changed'):
                archive.verify_generated_inputs(report)
            (self.root / name).write_bytes(name.encode())

    def test_candidate_requires_fixed_owned_path_and_stable_post_test_bytes(self):
        good = self.candidate()
        archive.candidate_record(good)
        bad = copy.deepcopy(good)
        bad['product_binary_sha256_after_tests'] = 'b' * 64
        with self.assertRaisesRegex(RuntimeError, 'not stable'):
            archive.candidate_record(bad)
        bad = copy.deepcopy(good)
        bad['candidate']['source'] = str(self.root / 'elsewhere/bazel')
        with self.assertRaisesRegex(RuntimeError, 'location'):
            archive.candidate_record(bad)


if __name__ == '__main__':
    unittest.main()
