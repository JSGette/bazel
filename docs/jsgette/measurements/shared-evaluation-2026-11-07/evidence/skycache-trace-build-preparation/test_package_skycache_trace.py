"""Tiny isolated Git fixtures only; never packages the real source or reads R:."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import package_skycache_trace as pack


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='trace-packager-test-', dir=pack.WORK)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source, self.provenance = self.root / 'source', self.root / 'provenance'
        self.source.mkdir()
        self.provenance.mkdir()
        (self.root / 'out').mkdir()
        self.output = self.root / 'out/bundle-v1'
        pack.git(self.source, 'init', '--quiet')
        pack.git(self.source, 'config', 'core.autocrlf', 'false')
        pack.git(self.source, 'config', 'core.filemode', 'false')
        (self.source / 'safe.txt').write_bytes(b'base\n')
        (self.source / 'module.txt').write_bytes(b'original\n')
        (self.source / 'untouched.txt').write_bytes(b'unchanged\n')
        pack.git(self.source, 'add', '--', '.')
        pack.git(self.source, '-c', 'user.name=Offline fixture', '-c', 'user.email=fixture@example.invalid',
                 'commit', '--no-verify', '--no-gpg-sign', '--quiet', '-m', 'fixture base')
        self.base = pack.git(self.source, 'rev-parse', 'HEAD').decode().strip()
        (self.source / 'safe.txt').write_bytes(b'safe subset\n')
        pack.git(self.source, 'add', '--', 'safe.txt')
        self.baseline = pack.git(self.source, 'write-tree').decode().strip()
        rows = []
        for row in pack.tree_members(self.source, self.baseline).values():
            file = self.source / row['path']
            rows.append(dict(row, bytes=file.stat().st_size, sha256=pack.sha(file)))
        pack.document(self.provenance / 'source-members.json', rows)
        (self.provenance / 'base-source.tar').write_bytes(b'original preserved artifact')
        (self.provenance / 'safe-subset.patch').write_bytes(b'preserved fixture patch')
        self.safe = pack.sha(self.provenance / 'safe-subset.patch')
        pack.document(self.provenance / 'reconstruction.json', {
            'base_commit': self.base, 'patched_git_tree': self.baseline,
            'patched_members_verified': len(rows), 'patch': {'sha256': self.safe},
            'changed_paths': ['safe.txt'],
        })
        self.provenance_pins = {file.name: pack.sha(file) for file in self.provenance.iterdir()}
        (self.source / 'module.txt').write_bytes(b'instrumented\n')
        (self.source / 'test').mkdir()
        (self.source / 'test/collector.txt').write_bytes(b'new test\n')
        self.trace_pins = {name: pack.sha(self.source / name)
                           for name in ('module.txt', 'test/collector.txt')}
        self.constants = patch.multiple(pack, WORK=self.root, SOURCE=self.source,
            PROVENANCE=self.provenance, OUTPUT=self.output, BASE=self.base, BASELINE=self.baseline,
            SAFE_PATCH=self.safe, BASE_MEMBERS=len(rows), PROVENANCE_PINS=self.provenance_pins,
            TRACE_PINS=self.trace_pins)
        self.constants.start()
        self.addCleanup(self.constants.stop)

    def run_package(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return pack.package()

    def test_full_package_binds_export_files_and_both_patch_meanings(self):
        bundle = self.run_package()
        self.assertEqual(bundle['tests'], pack.build.TESTS)
        self.assertEqual(bundle['source_member_count'], 4)
        self.assertEqual(pack.git(self.source, 'rev-parse', 'HEAD').decode().strip(), self.base)
        self.assertEqual(pack.git(self.source, 'write-tree').decode().strip(), bundle['instrumented_git_tree'])
        self.assertEqual(pack.names(pack.git(self.source, 'diff', '--name-only', '-z', self.baseline,
                                            bundle['instrumented_git_tree'])), set(self.trace_pins))
        self.assertEqual({file.name: pack.sha(file) for file in self.provenance.iterdir()}, self.provenance_pins)
        rows = json.loads((self.output / 'source-members.json').read_bytes())
        self.assertEqual({row['path'] for row in rows},
                         {'safe.txt', 'module.txt', 'untouched.txt', 'test/collector.txt'})
        self.assertTrue(pack.build.verify_archive_members(self.output / 'source.tar', rows)['exact_initial_membership'])
        for key, filename in (('source_archive', 'source.tar'), ('source_members', 'source-members.json'),
                              ('instrumentation_patch', 'instrumentation.patch'), ('combined_patch', 'combined.patch')):
            self.assertEqual(bundle[key], pack.artifact(self.output / filename))
            self.assertTrue(bundle[key]['path'].startswith('/mnt/c/'))
        instrumentation = (self.output / 'instrumentation.patch').read_bytes()
        combined = (self.output / 'combined.patch').read_bytes()
        self.assertNotIn(b'diff --git a/safe.txt', instrumentation)
        self.assertIn(b'diff --git a/safe.txt', combined)
        self.assertEqual(json.loads((self.output / 'bundle.json').read_bytes()), bundle)

    def test_unexpected_tracked_or_ignored_file_rejected_before_staging(self):
        (self.source / 'untouched.txt').write_bytes(b'unreviewed\n')
        with self.assertRaisesRegex(RuntimeError, 'instrumentation paths'):
            self.run_package()
        self.assertFalse(self.output.exists())
        self.assertEqual(pack.git(self.source, 'write-tree').decode().strip(), self.baseline)
        (self.source / 'untouched.txt').write_bytes(b'unchanged\n')
        (self.source / '.git/info/exclude').write_bytes(b'ignored.tmp\n')
        (self.source / 'ignored.tmp').write_bytes(b'untracked hidden content')
        with self.assertRaisesRegex(RuntimeError, 'instrumentation paths'):
            self.run_package()

    def test_trace_or_baseline_provenance_mutation_rejected(self):
        (self.source / 'module.txt').write_bytes(b'unreviewed instrumentation\n')
        with self.assertRaisesRegex(RuntimeError, 'Source content differs'):
            self.run_package()
        (self.source / 'module.txt').write_bytes(b'instrumented\n')
        (self.provenance / 'base-source.tar').write_bytes(b'changed original artifact')
        with self.assertRaisesRegex(RuntimeError, 'Source content differs'):
            self.run_package()

    def test_unexpected_index_and_existing_bundle_fail_without_overwrite(self):
        pack.git(self.source, 'add', '--', 'module.txt')
        with self.assertRaisesRegex(RuntimeError, 'index changed'):
            self.run_package()
        pack.git(self.source, 'read-tree', self.baseline)
        self.output.mkdir()
        sentinel = self.output / 'keep.txt'
        sentinel.write_bytes(b'preserve')
        with self.assertRaisesRegex(RuntimeError, 'Fresh owned bundle'):
            self.run_package()
        self.assertEqual(sentinel.read_bytes(), b'preserve')

    def test_export_current_source_must_still_match_git_blob(self):
        baseline = pack.verify_baseline()
        pack.git(self.source, 'add', '--', *self.trace_pins)
        tree = pack.git(self.source, 'write-tree').decode().strip()
        archive = self.root / 'export.tar'
        pack.git(self.source, 'archive', '--format=tar', tree, output=archive)
        (self.source / 'untouched.txt').write_bytes(b'raced after Git archive')
        with self.assertRaisesRegex(RuntimeError, 'Source content differs'):
            pack.verify_export(archive, pack.tree_members(self.source, tree), baseline)


if __name__ == '__main__':
    unittest.main()
