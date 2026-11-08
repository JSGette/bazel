"""Offline checks of the frozen-bundle boundary; no build/runtime access."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import build_skycache_trace as runner


class TraceBuildBundleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='trace-bundle-test-', dir=runner.WORK)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.work_patch = patch.object(runner, 'WORK', self.root)
        self.work_patch.start()
        self.addCleanup(self.work_patch.stop)

    def bundle(self):
        result = {
            'schema_version': 1, 'base_commit': runner.BASE,
            'baseline_git_tree': runner.BASELINE_TREE,
            'safe_subset_patch_sha256': runner.SAFE_PATCH,
            'instrumented_git_tree': '1' * 40, 'tests': list(runner.TESTS),
        }
        for name in ('source_archive', 'source_members', 'instrumentation_patch', 'combined_patch'):
            data = (name + '\n').encode()
            path = self.root / name
            path.write_bytes(data)
            result[name] = {'path': str(path), 'bytes': len(data),
                            'sha256': hashlib.sha256(data).hexdigest()}
        return result

    def test_valid_bundle_and_all_frozen_pins(self):
        good = self.bundle()
        runner.validate_bundle(good)
        mutations = {
            'base_commit': '0' * 40, 'baseline_git_tree': '0' * 40,
            'safe_subset_patch_sha256': '0' * 64,
            'instrumented_git_tree': runner.BASELINE_TREE,
            'tests': good['tests'][:-1],
        }
        for key, value in mutations.items():
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                bad = copy.deepcopy(good)
                bad[key] = value
                runner.validate_bundle(bad)

    def test_modified_bytes_rejected_even_when_size_matches(self):
        good = self.bundle()
        path = Path(good['source_archive']['path'])
        path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(RuntimeError, 'digest differs'):
            runner.validate_bundle(good)

    def test_outside_bundle_and_invalid_sizes_rejected(self):
        good = self.bundle()
        bad = copy.deepcopy(good)
        path = Path(__file__).resolve()
        bad['source_members'] = {'path': str(path), 'bytes': path.stat().st_size,
                                 'sha256': runner.sha(path)}
        with self.assertRaisesRegex(RuntimeError, 'outside frozen work bundle'):
            runner.validate_bundle(bad)
        for size in (True, 0, 16 * 1024**2 + 1):
            with self.subTest(size=size), self.assertRaises(RuntimeError):
                bad = copy.deepcopy(good)
                bad['source_members']['bytes'] = size
                runner.validate_bundle(bad)

    def archive(self, entries):
        destination = self.root / 'source.tar'
        with tarfile.open(destination, 'w:') as stream:
            for name, kind, content, mode in entries:
                info = tarfile.TarInfo(name)
                info.mode = mode
                info.type = kind
                info.size = len(content) if kind == tarfile.REGTYPE else 0
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    info.linkname = 'top.txt'
                stream.addfile(info, io.BytesIO(content) if info.isfile() else None)
        return destination

    @staticmethod
    def members():
        return [{'path': 'top.txt', 'mode': '100644', 'bytes': 4},
                {'path': 'pkg/tool.sh', 'mode': '100755', 'bytes': 3}]

    @staticmethod
    def entries():
        return [('top.txt', tarfile.REGTYPE, b'text', 0o644),
                ('pkg/', tarfile.DIRTYPE, b'', 0o755),
                ('pkg/tool.sh', tarfile.REGTYPE, b'run', 0o755)]

    def test_exact_regular_file_and_directory_membership(self):
        result = runner.verify_archive_members(self.archive(self.entries()), self.members())
        self.assertEqual(result, {'files': 2, 'directories': 1, 'exact_initial_membership': True})

    def test_missing_and_undeclared_files_or_directories_rejected(self):
        cases = [self.entries()[:-1], self.entries()[::2],
                 self.entries() + [('extra', tarfile.REGTYPE, b'', 0o644)],
                 self.entries() + [('empty/', tarfile.DIRTYPE, b'', 0o755)]]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(RuntimeError):
                runner.verify_archive_members(self.archive(entries), self.members())

    def test_duplicate_files_directories_and_records_rejected(self):
        for entry in (self.entries()[0], self.entries()[1]):
            with self.subTest(entry=entry), self.assertRaises(RuntimeError):
                runner.verify_archive_members(self.archive(self.entries() + [entry]), self.members())
        with self.assertRaisesRegex(RuntimeError, 'Duplicate source member records'):
            runner.verify_archive_members(self.archive(self.entries()), self.members() * 2)

    def test_links_and_noncanonical_file_paths_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                entries = self.entries()
                entries[0] = ('top.txt', kind, b'', 0o644)
                runner.verify_archive_members(self.archive(entries), self.members())
        for name in ('../top.txt', '/top.txt', 'pkg/../top.txt', 'pkg\\tool.sh', './top.txt'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                entries = self.entries()
                entries[0] = (name, tarfile.REGTYPE, b'text', 0o644)
                runner.verify_archive_members(self.archive(entries), self.members())

    def test_declared_size_and_git_executable_bit_rejected_on_mismatch(self):
        for mode, size in (('100644', 3), ('100755', 4), ('120000', 4)):
            with self.subTest(mode=mode, size=size), self.assertRaises(RuntimeError):
                members = self.members()
                members[0].update(mode=mode, bytes=size)
                runner.verify_archive_members(self.archive(self.entries()), members)


if __name__ == '__main__':
    unittest.main()
