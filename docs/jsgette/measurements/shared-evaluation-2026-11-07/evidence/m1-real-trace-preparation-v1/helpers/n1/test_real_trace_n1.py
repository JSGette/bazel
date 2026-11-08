"""Offline N1 protocol tests only; synthetic records are never Java qualification."""
import copy
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import prepare_real_trace_n1 as prepare
import run_real_trace_n1 as runner
import archive_real_trace_n1 as archive

WORK = Path(__file__).resolve().parent


class ProtocolTest(unittest.TestCase):
    def test_fixed_order_five_pairs_each_separate_lifecycle(self):
        rows = runner.schedule()
        self.assertEqual(len(rows), 20)
        self.assertEqual(len({row['run_id'] for row in rows}), 20)
        for pair in range(1, 6):
            restart = [row['trace_enabled'] for row in rows if row['pair'] == pair and row['state'] == 'restart']
            warm = [row['trace_enabled'] for row in rows if row['pair'] == pair and row['state'] == 'warm']
            self.assertEqual(restart, [False, True] if pair % 2 else [True, False])
            self.assertEqual(warm, list(reversed(restart)))
        self.assertEqual(runner.HISTORY['preparations_per_engine'], 1)
        self.assertFalse(runner.HISTORY['preparation_trace_enabled'])

    def test_drain_requires_closed_unfailed_acknowledged_publication(self):
        ownership = {'after_session_close': {'session_closed': True, 'pending_validation_entries': 0}}
        runner.check_drain_documents(ownership, {'failed': False, 'writes': 0, 'published': False})
        runner.check_drain_documents(ownership, {'failed': False, 'writes': 3, 'published': True})
        for bad_owner, stats in (({}, {'failed': False, 'writes': 0}),
                                ({'after_session_close': {'session_closed': False, 'pending_validation_entries': 0}},
                                 {'failed': False, 'writes': 0}),
                                ({'after_session_close': {'session_closed': True, 'pending_validation_entries': 1}},
                                 {'failed': False, 'writes': 0}),
                                (ownership, {'failed': True, 'writes': 0}),
                                (ownership, {'failed': False, 'writes': 3, 'published': False})):
            with self.assertRaises(RuntimeError):
                runner.check_drain_documents(bad_owner, stats)

    def test_directory_snapshot_detects_empty_directory_addition(self):
        scratch = WORK / 'offline-test-tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            (root / '.git').write_text('administrative pointer')
            (root / 'a.txt').write_text('a')
            before = runner.directory_inventory(root)
            (root / 'empty').mkdir()
            after = runner.directory_inventory(root)
            self.assertEqual(before, [{'path': 'a.txt', 'type': 'file'}])
            self.assertIn({'path': 'empty', 'type': 'directory'}, after)
            self.assertNotEqual(runner.nc.digest(before), runner.nc.digest(after))

    def test_trace_policy_retains_skycache_when_tracing_disabled(self):
        paths = {'disk': Path('/owned/disk'), 'repository': Path('/owned/repo'), 'bases': {'0': Path('/owned/base')},
                 'tmp': Path('/owned/tmp'), 'skycache': Path('/owned/skycache')}
        row = {'index': 0}
        off = runner.policy(paths, row)
        on = runner.policy(paths, row, Path('/owned/trace'), Path('/owned/context'))
        self.assertTrue(off['experimental_local_skycache'])
        self.assertEqual(off['experimental_m1_trace'], '')
        self.assertEqual(off['symlink_prefix'], str(Path('/owned/links')) + '/')
        self.assertEqual({k: v for k, v in off.items() if not k.startswith('experimental_m1_trace')},
                         {k: v for k, v in on.items() if not k.startswith('experimental_m1_trace')})

    def test_manifest_hash_has_no_launch_self_reference(self):
        candidate = {'destination': '/candidate', 'sha256': 'a' * 64, 'bytes': 1}
        with patch.object(prepare, 'wsl_path', side_effect=lambda path: '/mnt/c/' + Path(path).name):
            path, raw, argv = prepare.make_manifest(runner.schedule()[0], Path('/frozen'), candidate,
                {'base_commit': 'b' * 40}, {}, {}, {}, {}, '/source', {}, {'path': '/reference'}, {})
        value = json.loads(raw)
        digest = runner.smoke.digest(raw)
        self.assertEqual(argv, value['launch_argv_prefix'] + ['--manifest-sha256', digest])
        runtime_argv, runtime_hash = runner.launch_binding(value['launch_argv_prefix'], digest)
        self.assertEqual(runtime_argv, argv)
        self.assertEqual(runtime_hash, runner.smoke.digest(prepare.encoded(argv)))
        self.assertNotIn('launch_argv', value)
        self.assertNotIn(digest.encode(), raw)

    def test_unknown_cpu_bounds_are_not_clamped_to_zero(self):
        result = runner.nc.harness_cpu_bounds({'harness_process_cpu_before_ns': 0, 'harness_process_cpu_after_ns': 100},
            {'harness_process_cpu_before_ns': 200, 'harness_process_cpu_after_ns': 500}, 200)
        self.assertEqual(result['non_harness_status'], 'unknown')
        self.assertIsNone(result['non_harness_scope_cpu_ns_bounds'])

    def test_smoke_requires_actual_cases_and_successful_owned_cleanup(self):
        candidate, source = {'sha256': 'candidate'}, {'reconstructed_git_tree': 'source'}
        rows = [{'name': name, 'engine_id': engine, 'variant': variant, 'trace_enabled': enabled,
                 'oracle_passed': True, 'command_id': str(i), **({'trace_id': 'trace' + str(i)} if enabled else {})}
                for i, (name, engine, variant, enabled) in enumerate(runner.smoke.CASES)]
        report = {'status': 'real_trace_interop_passed_no_gate_verdict', 'candidate': candidate, 'source_identity': source,
                  'cases': rows, 'scope_before': {'name': '/owned/test.scope'}, 'scope_after': {'name': '/owned/test.scope'},
                  'cleanup': [{'recorded_identity_absent': True}, {'recorded_identity_absent': True}]}
        guard = {'status': 'completed', 'exit_code': 0, 'scope': {'unit': 'test.scope'},
                 'final_scope_cleanup': {'present': False, 'signalled': False, 'verified': True}}
        runner.validate_smoke_summary(report, guard, candidate, source)
        for mutate in (lambda r, g: r.update(status='failed'), lambda r, g: r['cases'].pop(),
                       lambda r, g: g['final_scope_cleanup'].update(signalled=True),
                       lambda r, g: r['cleanup'][0].update(recorded_identity_absent=False),
                       lambda r, g: r['cases'][1].update(trace_id=r['cases'][2]['trace_id'])):
            trial_report, trial_guard = copy.deepcopy((report, guard))
            mutate(trial_report, trial_guard)
            with self.assertRaises(RuntimeError):
                runner.validate_smoke_summary(trial_report, trial_guard, candidate, source)

    def test_archived_smoke_proof_uses_actual_producer_shape(self):
        metadata = {'passed': True, 'interop_verification_sha256': 'p'}
        proof = {'verified': True, 'interop_passed': True, 'report_sha256': 'r', 'errors': [],
                 'recorded_daemons_absent': True, 'final_scope_absent': True, 'final_scope_unsignalled': True}
        prepare.validate_smoke_receipt(metadata, proof, 'p', 'r')
        with self.assertRaises(ValueError):
            prepare.validate_smoke_receipt(metadata, {**proof, 'report_sha256': 'changed'}, 'p', 'r')
        with self.assertRaises(ValueError):
            prepare.validate_smoke_receipt(metadata, proof, 'changed', 'r')

    def test_source_candidate_accepts_linux_identity_under_windows_emitter(self):
        root = '/mnt/shared-evaluation-20261107/' + runner.smoke.BUILD_ROOT.name
        candidate = {'destination': root + '/candidate/bazel-skycache-trace', 'sha256': 'a' * 64, 'bytes': 123}
        report = {'status': 'build_and_focused_tests_passed_no_gate_verdict', 'root': root,
                  'source_before': {'a': 'b'}, 'source_after': {'a': 'b'}, 'candidate': candidate,
                  'commands': [{'name': n, 'returncode': 0} for n in ('version', 'build', 'tests', 'shutdown')],
                  'product_binary_sha256_after_build': candidate['sha256'],
                  'product_binary_sha256_after_tests': candidate['sha256'],
                  'bundle': {'base_commit': runner.smoke.BASE, 'combined_patch': {'sha256': 'b' * 64},
                             'instrumented_git_tree': 'c' * 40, 'baseline_git_tree': 'd' * 40}}
        proof = {'verified': True, 'qualification_passed': True, 'final_scope_absent': True,
                 'final_scope_unsignalled': True, 'report_sha256': 'r', 'path': candidate['destination'],
                 'sha256': candidate['sha256'], 'bytes': candidate['bytes']}
        self.assertEqual(prepare.source_candidate(report, proof, 'r')[0], candidate)
        with self.assertRaises(ValueError):
            prepare.source_candidate({**report, 'root': root.replace('/', '\\')}, proof, 'r')

    def test_guard_command_scope_and_abort_are_distinct(self):
        config = {'run_id': 'm1-trace-n1-reference-01', 'launch_argv_prefix': ['python3', '-B', '/literal.py']}
        argv, digest = runner.launch_binding(config['launch_argv_prefix'], 'a' * 64)
        unit = 'shared-eval-guard-' + config['run_id'] + '-' + 'b' * 32 + '.scope'
        identity = {'Id': unit, 'InvocationID': 'c' * 32, 'ControlGroup': '/owned/' + unit}
        policy = {'command_sha256': digest, 'scope': {'unit': unit}, 'memory_max_mib': 10240,
                  'cpu_quota_percent': 600, 'minimum_free_physical_bytes': 3072 * archive.MIB,
                  'minimum_free_commit_bytes': 4096 * archive.MIB}
        guard = {'status': 'completed', 'exit_code': 0, 'scope': {'unit': unit, 'invocation_id': 'c' * 32},
                 'final_scope_cleanup': {'verified': True, 'present': False, 'signalled': False}}
        self.assertTrue(archive.validate_control(config, 'a' * 64, prepare.encoded(argv), guard, policy, identity))
        self.assertFalse(archive.validate_control(config, 'a' * 64, prepare.encoded(argv),
            {**guard, 'status': 'infrastructure_abort', 'exit_code': 75,
             'final_scope_cleanup': {'verified': True, 'present': False, 'signalled': True}}, policy, identity))
        for bad_guard, bad_policy, bad_identity in (
                ({**guard, 'scope': {**guard['scope'], 'invocation_id': 'changed'}}, policy, identity),
                (guard, {**policy, 'command_sha256': 'changed'}, identity),
                (guard, policy, {**identity, 'ControlGroup': '/unrelated'})):
            with self.assertRaises(RuntimeError):
                archive.validate_control(config, 'a' * 64, prepare.encoded(argv), bad_guard, bad_policy, bad_identity)

    def test_reference_requires_successful_bound_external_verification(self):
        run_id = runner.SERIES + '-reference-01'
        helpers = {'archive_real_trace_n1.py': 'archiver', 'analyze_native_n2.py': 'accounting'}
        reference = {'manifest_sha256': 'manifest', 'config': {'postprocess_sha256': helpers}}
        metadata = {'kind': 'n1-trace-diagnostic', 'passed': True, 'run_id': run_id,
                    'report_sha256': 'report', 'manifest_sha256': 'manifest', 'archive_sha256': 'archive'}
        verified = {'verified': True, 'status': 'verified_diagnostic_no_gate_verdict', 'run_id': run_id,
                    'performance_eligible': False, 'milestone_verdict': None, 'report_sha256': 'report',
                    'manifest_sha256': 'manifest', 'archive_sha256': 'archive', 'postprocess_sha256': helpers}
        runner.validate_reference_verification(reference, 'report', metadata, verified, 'archive', helpers)
        for bad in ({}, {**verified, 'verified': False}, {**verified, 'status': 'stopped_for_assessment'},
                    {**verified, 'report_sha256': 'other'}, {**verified, 'manifest_sha256': 'other'},
                    {**verified, 'archive_sha256': 'other'}, {**verified, 'postprocess_sha256': {}},
                    {**verified, 'performance_eligible': True}):
            with self.assertRaises(RuntimeError):
                runner.validate_reference_verification(reference, 'report', metadata, bad, 'archive', helpers)
        with self.assertRaises(RuntimeError):
            runner.validate_reference_verification(reference, 'report', {**metadata, 'passed': False},
                                                     verified, 'archive', helpers)

    def test_imported_accounting_validator_must_match_manifest_pin(self):
        helpers = prepare.postprocess_helpers()
        self.assertEqual(archive.check_postprocess_helpers({'postprocess_sha256': helpers}), helpers)
        for changed in ({}, {**helpers, 'analyze_native_n2.py': '0' * 64},
                        {**helpers, 'archive_real_trace_n1.py': '0' * 64}):
            with self.assertRaises(RuntimeError):
                archive.check_postprocess_helpers({'postprocess_sha256': changed})

    def test_archive_member_corruption_and_trace_budget(self):
        scratch = WORK / 'offline-test-tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            directory = Path(temporary)
            payload = b'{"proof":true}'
            with tarfile.open(directory / 'runtime.tar.gz', 'w:gz') as out:
                info = tarfile.TarInfo('report.json')
                info.size = len(payload)
                out.addfile(info, io.BytesIO(payload))
            record = {'path': 'report.json', 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
            metadata = {'archive_sha256': archive.sha(directory / 'runtime.tar.gz'), 'members': [record]}
            archive.save(directory / 'runtime.tar.gz.json', metadata)
            reader = archive.Archive(directory)
            self.assertEqual(reader.json('report.json'), {'proof': True})
            reader.rows['report.json']['sha256'] = '0' * 64
            with self.assertRaises(RuntimeError):
                reader.raw('report.json')
            reader.tar.close()
            self.assertEqual(archive.member_limit('measured/trace.jsonl'), 256 * archive.MIB)
            self.assertNotIn('state/o00/server/jvm.out', archive.allowed_runtime_names())

    def test_materialized_output_rejects_outside_or_missing_file(self):
        scratch = WORK / 'offline-test-tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            base = root / 'owned'
            base.mkdir()
            executable = base / 'normalize_cmd'
            executable.write_bytes(b'nonempty executable proof')
            bep = root / 'bep.jsonl'
            bep.write_text('{}')
            metrics = {'finished': True, 'exit_code': {}, 'files': [{'uri': executable.as_uri()}]}
            # Production materialization runs on Linux. Mock URL path decoding on Windows for this pure boundary test.
            with patch.object(runner.baseline, 'bep_metrics', return_value=metrics), \
                 patch.object(runner, 'unquote', side_effect=lambda value: str(executable)):
                self.assertTrue(runner.materialized_output(bep, base)['verified_before_request_end'])
                executable.unlink()
                with self.assertRaises(RuntimeError):
                    runner.materialized_output(bep, base)

    def test_matrix_dry_run_launches_nothing_and_rejects_changed_command(self):
        scratch = WORK / 'offline-test-tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            directory = Path(temporary)
            row = {'run_id': runner.SERIES + '-reference-01', 'pair': 0, 'state': 'restart', 'trace_enabled': False}
            path, raw, command = prepare.make_manifest(row, directory,
                {'destination': '/candidate-unqualified-unit-test', 'sha256': 'a' * 64, 'bytes': 1}, {}, {}, {}, {}, {}, '/source', {})
            path.write_bytes(raw)
            command_path = directory / (row['run_id'] + '-command.json')
            command_path.write_bytes(prepare.encoded(command))
            row.update(manifest=str(path), manifest_sha256=archive.sha(path), command=str(command_path), command_sha256=archive.sha(command_path))
            order = {'schema_version': 1, 'role': 'reference', 'rows': [row], 'diagnostic_only': True,
                     'milestone_verdict': None, 'runner_timeout_seconds': 2400,
                     'control_tools': [{'path': str(p), 'sha256': archive.sha(p)} for p in (
                         WORK / 'archive_real_trace_n1.py', WORK / 'run_real_trace_n1_matrix.ps1',
                         runner.E / 'harness/run_auxiliary.ps1', runner.E / 'harness/analyze_native_n2.py')]}
            order_path = directory / 'order.json'
            order_path.write_bytes(prepare.encoded(order))
            argv = ['pwsh', '-NoProfile', '-File', str(WORK / 'run_real_trace_n1_matrix.ps1'), '-OrderJson', str(order_path), '-DryRun']
            result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['launched'])
            self.assertFalse((directory / 'execution-progress.json').exists())
            command_path.write_bytes(prepare.encoded(['do-not-execute']))
            result = subprocess.run(argv, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((directory / 'execution-progress.json').exists())


if __name__ == '__main__':
    unittest.main()
