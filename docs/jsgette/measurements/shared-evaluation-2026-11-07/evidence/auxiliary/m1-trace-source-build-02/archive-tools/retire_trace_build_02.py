"""Retire only the archived second compilation failure after live cleanup proof."""
import hashlib
import json
from pathlib import Path

E = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary')
D = E / 'm1-trace-source-build-02'
owner = E / 'active-owner.json'
old = json.loads(owner.read_bytes())
guard = json.loads((D / 'guard/result.json').read_bytes())
identity = json.loads((D / 'guard/scope-identity.json').read_bytes())
sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
assert old['run_id'] == D.name and old['status'] == 'stopped_for_assessment'
assert guard['status'] == 'command_failed' and guard['exit_code'] == 1
assert guard['final_scope_cleanup'] == {'present': False, 'signalled': False, 'verified': True}
assert identity['Id'] == guard['scope']['unit'] and identity['InvocationID'] == guard['scope']['invocation_id']
assert not (Path('/sys/fs/cgroup') / identity['ControlGroup'].lstrip('/')).exists()
Q = Path('/mnt/shared-evaluation-20261107/m1-skycache-trace-build-v2')
server = Q / 'build-output/server/server.pid.txt'
if server.exists():
    assert not Path('/proc', server.read_text().strip()).exists()
assert sha(D / 'runtime.tar.gz') == 'a248a9c322313f11f384e4f33bd9402ab66da7997e2b76cc401b4c2e1ee66c9a'
report = json.loads((D / 'report.json').read_bytes())
assert report['status'] == 'failed' and report['source_before'] == report['source_after']
assert [(r['name'], r['returncode']) for r in report['commands']] == [('version', 0), ('build', 1), ('shutdown', 0)]
collector = Q / 'build-output/execroot/_main/bazel-out/k8-fastbuild/bin/src/main/java/com/google/devtools/build/lib/skyframe/libm1_trace_collector.jar'
assessment = {
    'status': 'assessed_module_options_annotation_failure', 'architecture_gate_failure': False,
    'reason': 'OptionsClassProcessor requires annotated methods; the new module declared two annotated fields.',
    'repair': 'Adopt the actual abstract option-accessor API and update its call sites/tests. Preserve failed evidence and use an explicit verified incremental build-state reuse protocol with fresh qualification evidence.',
    'tests_executed': False, 'candidate_qualified': False, 'milestone_verdict': None,
    'live_scope_absent': True, 'build_server_absent': True, 'original_runtime_currently_preserved': True,
    'runtime_archive_sha256': sha(D / 'runtime.tar.gz'), 'report_sha256': sha(D / 'report.json'),
    'guard_result_sha256': sha(D / 'guard/result.json'), 'build_state_reuse_planned': True,
    'collector_jar_exists': collector.is_file(),
    'collector_jar_sha256': sha(collector) if collector.is_file() else None,
}
with (D / 'failure-assessment.json').open('x') as out:
    json.dump(assessment, out, indent=2)
    out.write('\n')
retired = D / 'retired-owner-after-assessment.json'
assert not retired.exists() and owner.resolve() == owner and retired.parent.resolve() == D
owner.rename(retired)
print(json.dumps(assessment))
