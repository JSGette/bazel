#!/usr/bin/env python3
"""One guarded N1 SkyCache trace session; explicit evidence, no retry/gate verdict."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import unquote, urlparse

WORK = Path(__file__).resolve().parent
E = WORK / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
sys.path.insert(0, str(E / 'harness'))
import baseline
import fixture
import m1_resources as resources
import native_comparison as nc
import qualify_skycache_trace as smoke

R = Path('/mnt/shared-evaluation-20261107')
SERIES = 'm1-trace-n1'
HISTORY = {'preparations_per_engine': 1, 'identity_calls_per_preparation': 2,
           'preparation_trace_enabled': False, 'separate_restart_warm_pools': True}
require = smoke.require


def schedule():
    return [{'run_id': f'{SERIES}-p{pair:02}-{state}-{mode}', 'pair': pair, 'state': state,
             'trace_enabled': mode == 'on'}
            for pair in range(1, 6) for state in ('restart', 'warm')
            for mode in (('off', 'on') if (pair % 2 == 1) == (state == 'restart') else ('on', 'off'))]


def launch_binding(prefix, manifest_sha):
    argv = prefix + ['--manifest-sha256', manifest_sha]
    raw_command = (json.dumps(argv, indent=2, sort_keys=True) + '\n').encode('utf-8')
    return argv, hashlib.sha256(raw_command).hexdigest()


def verify_documents(records):
    return {name: nc.checked_document(record) for name, record in records.items()}


def validate_config(config):
    require(config['schema_version'] == 1 and config['history'] == HISTORY
            and config['hosting'] == 'private' and config['clients'] == 1
            and config['scenario'] == 'identical' and config['sample_interval_seconds'] == 0.5
            and config['timeout_seconds'] == 600 and config['drain_deadline_seconds'] == 60,
            'Fixed N1 contract differs')
    reference = config['reference_only']
    require(type(reference) is bool, 'Reference role must be explicit')
    expected = ({'run_id': SERIES + '-reference-01', 'pair': 0, 'state': 'restart', 'trace_enabled': False}
                if reference else next((row for row in schedule() if row['run_id'] == config['run_id']), None))
    require(expected is not None and all(config[key] == value for key, value in expected.items()),
            'Run/order/lifecycle differs from fixed schedule')
    root = nc.absolute(config['run_root'])
    require(root == R / config['run_id'] and not root.exists() and not root.is_symlink(), 'Fresh fixed root required')
    require(config['task_root'] == str(R) and config['shared_name'] == config['run_id'], 'Owned root binding differs')
    require(bool(config.get('oracle_report')) != reference, 'Reference/oracle role mismatch')
    for path in (config['binary']['path'], config['source_repository'], config['repository_seed']['root']):
        require(not nc.absolute(path).is_relative_to(root), 'Read-only external input overlaps fresh state')
    required_helpers = {Path(module.__file__).resolve() for module in (sys.modules[__name__], smoke, baseline, fixture, resources, nc)}
    required_helpers.update({(E / 'harness' / name).resolve() for name in ('output_bases.py', 'native_source_build.py')})
    required_helpers.update({(WORK / 'm1-trace-design' / name).resolve() for name in ('replay.py', 'trace-v1.contract.json')})
    require({Path(name) for name in config['helper_sha256']} == required_helpers, 'Complete helper pin set required')
    for name, expected_hash in config['helper_sha256'].items():
        require(fixture.sha256(nc.absolute(name)) == expected_hash, 'Frozen helper changed: ' + name)
    docs = verify_documents(config['qualification'])
    require(docs['build']['status'] == 'build_and_focused_tests_passed_no_gate_verdict', 'Build qualification missing')
    candidate = smoke.validate_provenance({
        'schema_version': 1, 'root': str(smoke.ROOT), 'cases': [list(row) for row in smoke.CASES],
        'source_identity': config['source_identity']}, docs['build'], docs['candidate'], config['qualification']['build']['sha256'])
    require(config['binary'] == {'path': candidate['destination'], 'sha256': candidate['sha256'], 'bytes': candidate['bytes']},
            'Candidate differs from source qualification')
    validate_smoke_summary(docs['smoke'], docs['smoke_guard'], candidate, config['source_identity'])
    require(config['real_smoke_proof']['report_sha256'] == config['qualification']['smoke']['sha256']
            and {row['trace_sha256'] for row in config['real_smoke_proof']['actual_java_traces_verified']}
            == {row['trace_sha256'] for row in docs['smoke']['cases'] if row['trace_enabled']},
            'Actual archived Java wire proof missing')
    require(fixture.sha256(config['binary']['path']) == config['binary']['sha256'], 'Candidate bytes changed')
    return root, docs


def validate_smoke_summary(report, guard, candidate, source):
    require(report.get('status') == 'real_trace_interop_passed_no_gate_verdict'
            and report.get('candidate') == candidate and report.get('source_identity') == source,
            'Real Java smoke did not qualify this candidate/source')
    require(guard.get('status') == 'completed' and guard.get('exit_code') == 0
            and guard.get('final_scope_cleanup') == {'present': False, 'signalled': False, 'verified': True}
            and report['scope_before']['name'] == report['scope_after']['name']
            and report['scope_before']['name'].endswith('/' + guard['scope']['unit']), 'Smoke guard/cleanup differs')
    rows = report['cases']
    require([(r['name'], r['engine_id'], r['variant'], r['trace_enabled']) for r in rows] == smoke.CASES
            and all(row['oracle_passed'] for row in rows) and len({row['command_id'] for row in rows}) == 8
            and len({row['trace_id'] for row in rows if row['trace_enabled']}) == 5,
            'Eight real smoke cases/five traces were not verified')
    require(len(report['cleanup']) == 2 and all(row.get('recorded_identity_absent') is True
            and not row.get('error') for row in report['cleanup']), 'Smoke daemon cleanup incomplete')


def validate_reference_verification(reference, report_sha, metadata, verification, archive_sha, postprocess_sha):
    run_id = SERIES + '-reference-01'
    require(metadata.get('kind') == 'n1-trace-diagnostic' and metadata.get('passed') is True
            and metadata.get('run_id') == run_id and metadata.get('report_sha256') == report_sha
            and metadata.get('manifest_sha256') == reference['manifest_sha256']
            and metadata.get('archive_sha256') == archive_sha, 'Reference archive binding differs')
    require(verification.get('verified') is True
            and verification.get('status') == 'verified_diagnostic_no_gate_verdict'
            and verification.get('run_id') == run_id and verification.get('performance_eligible') is False
            and verification.get('milestone_verdict') is None
            and verification.get('report_sha256') == report_sha
            and verification.get('manifest_sha256') == reference['manifest_sha256']
            and verification.get('archive_sha256') == archive_sha
            and verification.get('postprocess_sha256') == postprocess_sha
            and reference['config'].get('postprocess_sha256') == postprocess_sha,
            'Independent reference lacks a matching successful archive verification')


def policy(paths, row, trace=None, context=None):
    return {**nc.effective_policy(paths, row),
            'experimental_remote_skycache': '', 'experimental_local_skycache': str(paths['skycache']),
            'experimental_analysis_cache_idle_retention': '3600s',
            'symlink_prefix': str(paths['skycache'].parent / 'links') + '/',
            'experimental_m1_trace': '' if trace is None else str(trace),
            'experimental_m1_trace_context': '' if context is None else str(context)}


def argv_for(config, paths, row, directory, trace=None, context=None):
    flags = ['--%s=%s' % (name, value) for name, values in policy(paths, row, trace, context).items()
             for value in (values if isinstance(values, list) else [values])]
    return startup(config, paths) + ['build', fixture.TARGET, *fixture.FLAGS, *flags,
        '--noshow_progress', '--color=no', '--curses=no', '--nobuild_event_json_file_path_conversion',
        '--build_event_json_file=' + str(directory / 'bep.jsonl')]


def startup(config, paths):
    return nc.startup(config, paths, 0) + ['--digest_function=SHA-256', '--install_base=' + str(paths['install'])]


def check_fixture(manifest, expected):
    require(all(manifest[key] == value for key, value in expected.items() if key != 'inputs'), 'Pinned Datadog projection differs')
    require(len(manifest['worktrees']) == 1, 'N1 fixture must contain exactly one worktree')
    row = manifest['worktrees'][0]
    require(all(row[key] == value for key, value in expected['inputs'].items()), 'Pinned target inputs differ')
    baseline.require_fixture_tree(row['workspace'], manifest['private_tree'])
    require(fixture.source_state(row['workspace']) == row['source_state'], 'Fixture source changed')


def directory_inventory(workspace):
    """Explicit controlled-fixture directory/type evidence, including empty directories."""
    workspace = Path(workspace)
    rows = []
    for parent, directories, files in os.walk(workspace, followlinks=False):
        for name in sorted(directories + files):
            path = Path(parent) / name
            relative = path.relative_to(workspace).as_posix()
            if relative == '.git':
                continue  # Per-worktree administrative pointer, not the source snapshot.
            kind = 'symlink' if path.is_symlink() else 'directory' if path.is_dir() else 'file' if path.is_file() else None
            require(kind is not None, 'Unsupported fixture directory entry')
            rows.append({'path': relative, 'type': kind})
            require(len(rows) <= 100000, 'Fixture directory inventory exceeds fixed bound')
    rows.sort(key=lambda row: row['path'])
    require(len(smoke.canonical(rows)) <= 16 * 1024**2, 'Fixture inventory byte bound exceeded')
    return rows


def fixture_snapshot(manifest):
    return {'schema_version': 1, 'source_revision': manifest['source_revision'],
            'projection_archive_sha256': manifest['projection_archive_sha256'], 'private_tree': manifest['private_tree'],
            'inputs': fixture.oracle_inputs(manifest['worktrees'][0]),
            'directory_entries': directory_inventory(manifest['worktrees'][0]['workspace'])}


def check_drain_documents(ownership, stats):
    after = ownership.get('after_session_close', {})
    require(after.get('session_closed') is True and after.get('pending_validation_entries') == 0,
            'Local publication/session drain is unknown')
    require(stats.get('failed') is False and type(stats.get('writes')) is int and stats['writes'] >= 0,
            'Local publication failed or status unknown')
    require(stats['writes'] == 0 or stats.get('published') is True, 'Required local publication not acknowledged')


def materialized_output(bep_path, output_base):
    """Prove the declared executable is locally available inside the request boundary."""
    require(bep_path.stat().st_size <= 32 * 1024**2, 'BEP exceeds fixed evidence bound')
    metrics = baseline.bep_metrics(bep_path)
    require(metrics['finished'] and metrics.get('exit_code', {}).get('code', 0) == 0,
            'Successful BEP completion missing')
    found = set()
    for entry in metrics['files']:
        uri = urlparse(entry.get('uri', ''))
        if uri.scheme == 'file' and not uri.netloc:
            path = Path(unquote(uri.path)).resolve()
            if path.name == 'normalize_cmd':
                require(path.is_relative_to(output_base.resolve()) and path.is_file() and path.stat().st_size > 0,
                        'Normalizer is not locally materialized inside its engine')
                found.add(path)
    require(len(found) == 1, 'Exactly one materialized normalizer required')
    return {'path': str(next(iter(found))), 'verified_before_request_end': True}


def scope_boundary(scope, label, identity=None):
    began, thread_cpu, process_cpu = time.monotonic_ns(), time.thread_time_ns(), time.process_time_ns()
    value = resources.read_cgroup_snapshot(expected_cgroup=scope['membership'], expected_identity=identity)
    ended = time.process_time_ns()
    require(value['eligible'], 'Scope boundary unavailable')
    return {'label': label, 'monotonic_ns': began, 'snapshot': value,
            'harness_process_cpu_before_ns': process_cpu, 'harness_process_cpu_after_ns': ended,
            'thread_cpu_ns': time.thread_time_ns() - thread_cpu, 'duration_ns': time.monotonic_ns() - began}


def window(start, end):
    total = nc.scope_delta(start['snapshot'], end['snapshot'])
    return {'raw_scope_cpu_seconds': total,
            'harness_cpu_bounds': nc.harness_cpu_bounds(start, end,
                (end['snapshot']['cpu.stat']['usage_usec'] - start['snapshot']['cpu.stat']['usage_usec']) * 1000),
            'boundary_wall_seconds': (end['monotonic_ns'] - start['monotonic_ns']) / 1e9}


class Engine:
    def __init__(self, config, manifest, scope):
        self.config, self.manifest, self.scope = config, manifest, scope
        self.root, self.row = Path(config['run_root']), manifest['worktrees'][0]
        self.paths = nc.layout(config, [self.row])
        self.paths.update(skycache=self.root / 'state/skycache', install=self.root / 'state/install')
        self.env = {**nc.launch_environment(self.paths), 'PYTHONDONTWRITEBYTECODE': '1',
                    'XDG_CACHE_HOME': str(self.root / 'home/cache'), 'XDG_CONFIG_HOME': str(self.root / 'home/config'),
                    'XDG_DATA_HOME': str(self.root / 'home/data')}
        self.known = None
        self.identities = []
        self.history = []
        self.snapshot = fixture_snapshot(manifest)
        if config.get('fixture_snapshot') is not None:
            require(self.snapshot == config['fixture_snapshot'], 'Complete fixture snapshot differs from independent reference')

    def discover(self):
        base = self.paths['bases']['0']
        identity = smoke.owned_daemon(base, self.scope['membership'])
        if identity is None:
            return {}
        actual = resources.read_process_identity(identity[0])
        require(actual is not None and actual.start_ticks == identity[1], 'Engine changed during identity read')
        if self.known is not None:
            require(actual == self.known.identity, 'Unexpected daemon replacement')
        mapping = resources.EngineMapping(str(base), base / 'server/server.pid.txt', actual)
        self.known = mapping
        if identity not in self.identities:
            self.identities.append(identity)
        return {'0': mapping}

    def confirm(self, label):
        require(self.known is not None, 'Do not create a daemon through info')
        for key, expected in (('server_pid', str(self.known.identity.pid)), ('output_base', str(self.paths['bases']['0']))):
            require(bool(self.discover()), 'Do not restart a missing daemon through info')
            directory = self.root / label / key
            result = nc.execute(startup(self.config, self.paths) + ['info', key], self.row['workspace'], self.env, directory, 90)
            require(result['exit_code'] == 0 and not result['timed_out']
                    and (directory / 'stdout.log').read_text().strip() == expected, 'Engine info mapping differs')
            self.history.append({'phase': label, 'command': 'info ' + key})
            self.discover()

    def shutdown(self, label):
        current = self.discover()
        if not current:
            return {'not_started_or_already_absent': True}
        known = self.known.identity
        result = nc.execute(startup(self.config, self.paths) + ['shutdown'], self.row['workspace'], self.env, self.root / label, 90)
        require(result['exit_code'] == 0 and not result['timed_out'], 'Owned shutdown failed')
        deadline = time.monotonic() + 10
        while resources.read_process_identity(known.pid) == known and time.monotonic() < deadline:
            time.sleep(0.05)
        require(resources.read_process_identity(known.pid) != known, 'Recorded daemon remains alive')
        require(smoke.owned_daemon(self.paths['bases']['0'], self.scope['membership']) is None, 'New daemon appeared at shutdown')
        self.known = None
        self.history.append({'phase': label, 'command': 'shutdown'})
        return {'identity': [known.pid, known.start_ticks], 'recorded_identity_absent': True, 'process': result}

    def phase(self, name, enabled, lifecycle, oracles, reference_only):
        directory = self.root / name
        directory.mkdir()
        sampler = resources.ResourceSampler({'0': str(self.paths['bases']['0'])}, expected_cgroup=self.scope['membership'])
        reads, samples = [], []
        stream = (directory / 'samples.jsonl').open('x', encoding='utf-8')
        process, executor = None, None
        sidecar_names = ('local-skycache-ownership.json', 'local-skycache.json')
        request_start = None

        def scope_read(label):
            record = scope_boundary(self.scope, label,
                                    request_start['snapshot']['directory_identity'] if request_start else None)
            reads.append(record)
            return record

        def sample(label):
            began = time.thread_time_ns()
            cpu_before = time.process_time_ns()
            value = sampler.sample(self.discover())
            value.update(label=label, discovery_and_sample_thread_cpu_ns=time.thread_time_ns() - began,
                         harness_process_cpu_before_ns=cpu_before, harness_process_cpu_after_ns=time.process_time_ns())
            require(not value['eligible'] or value['pss_bytes'] > 0, 'Eligible JVM sample lacks positive PSS')
            samples.append(value)
            stream.write(json.dumps(value) + '\n')
            stream.flush()
            return value

        try:
            request_start = scope_read('prepared-request-start')
            check_fixture(self.manifest, self.config['fixture_expected'])
            require(fixture_snapshot(self.manifest) == self.snapshot, 'Source directory/type snapshot changed')
            for filename in sidecar_names:
                path = self.paths['bases']['0'] / filename
                if path.exists() or path.is_symlink():
                    require(path.resolve() == path and path.is_file() and path.is_relative_to(self.root),
                            'Unsafe advisory sidecar path')
                    raw = path.read_bytes()
                    require(len(raw) < 2 * 1024**2, 'Prior advisory sidecar exceeds budget')
                    (directory / ('previous-' + filename)).write_bytes(raw)
                    path.unlink()  # Only these exact owned advisory files; never a cache publication.
            trace = directory / 'trace.jsonl' if enabled else None
            context_path = directory / 'context.json' if enabled else None
            argv = argv_for(self.config, self.paths, self.row, directory, trace, context_path)
            context, context_sha = None, None
            if enabled:
                binding = {'logical_repository_id': 'datadog-normalizer-projection-v1', 'physical_root': self.row['workspace'],
                    'filesystem_policy': {'os': 'linux', 'case_sensitive': True, 'digest_algorithm': 'SHA-256'},
                    'binding_provenance_sha256': self.config['binding_provenance_sha256']}
                context = {'engine_id': self.config['run_id'], 'cohort_id': SERIES, 'phase': lifecycle, 'hosting': 'private',
                    'binary_sha256': self.config['binary']['sha256'], 'source_identity': self.config['source_identity'],
                    'semantic_epoch': 'm1-trace-main-facts-v1', 'snapshot_id': nc.digest(self.snapshot),
                    'command_policy_sha256': nc.digest(argv), 'repository_bindings': {'main': binding}, 'bzl_inlining': 'unknown'}
                fixture.save_new(context_path, context)
                context_sha = fixture.sha256(context_path)
            fixture.save_new(directory / 'command.json', {'argv': argv, 'cwd': self.row['workspace']})
            initial = sample('initial')
            require((lifecycle == 'warm' and initial['eligible']) or (lifecycle == 'restart' and not self.discover()),
                    'Wrong pre-build daemon lifecycle')
            boot_start = time.clock_gettime(time.CLOCK_BOOTTIME)
            build_start = scope_read('build-start')
            begin = time.monotonic_ns()
            with (directory / 'stdout.log').open('xb') as stdout, (directory / 'stderr.log').open('xb') as stderr:
                process = subprocess.Popen(argv, cwd=self.row['workspace'], env=self.env, stdout=stdout, stderr=stderr)

                def wait_client():
                    code = process.wait()
                    exited = time.monotonic_ns()
                    return code, exited, scope_read('build-end')

                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='trace-client-wait')
                future = executor.submit(wait_client)
                deadline, next_sample = time.monotonic() + self.config['timeout_seconds'], time.monotonic()
                while not future.done():
                    require(time.monotonic() < deadline, 'Build deadline exceeded')
                    if time.monotonic() >= next_sample:
                        sample('periodic')
                        next_sample = time.monotonic() + 0.5
                    time.sleep(0.01)
                code, exited, build_end = future.result()
            self.history.append({'phase': name, 'command': 'build', 'trace_enabled': enabled})
            result = {'exit_code': code, 'timed_out': False, 'started_ns': begin, 'exited_ns': exited}
            fixture.save_new(directory / 'process-result.json', result)
            require(code == 0, 'Build failed')
            final = sample('after-response')
            require(final['eligible'] and self.known is not None, 'Final engine accounting missing')
            complete_seen = lifecycle == 'warm'
            for value in samples:
                require(value['identity_continuity_valid'] and value['membership_continuity_valid']
                        and value['cgroup']['eligible'], 'Sample identity/scope failed')
                if value['eligible']:
                    complete_seen = True
                else:
                    errors = [error for engine in value['engines'].values() for error in engine['errors'].values()]
                    require(not complete_seen and lifecycle == 'restart' and errors
                            and all(error == 'MissingEngine' for error in errors), 'Invalid/past-startup missing sample')
            if lifecycle == 'restart':
                require(self.known.identity.start_ticks >= int(boot_start * final['clock_ticks_per_second']),
                        'Restarted daemon predates the measured request')
            deadline = exited / 1e9 + self.config['drain_deadline_seconds']
            while True:
                live = {int(pid) for pid in (Path('/sys/fs/cgroup') / self.scope['membership'].lstrip('/') / 'cgroup.procs').read_text().split()}
                extra = live - {os.getpid(), self.known.identity.pid}
                if not extra:
                    break
                require(time.monotonic() < deadline, 'Request descendants did not drain')
                sample('drain')
                time.sleep(0.5)
            sidecars = {}
            for filename in sidecar_names:
                path = self.paths['bases']['0'] / filename
                require(path.is_file() and path.resolve() == path, 'Fresh current-command sidecar absent or redirected')
                raw = path.read_bytes()
                require(len(raw) < 2 * 1024**2, 'Sidecar budget exceeded')
                sidecars[filename] = json.loads(raw)
                (directory / filename).write_bytes(raw)
            check_drain_documents(sidecars['local-skycache-ownership.json'], sidecars['local-skycache.json'])
            output_availability = materialized_output(directory / 'bep.jsonl', self.paths['bases']['0'])
            if enabled:
                receipt, receipt_sha = smoke.document(Path(str(trace) + '.completion.json'), limit=4096)
                require(receipt['completion'] is True and receipt['context_sha256'] == context_sha
                        and receipt['trace_path'] == str(trace) and fixture.sha256(context_path) == context_sha,
                        'Java completion/context proof differs')
            else:
                require(not (directory / 'trace.jsonl').exists() and not (directory / 'trace.jsonl.completion.json').exists(),
                        'Trace-disabled phase produced trace artifacts')
            drain_final = sample('drain-final')
            require(time.monotonic() <= deadline, 'Publication/materialization proof exceeded drain deadline')
            request_end = scope_read('prepared-request-drained')
            require(drain_final['eligible'], 'Drain memory/accounting missing')
            stream.close()
            self.confirm(name + '-identity')
            verified = nc.verify_build(self.row, result, directory, self.paths, oracles, reference_only, self.known)
            events = [json.loads(line) for line in (directory / 'bep.jsonl').read_text().splitlines()]
            verified['trace_policy'] = nc.audit_policy(events, policy(self.paths, self.row, trace, context_path))
            if enabled:
                require(receipt['command_id'] == verified['bep_identity']['build_uuid'], 'Receipt/BEP UUID differs')
                verified['trace'] = {'path': str(trace), 'sha256': fixture.sha256(trace), 'bytes': trace.stat().st_size,
                    'context_path': str(context_path), 'context_sha256': context_sha,
                    'completion_sha256': receipt_sha, 'context': context,
                    'full_wire_replay_required_after_guard': True}
            check_fixture(self.manifest, self.config['fixture_expected'])
            require(fixture_snapshot(self.manifest) == self.snapshot, 'Source directory/type snapshot changed during build')

            value = {'phase': name, 'passed': True, 'trace_enabled': enabled, 'lifecycle': lifecycle, 'result': verified,
                     'build': window(build_start, build_end), 'prepared_request': window(request_start, request_end),
                     'tail': window(build_end, request_end), 'client_wall_seconds': (exited - begin) / 1e9,
                     'daemon_cpu_seconds': nc.cpu_delta(initial, final, lifecycle),
                     'daemon_cpu_note': 'Process endpoints extend outside build-only scope endpoints; no evaluator attribution.',
                     'sampled_peak_daemon_pss_bytes': max(s['pss_bytes'] for s in samples if s['eligible']),
                     'complete_samples': sum(s['eligible'] for s in samples), 'samples': len(samples),
                     'initial': initial, 'final': final, 'drain_final': drain_final, 'scope_reads': reads,
                     'drain': {'local_session_closed': True, 'required_pending_validation': 0,
                               'transient_descendants_absent': True, 'deadline_seconds': 60,
                               'output_availability': output_availability},
                     'uncovered_memory_roles': ['short_lived_clients', 'python_harness', 'other_non_daemon_processes'],
                     'performance_eligible': False}
            fixture.save_new(directory / 'result.json', value)
            return value
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            stream.close()
            fixture.save_new(directory / 'partial-accounting.json', {'scope_reads': reads, 'sample_count': len(samples)})


def run(config, manifest_sha, manifest_path):
    root, _ = validate_config(config)
    scope = nc.attest_scope(config)
    root.mkdir()
    raw_manifest = Path(manifest_path).read_bytes()
    require(smoke.digest(raw_manifest) == manifest_sha, 'Input manifest changed before capture')
    (root / 'manifest.json').write_bytes(raw_manifest)
    report = {'schema_version': 1, 'status': 'running', 'passed': False, 'reference_only': config['reference_only'],
              'performance_eligible': False, 'milestone_verdict': None, 'config': config, 'manifest_sha256': manifest_sha,
              'scope': scope, 'phases': [], 'requires_successful_external_guard': True,
              'measurement_note': 'Trace perturbation and PSS observer overhead unqualified; no trace-CPU subtraction.'}
    engine, start = None, None
    old_temp = {name: os.environ.get(name) for name in ('TMP', 'TMPDIR')}
    try:
        start = scope_boundary(scope, 'lifecycle-start')
        temp = root / 'temp/tmp'
        temp.mkdir(parents=True)
        os.environ.update(TMP=str(temp), TMPDIR=str(temp))
        manifest = fixture.prepare(config['source_repository'], root, 1, 'identical')
        check_fixture(manifest, config['fixture_expected'])
        report['fixture'] = manifest
        engine = Engine(config, manifest, scope)
        report['fixture_snapshot'] = engine.snapshot
        report['snapshot_id'] = nc.digest(engine.snapshot)
        for name in ('user', 'tmp', 'java', 'home', 'disk', 'skycache'):
            engine.paths[name].mkdir(parents=True, exist_ok=True)
        nc.seed_repository(config['repository_seed'], engine.paths['repository'])
        oracles = {}
        if not config['reference_only']:
            reference = nc.checked_document(config['oracle_report'])
            guard = nc.checked_document(config['oracle_report']['guard_result'])
            metadata = nc.checked_document(config['oracle_report']['archive_metadata'])
            verification = nc.checked_document(config['oracle_report']['verification'])
            archive_record = config['oracle_report']['archive']
            require(fixture.sha256(archive_record['path']) == archive_record['sha256'], 'Reference archive changed')
            validate_reference_verification(reference, config['oracle_report']['sha256'], metadata, verification,
                                             archive_record['sha256'], config['postprocess_sha256'])
            require(reference['passed'] and reference['reference_only'] and reference['config']['binary'] == config['binary']
                    and reference['config']['source_identity'] == config['source_identity']
                    and reference['config']['repository_seed'] == config['repository_seed']
                    and reference['config']['fixture_expected'] == config['fixture_expected'], 'Independent reference differs')
            require(guard['status'] == 'completed' and guard['exit_code'] == 0
                    and guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True}
                    and reference['scope']['membership'].endswith('/' + guard['scope']['unit']), 'Reference scope proof differs')
            result = reference['phases'][0]['result']
            require(result['behavior']['passed'] and result['policy_verified'], 'Reference output proof missing')
            oracles = {fixture.oracle_key(manifest['worktrees'][0]): result['output']['sha256']}
        require(not engine.discover(), 'Fresh root has an engine already')
        if config['reference_only']:
            report['phases'].append(engine.phase('reference', False, 'restart', {}, True))
        else:
            report['phases'].append(engine.phase('preparation', False, 'restart', oracles, False))
            prepared = engine.known.identity
            if config['state'] == 'restart':
                report['preparation_shutdown'] = engine.shutdown('preparation-shutdown')
            report['phases'].append(engine.phase('measured', config['trace_enabled'], config['state'], oracles, False))
            actual = engine.known.identity
            require((actual == prepared) == (config['state'] == 'warm'), 'Measured preparation/lifecycle identity differs')
        report.update(passed=True, status='completed_diagnostic_pending_external_guard_and_wire_replay')
    except Exception as error:
        report.update(status='failed', error=type(error).__name__ + ': ' + str(error))
    finally:
        if engine is not None:
            try:
                report['shutdown'] = engine.shutdown('final-shutdown')
                check_fixture(engine.manifest, config['fixture_expected'])
                require(fixture_snapshot(engine.manifest) == engine.snapshot, 'Source snapshot changed during cleanup')
                require(all(smoke.identity(pid) != [pid, ticks] for pid, ticks in engine.identities), 'Recorded daemon remains alive')
                require(fixture.sha256(config['binary']['path']) == config['binary']['sha256'], 'Candidate changed')
                report['final_scope_attestation'] = nc.attest_scope(config, require_fresh_preflight=False)
                for key in ('guard_policy_sha256', 'scope_identity_sha256', 'host_preflight_sha256'):
                    require(report['final_scope_attestation'][key] == scope[key], 'Guard evidence changed')
                report.update(owned_daemons_absent=True, source_and_binary_unchanged=True,
                              daemon_identities=engine.identities, history=engine.history)
            except Exception as error:
                report.update(passed=False, status='failed', cleanup_error=type(error).__name__ + ': ' + str(error))
        if start is not None:
            try:
                end = scope_boundary(scope, 'lifecycle-end', start['snapshot']['directory_identity'])
                report.update(lifecycle_scope_start=start, lifecycle_scope_end=end,
                              lifecycle_window=window(start, end))
            except Exception as error:
                report.update(passed=False, status='failed', lifecycle_error=str(error))
        for name, value in old_temp.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        try:
            for name, expected in config['helper_sha256'].items():
                require(fixture.sha256(name) == expected, 'Helper changed: ' + name)
            require(fixture.sha256(manifest_path) == manifest_sha and fixture.sha256(root / 'manifest.json') == manifest_sha,
                    'Pinned manifest changed')
        except Exception as error:
            report.update(passed=False, status='failed', final_input_error=type(error).__name__ + ': ' + str(error))
        fixture.save_new(root / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    config, actual = smoke.document(args.manifest, args.manifest_sha256)
    launched, command_sha = launch_binding(config['launch_argv_prefix'], actual)
    require(sys.orig_argv == launched, 'Literal launched argv differs')
    config['launch_argv'] = launched  # Derived from pinned bytes; avoids a manifest/self-hash cycle.
    config['guard_command_sha256'] = command_sha
    report = run(config, actual, args.manifest)
    print(json.dumps({'passed': report['passed'], 'report': str(Path(config['run_root']) / 'report.json'),
                      'performance_eligible': False, 'milestone_verdict': None}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
