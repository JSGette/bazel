"""Pure Python tests; synthetic worker evidence only, no native Bazel commands."""

import copy
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name('compare_three_arms.py')
spec = importlib.util.spec_from_file_location('comparison_test_module', MODULE)
comparison = importlib.util.module_from_spec(spec)
spec.loader.exec_module(comparison)


def metrics(elapsed=10, user=2, system=1, rss=100):
    return {'exit_code': 0, 'timed_out': False, 'elapsed_seconds': elapsed,
            'resources': {'user_cpu_seconds': user, 'system_cpu_seconds': system,
                          'ru_maxrss_raw': rss, 'ru_maxrss_unit': 'KiB'}}


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        fixture = self.root / 'fixture'
        fixture.mkdir()
        self.content = {'//:consumer': ('consumer.txt', b'consumer\n'),
                        '//:producer': ('producer.txt', b'producer\n')}
        for name in ('consumer', 'producer'):
            output, content = self.content['//:' + name]
            (fixture / (name + '.json')).write_text(json.dumps({
                'schema_version': 1, 'targets': ['//:' + name],
                'outputs': {output: hashlib.sha256(content).hexdigest()}}))
        (fixture / 'MODULE.bazel').write_text('module(name="fake_fixture")\n')
        for binary in ('vanilla', 'current'):
            (self.root / (binary + '-bazel')).write_bytes(binary.encode())
            (self.root / (binary + '-source')).mkdir()
        self.args = SimpleNamespace(
            vanilla_bazel=self.root / 'vanilla-bazel', current_bazel=self.root / 'current-bazel',
            vanilla_source_repo=self.root / 'vanilla-source', vanilla_source_commit='a' * 40,
            current_source_repo=self.root / 'current-source', current_source_commit='b' * 40,
            manifest=fixture / 'consumer.json', producer_manifest=fixture / 'producer.json',
            run_dir=self.root / 'result', heap_mb=2048, jobs=6, timeout_seconds=10)
        self.calls = []

    def fake_worker(self, directory, argv, workspace, environment, timeout, expected_harness_sha):
        self.assertEqual(expected_harness_sha, comparison.baseline.digest(comparison.WORKER))
        directory.mkdir()
        output_base = directory / 'output-base'
        output_base.mkdir()
        targets = argv[argv.index('--') + 1:]
        self.calls.append((directory.name, argv, targets))
        output_name, content = self.content[targets[0]]
        output = output_base / output_name
        output.write_bytes(content)
        events = [
            {'id': {'namedSet': {'id': 'outputs'}}, 'namedSetOfFiles': {
                'files': [{'name': output_name, 'uri': output.as_uri()}]}},
            {'id': {'targetCompleted': {'label': targets[0], 'configuration': {'id': 'actual'}}},
             'completed': {'success': True, 'outputGroup': [{'name': 'default', 'fileSets': [{'id': 'outputs'}]}]}},
            {'id': {'buildFinished': {}}, 'finished': {'exitCode': {'name': 'SUCCESS'}},
             'children': [{'buildMetrics': {}}]},
            {'id': {'buildMetrics': {}}, 'buildMetrics': {}, 'lastMessage': True},
        ]
        if directory.name.startswith('r') and directory.name.endswith('fork-on'):
            events[-1]['buildMetrics'] = {'remoteAnalysisCacheStatistics': {
                'cacheHitsBySkyfunction': [{'key': 'CONFIGURED_TARGET', 'value': '1'}]}}
        (directory / 'build.bep.json').write_text('\n'.join(map(json.dumps, events)))
        comparison.baseline.save(directory / 'command.json', {'argv': argv})
        for name in ('stdout.log', 'stderr.log'):
            (directory / name).write_text('synthetic worker output\n')
        for flag in argv:
            if flag.startswith('--experimental_local_skycache=') and flag.split('=', 1)[1]:
                cache = Path(flag.split('=', 1)[1])
                if directory.name.startswith('r'):
                    self.assertTrue((cache / 'seed.manifest').exists())
                else:
                    self.assertFalse(cache.exists())
                cache.mkdir(exist_ok=True)
                (cache / 'seed.manifest').write_text('fake publication')
        result = metrics()
        comparison.baseline.save(directory / 'metrics.json', result)
        return result

    def run_fake(self, worker=None):
        provenance = lambda repo, commit: ({'repository': str(repo), 'commit': commit,
                                            'source_state': 'clean', 'binary_source_binding': 'operator-declared'}, b'')
        with patch.object(comparison.baseline, 'source_provenance', side_effect=provenance), \
                patch.object(comparison.baseline, 'run_command', side_effect=worker or self.fake_worker):
            return comparison.experiment(self.args)

    def test_measured_schedule_contains_all_permutations_and_balanced_positions(self):
        schedule = comparison.schedule()
        self.assertEqual(len(schedule), 27)
        self.assertEqual([row['arm'] for row in schedule[:6]], list(comparison.ARMS) + list(reversed(comparison.ARMS)))
        self.assertEqual([row['role'] for row in schedule[6:9]], ['producer'] * 3)
        rounds = [tuple(row['arm'] for row in schedule if row['role'] == 'measured' and row['round'] == n)
                  for n in range(6)]
        self.assertEqual(set(rounds), set(itertools.permutations(comparison.ARMS)))
        for position in range(3):
            for arm in comparison.ARMS:
                self.assertEqual(sum(order[position] == arm for order in rounds), 2)

    def test_vanilla_omits_all_custom_flags_and_common_resource_limits_match(self):
        commands = {}
        for arm in comparison.ARMS:
            commands[arm] = comparison.command(Path('/binary'), self.root, self.root / arm,
                                                self.root / 'cache' if arm == 'fork-on' else None,
                                                ['//:consumer'], 3072, 4, arm)
            self.assertIn('--jobs=4', commands[arm])
            self.assertIn('--host_jvm_args=-Xmx3072m', commands[arm])
            self.assertIn('--batch', commands[arm])
            self.assertIn('--remote_cache=', commands[arm])
            self.assertIn('--disk_cache=', commands[arm])
        self.assertFalse(any(flag.startswith('--experimental_') for flag in commands['vanilla']))
        self.assertIn('--experimental_local_skycache=', commands['fork-off'])
        self.assertIn(f'--experimental_local_skycache={self.root / "cache"}', commands['fork-on'])
        for jobs in (0, 7):
            with self.assertRaises(ValueError):
                comparison.command(Path('/binary'), self.root, self.root / 'run', None, ['//:x'], 2048, jobs, 'vanilla')

    def test_statistics_exclude_warmups_and_include_matching_producers_in_amortization(self):
        rows = []
        for planned in comparison.schedule():
            value = {'vanilla': 10, 'fork-off': 20, 'fork-on': 5}[planned['arm']]
            elapsed = 999 if planned['role'] == 'warmup' else value * (10 if planned['role'] == 'producer' else 1)
            rows.append(dict(planned, status='passed', metrics=metrics(elapsed, rss=elapsed)))
        stats = comparison.summarize(rows)
        self.assertEqual(stats['descriptive_by_arm']['fork-on']['elapsed_seconds'],
                         {'count': 6, 'median': 5.0, 'min': 5, 'max': 5})
        first = stats['amortization'][0]
        self.assertEqual(first['producer_plus_consumers']['vanilla']['elapsed_seconds'], 110)
        self.assertEqual(first['producer_plus_consumers']['fork-on']['elapsed_seconds'], 55)
        self.assertEqual(first['producer_plus_consumers']['fork-on']['maxrss_kib'], 50)
        self.assertEqual(first['producer_inclusive_ratios']['fork-on/vanilla']['elapsed_seconds'], 0.5)
        self.assertEqual(stats['entire_recorded_sequence']['maxrss_kib'], 999)
        self.assertEqual(stats['round_ratios'][0]['ratios']['fork-off/vanilla']['elapsed_seconds'], 2)

    def test_invalid_or_incomplete_measurements_cannot_produce_statistics(self):
        rows = [dict(row, status='passed', metrics=metrics()) for row in comparison.schedule()]
        for invalid in (rows[:-1], [dict(rows[0], status='failed')] + rows[1:], list(reversed(rows))):
            with self.assertRaises(ValueError):
                comparison.summarize(invalid)
        for invalid in (dict(metrics(), exit_code=1), dict(metrics(), timed_out=True), metrics(float('nan')),
                        metrics(rss=0), dict(metrics(), resources={'ru_maxrss_unit': 'bytes'})):
            with self.assertRaises((ValueError, KeyError)):
                comparison.resource_metrics(invalid)
        self.assertIsNone(comparison.ratio(1, 0))

    def test_bep_hit_and_action_counts_with_legitimate_missing_upstream_metrics(self):
        for events in ([], [{'buildMetrics': {}}]):
            observed = comparison.bep_observations(events)
            self.assertEqual(observed['configured_target_cache_hits'], 0)
            self.assertFalse(observed['cache_statistics_present'])
            self.assertIsNone(observed['actions_executed'])
            comparison.validate_cache_hits('vanilla', 'measured', observed)
        events = [{'buildMetrics': {
            'remoteAnalysisCacheStatistics': {'cacheHitsBySkyfunction': [
                {'key': 'CONFIGURED_TARGET', 'value': '8'}, {'key': 'OTHER', 'value': '99'}]},
            'actionSummary': {'actionsExecuted': '9', 'actionData': [
                {'mnemonic': 'FileWrite', 'actionsCreated': '8', 'actionsExecuted': '8'}]}}}]
        observed = comparison.bep_observations(events)
        self.assertEqual(observed['configured_target_cache_hits'], 8)
        self.assertEqual(observed['actions_executed'], 9)
        self.assertEqual(observed['action_data'][0]['actions_executed'], 8)
        comparison.validate_cache_hits('fork-on', 'measured', observed)
        with self.assertRaises(ValueError):
            comparison.bep_observations(events * 2)

    def test_on_miss_and_off_hit_gates(self):
        zero = {'configured_target_cache_hits': 0}
        one = {'configured_target_cache_hits': 1}
        with self.assertRaisesRegex(ValueError, 'restored no configured targets'):
            comparison.validate_cache_hits('fork-on', 'measured', zero)
        for arm in ('vanilla', 'fork-off'):
            with self.assertRaisesRegex(ValueError, 'unexpectedly restored'):
                comparison.validate_cache_hits(arm, 'measured', one)
        for role in ('producer', 'warmup'):
            comparison.validate_cache_hits('fork-on', role, zero)

    def test_full_fake_experiment_preserves_raw_results_and_uses_only_measured_seed_copies(self):
        result = self.run_fake()
        summary = json.loads((result / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'passed')
        self.assertEqual(len(self.calls), 27)
        self.assertTrue((result / 'statistics.json').exists())
        for label, argv, targets in self.calls:
            self.assertEqual(targets, ['//:producer'] if label.startswith('producer-') else ['//:consumer'])
            self.assertTrue((result / 'runs' / label / 'stdout.log').exists())
            state = 'vanilla' if label.endswith('vanilla') else 'current'
            self.assertIn(f'--output_user_root={result / "binary-state" / state / "bazel-user"}', argv)
        for row in summary['runs']:
            self.assertTrue(row['validation']['passed'])
            self.assertIn('build.bep.json', row['raw_artifacts_sha256'])
        self.assertEqual((result / 'caches' / 'producer-seed' / 'seed.manifest').read_text(), 'fake publication')

    def test_failed_command_is_authoritative_and_stops_without_statistics(self):
        def fail(*args, **kwargs):
            result = self.fake_worker(*args, **kwargs)
            return dict(result, exit_code=1)
        with self.assertRaisesRegex(ValueError, 'command failed'):
            self.run_fake(fail)
        summary = json.loads((self.args.run_dir / 'summary.json').read_text())
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(summary['runs'][0]['status'], 'failed')
        self.assertFalse((self.args.run_dir / 'statistics.json').exists())
        self.assertTrue((self.args.run_dir / 'runs' / 'w00-vanilla' / 'stdout.log').exists())

    def test_changed_binary_and_comparison_harness_are_rejected_after_command(self):
        for mutation in ('binary', 'comparison'):
            with self.subTest(mutation=mutation):
                self.args.run_dir = self.root / ('mutation-' + mutation)
                original_digest = comparison.baseline.digest
                changed = False

                def digest(path):
                    if changed and mutation == 'comparison' and Path(path).resolve() == MODULE.resolve():
                        return '0' * 64
                    return original_digest(path)

                def worker(*args, **kwargs):
                    nonlocal changed
                    result = self.fake_worker(*args, **kwargs)
                    changed = True
                    if mutation == 'binary':
                        self.args.vanilla_bazel.write_bytes(b'changed binary')
                    return result

                with patch.object(comparison.baseline, 'digest', side_effect=digest), self.assertRaisesRegex(ValueError, 'changed during'):
                    self.run_fake(worker)
                self.assertFalse((self.args.run_dir / 'statistics.json').exists())

    def test_different_producer_fixture_directory_rejected_before_run_creation(self):
        other = self.root / 'other'
        other.mkdir()
        self.args.producer_manifest = other / 'producer.json'
        self.args.producer_manifest.write_text(self.args.manifest.read_text())
        with self.assertRaisesRegex(ValueError, 'share one fixture directory'):
            comparison.experiment(self.args)
        self.assertFalse(self.args.run_dir.exists())


if __name__ == '__main__':
    unittest.main()
