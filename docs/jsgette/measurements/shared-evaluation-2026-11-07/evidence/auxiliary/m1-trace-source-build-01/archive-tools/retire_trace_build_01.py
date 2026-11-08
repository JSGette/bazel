"""Retire the failed owner after preserved compilation evidence and live cleanup."""
import hashlib
import json
from pathlib import Path

E = Path('/mnt/c/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07/evidence/auxiliary')
D = E / 'm1-trace-source-build-01'
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
server = Path('/mnt/shared-evaluation-20261107/m1-skycache-trace-build-v1/build-output/server/server.pid.txt')
if server.exists():
    assert not Path('/proc', server.read_text().strip()).exists()
assert sha(D / 'runtime.tar.gz') == '158f42eb85d1a2a41e085a4a9770a43b343ad50730cae42896bdc05020302d8e'
report = json.loads((D / 'report.json').read_bytes())
assert report['status'] == 'failed' and report['source_before'] == report['source_after']
assert [(r['name'], r['returncode']) for r in report['commands']] == [('version', 0), ('build', 1), ('shutdown', 0)]
assessment = {
    'status': 'assessed_collector_java_api_compile_failure', 'architecture_gate_failure': False,
    'reason': 'Two references to nonexistent SkyFunctions.FILE_STATE and getClass() selected on an inaccessible private singleton type.',
    'repair': 'Use the actual file-state function identity and accessible exact-class comparison; preserve semantics for deserialized nonexistent states. Freeze a new source bundle and rerun source/unit qualification in fresh v2 runtime state.',
    'tests_executed': False, 'candidate_qualified': False, 'milestone_verdict': None,
    'live_scope_absent': True, 'build_server_absent': True, 'original_runtime_preserved': True,
    'runtime_archive_sha256': sha(D / 'runtime.tar.gz'), 'report_sha256': sha(D / 'report.json'),
    'guard_result_sha256': sha(D / 'guard/result.json'), 'build_state_reuse': False,
}
with (D / 'failure-assessment.json').open('x') as out:
    json.dump(assessment, out, indent=2)
    out.write('\n')
retired = D / 'retired-owner-after-assessment.json'
assert not retired.exists() and owner.resolve() == owner and retired.parent.resolve() == D
owner.rename(retired)
print(json.dumps(assessment))
