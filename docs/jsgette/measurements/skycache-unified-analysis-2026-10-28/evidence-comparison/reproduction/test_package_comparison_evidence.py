"""Pure packaging checks using temporary files only; never inspect native runs."""
import gzip
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('packager', Path(__file__).with_name('package_comparison_evidence.py'))
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class PackagingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / 'inputs'
        self.inputs.mkdir()

    def test_large_text_compression_is_deterministic_and_original_auditable(self):
        source = self.inputs / 'large.json'
        source.write_bytes(b'{"padding":"' + b'x' * (p.THRESHOLD + 1) + b'"}\n')
        plan = p.PackagePlan()
        plan.add(source, 'primary/run/results.json')
        first = p.write_package(plan, self.root / 'first')
        second = p.write_package(plan, self.root / 'second')
        row = first['files']['primary/run/results.json']
        self.assertEqual(row, second['files']['primary/run/results.json'])
        self.assertEqual(row['storage_path'], 'primary/run/results.json.gz')
        packed = self.root / 'first' / row['storage_path']
        self.assertEqual(packed.read_bytes()[4:8], b'\x00' * 4)
        self.assertEqual(gzip.decompress(packed.read_bytes()), source.read_bytes())
        self.assertEqual(row['original_sha256'], p.digest(source))

    def test_small_text_and_profiles_are_byte_preserved(self):
        for name, contents in [('stats.json', b'{}'), ('build.profile.gz', b'profile' * 30000)]:
            source = self.inputs / name
            source.write_bytes(contents)
            plan = p.PackagePlan()
            plan.add(source, 'raw/' + name)
            index = p.write_package(plan, self.root / name)
            row = index['files']['raw/' + name]
            self.assertEqual(row['encoding'], 'identity')
            self.assertEqual(row['original_sha256'], row['stored_sha256'])

    def test_hash_mismatch_and_source_mutation_fail_closed(self):
        source = self.inputs / 'raw.json'
        source.write_text('{}')
        plan = p.PackagePlan()
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            plan.add(source, 'raw.json', '0' * 64)
        plan.add(source, 'raw.json')
        source.write_text('{"changed":true}')
        with self.assertRaisesRegex(ValueError, 'changed before copy'):
            p.write_package(plan, self.root / 'mutated')
        self.assertEqual(p.read_json(self.root / 'mutated/index.json')['status'], 'failed')

    def test_verify_rejects_corruption_and_unindexed_files(self):
        source = self.inputs / 'raw.json'
        source.write_text('{}')
        plan = p.PackagePlan()
        plan.add(source, 'raw.json')
        destination = self.root / 'package'
        p.write_package(plan, destination)
        (destination / 'extra.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'unindexed'):
            p.verify_package(destination)
        (destination / 'extra.json').unlink()
        (destination / 'raw.json').write_text('[]')
        with self.assertRaisesRegex(ValueError, 'stored checksum'):
            p.verify_package(destination)

    def test_never_accepts_binary_paths_or_build_state(self):
        source = self.inputs / 'binary'
        source.write_bytes(b'\x7fELF')
        with self.assertRaisesRegex(ValueError, 'unsupported artifact'):
            p.PackagePlan().add(source, 'binary')
        source = self.inputs / 'raw.json'
        source.write_text('{}')
        for logical in ('../escape.json', '/absolute.json', 'output-base/state.json', 'caches/a.json'):
            with self.subTest(logical=logical), self.assertRaises(ValueError):
                p.PackagePlan().add(source, logical)

    def test_running_and_partial_pass_are_rejected_but_explicit_failure_is_allowed(self):
        source = self.inputs / 'cohort'
        source.mkdir()
        (source / 'manifest.json').write_text('{"schedule":[]}')
        for status in ('running', 'passed'):
            (source / 'summary.json').write_text(json.dumps({'status': status, 'runs': []}))
            with self.assertRaises(ValueError):
                p.validate_cohort(source, 'synthetic')
            with self.assertRaises(ValueError):
                p.validate_cohort(source, 'synthetic', failed=True)
        (source / 'summary.json').write_text('{"status":"failed","runs":[]}')
        self.assertEqual(p.validate_cohort(source, 'synthetic', failed=True)['status'], 'failed')
        with self.assertRaises(ValueError):
            p.validate_cohort(source, 'synthetic')

    def test_retained_requires_seven_valid_builds_and_scoped_shutdown_per_arm(self):
        rows = [{'arm': arm, 'status': 'passed', 'shutdown': {'status': 'stopped'},
                 'builds': [{'number': n, 'metrics': {'exit_code': 0, 'timed_out': False},
                             'validation': {'passed': True},
                             'configured_target_hits': 2 if arm == 'fork-on' and n == 0 else 0}
                            for n in range(7)]} for arm in p.ARMS]
        source = self.inputs / 'retained'
        source.mkdir()
        state = {'status': 'passed', 'arms': rows}
        (source / 'summary.json').write_text(json.dumps(state))
        p.validate_cohort(source, 'retained')
        state['arms'][0]['shutdown']['status'] = 'unverified'
        (source / 'summary.json').write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'shutdown'):
            p.validate_cohort(source, 'retained')

    def test_cache_path_is_required_for_restored_consumers_and_absent_in_controls(self):
        p.check_cache_hits('fork-on', 2, True)
        p.check_cache_hits('fork-on', 0, False)
        p.check_cache_hits('vanilla', 0, True)
        for arm, hits, restored in [('fork-on', 0, True), ('vanilla', 1, False),
                                    ('fork-off', 1, True), ('fork-on', None, True),
                                    ('fork-on', -1, False)]:
            with self.subTest(arm=arm, hits=hits), self.assertRaises(ValueError):
                p.check_cache_hits(arm, hits, restored)

    def test_datadog_observations_use_configured_target_counts_only(self):
        row = {'build_metrics': {'remoteAnalysisCacheStatistics': {'cacheHitsBySkyfunction': [
            {'key': 'CONFIGURED_TARGET', 'value': '7'}, {'key': 'ASPECT', 'value': '99'}]}}}
        self.assertEqual(p.datadog_hits(row), 7)
        self.assertEqual(p.datadog_hits({}), 0)
    def test_storage_collision_does_not_create_passed_package(self):
        first, second = self.inputs / 'a.json', self.inputs / 'a.json.gz'
        first.write_text('x' * (p.THRESHOLD + 1))
        second.write_bytes(b'preexisting gzip')
        plan = p.PackagePlan()
        plan.add(first, 'a.json')
        plan.add(second, 'a.json.gz')
        with self.assertRaisesRegex(ValueError, 'collision'):
            p.write_package(plan, self.root / 'collision')
        self.assertEqual(p.read_json(self.root / 'collision/index.json')['status'], 'failed')


if __name__ == '__main__':
    unittest.main()