"""Small actual-wire-format fixtures; no Bazel, JVM, filesystem facts or live cache."""
from collections import Counter
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import replay


def utf16(value):
    return value.encode('utf-16-be', errors='surrogatepass').hex()


def regular(value='a', size=1):
    return {'class': 'RegularFileStateValueWithDigest', 'type': 'REGULAR_FILE', 'size': size,
            'digest_algorithm': 'SHA-256', 'digest_hex': replay.digest(value.encode())}


def listing(entries):
    return {'class': 'DirectoryListingStateValue', 'type': 'DIRECTORY_LISTING_STATE',
            'entries': sorted([[utf16(name), kind] for name, kind in entries])}


def node(index=1, path='file', function='FILE_STATE'):
    return {'type': 'node', 'node_id': index, 'function': function,
            'logical_key': ['main', utf16(path)], 'key_status': 'supported', 'reason': None}


def observation(index=1, value=None, groups=(), origin='evaluated_value'):
    return {'type': 'observation', 'observation_id': index, 'node_id': index, 'origin': origin,
            'value_status': 'exact_projection', 'projection': regular() if value is None else value, 'reason': None,
            'deps_status': 'grouped' if groups is not None else 'unavailable',
            'deps_origin': 'evaluated_direct_deps' if groups is not None else 'unavailable',
            'groups': list(groups) if groups is not None else None, 'dependency_read_contract': 'captured_state_only'}


def event(index=1):
    return {'type': 'event', 'node_id': index, 'event': 'evaluated', 'evaluation_state': 'BUILT',
            'new_value_supplied': True, 'new_error_supplied': False, 'direct_deps_supplied': True,
            'dirty_type': None, 'observation_id': index}


def interval(index=1, *, node_id=1, parent=None, wall=(0, 100), cpu=(0, 50), exclusive=50, occurrence=1):
    supported = cpu[0] is not None
    return {'type': 'interval', 'interval_id': index, 'parent_interval_id': parent, 'node_id': node_id,
            'thread_id': 1, 'virtual_thread': not supported, 'state': 'COMPUTE', 'occurrence': occurrence,
            'wall_start_ns': wall[0], 'wall_end_ns': wall[1], 'cpu_start_ns': cpu[0], 'cpu_end_ns': cpu[1],
            'status': 'complete' if supported else 'unsupported_cpu',
            'reason': None if supported else 'virtual_thread_cpu_unsupported', 'exclusive_cpu_ns': exclusive}


def header(engine, snapshot):
    return {'type': 'header', 'format': 'm1-typed-trace-v1',
            'trace_id': '00000000-0000-0000-0000-' + replay.digest((engine + snapshot).encode())[:12],
            'command_id': '10000000-0000-0000-0000-' + replay.digest((engine + snapshot).encode())[:12],
            'engine_id': engine, 'cohort_id': 'offline-fixture', 'phase': 'fixture', 'hosting': 'private',
            'binary_sha256': 'b' * 64, 'source_identity': {'base_commit': 'a' * 40, 'patch_sha256': 'c' * 64,
                                                         'reconstructed_git_tree': 'd' * 40},
            'semantic_epoch': 'file-types-v1', 'snapshot_id': replay.digest(snapshot.encode()),
            'command_policy_sha256': 'e' * 64,
            'context_sha256': '1' * 64, 'actual_digest_algorithm': 'SHA-256',
            'repository_bindings': {'main': {'logical_repository_id': 'repo-fixture',
                'physical_root': '/worktrees/' + engine, 'filesystem_policy': {
                    'os': 'linux', 'case_sensitive': True, 'digest_algorithm': 'SHA-256'},
                'binding_provenance_sha256': 'f' * 64}},
            'limits': dict(replay.LIMITS), 'cpu_capability': {
                'current_thread_supported': True, 'enabled': True, 'virtual_thread_supported': False},
            'bzl_inlining': 'unknown', 'context_status': 'declared',
            'capture_window': 'module_after_cache_setup_to_after_cache_finalization',
            'direct_skycache_witness_validation': 'unobserved'}


