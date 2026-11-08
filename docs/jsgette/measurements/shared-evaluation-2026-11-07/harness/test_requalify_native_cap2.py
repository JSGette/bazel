"""Small C-backed source archives only; no Bazel or runtime filesystem access."""
import hashlib
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

import requalify_native_cap2 as qualification


class RequalificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='.requalification-test-',
                                               dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        self.source.mkdir()
        self.original = qualification.ANCHOR + b'  original_info_command\n}\n'
        self.patched = self.original.replace(qualification.ANCHOR,
                                             qualification.ANCHOR + qualification.INSERT)
        self.archive = self.root / 'source.tar'
        with tarfile.open(self.archive, 'w') as archive:
            for name, data in ((qualification.TEST, self.original), ('product.java', b'unchanged product\n')):
                path = self.source / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                member = tarfile.TarInfo(name)
                member.size, member.mode = len(data), 0o644
                archive.addfile(member, io.BytesIO(data))

    def test_archive_verification_detects_unrelated_product_change(self):
        record, test = qualification.verify_base_source(self.archive, self.source)
        self.assertEqual(record['members_verified'], 2)
        self.assertEqual(test, self.original)
        (self.source / 'product.java').write_bytes(b'mutated product!!\n')
        with self.assertRaisesRegex(RuntimeError, 'Source .*changed'):
            qualification.verify_base_source(self.archive, self.source)

    def test_post_command_verification_allows_only_declared_test_patch(self):
        (self.source / qualification.TEST).write_bytes(self.patched)
        with self.assertRaises(RuntimeError):
            qualification.verify_base_source(self.archive, self.source)
        record, test = qualification.verify_base_source(self.archive, self.source, self.patched)
        self.assertEqual(test, self.patched)
        self.assertEqual(record['members_verified'], 2)
        (self.source / 'product.java').write_bytes(b'mutated product!!\n')
        with self.assertRaises(RuntimeError):
            qualification.verify_base_source(self.archive, self.source, self.patched)

    def test_patch_rejects_extra_change_even_with_matching_supplied_hash(self):
        digest = lambda data: hashlib.sha256(data).hexdigest()
        qualification.validate_patch(self.original, self.patched, digest(self.original), digest(self.patched))
        different = self.patched + b'# unrelated change\n'
        with self.assertRaisesRegex(RuntimeError, 'exact test-only'):
            qualification.validate_patch(self.original, different, digest(self.original), digest(different))

    def test_patch_rejects_wrong_hash(self):
        with self.assertRaisesRegex(RuntimeError, 'Old test SHA'):
            qualification.validate_patch(self.original, self.patched, '0' * 64, '0' * 64)

    def test_commands_preserve_build_state_except_evidence_and_test_tmp(self):
        state, output = self.root / 'state', self.root / 'qualification'
        original = qualification.build.commands(state)
        changed = qualification.requalification_commands(state, output)
        self.assertEqual(original['shutdown'], changed['shutdown'])
        for phase in ('build', 'tests'):
            old = original[phase]
            new = changed[phase]
            self.assertEqual(len(old), len(new))
            for a, b in zip(old, new):
                if a.startswith('--build_event_json_file='):
                    self.assertEqual(b, '--build_event_json_file=' + str(output / (phase + '.bep.jsonl')))
                elif a.startswith('--test_tmpdir='):
                    self.assertEqual(b, '--test_tmpdir=' + str(output / 'test-tmp'))
                else:
                    self.assertEqual(a, b)


if __name__ == '__main__':
    unittest.main()
