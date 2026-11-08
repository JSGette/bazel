#!/usr/bin/env python3
"""C-only emitter after actual build+Java smoke evidence; never launch or read R."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile

WORK = Path(__file__).resolve().parent
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
sys.path.insert(0, str(E / 'harness'))
import prepare_native_n2 as preserved
import run_real_trace_n1 as runner

R = PurePosixPath('/mnt/shared-evaluation-20261107')
require, encoded, c_file, document, wsl_path = (preserved.require, preserved.encoded, preserved.c_file,
                                             preserved.document, preserved.wsl_path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def evidence_record(path):
    value, record = document(path)
    record['path'] = wsl_path(record['path'])
    return value, record


def source_candidate(report, proof, report_hash):
    build_root = R / runner.smoke.BUILD_ROOT.name  # Linux identity even under Windows Python.
    require(report.get('status') == 'build_and_focused_tests_passed_no_gate_verdict'
            and report.get('root') == str(build_root)
            and report['source_before'] == report['source_after'], 'Successful source qualification required')
    for name in ('version', 'build', 'tests', 'shutdown'):
        rows = [row for row in report['commands'] if row['name'] == name]
        require(len(rows) == 1 and rows[0].get('returncode') == 0, 'Missing successful qualified command: ' + name)
    candidate = report['candidate']
    require(candidate['destination'] == str(build_root / 'candidate/bazel-skycache-trace')
            and candidate['sha256'] == report['product_binary_sha256_after_build'] == report['product_binary_sha256_after_tests'],
            'Candidate identity differs')
    require(proof.get('verified') is True and proof.get('qualification_passed') is True
            and proof.get('final_scope_absent') is True and proof.get('final_scope_unsignalled') is True
            and proof.get('report_sha256') == report_hash and proof.get('path') == candidate['destination']
            and proof.get('sha256') == candidate['sha256'] and proof.get('bytes') == candidate['bytes'], 'Candidate proof differs')
    bundle = report['bundle']
    require(bundle['base_commit'] == runner.smoke.BASE, 'Wrong reconstructed baseline')
    source = {'base_commit': bundle['base_commit'], 'patch_sha256': bundle['combined_patch']['sha256'],
              'reconstructed_git_tree': bundle['instrumented_git_tree']}
    require(source['reconstructed_git_tree'] != bundle['baseline_git_tree'], 'Instrumented tree missing')
    return candidate, source


def validate_smoke_receipt(metadata, proof, proof_sha, report_hash):
    require(metadata.get('passed') is True and metadata['interop_verification_sha256'] == proof_sha
            and proof.get('verified') is True and proof.get('interop_passed') is True
            and proof['report_sha256'] == report_hash and proof.get('errors') == []
            and proof.get('recorded_daemons_absent') is True and proof.get('final_scope_absent') is True
            and proof.get('final_scope_unsignalled') is True, 'Archived smoke verification differs')


def validate_smoke_archive(directory, report, report_hash):
    """Verify actual archived Java bytes; synthetic report assertions are insufficient."""
    directory = c_file(directory / 'report.json').parent
    metadata, meta_record = document(directory / 'runtime.tar.gz.json')
    proof, proof_record = document(directory / 'interop-verification.json')
    validate_smoke_receipt(metadata, proof, proof_record['sha256'], report_hash)
    require(proof['guard_result_sha256'] == sha(c_file(directory / 'guard/result.json'))
            and proof['scope_identity_sha256'] == sha(c_file(directory / 'guard/scope-identity.json'))
            and proof['candidate_sha256'] == report['candidate']['sha256'],
            'Smoke archive proof is not bound to this guard/candidate')
    archive_path = c_file(directory / 'runtime.tar.gz')
    require(metadata['archive_sha256'] == sha(archive_path), 'Smoke archive digest differs')
    expected = {row['path']: row for row in metadata['members']}
    require(len(expected) == len(metadata['members']), 'Duplicate smoke archive member')
    replay_path = WORK / 'm1-trace-design/replay.py'
    spec = importlib.util.spec_from_file_location('real_trace_n1_replay_check', replay_path)
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    checks = []
    with tarfile.open(archive_path) as archive:
        entries = {member.name: member for member in archive.getmembers()}
        require(len(entries) == len(archive.getmembers()), 'Duplicate actual smoke archive member')

        def member(name, limit):
            entry, row = entries[name], expected[name]
            require(entry.isfile() and entry.size == row['bytes'] and entry.size <= limit, 'Invalid bounded smoke member')
            raw = archive.extractfile(entry).read()
            require(hashlib.sha256(raw).hexdigest() == row['sha256'], 'Smoke member SHA differs: ' + name)
            return raw

        require(hashlib.sha256(member('report.json', 16 * 1024**2)).hexdigest() == report_hash,
                'Archived exact smoke report bytes differ')

        scratch = WORK / 'offline-test-tmp'
        scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='real-trace-proof-', dir=scratch) as temporary:
            for case in report['cases']:
                if not case['trace_enabled']:
                    continue
                name = case['name']
                trace_path = Path(temporary) / (name + '.jsonl')
                raw = member('traces/' + trace_path.name, 256 * 1024**2)
                receipt = member('traces/' + trace_path.name + '.completion.json', 4096)
                context_raw = member('contexts/' + name + '.json', 65536)
                trace_path.write_bytes(raw)
                Path(str(trace_path) + '.completion.json').write_bytes(receipt)
                parsed = replay.read_trace(trace_path)
                context = json.loads(context_raw)
                require(parsed['sha256'] == case['trace_sha256']
                        and parsed['completion_sha256'] == case['completion_sha256']
                        and hashlib.sha256(context_raw).hexdigest() == case['context_sha256'] == parsed['header']['context_sha256']
                        and parsed['header']['command_id'] == case['command_id'] and parsed['header']['trace_id'] == case['trace_id']
                        and {key: parsed['header'][key] for key in replay.CONTRACT['context_file_fields']} == context,
                        'Actual Java trace/context/receipt differs from smoke proof')
                require(parsed['completion']['trace_path'] == str(R / 'm1-trace-acceptance-v1/traces' / trace_path.name),
                        'Smoke receipt runtime path differs')
                checks.append({'trace_sha256': parsed['sha256'], 'completion_sha256': parsed['completion_sha256']})
        result_raw = member('replay-result.json', 8 * 1024**2)
        require(hashlib.sha256(result_raw).hexdigest() == report['replay_result_sha256'], 'Actual smoke replay result changed')
        result = json.loads(result_raw)
        require(len(result['traces']) == 5 and all(row['context_attested'] for row in result['traces'])
                and {row['trace_sha256'] for row in result['traces']} == {row['trace_sha256'] for row in checks},
                'Real smoke replay did not cover all Java traces')
    return {'archive_sha256': metadata['archive_sha256'], 'metadata_sha256': meta_record['sha256'],
            'interop_verification_sha256': proof_record['sha256'],
            'report_sha256': report_hash, 'actual_java_traces_verified': checks,
            'replay_result_sha256': report['replay_result_sha256']}


def fixed_helpers():
    paths = [WORK / name for name in ('run_real_trace_n1.py', 'qualify_skycache_trace.py')]
    paths += [E / 'harness' / name for name in ('native_comparison.py', 'm1_resources.py', 'baseline.py',
                                               'fixture.py', 'output_bases.py', 'native_source_build.py')]
    paths += [WORK / 'm1-trace-design' / name for name in ('replay.py', 'trace-v1.contract.json')]
    return {wsl_path(c_file(path)): sha(path) for path in paths}


def postprocess_helpers():
    return {'archive_real_trace_n1.py': sha(WORK / 'archive_real_trace_n1.py'),
            'analyze_native_n2.py': sha(E / 'harness/analyze_native_n2.py')}


def make_manifest(row, destination, candidate, source, qualified, smoke_proof, fixture_expected,
                  seed, repository, helpers, oracle=None, snapshot=None):
    reference = row['pair'] == 0
    config = {'schema_version': 1, **row, 'task_root': str(R), 'run_root': str(R / row['run_id']),
              'hosting': 'private', 'clients': 1, 'scenario': 'identical', 'shared_name': row['run_id'],
              'reference_only': reference, 'history': runner.HISTORY, 'sample_interval_seconds': 0.5,
              'timeout_seconds': 600, 'drain_deadline_seconds': 60, 'source_repository': repository,
              'binary': {'path': candidate['destination'], 'sha256': candidate['sha256'], 'bytes': candidate['bytes']},
              'source_identity': source, 'qualification': qualified, 'real_smoke_proof': smoke_proof,
              'fixture_expected': fixture_expected, 'fixture_snapshot': snapshot, 'repository_seed': seed,
              'helper_sha256': helpers, 'oracle_report': oracle,
              'postprocess_sha256': postprocess_helpers(),
              'binding_provenance_sha256': runner.nc.digest({'fixture_expected': fixture_expected,
                                                           'fixture_snapshot': snapshot, 'contract': 'main-source-binding-v1'})}
    evidence = PurePosixPath(wsl_path(E)) / 'evidence/auxiliary' / row['run_id']
    config['guard_evidence'] = str(evidence / 'guard')
    config['host_preflight'] = {'path': str(evidence / 'host-budget-preflight.json')}
    manifest_path = destination / (row['run_id'] + '.json')
    prefix = ['python3', '-B', wsl_path(WORK / 'run_real_trace_n1.py'), '--manifest', wsl_path(manifest_path)]
    config['launch_argv_prefix'] = prefix
    raw = encoded(config)
    command = prefix + ['--manifest-sha256', hashlib.sha256(raw).hexdigest()]
    return manifest_path, raw, command


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-evidence', type=Path, required=True)
    parser.add_argument('--smoke-evidence', type=Path, required=True)
    parser.add_argument('--role', choices=('reference', 'matrix'), required=True)
    parser.add_argument('--reference-evidence', type=Path)
    parser.add_argument('--source-repository', required=True, help='Literal canonical Linux read-only source Git path')
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    repository = str(preserved.linux_path(args.source_repository))
    destination = args.destination.resolve()
    require(destination.drive.upper() == 'C:' and destination.is_relative_to(WORK) and not destination.exists(),
            'Destination must be a new C-backed work child')
    build_report, build_record = evidence_record(args.build_evidence / 'report.json')
    candidate_proof, candidate_record = evidence_record(args.build_evidence / 'candidate-verification.json')
    candidate, source = source_candidate(build_report, candidate_proof, build_record['sha256'])
    smoke_report, smoke_record = evidence_record(args.smoke_evidence / 'report.json')
    smoke_guard, smoke_guard_record = evidence_record(args.smoke_evidence / 'guard/result.json')
    runner.validate_smoke_summary(smoke_report, smoke_guard, candidate, source)
    smoke_proof = validate_smoke_archive(args.smoke_evidence, smoke_report, smoke_record['sha256'])
    qualified = {'build': build_record, 'candidate': candidate_record, 'smoke': smoke_record, 'smoke_guard': smoke_guard_record}
    fixture_expected, seed = preserved.archived_inputs()
    helpers = fixed_helpers()
    oracle, snapshot = None, None
    if args.role == 'reference':
        require(args.reference_evidence is None, 'Reference cannot import an oracle')
        rows = [{'run_id': runner.SERIES + '-reference-01', 'pair': 0, 'state': 'restart', 'trace_enabled': False}]
    else:
        require(args.reference_evidence is not None, 'Matrix requires completed independent reference')
        reference, ref_record = evidence_record(args.reference_evidence / 'report.json')
        guard, guard_record = evidence_record(args.reference_evidence / 'guard/result.json')
        metadata, metadata_record = evidence_record(args.reference_evidence / 'runtime.tar.gz.json')
        verification, verification_record = evidence_record(args.reference_evidence / 'verification/result.json')
        archive_path = c_file(args.reference_evidence / 'runtime.tar.gz')
        archive_record = {'path': wsl_path(archive_path), 'sha256': sha(archive_path)}
        runner.validate_reference_verification(reference, ref_record['sha256'], metadata, verification,
                                               archive_record['sha256'], postprocess_helpers())
        preserved.successful_guard(guard)
        require(reference['passed'] and reference['reference_only'] and reference.get('owned_daemons_absent') is True
                and reference.get('source_and_binary_unchanged') is True
                and reference['scope']['membership'].endswith('/' + guard['scope']['unit'])
                and reference['config']['binary']['sha256'] == candidate['sha256']
                and reference['config']['source_identity'] == source
                and reference['config']['fixture_expected'] == fixture_expected
                and reference['config']['repository_seed'] == seed
                and reference['config']['helper_sha256'] == helpers
                and len(reference['phases']) == 1 and reference['phases'][0]['passed'],
                'Same-source/policy independent reference proof differs')
        snapshot = reference['fixture_snapshot']
        require(runner.nc.digest(snapshot) == reference['snapshot_id'], 'Complete reference snapshot digest differs')
        oracle = {**ref_record, 'guard_result': guard_record, 'verification': verification_record,
                  'archive_metadata': metadata_record, 'archive': archive_record}
        rows = runner.schedule()
    destination.mkdir()
    outputs = []
    for row in rows:
        path, raw, command = make_manifest(row, destination, candidate, source, qualified, smoke_proof,
                                          fixture_expected, seed, repository, helpers, oracle, snapshot)
        path.write_bytes(raw)
        command_path = destination / (row['run_id'] + '-command.json')
        command_path.write_bytes(encoded(command))
        outputs.append({**row, 'manifest': str(path), 'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                        'command': str(command_path), 'command_sha256': sha(command_path)})
    (destination / 'order.json').write_bytes(encoded({'schema_version': 1, 'role': args.role,
        'rows': outputs, 'diagnostic_only': True, 'milestone_verdict': None,
        'control_tools': [{'path': str(path), 'sha256': sha(path)} for path in (
            WORK / 'archive_real_trace_n1.py', WORK / 'run_real_trace_n1_matrix.ps1', E / 'harness/run_auxiliary.ps1',
            E / 'harness/analyze_native_n2.py')],
        'runner_timeout_seconds': 2400, 'execution': 'Serial run_auxiliary; each successful guard/report and real wire replay must be archived before next session.',
        'emitter_sha256': sha(Path(__file__)), 'protocol_sha256': sha(WORK / 'm1-real-trace-protocol.md')}))
    print(json.dumps({'role': args.role, 'sessions': len(outputs), 'order': str(destination / 'order.json'),
                      'launched': False, 'milestone_verdict': None}))


if __name__ == '__main__':
    main()
