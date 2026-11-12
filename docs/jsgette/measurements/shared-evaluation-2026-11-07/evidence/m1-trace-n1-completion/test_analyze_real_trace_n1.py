"""Offline arithmetic/proof checks; synthetic rows are never runtime evidence."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import analyze_real_trace_n1 as analyze


def boundary(cpu, monotonic, before, after):
    return {'monotonic_ns': monotonic, 'harness_process_cpu_before_ns': before,
            'harness_process_cpu_after_ns': after, 'snapshot': {
                'eligible': True, 'directory_identity': {'device': 1, 'inode': 2},
                'cpu.stat': {'usage_usec': cpu}, 'memory.events': {'oom': 0, 'oom_kill': 0}}}


class AnalyzerTest(unittest.TestCase):
    def test_output_requires_raw_bep_and_independent_reference_digest(self):
        config = {'run_root': '/owned/unit', 'reference_only': False}
        output = {'path': '/owned/unit/state/o00/bin/normalize_cmd', 'bytes': 23, 'sha256': 'a' * 64}
        entry = {'name': 'bin/normalize_cmd', 'uri': 'file://' + output['path'], 'length': '23', 'digest': 'a' * 64}
        def events(value):
            return [{'namedSetOfFiles': {'files': [value]}}]
        analyze.bind_output(events(entry), output, config, output)
        for key, value in (('uri', 'file:///outside/normalize_cmd'), ('digest', 'b' * 64),
                           ('length', '24'), ('length', True)):
            with self.assertRaises(RuntimeError):
                analyze.bind_output(events({**entry, key: value}), output, config, output)
        for bad_oracle in (None, {**output, 'sha256': 'b' * 64}, {**output, 'bytes': 24}):
            with self.assertRaises(RuntimeError):
                analyze.bind_output(events(entry), output, config, bad_oracle)
        with self.assertRaises(RuntimeError):
            analyze.bind_output(events(entry) * 2, output, config, output)

    def test_imported_helper_closure_is_complete_and_current(self):
        paths = {Path(module.__file__).resolve() for module in (analyze.runner, analyze.runner.smoke,
                 analyze.runner.baseline, analyze.runner.fixture, analyze.runner.resources, analyze.runner.nc)}
        paths.update(analyze.E / 'harness' / name for name in ('output_bases.py', 'native_source_build.py'))
        paths.update(analyze.WORK / 'm1-trace-design' / name for name in ('replay.py', 'trace-v1.contract.json'))
        pins = {analyze.linux_path(path): analyze.archive.sha(path) for path in paths}
        analyze.check_helper_closure(pins, {})
        for path in paths:
            self.assertEqual(analyze.c_path(analyze.linux_path(path)), path)
        missing = dict(pins)
        del missing[next(iter(missing))]
        with self.assertRaisesRegex(RuntimeError, 'Complete imported helper closure'):
            analyze.check_helper_closure(missing, {})
        original_sha = analyze.archive.sha
        changed = Path(analyze.runner.nc.__file__).resolve()
        with patch.object(analyze.archive, 'sha', side_effect=lambda path: 'changed' if path == changed else original_sha(path)):
            with self.assertRaisesRegex(RuntimeError, 'Imported helper changed'):
                analyze.check_helper_closure(pins, {})

    def test_conservative_ratio_uses_on_upper_off_lower(self):
        value = analyze.cpu_ratio([20, 24], [10, 12])
        self.assertEqual(value['bounds'], [20 / 12, 2.4])
        self.assertEqual(value['conservative_ratio'], 2.4)
        self.assertEqual(analyze.ratio(24, 10)['ratio'], 2.4)

    def test_unknown_denominator_never_zero_or_infinity(self):
        for on, off in ((None, [1, 2]), ([1, 2], [0, 2]), ([2, 1], [1, 2]),
                        ([1, float('inf')], [1, 2]), ([1, 2], [True, 2]),
                        ([1e308, 1e308], [1e-308, 1e-308])):
            self.assertIsNone(analyze.cpu_ratio(on, off)['conservative_ratio'])
        for off in (None, 0, -1, True, float('nan')):
            self.assertIsNone(analyze.ratio(1, off)['ratio'])

    def test_unknown_pair_prevents_favorable_subset_statistics(self):
        result = analyze.five_stats([0.1, 0.2, None, 0.3, 0.4])
        self.assertEqual(result['known_pairs'], 4)
        self.assertIsNone(result['median'])
        self.assertIsNone(result['range'])
        self.assertEqual(analyze.five_stats([5, 1, 3, 2, 4])['median'], 3)
        with self.assertRaises(RuntimeError):
            analyze.five_stats([1, 2, 3, 4])

    def test_raw_endpoints_recompute_quantized_bounds_and_reject_tampering(self):
        start, end = boundary(100, 0, 10, 20), boundary(200, 1000, 100, 110)
        reported = analyze.runner.window(start, end)
        result = analyze.checked_window(start, end, reported)
        self.assertEqual(result['harness_cpu_bounds']['scope_counter_resolution_ns'], 1000)
        self.assertEqual(result['non_harness_seconds_bounds'], [98900 / 1e9, 100920 / 1e9])
        wrong = copy.deepcopy(reported)
        wrong['raw_scope_cpu_seconds'] += 1
        with self.assertRaises(RuntimeError):
            analyze.checked_window(start, end, wrong)
        reset = copy.deepcopy(end)
        reset['snapshot']['cpu.stat']['usage_usec'] = 99
        with self.assertRaises(ValueError):
            analyze.checked_window(start, reset, reported)
        unknown = boundary(100, 1000, 100, 110)
        result = analyze.checked_window(start, unknown, analyze.runner.window(start, unknown))
        self.assertIsNone(result['non_harness_seconds_bounds'])
        self.assertEqual(result['raw_scope_cpu_seconds'], 0)

    def test_verified_receipt_must_bind_exact_archive_and_postprocessor(self):
        report = {'config': {'run_id': 'unit', 'postprocess_sha256': {'accounting': 'a'}}}
        metadata = {'passed': True, 'kind': 'n1-trace-diagnostic', 'run_id': 'unit',
                    'report_sha256': 'report', 'manifest_sha256': 'manifest', 'archive_sha256': 'archive'}
        verification = {'verified': True, 'status': 'verified_diagnostic_no_gate_verdict',
                        'performance_eligible': False, 'milestone_verdict': None, 'run_id': 'unit',
                        'archive_sha256': 'archive', 'report_sha256': 'report', 'manifest_sha256': 'manifest',
                        'postprocess_sha256': {'accounting': 'a'}}
        progress = {'status': 'captured_and_verified_no_gate_verdict', 'verification_sha256': 'verification',
                    'launch_exit_code': 0, 'capture_exit_code': 0}
        def check(v):
            analyze.linked_verification(report, 'report', metadata, v, 'verification', progress, 'manifest')
        check(verification)
        for key, value in (('archive_sha256', 'other'), ('verified', False),
                           ('postprocess_sha256', {'accounting': 'changed'}), ('performance_eligible', True)):
            bad = {**verification, key: value}
            with self.assertRaises(RuntimeError):
                check(bad)

    def test_pairing_keeps_windows_and_order_with_unknowns(self):
        rows = []
        for index, spec in enumerate(analyze.runner.schedule()):
            cost = 2 if spec['trace_enabled'] else 1
            measured = {'build_uuid': f'm{index}', 'client_wall_seconds': cost,
                        'daemon_cpu_seconds': cost, 'sampled_peak_daemon_pss_bytes': cost,
                        'windows': {name: {'non_harness_seconds_bounds': [cost, cost * 2],
                            'raw_scope_cpu_seconds': cost, 'boundary_wall_seconds': cost}
                            for name in analyze.WINDOWS}}
            rows.append({**spec, 'matching_contract': {'unit': True},
                         'preparation': {'build_uuid': f'p{index}'}, 'measured': measured})
        rows[1]['measured']['windows']['tail']['non_harness_seconds_bounds'] = None
        result = analyze.paired(rows)
        self.assertEqual(result['restart']['pairs'][0]['observed_order'], [r['run_id'] for r in rows[:2]])
        self.assertEqual(result['restart']['pairs'][0]['windows']['build']['non_harness_cpu']['conservative_ratio'], 4)
        self.assertIsNone(result['restart']['five_pair_statistics']['windows']['tail']['non_harness_cpu']['median'])
        self.assertEqual(result['warm']['five_pair_statistics']['windows']['tail']['non_harness_cpu']['median'], 4)
        rows[1]['matching_contract'] = {'unit': False}
        with self.assertRaises(RuntimeError):
            analyze.paired(rows)

    def test_incomplete_matrix_barrier_precedes_any_row_read(self):
        order = {'schema_version': 1, 'role': 'matrix', 'diagnostic_only': True, 'milestone_verdict': None,
                 'runner_timeout_seconds': 2400, 'rows': analyze.runner.schedule()}
        raw = json.dumps(order).encode()
        progress = {'status': 'running', 'role': 'matrix', 'order_sha256': hashlib.sha256(raw).hexdigest()}
        scratch = Path(__file__).parent / 'offline-test-tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            root = Path(temporary)
            with patch('sys.argv', ['analyze', '--order', str(root / 'order.json'), '--output', str(root / 'result.json')]), \
                    patch.object(analyze, 'read_json', side_effect=[(order, raw), (progress, b'{}')]), \
                    patch.object(analyze, 'summarize_row') as read_row:
                with self.assertRaisesRegex(RuntimeError, 'Matrix is incomplete'):
                    analyze.main()
                read_row.assert_not_called()
            self.assertFalse((root / 'result.json').exists())


if __name__ == '__main__':
    unittest.main()
