#!/usr/bin/env python3
"""Publish an already-emitted fixed N1 matrix; reuse the immutable common preparation."""
import argparse
import hashlib
import json
from pathlib import Path

import publish_real_trace_n1_preparation as common

WORK, E = common.WORK, common.E
SOURCE = E / 'evidence/m1-real-trace-preparation-v1'
SOURCE_SHA = 'd326e1a1a6ff8bec8bb162339c8effbd0b7b2e9871e5b4a9960d3ea95600c063'
DESTINATION = E / 'evidence/m1-real-trace-matrix-preparation-v1'
require, read, digest, windows, linux = common.require, common.read, common.digest, common.windows, common.linux


def archive_hash(path):
    require(path.drive.upper() == 'C:' and path.is_relative_to(E / 'evidence') and path.resolve() == path
            and path.is_file() and not path.is_symlink() and path.stat().st_size <= 512 * 1024**2,
            'Reference archive must be bounded existing C-backed evidence')
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def collect(order_path, expected_order):
    publication = json.loads(read(SOURCE / 'publication.json', SOURCE_SHA))
    for item in publication['files']:
        read(SOURCE / item['path'], item['sha256'])
        read(Path(item['source_windows']), item['sha256'])
    previous_order = json.loads(read(SOURCE / 'emitted/order.json', publication['order_sha256']))
    previous = json.loads(read(SOURCE / 'emitted/m1-trace-n1-reference-01.json', publication['manifest_sha256']))
    raw_order = read(order_path, expected_order)
    order = json.loads(raw_order)
    require(order['schema_version'] == 1 and order['role'] == 'matrix' and order['diagnostic_only'] is True
            and order['milestone_verdict'] is None and order['runner_timeout_seconds'] == 2400,
            'Completed matrix emission contract differs')
    for key in ('control_tools', 'emitter_sha256', 'protocol_sha256'):
        require(order[key] == previous_order[key], 'Matrix control/helper checkpoint differs: ' + key)
    expected_ids = [f'm1-trace-n1-p{pair:02}-{state}-{mode}' for pair in range(1, 6)
        for state in ('restart', 'warm')
        for mode in (('off', 'on') if (pair % 2 == 1) == (state == 'restart') else ('on', 'off'))]
    require([row['run_id'] for row in order['rows']] == expected_ids, 'Fixed 20-session order differs')
    files, records = {'emitted/order.json': raw_order}, []
    oracle, snapshot = None, None
    for row in order['rows']:
        manifest_path, command_path = (Path(row[key]) for key in ('manifest', 'command'))
        require(manifest_path == order_path.parent / (row['run_id'] + '.json')
                and command_path == order_path.parent / (row['run_id'] + '-command.json'), 'Unexpected emitted row path')
        raw_manifest, raw_command = read(manifest_path, row['manifest_sha256']), read(command_path, row['command_sha256'])
        config, command = json.loads(raw_manifest), json.loads(raw_command)
        require(all(config[key] == row[key] for key in ('run_id', 'pair', 'state', 'trace_enabled'))
                and config['reference_only'] is False and config['oracle_report'] is not None,
                'Matrix role/order fields differ')
        for key in ('helper_sha256', 'postprocess_sha256', 'binary', 'source_identity', 'qualification', 'real_smoke_proof',
                    'fixture_expected', 'repository_seed', 'source_repository', 'history', 'hosting', 'clients', 'scenario',
                    'sample_interval_seconds', 'timeout_seconds', 'drain_deadline_seconds'):
            require(config[key] == previous[key], 'Matrix differs from common reference policy: ' + key)
        require(command == ['python3', '-B', linux(WORK / 'run_real_trace_n1.py'), '--manifest', linux(manifest_path),
                            '--manifest-sha256', row['manifest_sha256']]
                and config['launch_argv_prefix'] == command[:-2], 'Literal emitted launch differs')
        if oracle is None:
            oracle, snapshot = config['oracle_report'], config['fixture_snapshot']
        require(config['oracle_report'] == oracle and config['fixture_snapshot'] == snapshot,
                'Matrix mixes reference/snapshot inputs')
        for path, raw in ((manifest_path, raw_manifest), (command_path, raw_command)):
            name = 'emitted/' + path.name
            files[name] = raw
            records.append({'path': name, 'source_windows': str(path), 'source_linux': linux(path),
                            'bytes': len(raw), 'sha256': digest(raw)})
    expected_reference = E / 'evidence/auxiliary/m1-trace-n1-reference-01'
    require(windows(oracle['path']) == expected_reference / 'report.json', 'Explicit expected reference required')
    reference = json.loads(read(windows(oracle['path']), oracle['sha256']))
    linked = {'report': oracle}
    documents = {}
    for key, filename in (('guard_result', 'guard/result.json'), ('verification', 'verification/result.json'),
                          ('archive_metadata', 'runtime.tar.gz.json')):
        item = oracle[key]
        require(windows(item['path']) == expected_reference / filename, 'Reference evidence path differs')
        documents[key] = json.loads(read(windows(item['path']), item['sha256']))
        linked[key] = item
    require(windows(oracle['archive']['path']) == expected_reference / 'runtime.tar.gz'
            and archive_hash(windows(oracle['archive']['path'])) == oracle['archive']['sha256'], 'Reference archive changed')
    proof, metadata, guard = (documents[key] for key in ('verification', 'archive_metadata', 'guard_result'))
    require(reference['passed'] is True and reference['reference_only'] is True
            and reference['fixture_snapshot'] == snapshot and reference['config']['helper_sha256'] == previous['helper_sha256']
            and proof['verified'] is True and proof['status'] == 'verified_diagnostic_no_gate_verdict'
            and proof['performance_eligible'] is False and proof['milestone_verdict'] is None
            and proof['report_sha256'] == metadata['report_sha256'] == oracle['sha256']
            and proof['manifest_sha256'] == metadata['manifest_sha256'] == reference['manifest_sha256']
            and proof['archive_sha256'] == metadata['archive_sha256'] == oracle['archive']['sha256']
            and proof['postprocess_sha256'] == previous['postprocess_sha256'] and metadata['passed'] is True
            and guard['status'] == 'completed' and guard['exit_code'] == 0
            and guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True},
            'Successful bound reference/archive verification is required')
    require(len(files) == 41 and sum(map(len, files.values())) <= 128 * 1024**2, 'Unexpected matrix publication size')
    records.insert(0, {'path': 'emitted/order.json', 'source_windows': str(order_path), 'source_linux': linux(order_path),
                      'bytes': len(raw_order), 'sha256': expected_order})
    receipt = {'schema_version': 1, 'kind': 'n1-matrix-preparation-provenance', 'files': records,
        'common_preparation': {'relative_path': '../m1-real-trace-preparation-v1', 'publication_sha256': SOURCE_SHA,
                               'published_commit_as_reported': '13a84cc4ec'},
        'reference_evidence': linked, 'reference_archive': oracle['archive'], 'original_order_path': str(order_path),
        'order_sha256': expected_order, 'source_identity': previous['source_identity'],
        'runtime_read': False, 'launched': False, 'milestone_verdict': None,
        'execution_note': 'Execute the original work paths. These are exact evidence copies; common helpers are linked, not relocated.'}
    return files, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--order', required=True, type=Path)
    parser.add_argument('--order-sha256', required=True)
    args = parser.parse_args()
    require(not DESTINATION.exists() and not DESTINATION.is_symlink(), 'Matrix publication already exists')
    files, receipt = collect(args.order.resolve(), args.order_sha256)
    DESTINATION.mkdir()
    for name, raw in files.items():
        path = DESTINATION / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
        require(path.read_bytes() == raw, 'Published matrix bytes differ')
    for row in receipt['files']:
        read(Path(row['source_windows']), row['sha256'])
    receipt['publisher_sha256'] = digest(read(Path(__file__).resolve()))
    with (DESTINATION / 'publication.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'published': str(DESTINATION), 'emitted_files': len(files), 'launched': False}))


if __name__ == '__main__':
    main()
