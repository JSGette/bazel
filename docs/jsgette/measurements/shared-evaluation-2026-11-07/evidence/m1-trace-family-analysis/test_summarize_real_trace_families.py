"""Synthetic offline checks; these records are never runtime evidence."""
from collections import Counter
import copy
import hashlib
import io
import json
import unittest

import summarize_real_trace_families as families


def fixture():
    rows = [
        {'type': 'header'},
        {'type': 'interval', 'node_id': 1, 'state': 'COMPUTE', 'exclusive_cpu_ns': 7},
        {'type': 'interval', 'node_id': 1, 'state': 'COMPUTE', 'exclusive_cpu_ns': None},
        {'type': 'interval', 'node_id': 1, 'state': 'CHECK_DIRTY', 'exclusive_cpu_ns': 3},
        {'type': 'observation', 'node_id': 1, 'origin': 'evaluated_value', 'value_status': 'unsupported',
         'reason': 'unsupported_value_TestValue', 'projection': None, 'deps_status': 'grouped',
         'deps_origin': 'evaluated_direct_deps', 'groups': [[[2, None]]]},
        {'type': 'observation', 'node_id': 2, 'origin': 'retained_done', 'value_status': 'exact_projection',
         'reason': None, 'projection': {'type': 'DIRECTORY', 'class': 'DirectoryFileStateValue'},
         'deps_status': 'grouped', 'deps_origin': 'retained_done', 'groups': []},
        {'type': 'event', 'node_id': 3},
        {'type': 'node', 'node_id': 1, 'function': 'TEST', 'key_status': 'unsupported',
         'reason': 'unsupported_function_or_root', 'logical_key': None},
        {'type': 'node', 'node_id': 2, 'function': 'FILE_STATE', 'key_status': 'supported',
         'reason': None, 'logical_key': ['main', '0061']},
        {'type': 'node', 'node_id': 3, 'function': 'NO_COMPUTE', 'key_status': 'unsupported',
         'reason': 'unsupported_function_or_root', 'logical_key': None}]
    counts = Counter(row['type'] for row in rows)
    raw = b''.join((json.dumps({**row, 'seq': index}) + '\n').encode() for index, row in enumerate(rows))
    coverage = {'node_count': 3, 'observation_count': 2, 'observed_node_count': 2,
                'retained_only_node_count': 1, 'dependency_only_node_count': 0}
    footer = {'type': 'footer', 'seq': len(rows), 'record_counts': {kind: counts[kind] for kind in families.KINDS},
        **{key: value for key, value in coverage.items() if key != 'observation_count'}, 'edge_count': 1,
        'unknown_counts': {'unsupported_value_TestValue': 1}, 'preceding_bytes_sha256': hashlib.sha256(raw).hexdigest(),
        'complete': True, 'limits_exceeded': [], 'writer_error': None, 'unbalanced_intervals': 0}
    raw += (json.dumps(footer) + '\n').encode()
    replay = {**coverage, 'exclusive_cpu_by_state': {
        'COMPUTE': {'known_exclusive_ns': 7, 'unknown_intervals': 1},
        'CHECK_DIRTY': {'known_exclusive_ns': 3, 'unknown_intervals': 0}}}
    return raw, replay


class FamilySummaryTest(unittest.TestCase):
    def summarize(self, raw, replay):
        return families.stream_trace(io.BytesIO(raw), hashlib.sha256(raw).hexdigest(), len(raw), replay)

    def test_late_node_join_preserves_unknown_cpu_and_captured_references(self):
        result = self.summarize(*fixture())
        family = result['functions']['TEST']
        self.assertEqual(family['exclusive_cpu_by_state']['COMPUTE'], {
            'known_exclusive_ns': 7, 'unknown_intervals': 1, 'intervals': 2, 'complete_exclusive_ns': None})
        self.assertEqual(family['exclusive_cpu_by_state']['CHECK_DIRTY']['complete_exclusive_ns'], 3)
        self.assertEqual(family['unsupported_value_classes'], {'TestValue': 1})
        self.assertEqual(family['key_roots'], {'unknown': 1})
        missing = result['functions']['NO_COMPUTE']
        self.assertEqual(missing['observed_nodes_without_compute_interval'], 1)
        self.assertIsNone(missing['exclusive_cpu_by_state']['COMPUTE']['complete_exclusive_ns'])
        self.assertEqual(result['functions']['FILE_STATE']['retained_only_nodes'], 1)
        self.assertEqual(result['functions']['FILE_STATE']['exact_projections'], [
            {'kind': 'DIRECTORY', 'class': 'DirectoryFileStateValue', 'root': 'main', 'observations': 1}])
        observations = result['observations_by_origin_and_dependencies']
        self.assertEqual(sum(row['null_observation_references'] for row in observations), 1)
        self.assertEqual(sum(row['empty_grouped_observations'] for row in observations), 1)

    def test_prior_raw_exclusive_totals_and_exact_bytes_are_required(self):
        raw, replay = fixture()
        bad = copy.deepcopy(replay)
        bad['exclusive_cpu_by_state']['COMPUTE']['unknown_intervals'] = 0
        with self.assertRaisesRegex(RuntimeError, 'Raw exclusive CPU differs'):
            self.summarize(raw, bad)
        with self.assertRaisesRegex(RuntimeError, 'Streamed trace identity differs'):
            families.stream_trace(io.BytesIO(raw), '0' * 64, len(raw), replay)
        with self.assertRaisesRegex(RuntimeError, 'Oversized'):
            families.stream_trace(io.BytesIO(b'x' * (families.MIB + 1)), '0' * 64, families.MIB + 1, replay)

    def test_rank_never_turns_missing_compute_into_measured_zero(self):
        summary = self.summarize(*fixture())
        rows = [{**copy.deepcopy(summary), 'state': state, 'pair': pair}
                for state in ('restart', 'warm') for pair in range(1, 6)]
        rankings = families.rank_functions(rows)
        test = next(row for row in rankings['restart'] if row['function'] == 'TEST')
        self.assertEqual(test['median_known_exclusive_compute_ns'], 7)
        self.assertEqual(test['complete_exclusive_compute_ns'], [None] * 5)
        missing = next(row for row in rankings['restart'] if row['function'] == 'NO_COMPUTE')
        self.assertEqual(missing['known_exclusive_compute_ns'], [None] * 5)
        self.assertIsNone(missing['median_known_exclusive_compute_ns'])


if __name__ == '__main__':
    unittest.main()
