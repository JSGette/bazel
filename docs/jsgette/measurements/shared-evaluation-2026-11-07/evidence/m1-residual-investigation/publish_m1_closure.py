"""Publish completed residual audit and preserved unavailable decoder attempts."""
import hashlib
import json
from pathlib import Path
import re

W = Path(__file__).resolve().parent
E = W / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
packet = E / 'evidence/m1-residual-investigation'
assert not packet.exists()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def new(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('xb') as f:
        f.write(data)

raw = (W / 'm1-residual-sidecars-01.json').read_bytes()
assert sha(raw) == '689e8521b5ae459e6349d5799e9e11bb7614e6a6cd13f18eeffe722602ad97e7'
d = json.loads(raw)
assert len(d['rows']) == 10 and d['milestone_verdict'] is None
sizes = [r['fresh_local_cache_statistics']['restore']['serialized_bytes'] for r in d['rows']]
assert (min(sizes), max(sizes)) == (21278547, 21294616)
new(E / 'm1-residual-sidecars.json', raw)
records = []
names = ('analyze_n1_residual_sidecars.py', 'test_analyze_n1_residual_sidecars.py',
         'analyze_residual_jfr.py', 'test_analyze_residual_jfr.py', 'decode_residual_jfr.py',
         'prepare_residual_budget.py', 'residual-budget-preparation.json', 'residual-decode-command.json',
         'restore_residual_mount.py', 'residual-mount-restore.json',
         'close_residual_runtime.py', 'residual-runtime-close.json', 'publish_m1_closure.py')
for name in names:
    b = (W / name).read_bytes()
    new(packet / name, b)
    records.append({'source': str(W / name), 'path': name, 'bytes': len(b), 'sha256': sha(b)})
new(packet / 'files.json', (json.dumps(records, indent=2) + '\n').encode())
new(packet / 'README.md', b'''# Final bounded residual investigation

The N1 sidecar audit completed on all ten restarted rows. The full-stack analyzer
and its seventeen synthetic tests were prepared, but no actual deep recording
decode or analysis ran. Both pre-launch failures are in the sibling auxiliary
directories m1-residual-decode-01 and m1-residual-decode-02. No actual decode
manifest/result exists. Scripts here preserve preparation, not invented results.

The decoder-only wrapper and immutable protocol/addendum are linked from the
M1 gate assessment. Runtime-close evidence verifies task-only unmount, loop
detach and keeper shutdown with the 512-GiB image preserved. The Python source
records why host-side /proc/PID/mountinfo was used during cleanup rather than
requiring an unrelated sandbox to mount its own procfs.
''')

audit = (W / 'm1-cache-cost-audit.md').read_text(encoding='utf-8')
audit = audit.replace('Source-only audit, 2026-11-08.', 'Source-only audit, 2026-11-08; reviewed 2026-11-12.')
old = 'and [LocalCacheSession.java:306](<C:/Users/jgett/Documents/Codex/2026-11-07/i-ha/work/skycache-trace-source/source/src/main/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheSession.java:306>) define the timer semantics.'
assert old in audit
audit = audit.replace(old, 'defines the get timer. LocalCacheSession.java:508–577 and 632–676 define session put/get histograms; line 306 defines finalization timing.')
audit = re.sub(r'\[([^\]]+)\]\(<C:/[^>]+>\)', lambda m: '`' + m.group(1) + '`', audit)
audit += '\nSource reconstruction and qualification are pinned by the [candidate proof](evidence/auxiliary/m1-trace-source-build-03/candidate-verification.json) and [v3 source preparation](evidence/skycache-trace-build-preparation-v3/). These line references describe that exact source, not a different branch revision.\n'
audit += '''
## Final follow-up status (2026-11-12)

The [ten-row measured sidecar audit](m1-residual-sidecars.json) completed. The
two historical restart CPU recordings were not decoded at greater depth:
host-budget admission and then task mount inspection stopped the fixed attempts
before launching the tool. Prepared analysis code has synthetic test coverage
only. See the [M1 assessment](m1-gate-assessment.md).

The original JFR print command omitted stack-depth, whose documented default is
five. A startup async-profiler agent can avoid the built-in module's early stop;
its VMInit/VMDeath boundaries still leave lifecycle residuals. A Java heap dump
can describe retention at capture time. None supplies semantic equivalence or
peak-PSS reclaimability. These are feasible alternatives, not experiments run:
[JFR reference](https://docs.oracle.com/en/java/javase/25/docs/specs/man/jfr.html),
[async-profiler 4.5 integration](https://github.com/async-profiler/async-profiler/blob/v4.5/docs/IntegratingAsyncProfiler.md),
[profiler lifecycle callbacks](https://github.com/async-profiler/async-profiler/blob/v4.5/src/vmEntry.cpp),
[heap-dump semantics](https://docs.oracle.com/en/java/javase/25/docs/specs/man/jcmd.html).
'''
new(E / 'm1-cache-cost-audit.md', audit.encode())

def edit(path, old, replacement):
    s = path.read_text(encoding='utf-8')
    assert old in s
    path.write_text(s.replace(old, replacement), encoding='utf-8', newline='\n')

plan = W / 'bazel/docs/jsgette/plans/shared-evaluation-gates-2026-11-07.md'
edit(plan, 'M0 complete — continue on repeated retained state; bounded M1 investigation in progress. No later gate has passed.',
     'M0 complete; M1 inconclusive after the bounded investigation. Stopped before M2 for an architectural pivot. No later gate has passed.')
edit(plan, '| M1: dependency-trace replay | In progress | Hosting and all twenty N1 trace controls complete; D034 records material trace overhead and unresolved semantic/CPU/memory coverage. The savings ceiling is still required before a graph rewrite |',
     '| M1: dependency-trace replay | Inconclusive — pivot required | All N1 controls and the bounded residual audit assessed; neither CPU nor memory savings ceiling established. D037 stops this route before M2 |')
edit(plan, '## M2: Implement immutable snapshots and explicit query inputs',
     '**Recorded outcome (2026-11-12):** the [final M1 assessment](../measurements/shared-evaluation-2026-11-07/m1-gate-assessment.md) is inconclusive. The one bounded residual investigation did not resolve either CPU or memory savings. Stop the current trace route and retain the useful native-hosting component. A completed immutable result-bundle experiment is proposed as a new architectural direction, not started and not an M2 pass.\n\n## M2: Implement immutable snapshots and explicit query inputs')
readme = E / 'README.md'
edit(readme, '## Current state\n', '## Current state\n\n**Stopped at the planned pivot: [M1 is inconclusive](m1-gate-assessment.md).** The final bounded residual audit leaves CPU and memory savings unresolved. M2–M6 were not started; evidence and the task runtime image are preserved.\n')
edit(readme, '**M1 in progress; preliminary N10 hosting diagnostic complete.**', '**M1 inconclusive; preliminary N10 hosting diagnostic complete.**')
edit(readme, 'D028 continues to typed-trace qualification without more native warm/variant runs or heap tuning.', 'D028 continued to typed-trace qualification without further native warm/variant runs or heap tuning.')

with (E / 'decision-log.md').open('a', encoding='utf-8', newline='\n') as f:
    f.write('''\n\n## D037 — M1 inconclusive: stop the trace route and present a pivot (2026-11-12)

The [final assessment](m1-gate-assessment.md) closes the one bounded residual investigation. All ten restarted sidecar/BEP evidence sets verified; each records 3,272 configured-target hits, 114 misses and 59,136 successful CAS gets. Three focused sidecar tests passed, followed by root review. These counts and overlapping elapsed histograms do not partition CPU or retained memory. Source review identifies synchronous validation, asynchronous decoding and finalization boundaries without inventing a cost split.

The D036 decoder-only second attempt also stopped before launch: the unchanged reset helper encountered an unrelated sandbox mount namespace without /proc/self/mountinfo. Both attempts are preserved; neither created a guard scope or decoder output. Per the frozen addendum no third attempt or new capture was made. The prepared full-stack analyzer passed seventeen synthetic tests and independent static review, but no real full-stack analysis ran. Missing deeper stacks do not prove architectural failure and would not by themselves establish semantic equivalence or PSS savings.

**Gate decision: M1 inconclusive — pivot required.** Unsupported CPU/state remains potentially removable; no optimistic impossibility bound or supported continuation headroom exists for both gates. Do not use cheap typed leaves as a whole-architecture ceiling. Do not infer a validation-only pivot from cache counters. M2–M6 remain not started, and the frozen 50% memory/30% CPU/10% latency goals are unchanged.

Retain qualified native hosting as a useful private-graph component. Recommend a separate future experiment with a completed immutable result bundle/dependency closure, explicit snapshot and repository bindings, output views independent of reusable state, and fully charged first attachment. This replaces the current trace route only through a new recorded architectural decision; no such implementation is claimed. Keep phase-barrier removal separate.

Task-only cleanup verified absent residual guard scopes, unmounted the task image from every observed namespace containing it, detached its exact loop device and stopped its PID/start-tick-verified keeper. The 512-GiB image and all evidence remain preserved; unrelated processes were not stopped. The [residual packet](evidence/m1-residual-investigation/) records the cleanup and the distinction between executed sidecar analysis and unexecuted decoder preparation. Stop here under the user's milestone-pivot rule.
''')

owner = E / 'evidence/auxiliary/active-owner.json'
failed = E / 'evidence/auxiliary/m1-residual-decode-02'
control = json.loads((failed / 'control.json').read_text())
assert control == json.loads(owner.read_text()) and control['run_id'] == 'm1-residual-decode-02'
assert control['status'] == 'stopped_for_assessment' and not (failed / 'guard').exists()
close = json.loads((W / 'residual-runtime-close.json').read_text())
assert close['status'] == 'closed_task_runtime_image_preserved' and close['keeper_absent']
assert not (W / 'm1-residual-analysis-01').exists()
new(failed / 'retired-owner-after-assessment.json', owner.read_bytes())
owner.unlink()
print(json.dumps({'milestone': 'M1', 'verdict': 'inconclusive_pivot_required', 'sidecar_rows': 10, 'actual_deep_decodes': 0}))
