"""Ten-engine archived accounting checks only; no Bazel/runtime filesystem."""
import copy
import unittest

import analyze_native_n10 as analysis


def sample(hosting='native'):
    processes = [{'pid': 100 + i, 'start_ticks': 200 + i, 'pss_bytes': 400 + i,
                  'errors': {}, 'engines': [str(n) for n in range(10)] if hosting == 'native' else [str(i)]}
                 for i in range(1 if hosting == 'native' else 10)]
    return {'eligible': True, 'expected_engine_count': 10, 'valid_engine_count': 10,
            'identity_continuity_valid': True, 'membership_continuity_valid': True,
            'cgroup': {'eligible': True}, 'pss_bytes': sum(row['pss_bytes'] for row in processes),
            'engines': {str(i): {'pid': 100 if hosting == 'native' else 100 + i,
                                'start_ticks': 200 if hosting == 'native' else 200 + i,
                                'errors': {}} for i in range(10)},
            'processes': processes, 'sample_duration_ns': 1000}


def results():
    return [{'index': i, 'exit_code': 0, 'timed_out': False, 'behavior': {'passed': True},
             'policy_verified': True, 'digest_oracle': {'required': True, 'matches': True},
             'started_ns': 10, 'exited_ns': 1010, 'output': {'sha256': 'a' * 64},
             'bep_identity': {'index': i, 'build_uuid': f'00000000-0000-0000-0000-{i:012d}',
                              'workspace': f'/worktree/{i}', 'server_pid': 100 + i,
                              'start_time_millis': 100 + i, 'finish_time_millis': 200 + i}}
            for i in range(10)]


