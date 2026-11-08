"""Offline protocol checks only; these are not Java trace interop evidence."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

WORK = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('smoke', WORK / 'qualify_skycache_trace.py')
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def inputs():
    source = {'base_commit': smoke.BASE, 'patch_sha256': 'a' * 64, 'reconstructed_git_tree': 'b' * 40}
    candidate = {'destination': str(smoke.BUILD_ROOT / 'candidate/bazel-skycache-trace'), 'sha256': 'c' * 64, 'bytes': 123}
    manifest = {'schema_version': 1, 'root': str(smoke.ROOT), 'cases': [list(row) for row in smoke.CASES],
                'source_identity': source}
    report = {'status': 'build_and_focused_tests_passed_no_gate_verdict', 'root': str(smoke.BUILD_ROOT),
              'commands': [{'name': name, 'returncode': 0} for name in ('version', 'build', 'tests', 'shutdown')],
              'source_before': {'files': 7}, 'source_after': {'files': 7}, 'candidate': candidate,
              'product_binary_sha256_after_build': candidate['sha256'], 'product_binary_sha256_after_tests': candidate['sha256'],
              'bundle': {'base_commit': smoke.BASE, 'instrumented_git_tree': 'b' * 40,
                         'baseline_git_tree': 'd' * 40, 'combined_patch': {'sha256': 'a' * 64}}}
    proof = {'verified': True, 'qualification_passed': True, 'final_scope_absent': True, 'final_scope_unsignalled': True,
             'report_sha256': 'e' * 64, 'path': candidate['destination'], 'sha256': candidate['sha256'], 'bytes': 123}
    return manifest, report, proof


class ProtocolTest(unittest.TestCase):
    def test_complete_source_and_candidate_binding(self):
        manifest, report, proof = inputs()
        self.assertEqual(smoke.validate_provenance(manifest, report, proof, 'e' * 64), report['candidate'])
        for mutate in (lambda m, r, p: r.update(status='failed'),
                       lambda m, r, p: p.update(final_scope_absent=False),
                       lambda m, r, p: p.update(final_scope_unsignalled=False),
                       lambda m, r, p: p.update(report_sha256='f' * 64),
                       lambda m, r, p: r['commands'].pop(),
                       lambda m, r, p: r.update(source_after={'files': 8}),
                       lambda m, r, p: r.update(product_binary_sha256_after_tests='f' * 64),
                       lambda m, r, p: m['source_identity'].update(patch_sha256='f' * 64),
                       lambda m, r, p: m['source_identity'].update(reconstructed_git_tree='d' * 40)):
            trial = copy.deepcopy((manifest, report, proof))
            mutate(*trial)
            with self.assertRaises(RuntimeError):
                smoke.validate_provenance(*trial, 'e' * 64)

    def test_exact_fixed_cases_and_trace_count(self):
        self.assertEqual(len(smoke.CASES), 8)
        self.assertEqual(sum(row[3] for row in smoke.CASES), 5)
        self.assertEqual([(v, enabled) for _, engine, v, enabled in smoke.CASES if engine == 'e0' and enabled],
                         [('A', True), ('B', True), ('A', True)])
        manifest, report, proof = inputs()
        manifest['cases'].reverse()
        with self.assertRaises(RuntimeError):
            smoke.validate_provenance(manifest, report, proof, 'e' * 64)

    def test_bep_requires_one_successful_command(self):
        root = WORK / 'offline-test-tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            path = Path(temporary) / 'bep.jsonl'
            rows = [{'started': {'uuid': 'actual-command'}}, {'finished': {'exitCode': {'name': 'SUCCESS'}}}]
            path.write_text('\n'.join(map(json.dumps, rows)))
            self.assertEqual(smoke.bep_identity(path), 'actual-command')
            explicit = copy.deepcopy(rows)
            explicit[1]['finished']['exitCode']['code'] = 0
            path.write_text('\n'.join(map(json.dumps, explicit)))
            self.assertEqual(smoke.bep_identity(path), 'actual-command')
            for bad in ([rows[0]], [rows[0], rows[0], rows[1]],
                        [rows[0], {'finished': {'exitCode': {'code': 1}}}]):
                path.write_text('\n'.join(map(json.dumps, bad)))
                with self.assertRaises(RuntimeError):
                    smoke.bep_identity(path)

    def test_actual_fixture_a_b_a_has_independent_oracles(self):
        root = WORK / 'offline-test-tmp'
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            workspace = Path(temporary)
            try:
                first = smoke.fixture_state(workspace, 'A')
            except OSError as error:
                if getattr(error, 'winerror', None) == 1314:
                    self.skipTest('Windows user lacks symlink privilege; live fixture requires Linux')
                raise
            a_bytes = b''.join((workspace / name).read_bytes() for name in ('inputs/a.txt', 'inputs/z.txt', 'link.txt'))
            self.assertEqual(a_bytes.hex(), first['expected_output_hex'])
            self.assertEqual([row for row in first['members'] if row['type'] == 'directory'],
                             [{'path': 'inputs', 'type': 'directory'}, {'path': 'optional', 'type': 'directory'}])
            changed = smoke.fixture_state(workspace, 'B')
            b_bytes = b''.join((workspace / name).read_bytes() for name in ('inputs/a.txt', 'inputs/z.txt', 'optional/added.txt', 'link.txt'))
            self.assertEqual(b_bytes.hex(), changed['expected_output_hex'])
            final = smoke.fixture_state(workspace, 'A')
            self.assertNotEqual(first['snapshot_id'], changed['snapshot_id'])
            self.assertEqual(first, final)
            self.assertFalse((workspace / 'optional/added.txt').exists())
            smoke.verify_fixture(workspace, final)
            (workspace / 'inputs/a.txt').write_text('wrong\n')
            with self.assertRaises(RuntimeError):
                smoke.verify_fixture(workspace, final)

    def test_snapshot_binds_empty_directory_and_rejects_missing_or_file_replacement(self):
        with tempfile.TemporaryDirectory(dir=WORK) as temporary:
            workspace = Path(temporary)
            (workspace / 'inputs').mkdir()
            optional = workspace / 'optional'
            optional.mkdir()
            for name in ('a.txt', 'z.txt'):
                (workspace / 'inputs' / name).write_text(name)
            members = smoke.fixture_members(workspace, ['inputs/a.txt', 'inputs/z.txt'])
            self.assertIn({'path': 'optional', 'type': 'directory'}, members)
            snapshot = smoke.digest(smoke.canonical(members))
            without_optional = [row for row in members if row['path'] != 'optional']
            self.assertNotEqual(snapshot, smoke.digest(smoke.canonical(without_optional)))
            state = {'variant': 'A', 'members': members}
            smoke.verify_fixture(workspace, state)
            optional.rmdir()
            with self.assertRaisesRegex(RuntimeError, 'directory changed'):
                smoke.verify_fixture(workspace, state)
            with self.assertRaisesRegex(RuntimeError, 'directory changed'):
                smoke.fixture_members(workspace, ['inputs/a.txt', 'inputs/z.txt'])
            optional.write_text('not a directory')
            with self.assertRaisesRegex(RuntimeError, 'directory changed'):
                smoke.verify_fixture(workspace, state)

    def test_snapshot_rejects_symlink_directory_even_if_it_resolves_to_directory(self):
        with tempfile.TemporaryDirectory(dir=WORK) as temporary:
            workspace = Path(temporary)
            (workspace / 'inputs').mkdir()
            optional = workspace / 'optional'
            optional.mkdir()
            state = {'variant': 'A', 'members': [{'path': 'optional', 'type': 'directory'}]}
            # Simulate just the symlink bit without requiring Windows symlink privileges.
            with patch.object(Path, 'is_symlink', autospec=True, side_effect=lambda path: path == optional):
                with self.assertRaisesRegex(RuntimeError, 'directory changed'):
                    smoke.verify_fixture(workspace, state)
                with self.assertRaisesRegex(RuntimeError, 'directory changed'):
                    smoke.fixture_members(workspace, [])

    def test_emitted_listing_must_match_current_variant_not_prior_a(self):
        def encoded(value):
            return value.encode('utf-16-be').hex()
        parsed = {'nodes': {1: {'logical_key': ['main', encoded('optional')]}},
                  'observations': {1: {'node_id': 1, 'value_status': 'exact_projection',
                    'projection': {'type': 'DIRECTORY_LISTING_STATE', 'entries': []}}}}
        self.assertEqual(smoke.check_listing_projections(parsed, {'variant': 'A'})[
                         'verified_exact_listing_observations'], {'optional': 1})
        with self.assertRaises(RuntimeError):
            smoke.check_listing_projections(parsed, {'variant': 'B'})
        parsed['observations'][1]['projection']['entries'] = [[encoded('added.txt'), 'FILE']]
        self.assertEqual(smoke.check_listing_projections(parsed, {'variant': 'B'})[
                         'paths_without_exact_listing_observations'], ['inputs'])


if __name__ == '__main__':
    unittest.main()
