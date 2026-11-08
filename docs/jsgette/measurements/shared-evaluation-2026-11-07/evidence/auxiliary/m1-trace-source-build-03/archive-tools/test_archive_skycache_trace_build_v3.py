"""Offline guard, command, candidate and real-BEP-shape rejection tests."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import archive_skycache_trace_build_v3 as archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='trace-archive-test-', dir=archive.WORK)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'reused-state'
        self.state.mkdir()
        self.spatch = patch.object(archive, 'S', self.state)
        self.spatch.start()
        self.addCleanup(self.spatch.stop)
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
        report = {'commands': [{'name': name, 'argv': argv, 'returncode': 0, 'cwd': str(self.state / 'source')} for name, argv in commands.items()]}
        archive.validate_commands(report, True)
        for mutation in ('missing', 'duplicate', 'changed', 'failed', 'wrong-root', 'wrong-order'):
            bad = copy.deepcopy(report)
            if mutation == 'missing':
                bad['commands'].pop()
            elif mutation == 'duplicate':
                bad['commands'].append(bad['commands'][0])
            elif mutation == 'changed':
                bad['commands'][2]['argv'].append('//unexpected:test')
            elif mutation == 'failed':
                bad['commands'][2]['returncode'] = 1
            elif mutation == 'wrong-root':
                bad['commands'][2]['cwd'] = str(self.root / 'source')
            else:
                bad['commands'][1], bad['commands'][2] = bad['commands'][2], bad['commands'][1]
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                archive.validate_commands(bad, True)
        archive.validate_commands({'commands': report['commands'][:1]}, False)

    def events(self):
        rows = [{'started': {'uuid': '00000000-0000-0000-0000-000000000001'}}]
        for label in archive.qualification.TESTS:
            directory = self.state / 'build-output/execroot/_main/bazel-out/k8-fastbuild/testlogs' / label[2:].replace(':', '/')
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
        return {'candidate': {'source': str(self.state / 'build-output/execroot/_main/bazel-out/k8-fastbuild/bin/src/bazel-dev'),
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

    def test_reuse_proof_binds_preserved_inputs_and_both_repair_preimages(self):
        q = archive.qualification
        def record(name, data):
            return {'path': name, 'mode': '100644', 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'git_blob': hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()}
        old = [record(name, b'old') for name in q.ALLOWED]
        old.extend(record('other/' + str(i), b'same') for i in range(18590))
        new = [record(name, b'new') for name in q.ALLOWED] + old[2:]
        (self.state / 'source-members.json').write_text(json.dumps(old))
        (self.root / 'source-members.json').write_text(json.dumps(new))
        old_proof = {'current': {'files_verified': 18592, 'member_records_sha256': 'a' * 64}}
        (self.root / 'prior-source-verification.json').write_text(json.dumps(old_proof))
        (self.root / 'prior-report.json').write_bytes(b'fake pinned report')
        prior_sha = archive.sha(self.root / 'prior-report.json')
        bundle = {'base_commit': q.BASE, 'baseline_git_tree': q.BASELINE_TREE, 'safe_subset_patch_sha256': q.SAFE_PATCH,
                  'instrumented_git_tree': 'e' * 40, 'previous_instrumented_git_tree': q.PREVIOUS_TREE,
                  'previous_trace_source_sha256': {name: old[index]['sha256'] for index, name in enumerate(q.ALLOWED)},
                  'repair_paths': list(q.ALLOWED)}
        (self.root / 'bundle.json').write_text(json.dumps(bundle))
        prior = {'bundle': dict(bundle, instrumented_git_tree=q.PREVIOUS_TREE,
                                trace_source_sha256=bundle['previous_trace_source_sha256'])}
        config = {'environment': {'TMPDIR': '/owned/tmp'}, 'generated_rc_sha256': 'c' * 64,
                  'seed_copy_sha256': 'd' * 64, 'configuration_sha256': 'e' * 64, 'bootstrap': {'sha256': 'f' * 64}}
        prior.update(config)
        absence = {'pid_file': str(self.state / 'build-output/server/server.pid.txt'),
                   'verified': True, 'live': False, 'pid': 123}
        report = {'prior_report': {'path': str(q.OLD_EVIDENCE / 'report.json'), 'sha256': prior_sha},
                  'prior_archive_sha256': q.PRIOR_ARCHIVE_SHA, 'prior_metadata_sha256': q.PRIOR_METADATA_SHA,
                  'source_before_patch': old_proof['current'], 'preserved_prior_files': {'report.json': prior_sha},
                  'preserved_prior_files_after': {'report.json': prior_sha}, 'repair': q.member_delta(old, new),
                  'bundle': bundle, 'bundle_sha256': archive.sha(self.root / 'bundle.json'),
                  'prior_server_absence': absence, 'prior_server_absence_before_patch': absence, **config}
        for folder, data in (('source-before', b'old'), ('source-after', b'new')):
            for name in q.ALLOWED:
                path = self.root / folder / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        with patch.object(q, 'PRIOR_REPORT_SHA', prior_sha), patch.object(q, 'read_prior', return_value=(prior, {}, old_proof)), \
                patch.object(q, 'preserved_files', return_value={'report.json': prior_sha}):
            self.assertTrue(archive.verify_reuse(report)['verified'])
            for folder in ('source-before', 'source-after'):
                path = self.root / folder / q.ALLOWED[0]
                saved = path.read_bytes()
                path.write_bytes(b'bad')
                with self.subTest(folder=folder), self.assertRaisesRegex(RuntimeError, 'preimage/postimage'):
                    archive.verify_reuse(report)
                path.write_bytes(saved)
            for kind in ('old-proof', 'preserved', 'live', 'environment'):
                bad = copy.deepcopy(report)
                if kind == 'old-proof':
                    bad['source_before_patch'] = {'files_verified': 0}
                elif kind == 'preserved':
                    bad['preserved_prior_files_after'] = {}
                elif kind == 'live':
                    bad['prior_server_absence_before_patch']['live'] = True
                else:
                    bad['environment']['TMPDIR'] = '/wrong'
                with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                    archive.verify_reuse(bad)


if __name__ == '__main__':
    unittest.main()
