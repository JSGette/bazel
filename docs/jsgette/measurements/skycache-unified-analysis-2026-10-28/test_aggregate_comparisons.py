"""Small evidence-validation tests; no native processes or real benchmark archives."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import aggregate_comparisons as aggregate


class AggregationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_hash_inventory_rejects_missing_tampered_and_escaping_files(self):
        file = self.root / 'raw.txt'
        file.write_text('original')
        hashes = {'raw.txt': aggregate.sha(file)}
        aggregate.check_files(self.root, hashes, {'raw.txt'})
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            aggregate.check_files(self.root, hashes, {'build.bep.json'})
        file.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            aggregate.check_files(self.root, hashes)
        with self.assertRaises(ValueError):
            aggregate.check_files(self.root, {'../outside': '0' * 64})

    def test_terminal_bep_truncation_fails(self):
        file = self.root / 'build.bep.json'
        success = {'id': {'buildFinished': {}}, 'finished': {'exitCode': {'name': 'SUCCESS'}}, 'lastMessage': True}
        file.write_text(json.dumps(success))
        self.assertEqual(aggregate.stream(file), [success])
        success.pop('lastMessage')
        file.write_text(json.dumps(success))
        with self.assertRaisesRegex(ValueError, 'lastMessage'):
            aggregate.stream(file)

    def test_datadog_cpu_and_rss_fields_are_normalized_without_changing_input(self):
        metrics = {'exit_code': 0, 'timed_out': False, 'elapsed_seconds': 8,
                   'resources': {'user_cpu_seconds': 12, 'system_cpu_seconds': 3, 'max_child_rss_kib': 2048}}
        value = aggregate.successful_metrics(metrics, 'datadog')
        self.assertEqual(value['total_cpu_seconds'], 15)
        self.assertEqual(value['maxrss_kib'], 2048)
        self.assertNotIn('ru_maxrss_raw', metrics['resources'])
        metrics['exit_code'] = 1
        with self.assertRaises(ValueError):
            aggregate.successful_metrics(metrics, 'datadog')

    def test_descriptive_summary_requires_six_distinct_rounds_per_arm(self):
        rows = [dict(workload='fixture', arm=arm, round=index,
                     **{metric: index + 1 for metric in aggregate.comparison.METRICS})
                for arm in aggregate.ARMS for index in range(6)]
        result = aggregate.describe(rows)
        self.assertEqual(result['fixture']['vanilla']['elapsed_seconds'],
                         {'count': 6, 'median': 3.5, 'min': 1, 'max': 6})
        with self.assertRaises(ValueError):
            aggregate.describe(rows[:-1])
        rows[0]['round'] = 1
        with self.assertRaises(ValueError):
            aggregate.describe(rows)

    def test_post_gc_heap_is_parsed_from_successful_raw_output_only(self):
        directory = self.root / 'heap-after-gc'
        directory.mkdir()
        output = directory / 'stdout.log'
        output.write_text('1234:\n garbage-first heap total 1048576K, used 321K\n')
        diagnostics = {'forced-gc': {'exit_code': 0, 'timed_out': False},
                       'heap-after-gc': {'exit_code': 0, 'timed_out': False,
                                         'heap': {'used_heap_bytes': None}}}
        (self.root / 'forced-gc').mkdir()
        for phase in ('forced-gc', 'heap-after-gc'):
            (self.root / phase / 'metrics.json').write_text(json.dumps({'exit_code': 0, 'timed_out': False}))
        self.assertEqual(aggregate.post_gc_heap(self.root, diagnostics), 321 * 1024)
        output.write_text('unrecognized collector output\n')
        self.assertIsNone(aggregate.post_gc_heap(self.root, diagnostics))
        output.write_text('heap total 100K, used 10K')
        diagnostics['forced-gc']['exit_code'] = 1
        self.assertIsNone(aggregate.post_gc_heap(self.root, diagnostics))
        self.assertIsNone(aggregate.post_gc_heap(self.root, {}))

    def test_jdk27_g1_heap_recovered_offline_and_existing_nonnull_value_must_agree(self):
        text = '1234:\n garbage-first heap   total reserved 2097152K, committed 133120K, used 38234K [0x0, 0x1)\n'
        parsed = aggregate.parse_heap_info(text)
        self.assertEqual(parsed, {'reserved_heap_bytes': 2097152 * 1024,
                                  'committed_heap_bytes': 133120 * 1024,
                                  'used_heap_bytes': 38234 * 1024})
        diagnostics = {}
        for phase in ('forced-gc', 'heap-after-gc'):
            directory = self.root / phase
            directory.mkdir()
            metrics = {'exit_code': 0, 'timed_out': False}
            (directory / 'metrics.json').write_text(json.dumps(metrics))
            diagnostics[phase] = dict(metrics)
        (self.root / 'heap-after-gc' / 'stdout.log').write_text(text)
        diagnostics['heap-after-gc']['heap'] = {'unavailable': 'Original parser did not recognize this format.'}
        self.assertEqual(aggregate.post_gc_heap(self.root, diagnostics), 38234 * 1024)
        diagnostics['heap-after-gc']['heap'] = {'used_heap_bytes': 38234 * 1024}
        self.assertEqual(aggregate.post_gc_heap(self.root, diagnostics), 38234 * 1024)
        diagnostics['heap-after-gc']['heap']['used_heap_bytes'] = 1
        with self.assertRaisesRegex(ValueError, 'disagrees with verified raw'):
            aggregate.post_gc_heap(self.root, diagnostics)

    def test_heap_summary_cannot_override_failed_raw_metrics(self):
        (self.root / 'forced-gc').mkdir()
        (self.root / 'forced-gc' / 'metrics.json').write_text(json.dumps({'exit_code': 1, 'timed_out': False}))
        diagnostics = {'forced-gc': {'exit_code': 0, 'timed_out': False},
                       'heap-after-gc': {'exit_code': 0, 'timed_out': False}}
        with self.assertRaisesRegex(ValueError, 'differs from raw'):
            aggregate.post_gc_heap(self.root, diagnostics)

    def test_retained_workload_label_is_preserved_and_shutdown_is_required(self):
        # Exercise retained_series orchestration with tiny serialized evidence;
        # unrelated hashing/output mechanics have dedicated tests elsewhere.
        workspace = self.root / 'producer' / 'workspace'
        workspace.mkdir(parents=True)
        (workspace.parent / 'manifest.json').write_text(json.dumps({'binaries': {}}))
        manifest = {'helpers': {}, 'binaries': {}, 'workspace': str(workspace), 'workspace_sha256': {},
                    'producer_command': str(self.root / 'producer-command.json'),
                    'producer_command_sha256': '0' * 64, 'fixture': {}}
        aggregate.baseline.save(self.root / 'manifest.json', manifest)
        arms = []
        for arm in aggregate.ARMS:
            builds = []
            for index in range(7):
                directory = self.root / 'runs' / arm / f'build-{index:02d}'
                directory.mkdir(parents=True)
                metrics = {'exit_code': 0, 'timed_out': False, 'elapsed_seconds': 1}
                process = {'before': None, 'after': {'status_raw': 'VmRSS: 1024 kB\n'}}
                aggregate.baseline.save(directory / 'metrics.json', metrics)
                aggregate.baseline.save(directory / 'process.json', process)
                builds.append(dict(number=index, metrics=metrics, validation={'passed': True},
                                   server_cpu_between_snapshots_seconds=0, **process))
            arms.append({'arm': arm, 'status': 'passed', 'builds': builds, 'artifacts_sha256': {},
                         'shutdown': {'status': 'stopped'}, 'heap_diagnostics': {}})
        result = {'status': 'passed', 'arms': arms}
        aggregate.baseline.save(self.root / 'summary.json', result)
        with patch.object(aggregate, 'check_hash'), patch.object(aggregate, 'check_files'), \
                patch.object(aggregate, 'source_snapshot'), patch.object(aggregate, 'stream', return_value=[]), \
                patch.object(aggregate.baseline, 'inventory', return_value={}), \
                patch.object(aggregate.baseline, 'validate_outputs', return_value={'passed': True}), \
                patch.object(aggregate.comparison, 'validate_cache_hits'), \
                patch.object(aggregate.retained, 'server_cpu_delta', return_value=0):
            self.assertEqual(aggregate.retained_series('provider workload', self.root)['workload'], 'provider workload')
            result['arms'][0]['shutdown']['status'] = 'unverified'
            aggregate.baseline.save(self.root / 'summary.json', result)
            with self.assertRaisesRegex(ValueError, 'shutdown'):
                aggregate.retained_series('provider workload', self.root)

    def test_csv_uses_lf_and_quotes_labels_without_inventing_empty_rows(self):
        path = self.root / 'metrics.csv'
        aggregate.write_csv(path, [{'workload': 'first, second', 'elapsed_seconds': 1.5}],
                            ['workload', 'elapsed_seconds'])
        self.assertEqual(path.read_bytes(), b'workload,elapsed_seconds\n"first, second",1.5\n')
        empty = self.root / 'producer.csv'
        aggregate.write_csv(empty, [], ['workload', 'arm'])
        self.assertEqual(empty.read_bytes(), b'workload,arm\n')

    def test_labels_and_retained_numbers_are_validated(self):
        self.assertEqual(aggregate.labeled('provider=/tmp/run'), ('provider', Path('/tmp/run')))
        for value in ('no_separator', '=path', 'label='):
            with self.assertRaises(Exception):
                aggregate.labeled(value)
        for value in (None, True, -1, float('nan')):
            with self.assertRaises(ValueError):
                aggregate.nonnegative(value, 'test')


if __name__ == '__main__':
    unittest.main()
