"""Offline tests only: no Bazel, WSL, runtime mount or existing cache access."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import native_source_build as build


class BuildLauncherTests(unittest.TestCase):
    def test_version_uses_command_after_startup_options(self):
        version = build.commands(Path('/task/new-build'))['version']
        self.assertEqual(version[-2:], ['version', '--gnu_format'])
        self.assertNotIn('--version', version)

    def test_explicit_object_database_ignores_broken_worktree_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            database, worktree = root / 'main.git', root / 'broken-worktree'
            worktree.mkdir()
            (worktree / '.git').write_text('gitdir: /nonexistent/foreign-platform-path\n')
            subprocess.run(['git', 'init', '--quiet', '--bare', str(database)], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            git = build.source_git_command(database)
            blob = subprocess.check_output(git + ['hash-object', '-w', '--stdin'],
                                           input=b'fixture\n', cwd=worktree).strip()
            tree = subprocess.check_output(git + ['mktree'], cwd=worktree,
                                           input=b'100644 blob ' + blob + b'\tfile.txt\n').strip()
            env = {**os.environ, 'GIT_AUTHOR_NAME': 'Offline Fixture',
                   'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                   'GIT_COMMITTER_NAME': 'Offline Fixture',
                   'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
            commit = subprocess.check_output(git + ['commit-tree', tree.decode()], cwd=worktree,
                                             input=b'fixture\n', env=env).decode().strip()
            observed = subprocess.check_output(git + ['rev-parse', commit + '^{tree}'],
                                               cwd=worktree).strip()
            self.assertEqual(observed, tree)
            archive = root / 'source.tar'
            subprocess.run(git + ['archive', '--format=tar', '--output=' + str(archive), commit],
                           cwd=worktree, check=True)
            source = root / 'source'
            source.mkdir()
            build.extract_archive(archive, source)
            self.assertEqual((source / 'file.txt').read_bytes(), b'fixture\n')

    def test_guard_requires_owned_scope_and_exact_budget(self):
        values = {'cgroup': '0::/user.slice/user-1000.slice/shared-eval-guard-fixture.scope\n',
                  'memory.max': str(10240 * 1024**2), 'memory.swap.max': '0',
                  'cpu.max': '600000 100000', 'cpu.stat': 'usage_usec 10',
                  'memory.current': '1', 'memory.peak': '1', 'memory.events': 'oom 0'}
        with patch.object(Path, 'read_text', autospec=True,
                          side_effect=lambda path: values[path.name]):
            self.assertTrue(build.guard_snapshot()['name'].endswith('fixture.scope'))
            values['memory.max'] = str(12 * 1024**3)
            with self.assertRaisesRegex(RuntimeError, '10 GiB'):
                build.guard_snapshot()
            values['cgroup'] = '0::/unowned.scope\n'
            with self.assertRaisesRegex(RuntimeError, 'externally owned'):
                build.guard_snapshot()

    def test_rc_only_optional_import_removed(self):
        original = ('common --java_language_version=25\n'
                    'try-import %workspace%/user.bazelrc\n'
                    'common --downloader_config=bazel_downloader.cfg\n')
        self.assertEqual(build.make_rc(original), original.replace(
            'try-import %workspace%/user.bazelrc\n', ''))

    def test_rc_missing_or_extra_import_fails(self):
        for value in ('common --jobs=2\n',
                      'try-import %workspace%/user.bazelrc\nimport /elsewhere\n'):
            with self.assertRaises(RuntimeError):
                build.make_rc(value)

    def test_command_contract(self):
        root = Path('/task/m1-native-build')
        commands = build.commands(root)
        self.assertIn('--host_jvm_args=-Xmx3g', commands['build'])
        self.assertIn('--repo_contents_cache=' + str(root / 'repo-contents'), commands['build'])
        self.assertIn('--repo_env=TMPDIR=' + str(root / 'tmp'), commands['build'])
        self.assertIn('--action_env=TMP=' + str(root / 'tmp'), commands['tests'])
        self.assertIn('--test_env=TMPDIR=' + str(root / 'tmp'), commands['tests'])
        self.assertIn('--lockfile_mode=error', commands['build'])
        self.assertIn('--local_test_jobs=1', commands['tests'])
        self.assertIn('--test_env=TEST_INSTALL_BASE=' + str(root / 'test-install/install'),
                      commands['tests'])
        self.assertIn('--sandbox_writable_path=' + str(root / 'test-install'), commands['tests'])
        self.assertIn('--sandbox_writable_path=' + str(root / 'download-cache'), commands['tests'])
        self.assertTrue(set(build.TESTS).issubset(commands['tests']))
        self.assertEqual(commands['shutdown'][-1], 'shutdown')
        self.assertFalse(any('--experimental_shared_server=' in arg for arg in commands['build']))

    def test_copy_verifies_and_advises_exact_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = root / 'source', root / 'copy'
            source.write_bytes(b'archive fixture')
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch.object(os, 'posix_fadvise', create=True) as advise, \
                    patch.object(os, 'POSIX_FADV_DONTNEED', 4, create=True):
                result = build.copy_verified(source, target, digest)
                self.assertEqual(advise.call_count, 1)
                self.assertEqual(advise.call_args.args[1:], (0, 0, 4))
                self.assertEqual(result['sha256'], digest)
                self.assertTrue(result['source_fadv_dontneed'])
            target.chmod(0o600)

    def test_bad_digest_also_releases_source_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source, target = root / 'source', root / 'copy'
            source.write_bytes(b'wrong')
            with patch.object(os, 'posix_fadvise', create=True) as advise, \
                    patch.object(os, 'POSIX_FADV_DONTNEED', 4, create=True):
                with self.assertRaisesRegex(RuntimeError, 'digest mismatch'):
                    build.copy_verified(source, target, '0' * 64)
                advise.assert_called_once()
            self.assertTrue(target.exists())
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                build.copy_verified(source, target, '0' * 64)

    def test_seed_inventory_is_download_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            artifact = root / 'content_addressable/sha256' / build.EMBEDDED_JDK / 'file'
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b'fake archive')
            marker = artifact.parent / ('id-' + 'a' * 64)
            marker.write_bytes(b'')
            (root / 'contents').mkdir()
            (root / 'contents/never-read').write_bytes(b'not a seed')
            entries = build.download_entries(root, 2, 100)
            self.assertEqual(set(entries), {(build.EMBEDDED_JDK, artifact),
                                          (hashlib.sha256(b'').hexdigest(), marker)})
            with self.assertRaisesRegex(RuntimeError, 'budget'):
                build.download_entries(root, 1, 1)

    def test_seed_rejects_nonempty_canonical_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            bucket = root / 'content_addressable/sha256' / build.EMBEDDED_JDK
            bucket.mkdir(parents=True)
            (bucket / 'file').write_bytes(b'fake archive')
            (bucket / ('id-' + 'a' * 64)).write_bytes(b'unexpected')
            with self.assertRaisesRegex(RuntimeError, 'canonical-ID'):
                build.download_entries(root, 3, 100)

    def test_seed_requires_pinned_jdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'content_addressable/sha256').mkdir(parents=True)
            with self.assertRaisesRegex(RuntimeError, 'JDK27'):
                build.download_entries(root, 1, 100)

    def archive(self, root, name, link=None):
        archive = root / 'source.tar'
        with tarfile.open(archive, 'w') as stream:
            member = tarfile.TarInfo(name)
            if link is not None:
                member.type, member.linkname = tarfile.SYMTYPE, link
                stream.addfile(member)
            else:
                member.size = 3
                stream.addfile(member, io.BytesIO(b'abc'))
        return archive

    def test_archive_extracts_plain_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / 'source'
            source.mkdir()
            build.extract_archive(self.archive(root, 'dir/file'), source)
            self.assertEqual((source / 'dir/file').read_bytes(), b'abc')

    def test_archive_rejects_traversal_and_escaping_symlink(self):
        for name, link in [('../escape', None), ('link', '../../escape')]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                source = root / 'source'
                source.mkdir()
                with self.assertRaises(RuntimeError):
                    build.extract_archive(self.archive(root, name, link), source)


if __name__ == '__main__':
    unittest.main()
