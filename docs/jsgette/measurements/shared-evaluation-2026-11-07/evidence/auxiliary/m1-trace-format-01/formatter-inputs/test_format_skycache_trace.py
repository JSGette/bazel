"""Offline dependency and acquisition boundary checks; no network or Java."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import format_skycache_trace as formatter


class Response(io.BytesIO):
    url = 'https://repo1.maven.org/maven2/example.jar'


class FormatterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='formatter-test-', dir=formatter.WORK)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = b'offline jar fixture'
        self.row = {'sha256': hashlib.sha256(self.payload).hexdigest(), 'url': Response.url}

    def test_manifest_exactly_matches_source_pinned_dependency_closure(self):
        manifest = json.loads(formatter.MANIFEST.read_bytes())
        lock = json.loads((formatter.SOURCE / 'maven_install.json').read_bytes())
        self.assertEqual(formatter.sha(formatter.SOURCE / 'maven_install.json'), formatter.LOCK_SHA)
        self.assertEqual(manifest['jars'], formatter.artifact_closure(lock))
        self.assertEqual(len(manifest['jars']), 7)
        self.assertEqual(len(manifest['files']), 7)
        self.assertEqual(manifest['buildifier'], formatter.BUILDIFIER)
        versions = {row['coordinate']: row['version'] for row in manifest['jars']}
        self.assertEqual(versions['com.google.guava:guava'], '33.7.1-jre')
        self.assertEqual(versions['com.google.googlejavaformat:google-java-format'], '1.35.0')

    def test_download_accepts_only_matching_bytes(self):
        with patch.object(formatter.urllib.request, 'urlopen', return_value=Response(self.payload)):
            result = formatter.acquire(self.row, self.root / 'good.jar', self.root / 'missing')
        self.assertEqual(result['method'], 'pinned_download')
        self.assertEqual((self.root / 'good.jar').read_bytes(), self.payload)
        with patch.object(formatter.urllib.request, 'urlopen', return_value=Response(b'wrong bytes')):
            with self.assertRaisesRegex(RuntimeError, 'digest mismatch'):
                formatter.acquire(self.row, self.root / 'wrong.jar', self.root / 'missing')

    def test_redirect_and_download_size_fail_closed(self):
        response = Response(self.payload)
        response.url = 'https://another.example/jar'
        with patch.object(formatter.urllib.request, 'urlopen', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'redirect'):
                formatter.acquire(self.row, self.root / 'redirect.jar', self.root / 'missing')
        with patch.object(formatter.urllib.request, 'urlopen', return_value=Response(self.payload)), \
                patch.object(formatter, 'MAX_JAR_BYTES', 2):
            with self.assertRaisesRegex(RuntimeError, 'exceeds bound'):
                formatter.acquire(self.row, self.root / 'oversized.jar', self.root / 'missing')

    def test_exact_cache_hit_uses_verified_copy_without_network(self):
        cached = self.root / 'cache' / self.row['sha256'] / 'file'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(self.payload)
        with patch.object(formatter.package.build.build, 'copy_verified', return_value={'verified': True}) as copy, \
                patch.object(formatter.urllib.request, 'urlopen') as network:
            result = formatter.acquire(self.row, self.root / 'cached.jar', self.root / 'cache')
        copy.assert_called_once_with(cached, self.root / 'cached.jar', self.row['sha256'])
        network.assert_not_called()
        self.assertEqual(result['method'], 'verified_cache_copy')

    def test_diff_budget_counts_only_changed_content_lines(self):
        difference, count = formatter.formatting_diff(b'one\ntwo\n', b'one\n  two\n', 'BUILD')
        self.assertEqual(count, 2)
        self.assertIn('--- before/BUILD', difference)
        self.assertIn('+++ formatted/BUILD', difference)


if __name__ == '__main__':
    unittest.main()
