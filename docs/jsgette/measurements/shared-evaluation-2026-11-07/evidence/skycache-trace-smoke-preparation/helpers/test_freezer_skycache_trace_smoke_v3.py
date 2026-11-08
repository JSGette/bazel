"""Synthetic C-only proof fixtures; never emit the actual smoke preparation."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import freezer_skycache_trace_smoke_v3 as freeze


def digest(value):
    return hashlib.sha256(freeze.encoded(value)).hexdigest()


def fixture():
    unit = 'shared-eval-guard-m1-trace-source-build-03-' + 'a' * 32 + '.scope'
    group = '/owned/' + unit
    bundle = {'base_commit': freeze.build.BASE, 'baseline_git_tree': freeze.build.BASELINE_TREE,
        'safe_subset_patch_sha256': freeze.build.SAFE_PATCH, 'instrumented_git_tree': freeze.TREE,
        'combined_patch': {'sha256': freeze.PATCH}, 'tests': list(freeze.build.TESTS),
        'source_archive': {'bytes': 590694400, 'sha256': 'b' * 64},
        'previous_instrumented_git_tree': freeze.build.PREVIOUS_TREE,
        'previous_trace_source_sha256': {name: 'a' * 64 for name in freeze.build.ALLOWED},
        'trace_source_sha256': {name: 'b' * 64 for name in freeze.build.ALLOWED},
        'repair_paths': list(freeze.build.ALLOWED)}
    product = {'destination': str(freeze.BUILD_ROOT / 'candidate/bazel-skycache-trace'),
               'source': str(freeze.STATE_ROOT / 'build-output/execroot/_main/bazel-out/k8-fastbuild/bin/src/bazel-dev'),
               'sha256': 'c' * 64, 'bytes': 100}
    commands = freeze.expected_commands()
    source_current = {'files_verified': 18592, 'member_records_sha256': 'd' * 64}
    report = {'root': str(freeze.BUILD_ROOT), 'status': 'build_and_focused_tests_passed_no_gate_verdict',
        'bundle': bundle, 'bundle_sha256': freeze.BUNDLE_SHA, 'scope_before': {'name': group}, 'scope_after': {'name': group},
        'helper_sha256': {freeze.linux_path(freeze.WORK / 'build_skycache_trace_v3.py'): freeze.BUILD_LAUNCHER_SHA,
                         freeze.linux_path(freeze.WORK / 'build_skycache_trace_v2.py'): freeze.build.PREVIOUS_HELPER_SHA,
                         freeze.linux_path(freeze.E / 'harness/native_source_build.py'): freeze.build.BUILD_HELPER},
        'commands': [{'name': name, 'argv': argv, 'returncode': 0, 'cwd': str(freeze.STATE_ROOT / 'source')}
                     for name, argv in commands.items()],
        'candidate': product, 'product_binary_sha256_after_build': product['sha256'],
        'product_binary_sha256_after_tests': product['sha256'], 'source_before': source_current, 'source_after': source_current,
        'source_after_patch': source_current, 'build_state_root': str(freeze.STATE_ROOT),
        'source_root': str(freeze.STATE_ROOT / 'source'), 'reused_build_state': True,
        'generated_rc_sha256': 'e' * 64, 'seed_copy_sha256': 'f' * 64,
        'environment': {'TMPDIR': str(freeze.STATE_ROOT / 'tmp')},
        'configuration_sha256': 'a' * 64, 'bootstrap': {'sha256': 'b' * 64}}
    old_current = {'files_verified': 18592, 'member_records_sha256': '1' * 64}
    prior = {'bundle': dict(bundle, instrumented_git_tree=freeze.build.PREVIOUS_TREE,
                            trace_source_sha256=dict(bundle['previous_trace_source_sha256'])),
             'root': str(freeze.STATE_ROOT), 'status': 'failed', 'source_before': old_current, 'source_after': old_current}
    prior.update({key: copy.deepcopy(report[key]) for key in
                  ('environment', 'generated_rc_sha256', 'seed_copy_sha256', 'configuration_sha256', 'bootstrap')})
    prior_source = {'verified': True, 'qualification_passed': False, 'current': old_current}
    prior_metadata = {'archive_sha256': freeze.build.PRIOR_ARCHIVE_SHA, 'current_source_verification': prior_source,
                      'members': [{'path': name, 'sha256': 'f' * 64} for name in freeze.build.PRESERVED]}
    preserved = {row['path']: row['sha256'] for row in prior_metadata['members']}
    preserved['source.tar'] = prior['bundle']['source_archive']['sha256']
    repairs = [{'path': name, 'old': {'path': name, 'mode': '100644', 'sha256': 'a' * 64},
                'new': {'path': name, 'mode': '100644', 'sha256': 'b' * 64}} for name in freeze.build.ALLOWED]
    report.update(prior_report={'path': freeze.linux_path(freeze.PRIOR_INPUT / 'report.json'), 'sha256': freeze.build.PRIOR_REPORT_SHA},
                  prior_archive_sha256=freeze.build.PRIOR_ARCHIVE_SHA, prior_metadata_sha256=freeze.build.PRIOR_METADATA_SHA,
                  source_before_patch=old_current, preserved_prior_files=preserved, preserved_prior_files_after=preserved, repair=repairs)
    absence = {'pid_file': str(freeze.STATE_ROOT / 'build-output/server/server.pid.txt'), 'verified': True, 'live': False, 'pid': None}
    report.update(prior_server_absence=absence, prior_server_absence_before_patch=absence)
    guard = {'status': 'completed', 'exit_code': 0, 'scope': {'unit': unit, 'invocation_id': '1' * 32},
             'final_scope_cleanup': {'present': False, 'signalled': False, 'verified': True}}
    identity = {'Id': unit, 'InvocationID': '1' * 32, 'ControlGroup': group}
    source = {'verified': True, 'qualification_passed': True, 'current': source_current,
        'instrumented_git_tree': freeze.TREE, 'source_archive': bundle['source_archive'],
        'generated_input_sha256': {'build.bazelrc': report['generated_rc_sha256'], 'seed-copy.json': report['seed_copy_sha256']}}
    source['incremental_reuse'] = {'verified': True, 'build_state_root': str(freeze.STATE_ROOT),
        'source_root': str(freeze.STATE_ROOT / 'source'), 'prior_report_sha256': freeze.build.PRIOR_REPORT_SHA,
        'prior_archive_sha256': freeze.build.PRIOR_ARCHIVE_SHA, 'preserved_prior_files': preserved, 'repair': repairs}
    tests = {'verified': True, 'qualification_passed': True,
        'build': {'success': True, 'command_uuids': ['build-uuid'], 'finished': [{'code': 0, 'name': 'SUCCESS'}], 'test_summaries': {}},
        'tests': {'success': True, 'command_uuids': ['tests-uuid'], 'finished': [{'code': 0, 'name': 'SUCCESS'}],
                  'test_summaries': {name: {'overallStatus': 'PASSED'} for name in freeze.build.TESTS}}}
    command = ['python3', '-B', freeze.linux_path(freeze.WORK / 'build_skycache_trace_v3.py'), '--bundle',
        freeze.linux_path(freeze.WORK / 'trace-build-preparation/bundle-v3/bundle.json'), '--bundle-sha256', freeze.BUNDLE_SHA]
    records = {'report': report, 'guard': guard, 'identity': identity, 'source': source, 'tests': tests, 'command': command,
               'prior_report': prior, 'prior_metadata': prior_metadata, 'prior_source': prior_source}
    hashes = {name: digest(value) for name, value in records.items()}
    hashes.update(prior_report=freeze.build.PRIOR_REPORT_SHA, prior_metadata=freeze.build.PRIOR_METADATA_SHA)
    records['policy'] = {'command_sha256': hashes['command'], 'memory_max_mib': 10240, 'cpu_quota_percent': 600}
    candidate = {'verified': True, 'qualification_passed': True, 'report_sha256': hashes['report'],
        'guard_result_sha256': hashes['guard'], 'scope_identity_sha256': hashes['identity'],
        'source_verification_sha256': hashes['source'], 'test_verification_sha256': hashes['tests'],
        'final_scope_absent': True, 'final_scope_unsignalled': True,
        'path': product['destination'], 'sha256': product['sha256'], 'bytes': product['bytes']}
    records['candidate'] = candidate
    records['archive'] = {'passed': True, 'report_status': report['status'], 'candidate': candidate,
        'current_source_verification': source, 'test_verification': tests, 'bundle_sha256': freeze.BUNDLE_SHA,
        'launcher_sha256': freeze.BUILD_LAUNCHER_SHA, 'missing_files': [], 'build_state_root': str(freeze.STATE_ROOT),
        'source_root': str(freeze.STATE_ROOT / 'source'), 'reused_build_state': True}
    hashes.update({name: digest(value) for name, value in records.items() if name not in hashes})
    return records, hashes, bundle


class FreezeTests(unittest.TestCase):
    def test_complete_proof_accepts_only_third_build_and_exact_source(self):
        records, hashes, bundle = fixture()
        self.assertEqual(freeze.validate_records(records, hashes, bundle), records['report']['candidate'])
        for mutation in (lambda r, h, b: r['report'].update(root='/mnt/other/build'),
                         lambda r, h, b: b.update(instrumented_git_tree='0' * 40),
                         lambda r, h, b: b['combined_patch'].update(sha256='0' * 64),
                         lambda r, h, b: r['report']['commands'].pop(),
                         lambda r, h, b: r['report'].update(product_binary_sha256_after_tests='0' * 64)):
            trial = copy.deepcopy((records, hashes, bundle))
            mutation(*trial)
            with self.assertRaises(RuntimeError):
                freeze.validate_records(*trial)

    def test_guard_and_linked_proofs_fail_closed(self):
        records, hashes, bundle = fixture()
        for mutation in (lambda r: r['guard']['final_scope_cleanup'].update(signalled=True),
                         lambda r: r['identity'].update(InvocationID='2' * 32),
                         lambda r: r['candidate'].update(report_sha256='0' * 64),
                         lambda r: r['candidate'].update(qualification_passed=False),
                         lambda r: r['source'].update(verified=False),
                         lambda r: r['archive']['missing_files'].append('tests.log')):
            trial = copy.deepcopy(records)
            mutation(trial)
            with self.assertRaises(RuntimeError):
                freeze.validate_records(trial, hashes, bundle)

    def test_missing_or_failed_thirteenth_suite_cannot_emit(self):
        records, hashes, bundle = fixture()
        last = freeze.build.TESTS[-1]
        records['tests']['tests']['test_summaries'].pop(last)
        with self.assertRaisesRegex(RuntimeError, 'All13'):
            freeze.validate_records(records, hashes, bundle)
        records['tests']['tests']['test_summaries'][last] = {'overallStatus': 'FAILED'}
        with self.assertRaisesRegex(RuntimeError, 'All13'):
            freeze.validate_records(records, hashes, bundle)

    def test_two_root_commands_keep_old_state_but_write_only_new_bep_paths(self):
        expected = freeze.expected_commands()
        for name in ('build', 'tests'):
            self.assertIn('--output_base=' + str(freeze.STATE_ROOT / 'build-output'), expected[name])
            self.assertIn('--build_event_json_file=' + str(freeze.BUILD_ROOT / (name + '.bep.jsonl')), expected[name])
            self.assertNotIn('--build_event_json_file=' + str(freeze.STATE_ROOT / (name + '.bep.jsonl')), expected[name])
        records, hashes, bundle = fixture()
        for kind in ('cwd', 'old-bep', 'missing-helper'):
            bad = copy.deepcopy(records)
            if kind == 'cwd':
                bad['report']['commands'][1]['cwd'] = str(freeze.BUILD_ROOT / 'source')
            elif kind == 'old-bep':
                row = bad['report']['commands'][1]
                row['argv'] = [arg.replace(str(freeze.BUILD_ROOT / 'build.bep.jsonl'),
                                           str(freeze.STATE_ROOT / 'build.bep.jsonl')) for arg in row['argv']]
            else:
                del bad['report']['helper_sha256'][freeze.linux_path(freeze.WORK / 'build_skycache_trace_v2.py')]
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                freeze.validate_records(bad, hashes, bundle)

    def test_incremental_source_and_repair_receipts_cannot_substitute_for_each_other(self):
        records, hashes, bundle = fixture()
        for kind in ('old-source', 'repair-hash', 'preserved', 'live', 'policy', 'old-report-digest'):
            bad, bad_hashes, bad_bundle = copy.deepcopy((records, hashes, bundle))
            if kind == 'old-source':
                bad['report']['source_before_patch'] = bad['report']['source_before']
            elif kind == 'repair-hash':
                bad['report']['repair'][0]['old']['sha256'] = '0' * 64
            elif kind == 'preserved':
                bad['report']['preserved_prior_files_after'] = {}
            elif kind == 'live':
                bad['report']['prior_server_absence_before_patch']['live'] = True
            elif kind == 'policy':
                bad['report']['environment']['TMPDIR'] = '/tmp'
            else:
                bad_hashes['prior_report'] = '0' * 64
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                freeze.validate_records(bad, bad_hashes, bad_bundle)
    def test_manifest_uses_posix_paths_and_actual_runtime_helper_hashes(self):
        helpers = {'qualify_skycache_trace.py': '1' * 64, 'm1-trace-design/replay.py': '2' * 64,
                   'm1-trace-design/trace-v1.contract.json': '3' * 64}
        manifest = freeze.manifest_for('4' * 64, '5' * 64, helpers)
        self.assertEqual(manifest['root'], '/mnt/shared-evaluation-20261107/m1-trace-acceptance-v1')
        self.assertNotIn('\\', manifest['build_report']['path'])
        self.assertTrue(manifest['build_report']['path'].startswith('/mnt/c/'))
        self.assertEqual(manifest['harness_sha256'], helpers['qualify_skycache_trace.py'])
        self.assertEqual(len(manifest['cases']), 8)
        self.assertEqual(sum(row[3] for row in manifest['cases']), 5)

    def archive(self, directory, tests, *, bad_suite=False):
        path = directory / 'runtime.tar.gz'
        members = []
        with tarfile.open(path, 'w:gz') as archive:
            for name, key in (('build.bep.jsonl', 'build'), ('tests.bep.jsonl', 'tests')):
                rows = [{'started': {'uuid': tests[key]['command_uuids'][0]}}]
                for label, summary in tests[key]['test_summaries'].items():
                    rows.append({'id': {'testSummary': {'label': label}}, 'testSummary': summary})
                if bad_suite and key == 'tests':
                    rows.pop()
                rows.append({'finished': {'exitCode': tests[key]['finished'][0]}})
                raw = b''.join(freeze.encoded(row).replace(b'\n', b'') + b'\n' for row in rows)
                member = tarfile.TarInfo(name)
                member.size = len(raw)
                archive.addfile(member, io.BytesIO(raw))
                members.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
        return path, {'archive_bytes': path.stat().st_size, 'archive_sha256': freeze.sha(path), 'members': members}

    def test_actual_archived_bep_must_match_success_receipt(self):
        records, _, _ = fixture()
        with tempfile.TemporaryDirectory(prefix='freeze-trace-test-', dir=freeze.WORK) as temporary:
            root = Path(temporary)
            path, metadata = self.archive(root, records['tests'])
            freeze.verify_archived_bep(path, metadata, records['tests'])
            path, metadata = self.archive(root, records['tests'], bad_suite=True)
            with self.assertRaisesRegex(RuntimeError, 'Actual archived BEP differs'):
                freeze.verify_archived_bep(path, metadata, records['tests'])

    def test_existing_preparation_is_preserved_before_any_input_read(self):
        with tempfile.TemporaryDirectory(prefix='freeze-trace-test-', dir=freeze.WORK) as temporary:
            root = Path(temporary).resolve()
            output = root / 'existing'
            output.mkdir()
            sentinel = output / 'keep.txt'
            sentinel.write_bytes(b'preserve prior preparation')
            with patch.object(freeze, 'WORK', root), patch.object(freeze, 'OUTPUT', output), \
                    patch.object(freeze, 'read_json') as read:
                with self.assertRaisesRegex(RuntimeError, 'outputs already exist'):
                    freeze.main()
                read.assert_not_called()
            self.assertEqual(sentinel.read_bytes(), b'preserve prior preparation')


if __name__ == '__main__':
    unittest.main()
