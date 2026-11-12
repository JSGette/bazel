"""Publish existing ten-trace family analysis and update the experiment record."""
import hashlib
import json
import re
from pathlib import Path

W = Path(__file__).resolve().parent
E = W / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
source = W / 'm1-trace-family-analysis-01.json'
raw = source.read_bytes()
d = json.loads(raw)
assert len(d['rows']) == 10 and d['milestone_verdict'] is None
assert hashlib.sha256((W / 'summarize_real_trace_families.py').read_bytes()).hexdigest() == d['helper_sha256']

def new(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f:
        f.write(data)

def edit(path, old, replacement):
    text = path.read_text(encoding='utf-8')
    assert old in text, (path, old)
    path.write_text(text.replace(old, replacement), encoding='utf-8', newline='\n')

new(E / 'm1-trace-family-coverage.json', raw)
packet = E / 'evidence/m1-trace-family-analysis'
records = []
for name in ('summarize_real_trace_families.py', 'test_summarize_real_trace_families.py', 'publish_n1_families.py'):
    b = (W / name).read_bytes()
    new(packet / name, b)
    records.append(dict(path=name, bytes=len(b), sha256=hashlib.sha256(b).hexdigest()))
new(packet / 'files.json', (json.dumps(records, indent=2) + '\n').encode())
lines = ['# Existing N1 trace families', '', 'Analysis: 2026-11-12. No new build, trace, cross-engine replay or gate verdict.', '',
    'This summary streams the ten already verified trace archives, checks their exact byte identities and reconciles every function/state subtotal with the earlier hierarchy-validated replay. The three focused helper tests passed, followed by independent code and result review. [Complete data](m1-trace-family-coverage.json), [helper and tests](evidence/m1-trace-family-analysis/), [N1 overhead assessment](m1-trace-n1-diagnostics.md).', '',
    '## Restarted requests: captured COMPUTE CPU', '',
    'These are exclusive CPU seconds inside traced function intervals, including any instrumentation within those intervals. They are neither trace-off costs nor avoided CPU. All five pairs are included.', '',
    '| Function | Pair 1 | Pair 2 | Pair 3 | Pair 4 | Pair 5 | Median |',
    '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for r in d['function_compute_rank_by_lifecycle']['restart'][:10]:
    vals = r['known_exclusive_compute_ns'] + [r['median_known_exclusive_compute_ns']]
    lines.append('| ' + r['function'] + ' | ' + ' | '.join(f'{v/1e9:.6f}' if v is not None else 'unknown' for v in vals) + ' |')
lines += ['', 'Configured-target restoration ranks first in each restarted trace, but its key/value semantics remain unsupported. Module loading and Bzl loading are also unsupported. A function name identifies a diagnostic family, not an equality or reuse contract.', '',
    '## All captured states', '', '| Lifecycle | Pair | COMPUTE, s | Other captured states, s | All captured states, s |', '| --- | ---: | ---: | ---: | ---: |']
for r in d['rows']:
    states = r['exclusive_cpu_by_state']
    assert all(v['unknown_intervals'] == 0 and v['complete_exclusive_ns'] is not None for v in states.values())
    compute = states['COMPUTE']['complete_exclusive_ns']/1e9
    total = sum(v['complete_exclusive_ns'] for v in states.values())/1e9
    lines.append(f"| {r['state']} | {r['pair']} | {compute:.6f} | {total-compute:.6f} | {total:.6f} |")
lines += ['', 'Known clocks do not mean complete command attribution. Warm requests record just two COMPUTE intervals (BUILD_DRIVER and ACTION_EXECUTION); fifteen other observed nodes have no COMPUTE interval. Retained-only nodes have no current-command CPU credit. Setup, asynchronous work, JVM work and cache finalization remain outside this state ledger or lack distinct attribution.', '',
    '## Semantic and memory limits', '',
    'The exact supported projections in these ten traces are only main-root directory and nonexistent FILE_STATE observations: 811 observations per restarted trace and 269 per warm trace. No digest-backed regular-file, symlink or directory-listing projections occur in this workload. The smoke demonstrated some of those types separately; its coverage must not be imported here.', '',
    'The combined unattributed cost remains the largest unresolved question. Do not subtract these state totals from trace-off request CPU: they come from perturbed executions and omit different work. No individual missing family is established as dominant. The trace also provides no retained-memory ownership partition; projection sizes cannot fill it.', '',
    'Keep all unsupported CPU and memory potentially removable in an optimistic model. Neither inexpensive supported leaves nor expensive unsupported names prove an architectural pass or failure. The next decision must resolve the bounded residual question or declare M1 inconclusive, as required by the frozen protocol.', '']
new(E / 'm1-trace-family-coverage.md', '\n'.join(lines).encode())

report = E / 'm1-trace-n1-diagnostics.md'
edit(report, 'The 415 measured PSS observations had no invalid samples; startup-pending observations remain explicitly recorded.',
     'The 415 measured sampling attempts comprise 395 complete and 20 startup-pending observations, with no invalid samples.')
edit(report, 'First finish the computation-family summary of these same, already captured bytes. Then select exactly one bounded investigation',
     'The [completed computation-family summary](m1-trace-family-coverage.md) leaves a combined unattributed cost envelope. Select exactly one bounded investigation')
readme = E / 'README.md'
edit(readme, 'D033 proceeds to N1 workload coverage and tracing-cost controls; no M1 gate has passed.',
     'D034 closes all twenty N1 sessions and ten replay checks. The [N1 assessment](m1-trace-n1-diagnostics.md) and [function coverage](m1-trace-family-coverage.md) show material trace overhead and unresolved CPU/memory ceilings; no M1 gate has passed.')
plan = W / 'bazel/docs/jsgette/plans/shared-evaluation-gates-2026-11-07.md'
edit(plan, 'Status as of 2026-11-08:', 'Status as of 2026-11-12:')
edit(plan, 'N2/N10 hosting diagnostics complete after qualification and 21 cap-ten acceptance checks; D028 continues to typed-trace qualification, with the savings ceiling still required before a graph rewrite',
     'Hosting and all twenty N1 trace controls complete; D034 records material trace overhead and unresolved semantic/CPU/memory coverage. The savings ceiling is still required before a graph rewrite')
edit(plan, 'D033 proceeds to N1 workload coverage and tracing-cost controls before assessing the savings ceiling.',
     'D034 closes all twenty N1 workload sessions and ten replay checks. The [N1 assessment](../measurements/shared-evaluation-2026-11-07/m1-trace-n1-diagnostics.md) and [function coverage](../measurements/shared-evaluation-2026-11-07/m1-trace-family-coverage.md) show material tracing cost, over 99% unsupported observations and unresolved CPU/memory ceilings.')
with (E / 'decision-log.md').open('a', encoding='utf-8', newline='\n') as f:
    f.write('''\n\n## D034 — Close N1 tracing controls without a sharing verdict (2026-11-12)

All twenty fixed sessions and ten separate wire replays completed on 2026-11-08; the interruption required no rerun. Independent archived verification on 2026-11-12 checked all forty build outputs against the fresh reference, complete provenance/effective-policy/accounting chains, real close receipts, replay results and absent unsignalled final scopes. Nine analyzer tests and three family-summary tests passed; independent review found no remaining correctness issue. The [completed coordinator/tools](evidence/m1-trace-n1-completion/) and thirty actual run/replay evidence directories preserve every result. Analysis SHA-256 is `c286ce13bc68394c4228229bba09422b2504df60e638e5987aacbb38c89e438b`.

The [N1 assessment](m1-trace-n1-diagnostics.md) publishes every ratio. Prepared-request CPU on/off medians are 1.2641 restart and 2.0101 warm; wall medians are 1.1709 and 1.9678. All five pairs remain included. One tiny warm post-response CPU interval is unknown; the full request/build/lifecycle intervals remain known. PSS covers daemons only; its separate observer qualification and whole-control ownership remain unresolved. These observations do not fail an invented tracing threshold or pass M3/M4.

The [existing-byte function summary](m1-trace-family-coverage.md) ranks configured targets first in each restarted trace, followed by module and Bzl loading. The exact projected subset is limited to directories/nonexistent FILE_STATE observations; over 99% of observations remain unsupported. Every complete savings ceiling remains unknown. Retained-only nodes, cross-run N1 repetitions, function names, Python allocation peaks and projection bytes earn no semantic sharing or PSS credit.

**Decision:** do not scale this costly observer to N2/N10 or widen semantic projection families now. Select the combined unattributed CPU/state question for the single bounded residual investigation allowed by the frozen protocol; there is no measured basis to label all of it validation, decoding or GC. Record that investigation's exact input/budget and stop condition before execution. M1 remains pending, M2–M6 not started, and the 50% memory/30% CPU/10% latency gates stay fixed.
''')
print(json.dumps({'family_analysis_sha256': hashlib.sha256(raw).hexdigest(), 'published_traces': 10}))