def encode(rows, footer_override=None):
    rows = copy.deepcopy(rows)
    for index, row in enumerate(rows):
        row['seq'] = index
    counts = Counter(row['type'] for row in rows)
    nodes = {row['node_id'] for row in rows if row['type'] == 'node'}
    observed = {row['node_id'] for row in rows if row['type'] in {'event', 'interval'}}
    retained = {row['node_id'] for row in rows if row['type'] == 'observation' and row['origin'] == 'retained_done'}
    raw = b''.join(replay.canonical(row) + b'\n' for row in rows)
    footer = {'type': 'footer', 'seq': len(rows), 'record_counts': {kind: counts[kind] for kind in replay.KINDS},
              'node_count': len(nodes), 'edge_count': sum(len(group) for row in rows if row['type'] == 'observation'
                                                        for group in row['groups'] or []),
              'observed_node_count': len(observed), 'retained_only_node_count': len(retained - observed),
              'dependency_only_node_count': len(nodes - observed - retained), 'unknown_counts': {},
              'limits_exceeded': [], 'unbalanced_intervals': 0, 'writer_error': None,
              'preceding_bytes_sha256': replay.digest(raw), 'complete': True}
    footer.update(footer_override or {})
    return raw + replay.canonical(footer) + b'\n'


def simple(engine='one', snapshot='A', value=None, groups=()):
    return [header(engine, snapshot), node(), observation(value=value, groups=groups), event(), interval()]


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='.replay-test-', dir=Path(__file__).resolve().parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.serial = 0

    def trace(self, rows, footer=None):
        self.serial += 1
        rows = copy.deepcopy(rows)
        rows[0]['trace_id'] = '00000000-0000-0000-0000-%012d' % self.serial
        rows[0]['command_id'] = '10000000-0000-0000-0000-%012d' % self.serial
        path = self.root / (str(self.serial) + '.jsonl')
        path.write_bytes(encode(rows, footer))
        Path(str(path) + '.completion.json').write_bytes(replay.canonical({
            'schema_version': 1, 'command_id': rows[0]['command_id'], 'context_sha256': rows[0]['context_sha256'],
            'trace_path': str(path.resolve()), 'completion': True}))
        return replay.read_trace(path)

    def model(self, *rows, attested=True):
        traces = [self.trace(value) for value in rows]
        contexts = {trace['sha256']: {'context': {key: trace['header'][key] for key in replay.CONTRACT['context_file_fields']},
                    'context_sha256': trace['header']['context_sha256'], 'completion_sha256': trace['completion_sha256'],
                    'trace_path': trace['completion']['trace_path']} for trace in traces} if attested else {}
        return replay.replay(traces, {'traces': contexts})

    def test_matching_exact_file_ignores_physical_root_and_snapshot_but_requires_attestation(self):
        result = self.model(simple(), simple('two', 'different snapshot'))['traces'][-1]
        self.assertEqual(result['strict_cross_engine_observation_matches'], 1)
        self.assertEqual(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'], 50)
        missing = self.model(simple(), simple('two'), attested=False)['traces'][-1]
        self.assertFalse(missing['context_attested'])
        self.assertIsNone(missing['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'])

    def test_repository_and_semantic_epoch_change_but_provenance_does_not_change_leaf_identity(self):
        for mutate in (lambda h: h['repository_bindings']['main'].update(logical_repository_id='another-repo'),
                       lambda h: h.update(semantic_epoch='another-equality-policy')):
            other = simple('two')
            mutate(other[0])
            self.assertEqual(self.model(simple(), other)['traces'][-1]['strict_cross_engine_observation_matches'], 0)
        other = simple('two')
        other[0]['command_policy_sha256'] = '0' * 64
        other[0]['repository_bindings']['main']['binding_provenance_sha256'] = '0' * 64
        self.assertEqual(self.model(simple(), other)['traces'][-1]['strict_cross_engine_observation_matches'], 1)
        directory = {'class': 'DirectoryFileStateValue', 'type': 'DIRECTORY'}
        other = simple('two', value=directory)
        other[0]['repository_bindings']['main']['filesystem_policy']['digest_algorithm'] = 'SHA-1'
        other[0]['actual_digest_algorithm'] = 'SHA-1'
        self.assertEqual(self.model(simple(value=directory), other)['traces'][-1]['strict_cross_engine_observation_matches'], 0)

    def test_metadata_proxy_and_error_values_remain_unknown_even_if_digest_known(self):
        for status, reason in (('unsupported', 'metadata_backed_regular'), ('error', 'error_info')):
            rows = simple('two')
            rows[2].update(value_status=status, projection=None, reason=reason)
            result = self.model(simple(), rows)['traces'][-1]
            self.assertEqual(result['strict_cross_engine_observation_matches'], 0)
            self.assertIsNone(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'])
        malformed = simple()
        malformed[2]['projection']['class'] = 'RegularFileStateValueWithContentsProxy'
        with self.assertRaisesRegex(ValueError, 'concrete class'):
            self.trace(malformed)

    def test_size_type_symlink_absence_and_exact_listing_names_are_distinct(self):
        values = [regular('same', 1), regular('same', 2),
                  {'class': 'DirectoryFileStateValue', 'type': 'DIRECTORY'},
                  {'class': 'NonexistentFileStateValue', 'type': 'NONEXISTENT'},
                  {'class': 'SymlinkFileStateValue', 'type': 'SYMLINK', 'target_utf16be_hex': utf16('a')},
                  {'class': 'SymlinkFileStateValue', 'type': 'SYMLINK', 'target_utf16be_hex': utf16('b')}]
        result = self.model(*(simple(str(i), str(i), value=value) for i, value in enumerate(values)))
        self.assertEqual(result['table']['retained_complete_variants'], len(values))
        self.assertTrue(all(row['strict_cross_engine_observation_matches'] == 0 for row in result['traces']))
        listings = [listing([('é', 'FILE'), ('\ud800', 'SYMLINK')]),
                    listing([('e\u0301', 'FILE'), ('\ud800', 'SYMLINK')]),
                    listing([('é', 'DIRECTORY'), ('\ud800', 'SYMLINK')])]
        rows = []
        for i, value in enumerate(listings):
            trace = simple(str(i), str(i), value=value)
            trace[1]['function'] = 'DIRECTORY_LISTING_STATE'
            rows.append(trace)
        self.assertEqual(self.model(*rows)['table']['retained_complete_variants'], 3)

    def test_null_dependencies_are_unknown_not_empty_and_free_oracle_is_separate(self):
        result = self.model(simple(), simple('two', groups=None))['traces'][-1]
        self.assertEqual(result['strict_cross_engine_observation_matches'], 0)
        self.assertEqual(result['projection_cross_engine_observation_matches'], 1)
        self.assertIsNone(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'])
        self.assertEqual(result['captured_node_cpu_ceilings']['projection_free_oracle']['complete_cpu_ceiling_ns'], 50)

    def test_explicit_current_value_capture_keeps_null_callback_flags(self):
        current = simple('two')
        current[2].update(origin='evaluated_current', deps_origin='retained_done')
        current[3].update(new_value_supplied=False, direct_deps_supplied=False)
        result = self.model(simple(), current)['traces'][-1]
        self.assertEqual(result['strict_cross_engine_observation_matches'], 1)
        current[3]['new_error_supplied'] = True
        with self.assertRaisesRegex(ValueError, 'Error callback'):
            self.trace(current)

    def dependency_trace(self, engine, groups, child_value=None):
        return [header(engine, engine), node(1, 'parent'), node(2, 'child'),
                observation(1, groups=groups), observation(2, value=child_value, origin='retained_done'), event(), interval()]

    def test_complete_group_shape_and_transitive_values_are_required(self):
        base = self.dependency_trace('one', [[[2, 2]]])
        for other in (self.dependency_trace('two', [[], [[2, 2]]]),
                      self.dependency_trace('two', [[[2, 2]]], regular('changed'))):
            result = self.model(base, other)['traces'][-1]
            self.assertEqual(result['captured_node_cpu_ceilings']['strict_captured_table']['matched_observed_nodes'], 0)
            self.assertEqual(result['captured_node_cpu_ceilings']['projection_free_oracle']['matched_observed_nodes'], 1)
        unknown = self.dependency_trace('two', [[[2, None]]])
        result = self.model(base, unknown)['traces'][-1]
        self.assertIsNone(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'])

    def test_a_b_a_variants_are_retained_with_separate_same_engine_accounting(self):
        for engines, cross in ((('one', 'two', 'three'), True), (('one', 'one', 'one'), False)):
            result = self.model(*(simple(engine, value, regular(value)) for engine, value in zip(engines, ('A', 'B', 'A'))))
            self.assertEqual(result['table']['retained_complete_variants'], 2)
            last = result['traces'][-1]
            self.assertEqual(last['strict_cross_engine_observation_matches'], int(cross))
            self.assertEqual(last['same_engine_variant_matches_no_credit'], int(not cross))
            self.assertEqual(last['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'], 50 if cross else 0)

    def nested(self, engine, unknown=False):
        rows = [header(engine, engine), node(1, 'outer'), node(2, 'inner'), observation(1), observation(2),
                interval(2, node_id=2, parent=1, wall=(20, 50), cpu=(None, None) if unknown else (20, 50),
                         exclusive=None if unknown else 30),
                interval(1, wall=(0, 100), cpu=(0, 100), exclusive=None if unknown else 70)]
        if unknown:
            rows[-1].update(status='unsupported_cpu', reason='cpu_unavailable')
        return rows

    def test_nested_exclusive_compute_is_not_double_counted(self):
        result = self.model(self.nested('one'), self.nested('two'))['traces'][-1]
        self.assertEqual(result['exclusive_cpu_by_state']['COMPUTE']['known_exclusive_ns'], 100)
        self.assertEqual(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'], 100)

    def test_virtual_child_cpu_makes_ancestor_exclusive_cpu_unknown(self):
        result = self.model(self.nested('one'), self.nested('two', unknown=True))['traces'][-1]
        self.assertEqual(result['exclusive_cpu_by_state']['COMPUTE']['unknown_intervals'], 2)
        self.assertEqual(result['captured_node_cpu_ceilings']['strict_captured_table']['unknown_cpu_nodes'], 2)
        self.assertIsNone(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'])
        wrong = self.nested('two', unknown=True)
        wrong[-1]['exclusive_cpu_ns'] = 100
        with self.assertRaisesRegex(ValueError, 'Derived exclusive'):
            self.trace(wrong)

    def test_missing_compute_is_unknown_and_retained_only_earns_no_cpu(self):
        without_compute = simple('two')[:-1]
        result = self.model(simple(), without_compute)['traces'][-1]
        self.assertEqual(result['observed_nodes_missing_or_unknown_compute_cpu'], 1)
        self.assertIsNone(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'])
        retained = [header('two', 'B'), node(), observation(origin='retained_done')]
        result = self.model(simple(), retained)['traces'][-1]
        self.assertEqual(result['strict_cross_engine_observation_matches'], 1)
        self.assertEqual(result['retained_only_node_count'], 1)
        self.assertEqual(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'], 0)

    def test_restart_attempts_sum_only_exclusive_compute_without_pairing_to_values(self):
        rows = simple('two')
        rows[-1] = interval(cpu=(0, 20), exclusive=20)
        rows.append(interval(2, wall=(110, 200), cpu=(30, 60), exclusive=30, occurrence=2))
        result = self.model(simple(), rows)['traces'][-1]
        self.assertEqual(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'], 50)

    def test_dependency_cycles_are_unknown(self):
        rows = self.dependency_trace('two', [[[2, 2]]])
        rows[4]['groups'] = [[[1, 1]]]
        result = self.model(rows)['traces'][0]
        self.assertTrue(any('dependency_cycle' in reason for reason in result['unknown_observations_by_reason']))
        self.assertIsNone(result['captured_node_cpu_ceilings']['strict_captured_table']['complete_cpu_ceiling_ns'])

    def test_bzl_input_digest_does_not_make_a_module_value_hit(self):
        def bzl(engine):
            n = node(function='BZL_LOAD')
            n.update(key_status='unsupported', logical_key=None, reason='module_value_identity_unsupported')
            obs = observation()
            obs.update(value_status='unsupported', projection=None, reason='module_value_identity_unsupported')
            evidence = {'type': 'bzl_input', 'node_id': 1, 'observation_id': 1, 'key_variant': 'build',
                        'label_parts_utf16be_hex': [utf16(''), utf16('pkg'), utf16('defs.bzl')],
                        'build_prelude': False, 'builtins': False, 'scl_dialect': False,
                        'semantics_binding': None, 'repository_mapping_binding': None,
                        'transitive_digest_hex': 'aabb', 'status': 'input_evidence_only', 'reason': 'bindings_unavailable'}
            return [header(engine, engine), n, obs, evidence, event(), interval()]
        result = self.model(bzl('one'), bzl('two'))['traces'][-1]
        self.assertEqual(result['strict_cross_engine_observation_matches'], 0)
        self.assertIsNone(result['bzl_input_value_hits'])

    def test_duplicate_ids_dangling_edges_and_invalid_class_fail_closed(self):
        duplicate = simple()
        duplicate.insert(2, node())
        dangling = simple()
        dangling[2]['groups'] = [[[99, None]]]
        invalid_size = simple()
        invalid_size[2]['projection']['size'] = True
        for rows in (duplicate, dangling, invalid_size):
            with self.assertRaises(ValueError):
                self.trace(rows)

    def test_footer_overflow_hash_and_missing_footer_fail_closed(self):
        for footer in ({'complete': False}, {'limits_exceeded': ['max_edges']},
                       {'preceding_bytes_sha256': '0' * 64}, {'writer_error': 'disk full'}):
            with self.assertRaises(ValueError):
                self.trace(simple(), footer)
        path = self.root / 'truncated.jsonl'
        path.write_bytes(encode(simple()).rsplit(b'\n', 2)[0] + b'\n')
        with self.assertRaises(ValueError):
            replay.read_trace(path)
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON'):
            replay.parse_json('{"node_id":1,"node_id":2}')

    def test_listing_order_and_interval_counter_regression_fail_closed(self):
        rows = simple(value=listing([('a', 'FILE'), ('b', 'SYMLINK')]))
        rows[1]['function'] = 'DIRECTORY_LISTING_STATE'
        rows[2]['projection']['entries'].reverse()
        with self.assertRaisesRegex(ValueError, 'ordered'):
            self.trace(rows)
        rows = simple()
        rows[-1]['cpu_end_ns'] = -1
        with self.assertRaises(ValueError):
            self.trace(rows)

    def test_completion_missing_mismatch_or_unattested_rebinding_cannot_enter_table(self):
        trace = self.trace(simple())
        receipt_path = Path(trace['path'] + '.completion.json')
        receipt = json.loads(receipt_path.read_text())
        for field, value in (('completion', False), ('command_id', '0' * 36), ('context_sha256', '0' * 64)):
            changed = dict(receipt, **{field: value})
            receipt_path.write_bytes(replay.canonical(changed))
            with self.assertRaisesRegex(ValueError, 'Completion receipt identity'):
                replay.read_trace(Path(trace['path']))
        receipt_path.unlink()
        with self.assertRaisesRegex(ValueError, 'Completion receipt missing'):
            replay.read_trace(Path(trace['path']))
        with self.assertRaisesRegex(ValueError, 'Caller attestation differs'):
            replay.replay([trace], {'traces': {trace['sha256']: {'context': {}}}})

    def test_duplicate_capture_and_table_budget_fail_closed(self):
        trace = self.trace(simple())
        with self.assertRaisesRegex(ValueError, 'Duplicate captured'):
            replay.replay([trace, trace], {'traces': {}})
        with patch.object(replay, 'MAX_TABLE_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'table byte/count bound'):
                self.model(simple())
        with patch.object(replay, 'MAX_MODEL_BYTES', 1):
            with self.assertRaisesRegex(ValueError, 'transient model payload bound'):
                self.model(simple())

    def test_cli_emits_separate_python_costs_and_refuses_output_overwrite(self):
        traces = [self.trace(simple()), self.trace(simple('two'))]
        attestations = {'traces': {t['sha256']: {
            'context': {key: t['header'][key] for key in replay.CONTRACT['context_file_fields']},
            'context_sha256': t['header']['context_sha256'], 'completion_sha256': t['completion_sha256'],
            'trace_path': t['completion']['trace_path']} for t in traces}}
        attest = self.root / 'attestations.json'
        attest.write_bytes(replay.canonical(attestations))
        output = self.root / 'result.json'
        argv = ['replay.py', *(t['path'] for t in traces), '--attestations', str(attest), '--output', str(output)]
        with patch('sys.argv', argv):
            replay.main()
        result = json.loads(output.read_text())
        self.assertIsNone(result['milestone_verdict'])
        self.assertIsNone(result['python_observer']['process_rss_bytes'])
        self.assertGreaterEqual(result['python_observer']['lookup_validation_thread_cpu_ns'], 0)
        self.assertGreaterEqual(result['python_observer']['lookup_validation_wall_ns'], 0)
        self.assertGreater(result['python_observer']['wall_clock_resolution_seconds'], 0)
        self.assertGreater(result['python_observer']['lookup_traced_peak_increment_bytes'], 0)
        with patch('sys.argv', argv):
            with self.assertRaisesRegex(ValueError, 'New output'):
                replay.main()


if __name__ == '__main__':
    unittest.main()