class AnalysisTests(unittest.TestCase):
    def test_ratios_preserve_bounds_unknowns_and_zero_denominators(self):
        self.assertEqual(analysis.ratio_bounds([6, 8], [10, 12])['bounds'], [0.5, 0.8])
        for candidate, baseline in ((None, [1, 2]), ([1, 2], [0, 2]), ([2, 1], [1, 2]),
                                    ([1, True], [1, 2]), ([1, float('nan')], [1, 2]),
                                    ([-1, 2], [1, 2]), ([1, 2], [None, None])):
            self.assertIsNone(analysis.ratio_bounds(candidate, baseline)['bounds'])
        self.assertEqual(analysis.ratio_bounds([0, 0], [1, 2])['bounds'], [0, 0])

    def test_ten_native_engines_count_one_pid_pss_once(self):
        result = analysis.sample_coverage([sample()], 10, 'native')
        self.assertEqual(result['verified_peak_pss_bytes'], 400)
        self.assertEqual(result['expected_unique_processes'], 1)
        self.assertEqual(result['complete_unique_process_samples'], 1)
        for mutate in (lambda s: s['processes'].append(dict(s['processes'][0])),
                       lambda s: s.update(pss_bytes=4000),
                       lambda s: s['processes'][0].update(pss_bytes=None),
                       lambda s: s.update(identity_continuity_valid=False)):
            invalid = sample()
            mutate(invalid)
            self.assertEqual(analysis.sample_coverage([invalid], 10, 'native')['invalid_sample_indices'], [0])

    def test_ten_private_processes_sum_pss_and_reject_two_plus_zero_engine_mapping(self):
        private = sample('private')
        result = analysis.sample_coverage([private], 10, 'private')
        self.assertEqual(result['expected_unique_processes'], 10)
        self.assertEqual(result['verified_peak_pss_bytes'], sum(range(400, 410)))
        bad = copy.deepcopy(private)
        bad['engines']['9'].update(pid=100, start_ticks=200)
        bad['processes'][0]['engines'].append('9')
        bad['processes'][9]['engines'] = []
        self.assertEqual(analysis.sample_coverage([bad], 10, 'private')['invalid_sample_indices'], [0])
        for process in bad['processes']:
            del process['engines']
        self.assertEqual(analysis.sample_coverage([bad], 10, 'private')['invalid_sample_indices'], [0])
        private['processes'][9]['engines'] = ['8']
        self.assertEqual(analysis.sample_coverage([private], 10, 'private')['invalid_sample_indices'], [0])

    def test_native_reciprocal_list_must_cover_all_ten_once(self):
        for keys in ([str(i) for i in range(9)], ['0'] + [str(i) for i in range(10)],
                     [str(i) for i in range(9)] + ['unknown'], None):
            invalid = sample()
            invalid['processes'][0]['engines'] = keys
            self.assertEqual(analysis.sample_coverage([invalid], 10, 'native')['invalid_sample_indices'], [0])

    def test_missing_tenth_engine_is_startup_pending_only_before_complete(self):
        pending = sample('private')
        pending.update(eligible=False, valid_engine_count=9, pss_bytes=None)
        pending['processes'].pop()
        pending['engines']['9'] = {'errors': {'mapping': 'MissingEngine'}}
        result = analysis.sample_coverage([pending, sample('private')], 10, 'private')
        self.assertEqual(result['pending_startup_samples'], 1)
        self.assertEqual(result['complete_unique_process_samples'], 1)
        self.assertEqual(result['invalid_sample_indices'], [])
        self.assertEqual(analysis.sample_coverage([sample('private'), pending], 10, 'private')['invalid_sample_indices'], [1])

    def test_ten_uuid_overlap_and_independent_output_oracle_required(self):
        proof = analysis.result_proof(results(), 10, False)
        self.assertEqual(proof['unique_build_uuids'], 10)
        self.assertEqual(proof['all_clients_overlap_seconds'], 0.091)
        for change in (lambda rows: rows[9]['bep_identity'].update(build_uuid=rows[0]['bep_identity']['build_uuid']),
                       lambda rows: rows[9]['bep_identity'].update(start_time_millis=201, finish_time_millis=300),
                       lambda rows: rows[9]['digest_oracle'].update(matches=False),
                       lambda rows: rows.pop()):
            rows = results()
            change(rows)
            with self.assertRaises(ValueError):
                analysis.result_proof(rows, 10, False)

    def test_tenth_bep_pid_must_match_its_engine_not_process_union(self):
        intervals = [row['bep_identity'] for row in results()]
        fixture = {i: {'workspace': f'/worktree/{i}'} for i in range(10)}
        final = sample('private')
        analysis.validate_result_bindings(intervals, fixture, final)
        swapped = copy.deepcopy(intervals)
        swapped[0]['server_pid'], swapped[9]['server_pid'] = 109, 100
        with self.assertRaises(ValueError):
            analysis.validate_result_bindings(swapped, fixture, final)
        final['processes'].pop()
        with self.assertRaises(ValueError):
            analysis.validate_result_bindings(intervals, fixture, final)

    def test_missing_and_reset_pressure_counters_stay_unknown(self):
        before = {'eligible': True, 'directory_identity': {'inode': 1},
                  'memory.events': {'max': 5}, 'memory.pressure': {'some': {'total': 100}}}
        after = copy.deepcopy(before)
        after['memory.events']['max'] = 7
        after['memory.pressure']['some']['total'] = 125
        result = analysis.pressure_summary(before, after)
        self.assertEqual(result['counter_deltas']['memory.events']['max'], 2)
        self.assertEqual(result['pressure']['memory.pressure']['some']['total_stall_usec_delta'], 25)
        self.assertIsNone(result['pressure']['memory.pressure']['full']['total_stall_usec_delta'])
        after['memory.events']['max'] = 4
        self.assertIsNone(analysis.pressure_summary(before, after)['counter_deltas']['memory.events']['max'])

    def test_missing_role_blocks_ratios_and_never_declares_gate(self):
        rows = [{'role': role, 'diagnostic_verified': False} for role in analysis.ROLES]
        result = analysis.comparisons(rows)
        self.assertIsNotNone(result['blocked_reason'])
        self.assertIsNone(result['milestone_verdict'])
        self.assertNotIn('non_harness_cpu_native_over_private', result)


if __name__ == '__main__':
    unittest.main()
