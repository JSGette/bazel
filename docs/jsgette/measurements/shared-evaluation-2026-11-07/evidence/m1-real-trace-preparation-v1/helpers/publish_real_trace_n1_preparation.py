#!/usr/bin/env python3
"""Copy already-emitted N1 reference preparation and exact C-backed provenance only."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath

WORK = Path(__file__).resolve().parent
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
H = E / 'harness'
DESTINATION = E / 'evidence/m1-real-trace-preparation-v1'
SIX = {
    'prepare_real_trace_n1.py': '403d87e1df49245b77ae7930ea39dbfdb31f89f3ec5464afb6ec5ab65f309aab',
    'run_real_trace_n1.py': '12eeb1d50a05291fab9877be3a8d2c70bd8b8d236369b0f125926efe2ccb8ab3',
    'archive_real_trace_n1.py': '26926742198087465ac19830d65b8969dd078d0b86a3331a5f6dcb01557e699c',
    'run_real_trace_n1_matrix.ps1': 'ef82643a7b6083e1637a29941ebe3a4a068712e7b014f4a012f219d2ad07ab0e',
    'test_real_trace_n1.py': '83a2cdbcca6dd11018879416299e9506d01a049c56961256bbb6626e700f6164',
    'm1-real-trace-protocol.md': '31140cb20b938118c1585d921988a651369327a4ec97d70e8207566a45250cde',
}
CONTROLS = {
    'run_guarded.ps1': '392087d82b47dbb1fdf40e6814ede576cd371a861e828222c123fa621d2fe313',
    'host_memory_guard.psm1': '1a90a0cf8bdc0b9390475733b8915447eb6e774e3de88428bc45aaed105a6b3c',
    'reset_task_mount.py': 'da301d14b2e9916b03e106be86edd233488dbce1a935a160ce91e4451158382f',
}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def linux(path):
    return str(PurePosixPath('/mnt/c', *path.parts[1:]))


def windows(value):
    path = PurePosixPath(value)
    require(str(path) == value and path.parts[:3] == ('/', 'mnt', 'c')
            and '..' not in path.parts and '\\' not in value, 'Only canonical C-backed Linux inputs allowed')
    return Path('C:/').joinpath(*path.parts[3:])


def read(path, expected=None):
    require(path.is_absolute() and path.drive.upper() == 'C:' and path.is_relative_to(WORK)
            and path.resolve() == path and path.is_file() and not path.is_symlink()
            and path.stat().st_size <= 4 * 1024**2, 'Invalid bounded C-backed input: ' + str(path))
    raw = path.read_bytes()
    require(expected is None or digest(raw) == expected, 'Pinned input changed: ' + str(path))
    return raw


def collect(order_path, expected_order):
    """Read-only validation, suitable before the one explicit publication invocation."""
    raw_order = read(order_path, expected_order)
    order = json.loads(raw_order)
    require(order['schema_version'] == 1 and order['role'] == 'reference' and len(order['rows']) == 1
            and order['diagnostic_only'] is True and order['milestone_verdict'] is None,
            'Exactly the emitted diagnostic reference is required')
    row = order['rows'][0]
    require(row['run_id'] == 'm1-trace-n1-reference-01' and row['pair'] == 0
            and row['state'] == 'restart' and row['trace_enabled'] is False, 'Reference row differs')
    manifest_path, command_path = (Path(row[key]) for key in ('manifest', 'command'))
    require(manifest_path == order_path.parent / (row['run_id'] + '.json')
            and command_path == order_path.parent / (row['run_id'] + '-command.json'), 'Emitted file locations differ')
    raw_manifest = read(manifest_path, row['manifest_sha256'])
    raw_command = read(command_path, row['command_sha256'])
    config, command = json.loads(raw_manifest), json.loads(raw_command)
    require(config['reference_only'] is True and config['oracle_report'] is None
            and config['run_id'] == row['run_id'] and config['hosting'] == 'private' and config['clients'] == 1
            and command == ['python3', '-B', linux(WORK / 'run_real_trace_n1.py'), '--manifest', linux(manifest_path),
                            '--manifest-sha256', row['manifest_sha256']]
            and config['launch_argv_prefix'] == command[:-2], 'Literal reference launch differs')
    require(order['emitter_sha256'] == SIX['prepare_real_trace_n1.py']
            and order['protocol_sha256'] == SIX['m1-real-trace-protocol.md'], 'Emitter/protocol version differs')
    files, records = {}, []

    def add(path, name, expected, binding):
        raw = read(path, expected)
        if name in files:
            require(files[name] == raw, 'Conflicting publication copy')
            return
        files[name] = raw
        records.append({'path': name, 'source_windows': str(path), 'source_linux': linux(path),
                        'bytes': len(raw), 'sha256': digest(raw), 'binding': binding})

    for name, expected in SIX.items():
        add(WORK / name, 'helpers/n1/' + name, expected, 'frozen_six_file_checkpoint')
    runtime = [WORK / name for name in ('run_real_trace_n1.py', 'qualify_skycache_trace.py')]
    runtime += [H / name for name in ('baseline.py', 'fixture.py', 'm1_resources.py', 'native_comparison.py',
                                     'native_source_build.py', 'output_bases.py')]
    runtime += [WORK / 'm1-trace-design' / name for name in ('replay.py', 'trace-v1.contract.json')]
    require(set(config['helper_sha256']) == {linux(path) for path in runtime}, 'Runtime import closure differs')
    for path in runtime:
        name = 'helpers/n1/' + path.name if path.name in SIX else 'helpers/imports/' + path.name
        add(path, name, config['helper_sha256'][linux(path)], 'manifest_runtime_helper_pin')
    tools = {Path(item['path']): item['sha256'] for item in order['control_tools']}
    expected_tools = {WORK / 'archive_real_trace_n1.py', WORK / 'run_real_trace_n1_matrix.ps1',
                      H / 'run_auxiliary.ps1', H / 'analyze_native_n2.py'}
    require(set(tools) == expected_tools and len(tools) == len(order['control_tools']), 'Control closure differs')
    for path, expected in tools.items():
        if path.name in config['postprocess_sha256']:
            require(expected == config['postprocess_sha256'][path.name], 'Postprocessor pin differs')
        add(path, 'helpers/' + ('n1/' if path.name in SIX else 'control/') + path.name, expected, 'order_control_pin')
    for name, expected in CONTROLS.items():
        add(H / name, 'helpers/control/' + name, expected, 'frozen_auxiliary_embedded_pin')
    # This preparer-only import is not an emitted runtime pin. Preserve that distinction explicitly.
    add(H / 'prepare_native_n2.py', 'helpers/imports/prepare_native_n2.py',
        '5a4cfafe315aed6ecb46b3a9dc919c6c02c95a5cfb661d218faea0a1db430934',
        'publication_checkpoint_of_preparer_only_import_not_manifest_execution_attestation')
    for path, name, expected in ((order_path, 'emitted/order.json', expected_order),
            (manifest_path, 'emitted/' + manifest_path.name, row['manifest_sha256']),
            (command_path, 'emitted/' + command_path.name, row['command_sha256'])):
        add(path, name, expected, 'emitted_literal_bytes')
    qualification = {}
    for name, item in config['qualification'].items():
        qualification[name] = json.loads(read(windows(item['path']), item['sha256']))
    require(qualification['build']['status'] == 'build_and_focused_tests_passed_no_gate_verdict'
            and qualification['candidate']['verified'] is True and qualification['candidate']['qualification_passed'] is True
            and qualification['candidate']['report_sha256'] == config['qualification']['build']['sha256']
            and qualification['candidate']['sha256'] == config['binary']['sha256']
            and qualification['smoke']['status'] == 'real_trace_interop_passed_no_gate_verdict'
            and qualification['smoke_guard']['status'] == 'completed' and qualification['smoke_guard']['exit_code'] == 0,
            'Successful archived source/smoke preparation evidence is missing')
    smoke_dir = windows(config['qualification']['smoke']['path']).parent
    proof = config['real_smoke_proof']
    metadata = json.loads(read(smoke_dir / 'runtime.tar.gz.json', proof['metadata_sha256']))
    interop = json.loads(read(smoke_dir / 'interop-verification.json', proof['interop_verification_sha256']))
    require(metadata['passed'] is True and metadata['archive_sha256'] == proof['archive_sha256']
            and interop['verified'] is True and interop['interop_passed'] is True
            and interop['report_sha256'] == proof['report_sha256'] == config['qualification']['smoke']['sha256'],
            'Successful actual-smoke archive proof differs')
    add(Path(__file__).resolve(), 'helpers/publish_real_trace_n1_preparation.py', None, 'publication_tool_snapshot')
    require(len(files) <= 32 and sum(map(len, files.values())) <= 16 * 1024**2, 'Publication exceeds fixed small-file bound')
    receipt = {'schema_version': 1, 'kind': 'n1-reference-preparation-provenance', 'files': records,
               'original_order_path': str(order_path), 'order_sha256': expected_order,
               'manifest_sha256': row['manifest_sha256'], 'command_sha256': row['command_sha256'],
               'qualification_inputs_checked': config['qualification'], 'source_identity': config['source_identity'],
               'candidate_identity_only_binary_not_read_or_copied': config['binary'],
               'real_smoke_proof': proof, 'runtime_read': False, 'launched': False, 'milestone_verdict': None,
               'execution_note': 'Copies preserve provenance only. Execute original work paths; copied relative imports are not relocated.'}
    return files, receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--order', type=Path, required=True)
    parser.add_argument('--order-sha256', required=True)
    args = parser.parse_args()
    require(not DESTINATION.exists() and not DESTINATION.is_symlink(), 'Publication destination already exists')
    files, receipt = collect(args.order.resolve(), args.order_sha256)
    DESTINATION.mkdir()
    for name, raw in files.items():
        path = DESTINATION / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
        require(path.read_bytes() == raw, 'Published copy differs')
    for row in receipt['files']:
        require(digest(read(Path(row['source_windows']))) == row['sha256'], 'Source changed during publication')
    with (DESTINATION / 'publication.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'published': str(DESTINATION), 'files': len(files), 'launched': False}))


if __name__ == '__main__':
    main()
