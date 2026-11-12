"""Publish completed, already verified N1 evidence without rerunning a workload."""
import hashlib
import json
from pathlib import Path

W = Path(__file__).resolve().parent
E = W / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
source = W / 'm1-trace-n1-analysis-01.json'
raw = source.read_bytes()
assert hashlib.sha256(raw).hexdigest() == 'c286ce13bc68394c4228229bba09422b2504df60e638e5987aacbb38c89e438b'
d = json.loads(raw)
assert d['verified_rows'] == 20 and d['milestone_verdict'] is None
assert hashlib.sha256((W / 'analyze_real_trace_n1.py').read_bytes()).hexdigest() == d['analyzer_sha256']

def new(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(contents)

new(E / 'm1-trace-n1-diagnostics.json', raw)
packet = E / 'evidence/m1-trace-n1-completion'
assert not packet.exists()
records = []
inputs = [W / name for name in ('analyze_real_trace_n1.py', 'test_analyze_real_trace_n1.py')]
inputs += [W / 'm1-trace-n1-matrix-preparation-v1' / name for name in ('order.json', 'execution-progress.json')]
inputs += sorted((W / 'm1-trace-n1-matrix-preparation-v1').glob('*.log'))
inputs += sorted((W / 'm1-trace-n1-matrix-preparation-v1').glob('*-wire-command.json'))
inputs += [Path(__file__).resolve()]
for path in inputs:
    data = path.read_bytes()
    assert len(data) <= 4 * 1024**2
    new(packet / path.name, data)
    records.append({'source': str(path), 'path': path.name, 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()})
new(packet / 'files.json', (json.dumps(records, indent=2) + '\n').encode())

lines = ['# N1 trace cost and coverage — completed diagnostics', '',
    'Measurements: 2026-11-08. Verification and assessment: 2026-11-12.', '',
    '**Outcome:** all twenty fixed sessions and ten separate replay checks passed. Tracing has material cost and leaves most semantic observations unsupported. This does not pass M1 or establish that shared evaluation cannot work.', '',
    'The same qualified SkyCache binary ran in both arms, with tracing disabled for preparation. Each session had independent state; restarted and warm lifecycles remained separate. The independent reference, source/behavior oracles, effective policies, complete raw accounting, close receipts and final unsignalled cleanup were verified. All forty build outputs matched the reference digest. [Complete data](m1-trace-n1-diagnostics.json), [frozen protocol](evidence/m1-real-trace-preparation-v1/helpers/n1/m1-real-trace-protocol.md), [completed coordinator and analysis tools](evidence/m1-trace-n1-completion/).', '',
    '## Every prepared-request pair', '',
    'Ratios are tracing on / off. CPU uses the conservative non-harness upper/lower ratio. This remainder includes clients and any compiler descendants; it is not automatically evaluator CPU. The prepared request includes descriptor/context checks, build, materialization and required drain, while initial preparation and subsequent behavior checks remain in the separate lifecycle window.', '',
    '| Lifecycle | Pair | CPU ratio | Wall ratio | Off wall, s | On wall, s |',
    '| --- | ---: | ---: | ---: | ---: | ---: |']
for state, comparison in d['comparisons'].items():
    for pair in comparison['pairs']:
        q = pair['windows']['prepared_request']
        lines.append(f"| {state} | {pair['pair']} | {q['non_harness_cpu']['conservative_ratio']:.4f} | {q['boundary_wall']['ratio']:.4f} | {q['boundary_wall']['off']:.4f} | {q['boundary_wall']['on']:.4f} |")
lines += ['', '## Median and full range', '',
    '| Lifecycle | Window | CPU ratio median [range] | Wall ratio median [range] |',
    '| --- | --- | --- | --- |']
def stats(value):
    if value['status'] != 'complete':
        return 'unknown'
    return f"{value['median']:.4f} [{value['range'][0]:.4f}, {value['range'][1]:.4f}]"
for state, comparison in d['comparisons'].items():
    for name, window in comparison['five_pair_statistics']['windows'].items():
        lines.append(f"| {state} | {name} | {stats(window['non_harness_cpu'])} | {stats(window['boundary_wall'])} |")
lines += ['', 'Whole lifecycle includes fixture/seed work, preparation, checks and shutdown; its smaller ratios do not remove the measured request overhead. Warm pair 3 has an unknown non-harness CPU bound in its short post-response tail, preserved as unknown. Its complete request/build/lifecycle CPU bounds remain known. No trace overhead is subtracted from captured function costs.', '',
    '## Actual trace coverage', '',
    '| Lifecycle | Nodes per trace | Observations per trace | Unsupported key/value observations | Observed / retained-only nodes | Trace bytes, range |',
    '| --- | ---: | ---: | ---: | --- | --- |']
for state in ('restart', 'warm'):
    traces = [r['trace'] for r in d['rows'] if r['state'] == state and r['trace_enabled']]
    summaries = [t['replay_summary'] for t in traces]
    fields = ('node_count', 'observation_count', 'observed_node_count', 'retained_only_node_count')
    assert all(all(s[k] == summaries[0][k] for k in fields) for s in summaries)
    s = summaries[0]
    unsupported = s['unknown_observations_by_reason']['key_or_value_unsupported']
    assert all(x['unknown_observations_by_reason'] == s['unknown_observations_by_reason'] for x in summaries)
    lines.append(f"| {state} | {s['node_count']:,} | {s['observation_count']:,} | {unsupported:,} ({100*unsupported/s['observation_count']:.2f}%) | {s['observed_node_count']:,} / {s['retained_only_node_count']:,} | {min(t['bytes'] for t in traces):,}–{max(t['bytes'] for t in traces):,} |")
lines += ['', 'All ten captures stayed within the frozen bounds and had verified context/close receipts. Each replay retained 269 complete variants. Every whole-graph savings ceiling remains unknown. Single-trace replay deliberately earns zero cross-engine credit; these repeated N1 captures are not a concurrent sharing experiment. Retained-only nodes earn no current-command CPU credit. Value bytes, Python allocations and JVM/process memory are different units.', '',
    'The separately guarded parser/table processing ran after each measured session. With Python tracemalloc enabled, restarted traces required about 34.8–35.1 CPU-seconds to parse and 3.70–3.77 CPU-seconds for table processing; warm traces required 6.64–6.84 and 1.24–1.25 CPU-seconds respectively. These are costs of this diagnostic JSON/parser implementation, not a production cache lookup or avoided evaluator work.', '',
    '## Memory, work and limits', '',
    'All twenty measured BEPs reported one internal action; restarted builds also reported 23 action-cache hits. No compiler action was reported executed in the measured requests. The 415 measured PSS observations had no invalid samples; startup-pending observations remain explicitly recorded. No measured-build OOM or reclaim scan was recorded. Throttling and I/O pressure are retained in the data.', '',
    '| Lifecycle | Sampled daemon-PSS ratio median [range] |', '| --- | --- |']
for state, comparison in d['comparisons'].items():
    lines.append(f"| {state} | {stats(comparison['five_pair_statistics']['partial_daemon_pss'])} |")
lines += ['', 'Daemon PSS is partial, sampled process memory. Client/harness/other-role memory and a full ownership partition are missing; cgroup peaks include preparation and cannot be subtracted into request peaks. The separate 500-ms PSS versus endpoint-only observer qualification is still pending. These are diagnostic ratios with no M3/M4 threshold verdict.', '',
    '## Next decision', '',
    'Do not scale this observer to an N2/N10 sharing claim or multiply its function costs into trace-off totals. First finish the computation-family summary of these same, already captured bytes. Then select exactly one bounded investigation of the largest measured missing cost question; an undifferentiated residual remains explicitly undifferentiated. The investigation must address the CPU and memory uncertainty relevant to the decision without treating allocation, serialization size, or sampled stack frequency as retained PSS savings. If that one investigation cannot resolve the whole-architecture ceiling, record M1 inconclusive and present the pivot. The frozen 50% memory, 30% CPU and 10% latency goals remain unchanged.', '']
new(E / 'm1-trace-n1-diagnostics.md', '\n'.join(lines).encode())
print(json.dumps({'published_rows': 20, 'coordinator_files': len(records), 'milestone_verdict': None}))
