"""Offline bounded Git/tar fixtures only; no Bazel or task-volume access."""
import copy
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import build_native_cap10 as launcher


class Cap10LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='.cap10-test-', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.base = {name: ('base ' + name + '\n').encode() for name in launcher.FILES}
        self.base[launcher.previous.TEST] = b'original test\n'
        self.base['unrelated.txt'] = b'untouched\n'
        self.archive = self.root / 'source.tar'
        with tarfile.open(self.archive, 'w') as archive:
            for name, data in self.base.items():
                target = self.source / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o644
                archive.addfile(info, io.BytesIO(data))

    def git(self, *argv):
        return subprocess.check_output(['git', '-C', str(self.source), '-c', 'core.autocrlf=false', *argv],
                                       stderr=subprocess.PIPE)

    def prepare_commit(self, omit=None, extra=False):
        self.git('init', '-q')
        self.git('config', 'user.email', 'offline-test@example.invalid')
        self.git('config', 'user.name', 'Offline fixture')
        self.git('add', '.')
        self.git('commit', '-qm', 'base')
        for name in launcher.FILES:
            if name != omit:
                (self.source / name).write_bytes(self.base[name] + b'cap ten\n')
        if extra:
            (self.source / 'unrelated.txt').write_bytes(b'extra commit change\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'cap ten')
        commit = self.git('rev-parse', 'HEAD').decode().strip()
        selected = self.git('diff', '--binary', '--full-index', '--no-ext-diff', '--no-renames',
                            'HEAD^', 'HEAD', '--', *launcher.FILES)
        for name, data in self.base.items():
            (self.source / name).write_bytes(data)
        return commit, selected, ['git', '--git-dir=' + str(self.source / '.git')]

    def test_all_archived_files_checked_with_only_explicit_overrides(self):
        original = launcher.verify_source(self.archive, self.source, {})
        overrides = {launcher.previous.TEST: b'patched test\n', launcher.FILES[0]: b'cap ten\n'}
        for name, data in overrides.items():
            (self.source / name).write_bytes(data)
        current = launcher.verify_source(self.archive, self.source, overrides)
        self.assertNotEqual(original['tracked_content_sha256'], current['tracked_content_sha256'])
        self.assertEqual(current['override_paths'], sorted(overrides))
        (self.source / 'unrelated.txt').write_bytes(b'wrongtext\n')
        with self.assertRaisesRegex(RuntimeError, 'Source .*changed'):
            launcher.verify_source(self.archive, self.source, overrides)

    def test_unknown_override_cannot_hide_unarchived_source(self):
        with self.assertRaisesRegex(RuntimeError, 'Override missing'):
            launcher.verify_source(self.archive, self.source, {'absent.java': b'extra'})

    def test_committed_patch_binds_exact_old_and_new_bytes(self):
        commit, data, git = self.prepare_commit(extra=True)
        record, overrides = launcher.committed_patch(git, commit, data, self.source, dict(os.environ))
        self.assertEqual(set(overrides), set(launcher.FILES))
        self.assertEqual(record['sha256'], launcher.digest(data))
        for row in record['files']:
            self.assertEqual(row['old_sha256'], launcher.digest(self.base[row['path']]))
            self.assertEqual(row['new_sha256'], launcher.digest(overrides[row['path']]))
        # The full commit can contain evidence changes, but the applied patch cannot.
        full = self.git('diff', '--binary', '--full-index', '--no-ext-diff', '--no-renames', 'HEAD^', 'HEAD')
        with self.assertRaisesRegex(RuntimeError, 'exact selected committed diff'):
            launcher.committed_patch(git, commit, full, self.source, dict(os.environ))

    def test_patch_rejects_current_source_different_from_commit_parent(self):
        commit, data, git = self.prepare_commit()
        (self.source / launcher.FILES[2]).write_bytes(b'unrecorded source change\n')
        with self.assertRaisesRegex(RuntimeError, 'differs from patch parent'):
            launcher.committed_patch(git, commit, data, self.source, dict(os.environ))

    def test_patch_requires_all_four_explicit_files(self):
        commit, data, git = self.prepare_commit(omit=launcher.FILES[-1])
        with self.assertRaisesRegex(RuntimeError, 'all four'):
            launcher.committed_patch(git, commit, data, self.source, dict(os.environ))

    def test_exact_patch_applies_and_full_archive_guard_detects_later_edit(self):
        commit, data, git = self.prepare_commit()
        _, overrides = launcher.committed_patch(git, commit, data, self.source, dict(os.environ))
        # Use a source directory without Git metadata, like the real archived source.
        clean = self.root / 'archived-source'
        clean.mkdir()
        for name, body in self.base.items():
            target = clean / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '--check', '-'], cwd=clean,
                       input=data, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess.run(['git', '-c', 'core.autocrlf=false', 'apply', '-'], cwd=clean,
                       input=data, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        launcher.verify_source(self.archive, clean, overrides)
        (clean / launcher.FILES[0]).write_bytes(overrides[launcher.FILES[0]] + b'late edit\n')
        with self.assertRaisesRegex(RuntimeError, 'Source .*changed'):
            launcher.verify_source(self.archive, clean, overrides)

    def prior(self):
        shared = {key: key for key in ('configuration_sha256', 'generated_rc_sha256', 'bootstrap',
                  'embedded_jdk_sha256', 'compilation_jdk', 'seed_manifest_sha256', 'tool_versions',
                  'rc_transformation', 'source_git_dir')}
        prior = dict(shared, status=launcher.SUCCESS, root=str(launcher.Q2), build_state_root=str(launcher.S),
                     source_commit=launcher.build.COMMIT, source_tree=launcher.build.TREE,
                     source_archive_sha256=launcher.ARCHIVE_SHA, unchanged_admission_cap=2,
                     source_is_base_plus_qualification_test_patch=True, environment={'sanitized': 'fixture'},
                     qualification_test_patch={'path': launcher.previous.TEST, 'old_sha256': launcher.OLD_TEST,
                                               'new_sha256': launcher.NEW_TEST},
                     candidate={'sha256': launcher.OLD_BINARY},
                     product_binary_sha256_before=launcher.OLD_BINARY,
                     product_binary_sha256_after=launcher.OLD_BINARY,
                     commands=[{'name': name, 'argv': argv, 'returncode': 0} for name, argv in
                               launcher.previous.requalification_commands(launcher.S, launcher.Q2).items()
                               if name in ('build', 'tests', 'shutdown')])
        return prior, dict(shared, source_archive_sha256=launcher.ARCHIVE_SHA)

    def test_prior_requires_successful_build_test_shutdown_and_old_binary(self):
        prior, original = self.prior()
        with patch.object(launcher.previous, 'validate_prior', return_value=prior['environment']):
            launcher.validate_prior(prior, original)
            for name in ('build', 'tests', 'shutdown'):
                bad = copy.deepcopy(prior)
                next(row for row in bad['commands'] if row['name'] == name)['returncode'] = 1
                with self.assertRaisesRegex(RuntimeError, 'not qualified'):
                    launcher.validate_prior(bad, original)
            bad = copy.deepcopy(prior)
            bad['candidate']['sha256'] = '0' * 64
            with self.assertRaisesRegex(RuntimeError, 'binary changed'):
                launcher.validate_prior(bad, original)


if __name__ == '__main__':
    unittest.main()
