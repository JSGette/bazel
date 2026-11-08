"""Offline incremental-repair boundaries; no runtime source or build access."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import build_skycache_trace_v3 as runner


def member(name, raw):
    return {'path': name, 'mode': '100644', 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(),
            'git_blob': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()}


class IncrementalBuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='trace-v3-test-', dir=runner.WORK)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def records(self):
        old = [member(name, b'old') for name in runner.ALLOWED]
        old.extend(member('other/' + str(index), b'same') for index in range(18590))
        new = [member(name, b'new') for name in runner.ALLOWED] + old[2:]
        return old, new

    def test_commands_reuse_build_state_and_only_redirect_two_bep_paths(self):
        with patch.object(runner.build, 'TESTS', runner.TESTS):
            previous = runner.build.commands(runner.S)
        current = runner.commands()
        self.assertEqual(list(current), ['version', 'build', 'tests', 'shutdown'])
        self.assertEqual(len(runner.TESTS), 13)
        for name in current:
            changes = [(a, b) for a, b in zip(previous[name], current[name]) if a != b]
            self.assertEqual(len(current[name]), len(previous[name]))
            expected = [] if name in ('version', 'shutdown') else [
                ('--build_event_json_file=' + str(runner.S / (name + '.bep.jsonl')),
                 '--build_event_json_file=' + str(runner.ROOT / (name + '.bep.jsonl')))]
            self.assertEqual(changes, expected)
        self.assertTrue(all(label in current['tests'] for label in runner.TESTS))

    def test_only_two_exact_replacements_with_identical_member_set_are_accepted(self):
        old, new = self.records()
        changes = runner.member_delta(old, new)
        self.assertEqual([row['path'] for row in changes], sorted(runner.ALLOWED))
        for kind in ('extra-change', 'removed', 'duplicate', 'mode', 'unchanged', 'oversized'):
            bad = copy.deepcopy(new)
            if kind == 'extra-change':
                bad[2] = member(bad[2]['path'], b'changed')
            elif kind == 'removed':
                bad.pop()
            elif kind == 'duplicate':
                bad[-1] = bad[-2]
            elif kind == 'mode':
                bad[0]['mode'] = '100755'
            elif kind == 'unchanged':
                bad[0] = old[0]
            else:
                bad[0]['bytes'] = 256 * 1024 + 1
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                runner.member_delta(old, bad)

    def test_tar_replacements_are_bound_to_content_size_and_git_blob(self):
        old, new = self.records()
        changes = runner.member_delta(old, new)
        archive_path = self.root / 'repair.tar'
        with tarfile.open(archive_path, 'w:') as archive:
            for name in runner.ALLOWED:
                info = tarfile.TarInfo(name)
                info.size = 3
                archive.addfile(info, io.BytesIO(b'new'))
        self.assertEqual(runner.replacement_bytes(archive_path, changes), {name: b'new' for name in runner.ALLOWED})
        for key, value in (('sha256', 'a' * 64), ('git_blob', 'a' * 40), ('bytes', 4)):
            bad = copy.deepcopy(changes)
            bad[0]['new'][key] = value
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                runner.replacement_bytes(archive_path, bad)

    def prior(self):
        group = '/user.slice/owned/prior.scope'
        proof = {'files_verified': 18592, 'member_records_sha256': 'a' * 64}
        prior = {'root': str(runner.S), 'status': 'failed', 'commands': [
            {'name': name, 'returncode': code} for name, code in [('version', 0), ('build', 1), ('shutdown', 0)]],
            'source_before': proof, 'source_after': proof, 'scope_before': {'name': group}, 'scope_after': {'name': group}}
        source = {'verified': True, 'qualification_passed': False, 'current': proof}
        metadata = {'current_source_verification': source, 'passed': False, 'archive_sha256': runner.PRIOR_ARCHIVE_SHA}
        unit = 'shared-eval-guard-m1-trace-source-build-02-' + 'a' * 32 + '.scope'
        guard = {'status': 'command_failed', 'exit_code': 1,
                 'final_scope_cleanup': {'verified': True, 'present': False, 'signalled': False},
                 'scope': {'unit': unit, 'invocation_id': 'b' * 32}}
        identity = {'Id': unit, 'InvocationID': 'b' * 32, 'ControlGroup': group}
        return prior, metadata, source, guard, identity

    def test_prior_must_be_pinned_failed_build_with_clean_source_and_shutdown(self):
        good = self.prior()
        runner.validate_prior_records(*good)
        for kind in ('passed', 'tests-ran', 'no-shutdown', 'source-changed', 'wrong-invocation', 'signalled'):
            bad = copy.deepcopy(good)
            if kind == 'passed':
                bad[0]['status'] = runner.SUCCESS
            elif kind == 'tests-ran':
                bad[0]['commands'].insert(2, {'name': 'tests', 'returncode': 0})
            elif kind == 'no-shutdown':
                bad[0]['commands'][-1]['returncode'] = 1
            elif kind == 'source-changed':
                bad[0]['source_after'] = {'files_verified': 18591}
            elif kind == 'wrong-invocation':
                bad[4]['InvocationID'] = 'c' * 32
            else:
                bad[3]['final_scope_cleanup']['signalled'] = True
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                runner.validate_prior_records(*bad)

    def test_prior_logs_manifest_and_large_source_artifact_are_preserved(self):
        rows = []
        for name in runner.PRESERVED:
            path = self.root / name
            path.write_bytes(name.encode())
            rows.append({'path': name, 'sha256': runner.sha(path)})
        source = self.root / 'source.tar'
        source.write_bytes(b'tiny fake original source artifact')
        prior = {'bundle': {'source_archive': {'sha256': runner.sha(source)}}}
        metadata = {'members': rows}
        with patch.object(runner, 'S', self.root):
            self.assertEqual(len(runner.preserved_files(prior, metadata)), len(runner.PRESERVED) + 1)
            for name in ('report.json', 'build.bep.jsonl', 'source-members.json', 'source.tar'):
                path = self.root / name
                raw = path.read_bytes()
                path.write_bytes(b'mutation')
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Preserved prior file changed'):
                    runner.preserved_files(prior, metadata)
                path.write_bytes(raw)

    def test_missing_or_stale_pid_is_allowed_but_live_or_malformed_pid_is_rejected(self):
        proc = self.root / 'proc'
        proc.mkdir()
        pid_file = self.root / 'build-output/server/server.pid.txt'
        pid_file.parent.mkdir(parents=True)
        with patch.object(runner, 'S', self.root):
            self.assertIsNone(runner.prior_server_absence(proc)['pid'])
            pid_file.write_text('123\n', encoding='ascii')
            self.assertEqual(runner.prior_server_absence(proc)['pid'], 123)
            (proc / '123').mkdir()
            with self.assertRaisesRegex(RuntimeError, 'still live'):
                runner.prior_server_absence(proc)
            pid_file.write_text('00123\n', encoding='ascii')
            with self.assertRaisesRegex(RuntimeError, 'still live'):
                runner.prior_server_absence(proc)
            for value in ('0', '-1', 'x', '2147483648', '123\n456'):
                pid_file.write_text(value, encoding='ascii')
                with self.subTest(value=value), self.assertRaises(RuntimeError):
                    runner.prior_server_absence(proc)

    def test_published_bundle_shape_requires_consistent_explicit_predecessor(self):
        bundle = {'base_commit': runner.BASE, 'baseline_git_tree': runner.BASELINE_TREE,
                  'safe_subset_patch_sha256': runner.SAFE_PATCH, 'instrumented_git_tree': 'e' * 40,
                  'previous_instrumented_git_tree': runner.PREVIOUS_TREE,
                  'previous_trace_source_sha256': {name: 'a' * 64 for name in runner.ALLOWED},
                  'repair_paths': list(runner.ALLOWED)}
        prior = {'bundle': dict(bundle, instrumented_git_tree=runner.PREVIOUS_TREE)}
        prior['bundle']['trace_source_sha256'] = dict(bundle['previous_trace_source_sha256'])
        runner.validate_lineage(prior, bundle)
        for key in ('base_commit', 'baseline_git_tree', 'safe_subset_patch_sha256', 'instrumented_git_tree',
                    'previous_instrumented_git_tree', 'previous_trace_source_sha256', 'repair_paths'):
            bad = dict(bundle)
            if key == 'repair_paths':
                bad[key] = [runner.ALLOWED[0]] * 2
            elif key == 'previous_trace_source_sha256':
                bad[key] = dict(bundle[key], unexpected='b' * 64)
            else:
                bad[key] = runner.PREVIOUS_TREE if key == 'instrumented_git_tree' else '0' * len(bad[key])
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                runner.validate_lineage(prior, bad)

    def test_actual_c_backed_bundles_have_the_declared_two_file_lineage(self):
        prior = json.loads((runner.WORK / 'trace-build-preparation/bundle-v2/bundle.json').read_text())
        current = json.loads((runner.WORK / 'trace-build-preparation/bundle-v3/bundle.json').read_text())
        runner.validate_lineage({'bundle': prior}, current)


if __name__ == '__main__':
    unittest.main()
