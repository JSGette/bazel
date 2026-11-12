"""Synthetic proof/classification tests; no historical samples are analyzed here."""
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import analyze_residual_jfr as analyze


def frame(owner, method):
    return {'method': {'type': {'name': owner}, 'name': method}}


def event(frames, truncated=False):
    return {'type': 'jdk.ExecutionSample', 'values': {
        'startTime': '2026-11-08T00:00:00.000000001Z',
        'sampledThread': {'javaName': 'arbitrary', 'javaThreadId': 7},
        'stackTrace': {'frames': frames, 'truncated': truncated}}}


def document(events):
    return {'recording': {'events': events}}


class ResidualJfrTest(unittest.TestCase):
    def test_utility_leaf_uses_nearest_decisive_domain_caller(self):
        frames = [frame('java/util/HashMap', 'get'),
                  frame(analyze.LOCAL + 'LocalDependencyValidator', 'validateRoot'),
                  frame(analyze.LOCAL + 'LocalAnalysisCacheClient', 'lookup')]
        category, caller, inclusive = analyze.classify(frames)
        self.assertEqual(category, 'cache_dependency_validation')
        self.assertEqual(caller['depth'], 1)
        self.assertEqual(set(inclusive), {'cache_dependency_validation', 'cache_lookup_bookkeeping'})

    def test_store_work_not_double_counted_with_validation_parent(self):
        category, _, inclusive = analyze.classify([
            frame('java/nio/FileChannel', 'read'),
            frame(analyze.LOCAL + 'SegmentedFingerprintValueStore', 'get'),
            frame(analyze.LOCAL + 'LocalDependencyValidator', 'validateRoot')])
        self.assertEqual(category, 'cache_store_evidence_hash_decode')
        self.assertIn('cache_dependency_validation', inclusive)

    def test_snapshot_capture_is_not_assumed_validation(self):
        category, _, _ = analyze.classify([frame(analyze.LOCAL + 'LocalInputSnapshot', 'captureSource')])
        self.assertEqual(category, 'other_cache_serialization')
        category, _, _ = analyze.classify([
            frame(analyze.LOCAL + 'LocalInputSnapshot', 'lambda$isUnchanged$0')])
        self.assertEqual(category, 'cache_dependency_validation')

    def test_common_pool_name_does_not_imply_reconstruction(self):
        frames = [frame('java/util/concurrent/ForkJoinPool', 'runWorker')]
        self.assertEqual(analyze.classify(frames)[0], 'unknown_no_decisive_caller')

    def test_generic_bazel_helper_does_not_hide_specific_parent(self):
        frames = [frame(analyze.BUILD + 'lib/vfs/Path', 'getInputStream'),
                  frame(analyze.LOCAL + 'LocalDependencyValidator', 'validate')]
        self.assertEqual(analyze.classify(frames)[0], 'cache_dependency_validation')

    def test_jvm_runtime_is_not_automatically_jit_or_gc(self):
        self.assertEqual(analyze.classify([frame('/lib/libjvm.so', 'unknown')])[0],
                         'jvm_runtime_unresolved')
        self.assertEqual(analyze.classify([frame('/lib/libjvm.so', 'G1CollectedHeap::collect')])[0],
                         'jvm_gc')
        self.assertEqual(analyze.classify([frame('.', 'Compile::Code_Gen')])[0], 'jvm_jit')
        self.assertEqual(analyze.classify([frame('.', 'G1CollectedHeap::collect')])[0], 'jvm_gc')

    def test_gc_barrier_compiler_and_mutator_work_is_not_collector_cpu(self):
        frames = [frame('/lib/libjvm.so', 'G1BarrierSetC2::load_at_resolved'),
                  frame('/lib/libjvm.so', 'Compile::Compile')]
        self.assertEqual(analyze.classify(frames)[0], 'jvm_jit')
        frames = [frame('/lib/libjvm.so', 'G1BarrierSetRuntime::write_ref_field_post_entry'),
                  frame(analyze.BUILD + 'skyframe/AbstractParallelEvaluator', 'compute')]
        self.assertEqual(analyze.classify(frames)[0], 'other_bazel_starlark')

    def test_generic_serialization_helper_does_not_hide_validation_caller(self):
        frames = [frame(analyze.SERIAL + 'PackedFingerprint', 'readFrom'),
                  frame(analyze.LOCAL + 'LocalDependencyValidator', 'validateRoot')]
        self.assertEqual(analyze.classify(frames)[0], 'cache_dependency_validation')

    def test_full_stack_adds_caller_beyond_five_and_preserves_truncation(self):
        frames = [frame('java/util/HashMap', 'get')] * 5 + [
            frame(analyze.LOCAL + 'LocalDependencyValidator', 'validateRoot')]
        result = analyze.analyze_events(document([event(frames, True)]),
                                        document([event(frames[:5], True)]),
                                        ' jdk.ExecutionSample 1 99\n jdk.NativeMethodSample 0 0\n')
        self.assertEqual(result['samples_with_more_than_original_frames'], 1)
        self.assertEqual(result['recorded_truncated_stack_samples'], 1)
        self.assertEqual(result['original_max5_same_classifier'], {'unknown_no_decisive_caller': 1})
        self.assertEqual(sum(result['disjoint_categories'].values()), 1)
        self.assertIsNone(result['cpu_seconds_derived_from_samples'])

    def test_mutated_event_or_prefix_rejected(self):
        original = document([event([frame('java/util/List', 'get')])])
        for mutation in ('frame', 'time', 'count', 'truncated'):
            deep = copy.deepcopy(original)
            if mutation == 'frame':
                deep['recording']['events'][0]['values']['stackTrace']['frames'][0]['method']['name'] = 'set'
            elif mutation == 'time':
                deep['recording']['events'][0]['values']['startTime'] = 'other'
            elif mutation == 'count':
                deep['recording']['events'].append(copy.deepcopy(deep['recording']['events'][0]))
            else:
                deep['recording']['events'][0]['values']['stackTrace']['truncated'] = True
            with self.assertRaises(RuntimeError):
                analyze.analyze_events(deep, original, ' jdk.ExecutionSample 1 99\n')

    def test_summary_mismatch_or_duplicate_is_rejected(self):
        doc = document([event([])])
        for summary in ('jdk.ExecutionSample 2 99\n', 'jdk.ExecutionSample 1 99\njdk.ExecutionSample 1 99\n', ''):
            with self.assertRaises(RuntimeError):
                analyze.analyze_events(doc, doc, summary)

    def test_missing_and_unknown_samples_remain_in_denominator(self):
        doc = document([event([]), event([frame('java/util/List', 'get')])])
        result = analyze.analyze_events(doc, doc, 'jdk.ExecutionSample 2 99\n')
        self.assertEqual(result['missing_stack_samples'], 1)
        self.assertEqual(result['disjoint_categories'], {'unknown_missing_stack': 1,
                                                        'unknown_no_decisive_caller': 1})

    def test_invalid_depth_duplicate_json_nonfinite_rejected(self):
        with self.assertRaises(RuntimeError):
            analyze.stack_of(event([frame('java/util/List', 'get')] * (analyze.MAX_DEPTH + 1)))
        for raw in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.assertRaises(RuntimeError):
                analyze.strict_json(raw)

    def test_empty_stderr_allowed_but_empty_decoded_or_wrong_digest_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stderr.log'
            path.write_bytes(b'')
            record = {'path': str(path), 'bytes': 0, 'sha256': analyze.sha(b'')}
            self.assertEqual(analyze.read_pinned(record, allow_empty=True), b'')
            with self.assertRaises(RuntimeError):
                analyze.read_pinned(record)
            with self.assertRaises(RuntimeError):
                analyze.read_pinned({**record, 'sha256': '0' * 64}, allow_empty=True)

    def test_command_pins_heap_depth_events_and_owned_raw_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            tool = {'path': '/owned/jfr'}
            row = {'raw': {'path': str(root / 'raw.jfr')},
                   'decoded': {'path': str(root / 'deep.json')},
                   'stderr': {'path': str(root / 'stderr.log')}}
            row['command'] = {'argv': [tool['path'], '-J-Xmx512m', '-J-XX:ActiveProcessorCount=2',
                '-J-Djava.io.tmpdir=/mnt/shared-evaluation-20261107/m1-residual-decoder-01',
                'print', '--json', '--stack-depth', '2048', '--events', ','.join(analyze.EVENTS),
                row['raw']['path']], 'exit_code': 0, 'wall_seconds': 1}
            analyze.check_command(row, tool, root)
            for index, replacement in ((1, '-J-Xmx2g'), (7, '5'), (9, 'jdk.CPULoad'),
                                       (10, str(root.parent / 'foreign.jfr'))):
                bad = copy.deepcopy(row)
                bad['command']['argv'][index] = replacement
                with self.assertRaises(RuntimeError):
                    analyze.check_command(bad, tool, root)

    def test_entire_archive_inventory_is_verified_including_unselected_members(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / 'raw.tar.gz'
            records = []
            with tarfile.open(archive, 'w:gz') as out:
                for name, content in (('wanted', b'one'), ('unselected', b'two')):
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    out.addfile(member, io.BytesIO(content))
                    records.append({'path': name, 'bytes': len(content), 'sha256': analyze.sha(content)})
            archive_record = {'path': str(archive), 'bytes': archive.stat().st_size,
                              'sha256': analyze.sha(archive.read_bytes())}
            metadata = root / 'metadata.json'
            def meta_record():
                metadata.write_text(json.dumps({'archive_sha256': archive_record['sha256'],
                                                'members': records}), encoding='utf-8')
                return {'path': str(metadata), 'bytes': metadata.stat().st_size,
                        'sha256': analyze.sha(metadata.read_bytes())}
            self.assertEqual(analyze.archive_members(archive_record, meta_record(), ['wanted']),
                             {'wanted': b'one'})
            records[1]['sha256'] = '0' * 64
            with self.assertRaisesRegex(RuntimeError, 'member digest'):
                analyze.archive_members(archive_record, meta_record(), ['wanted'])

    def test_manifest_binds_decode_report_raw_archives_and_historical_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write(path, content):
                path.write_bytes(content)
                return {'path': str(path), 'bytes': len(content), 'sha256': analyze.sha(content)}
            def write_json(path, value):
                return write(path, json.dumps(value).encode())
            tool = {'path': '/owned/jfr', 'sha256': analyze.TOOL_SHA}
            protocol = write(root / 'protocol.md', b'Synthetic protocol only')
            manifest = {'schema_version': 1, 'kind': 'm1-residual-jfr-analysis-v1',
                        'protocol': protocol, 'arms': []}
            report = {'schema_version': 1, 'kind': 'archived-m0-restart-fullstack-decode',
                      'protocol_sha256': protocol['sha256'], 'tool': tool, 'passed': True,
                      'diagnostic_only': True, 'milestone_verdict': None, 'wall_seconds': 2,
                      'decoder_sha256': analyze.sha(Path(analyze.__file__).with_name('decode_residual_jfr.py').read_bytes()),
                      'archive_validator_sha256': analyze.sha(Path(analyze.__file__).with_name('analyze_m0_diagnostics.py').read_bytes()),
                      'rows': []}
            archive_pins, raw_pins = {}, {}
            frames = [frame('java/util/List', 'get')] * 5 + [
                frame(analyze.LOCAL + 'LocalDependencyValidator', 'validateRoot')]
            for arm in ('off', 'on'):
                raw = ('synthetic raw ' + arm).encode()
                original_command = {'argv': ['/owned/jfr', 'print', '--json', '--events', ','.join(analyze.EVENTS), '/old/raw.jfr']}
                historical = {'binary_sha256': 'historical-binary', 'decoder': {
                    'tool': tool['path'], 'tool_sha256': tool['sha256']}}
                files = {'restart/cpu.jfr': raw,
                         'restart-jfr-decoded/stdout.log': json.dumps(document([event(frames[:5])])).encode(),
                         'restart-jfr-summary/stdout.log': b'jdk.ExecutionSample 1 99\n',
                         'report.json': json.dumps(historical).encode(),
                         'restart-jfr-decoded/command.json': json.dumps(original_command).encode()}
                archive = root / (arm + '.tar.gz')
                inventory = []
                with tarfile.open(archive, 'w:gz') as out:
                    for name, content in files.items():
                        member = tarfile.TarInfo(name)
                        member.size = len(content)
                        out.addfile(member, io.BytesIO(content))
                        inventory.append({'path': name, 'bytes': len(content), 'sha256': analyze.sha(content)})
                archive_record = {'path': str(archive), 'bytes': archive.stat().st_size,
                                  'sha256': analyze.sha(archive.read_bytes())}
                metadata = write_json(root / (arm + '-meta.json'), {
                    'archive_sha256': archive_record['sha256'], 'members': inventory})
                archive_pins[arm], raw_pins[arm] = archive_record['sha256'], analyze.sha(raw)
                contract = {'arm': arm, 'raw_member': 'restart/cpu.jfr', 'raw_sha256': raw_pins[arm],
                            'original_decoded_member': 'restart-jfr-decoded/stdout.log',
                            'summary_member': 'restart-jfr-summary/stdout.log'}
                manifest['arms'].append({**contract, 'archive': archive_record, 'archive_metadata': metadata})
                row = {**contract, 'archive_path': str(archive), 'archive_sha256': archive_pins[arm],
                       'raw': write(root / (arm + '.jfr'), raw),
                       'decoded': write_json(root / (arm + '.json'), document([event(frames)])),
                       'stderr': write(root / (arm + '.log'), b''),
                       'historical_report_sha256': analyze.sha(files['report.json']),
                       'original_command': original_command}
                row['command'] = {'argv': [tool['path'], '-J-Xmx512m', '-J-XX:ActiveProcessorCount=2',
                    '-J-Djava.io.tmpdir=/mnt/shared-evaluation-20261107/m1-residual-decoder-01',
                    'print', '--json', '--stack-depth', '2048', '--events', ','.join(analyze.EVENTS),
                    row['raw']['path']], 'exit_code': 0, 'wall_seconds': 1}
                report['rows'].append(row)
            manifest['decode_report'] = write_json(root / 'decode-report.json', report)
            with patch.object(analyze, 'PROTOCOL_SHA', protocol['sha256']), \
                    patch.dict(analyze.ARCHIVE_PINS, archive_pins), patch.dict(analyze.RAW_PINS, raw_pins):
                result = analyze.analyze_manifest(manifest)
                self.assertEqual(len(result['rows']), 2)
                self.assertTrue(all(row['historical_binary_sha256'] == 'historical-binary' for row in result['rows']))
                self.assertFalse(result['performance_eligible'])
                self.assertIsNone(result['milestone_verdict'])
                Path(report['rows'][0]['raw']['path']).write_bytes(b'altered raw')
                with self.assertRaises(RuntimeError):
                    analyze.analyze_manifest(manifest)


if __name__ == '__main__':
    unittest.main()
