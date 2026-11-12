"""Synthetic offline evidence checks; no runtime or architecture measurements."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import analyze_n1_residual_sidecars as audit


class ResidualSidecarTest(unittest.TestCase):
    def test_omitted_histogram_fields_remain_missing_and_no_latency_sum_is_created(self):
        raw = {'histogramBucket': [{'max': '1', 'count': '4'}, {'min': '1', 'max': '2'}]}
        result = audit.histogram({'latency': raw}, 'latency')
        self.assertEqual(result['missing_bucket_fields'], {'min': [0], 'max': [], 'count': [1]})
        self.assertEqual(result['known_recorded_count_sum'], 4)
        self.assertIsNone(result['complete_recorded_count_sum'])
        self.assertNotIn('min', raw['histogramBucket'][0])
        absent = audit.histogram({}, 'latency')
        self.assertFalse(absent['present'])
        self.assertIsNone(absent['complete_recorded_count_sum'])
        self.assertEqual(audit.histogram({'latency': {'histogramBucket': []}}, 'latency')['complete_recorded_count_sum'], 0)
        for invalid in (True, -1, 'nan'):
            with self.assertRaises(RuntimeError):
                audit.histogram({'latency': {'histogramBucket': [{'count': invalid}]}}, 'latency')

    def test_retrieval_phases_remain_separate_and_missing_scalar_is_not_zero(self):
        cache = {'cacheHits': '7', 'latencyBySkyfunction': [
            {'skyfunction': 'CONFIGURED_TARGET', 'outcome': 'HIT', 'phase': phase,
             'latency': {'histogramBucket': [{'max': '1', 'count': '7'}]}}
            for phase in ('TOTAL', 'INITIAL_QUERY')]}
        summary = audit.cache_summary({'remoteAnalysisCacheStatistics': cache})
        self.assertEqual(summary['raw'], cache)
        self.assertEqual(summary['counters']['cacheHits']['integer'], 7)
        self.assertFalse(summary['counters']['cacheMisses']['present'])
        self.assertIsNone(summary['counters']['cacheMisses']['integer'])
        self.assertEqual([row['latency']['complete_recorded_count_sum'] for row in summary['retrieval_distributions']], [7, 7])
        self.assertEqual([row['phase']['raw'] for row in summary['retrieval_distributions']], ['TOTAL', 'INITIAL_QUERY'])
        self.assertFalse(summary['optional_fields']['uploadPhaseTimings']['present'])
        self.assertEqual(audit.cache_summary({}), {'present': False, 'raw': None})

    def test_only_fresh_measured_members_are_read_and_member_digest_is_required(self):
        content = {name: b'{}\n' for name in audit.SELECTED}
        content['measured/previous-local-skycache.json'] = b'prior command: not parsed as measured JSON'
        content['preparation/local-skycache.json'] = b'preparation: not parsed as measured JSON'
        metadata = {'members': [{'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                               for name, raw in sorted(content.items())]}
        scratch = Path(__file__).parent / 'offline-test-tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as temporary:
            directory = Path(temporary)
            with tarfile.open(directory / 'runtime.tar.gz', 'w:gz') as archive:
                for name, raw in sorted(content.items()):
                    member = tarfile.TarInfo(name)
                    member.size = len(raw)
                    archive.addfile(member, io.BytesIO(raw))
            values, inventory = audit.read_measured(directory, metadata)
            self.assertEqual(set(values), audit.SELECTED)
            previous = next(row for row in inventory if row['path'].startswith('measured/previous-'))
            self.assertEqual(previous['audit_role'], 'excluded_prior_command_advisory')
            self.assertFalse(previous['content_read'])
            self.assertFalse(any(row['path'].startswith('preparation/') for row in inventory))
            selected = next(row for row in metadata['members'] if row['path'] == 'measured/local-skycache.json')
            selected['sha256'] = '0' * 64
            with self.assertRaisesRegex(RuntimeError, 'member digest differs'):
                audit.read_measured(directory, metadata)


if __name__ == '__main__':
    unittest.main()
