"""Offline whitelist and process-accounting checks; no runtime reads or launches."""
import copy
from pathlib import Path
import tempfile
import unittest

import archive_native_n10 as archive


class N10ArchiveTests(unittest.TestCase):
    def endpoint(self, role):
        group = '/owned/task.scope'
        bases = {str(i): '/owned/o%02d' % i for i in range(10)}
        engines = {str(i): {'pid': 100 if role == 'native' else 100 + i,
                           'start_ticks': 200 if role == 'native' else 200 + i,
                           'output_base': bases[str(i)], 'errors': {}} for i in range(10)}
        processes = []
        for index in range(1 if role == 'native' else 10):
            keys = list(engines) if role == 'native' else [str(index)]
            processes.append({'pid': 100 + index, 'start_ticks': 200 + index, 'engines': keys,
                              'cgroup': group, 'cgroup_before': group, 'cgroup_after': group, 'errors': {}})
        identity = {'device': 1, 'inode': 2}
        return {'eligible': True, 'identity_continuity_valid': True, 'membership_continuity_valid': True,
                'expected_engine_count': 10, 'valid_engine_count': 10, 'engines': engines, 'processes': processes,
                'cgroup': {'eligible': True, 'binding_valid': True, 'identity_continuity_valid': True,
                           'expected_cgroup': group, 'directory_identity': identity,
                           'directory_identity_after': identity, 'expected_directory_identity': identity}}, group, bases

    def test_native_deduplicates_one_pid_and_private_requires_ten(self):
        for role, count in (('native', 1), ('private', 10), ('ref', 10)):
            final, group, bases = self.endpoint(role)
            self.assertEqual(archive.verify_endpoint(final, role, group, bases)['unique_daemon_count'], count)
        final, group, bases = self.endpoint('native')
        with self.assertRaisesRegex(RuntimeError, 'unique daemon count'):
            archive.verify_endpoint(final, 'private', group, bases)

    def test_duplicate_or_missing_engine_coverage_is_rejected(self):
        final, group, bases = self.endpoint('native')
        for keys in ([str(i) for i in range(9)], ['0'] + [str(i) for i in range(9)]):
            bad = copy.deepcopy(final)
            bad['processes'][0]['engines'] = keys
            with self.assertRaisesRegex(RuntimeError, 'coverage|cardinality'):
                archive.verify_endpoint(bad, 'native', group, bases)

    def test_private_two_engines_and_empty_process_or_invalid_pid_are_rejected(self):
        final, group, bases = self.endpoint('private')
        bad = copy.deepcopy(final)
        bad['processes'][0]['engines'] = ['0', '1']
        bad['processes'][1]['engines'] = []
        bad['engines']['1'].update(pid=100, start_ticks=200)
        with self.assertRaisesRegex(RuntimeError, 'cardinality'):
            archive.verify_endpoint(bad, 'private', group, bases)
        for field in ('pid', 'start_ticks'):
            for value in (0, -1, True):
                bad = copy.deepcopy(final)
                bad['processes'][0][field] = value
                with self.assertRaisesRegex(RuntimeError, 'Invalid process identity'):
                    archive.verify_endpoint(bad, 'private', group, bases)

    def test_same_pid_with_different_start_ticks_is_not_two_live_processes(self):
        final, group, bases = self.endpoint('private')
        final['processes'][1]['pid'] = final['processes'][0]['pid']
        final['engines']['1']['pid'] = final['processes'][0]['pid']
        with self.assertRaisesRegex(RuntimeError, 'unique daemon count'):
            archive.verify_endpoint(final, 'private', group, bases)

    def test_membership_prefix_collision_or_transition_is_rejected(self):
        final, group, bases = self.endpoint('native')
        bad = copy.deepcopy(final)
        bad['processes'][0].update(cgroup=group + '-other', cgroup_before=group + '-other',
                                   cgroup_after=group + '-other')
        with self.assertRaisesRegex(RuntimeError, 'membership'):
            archive.verify_endpoint(bad, 'native', group, bases)
        bad = copy.deepcopy(final)
        bad['processes'][0]['cgroup_after'] = group + '/moved'
        with self.assertRaisesRegex(RuntimeError, 'membership'):
            archive.verify_endpoint(bad, 'native', group, bases)

    def test_output_mapping_and_scope_replacement_are_rejected(self):
        final, group, bases = self.endpoint('private')
        bad = copy.deepcopy(final)
        bad['engines']['9']['output_base'] = bases['0']
        with self.assertRaisesRegex(RuntimeError, 'output-base mapping'):
            archive.verify_endpoint(bad, 'private', group, bases)
        bad = copy.deepcopy(final)
        bad['cgroup']['directory_identity_after'] = {'device': 1, 'inode': 3}
        with self.assertRaisesRegex(RuntimeError, 'scope identity'):
            archive.verify_endpoint(bad, 'private', group, bases)

    def test_fixed_ten_client_whitelist_excludes_state_and_eleventh_client(self):
        with tempfile.TemporaryDirectory(prefix='.n10-archive-test-', dir=Path(__file__).resolve().parent) as temp:
            root = Path(temp).resolve()
            included = ('report.json', 'measured/c09/command.json', 'preparation/c09/bep.jsonl',
                        'measured-identity/c09-output_base/stdout.log', 'measured/c09/behavior/out/switch.json')
            excluded = ('state/cache/large-file', 'measured/c10/command.json', 'home/secret',
                        'measured/c09/raw-environment.log')
            for name in included + excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{}')
            self.assertEqual(set(archive.collect_files(root)), set(included))

    def test_known_evidence_directory_cannot_be_mistaken_for_file(self):
        with tempfile.TemporaryDirectory(prefix='.n10-archive-test-', dir=Path(__file__).resolve().parent) as temp:
            root = Path(temp).resolve()
            (root / 'report.json').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'non-file evidence'):
                archive.collect_files(root)


if __name__ == '__main__':
    unittest.main()
