"""Preserve decoder admission failure and create a smaller decoder-only guard."""
import hashlib
import json
from pathlib import Path

W = Path(__file__).resolve().parent
E = W / 'bazel/docs/jsgette/measurements/shared-evaluation-2026-11-07'
failed = E / 'evidence/auxiliary/m1-residual-decode-01'
owner = E / 'evidence/auxiliary/active-owner.json'
control = json.loads((failed / 'control.json').read_text())
preflight = json.loads((failed / 'host-budget-preflight.json').read_text())
assert control == json.loads(owner.read_text())
assert control['run_id'] == 'm1-residual-decode-01' and control['status'] == 'stopped_for_assessment'
assert control['error'] == 'Host budget preflight timed out; workload not launched.'
assert preflight['status'] == 'timeout' and not (failed / 'guard').exists()
assert not (W / 'm1-residual-analysis-01').exists()
assert json.loads((failed / 'task-reset.json').read_text())['status'] == 'completed'
with (failed / 'retired-owner-after-assessment.json').open('xb') as f:
    f.write(owner.read_bytes())
owner.unlink()

source = E / 'harness/run_auxiliary.ps1'
raw = source.read_text()
replacements = {
    'memory_max_mib=10240': ('memory_max_mib=1536', 3),
    'minimum_physical_bytes=14495514624L': ('minimum_physical_bytes=5368709120L', 1),
    'minimum_commit_bytes=15569256448L': ('minimum_commit_bytes=6442450944L', 1),
    '-MemoryMaxMiB 10240': ('-MemoryMaxMiB 1536', 1),
}
for before, (after, count) in replacements.items():
    assert raw.count(before) == count, before
    raw = raw.replace(before, after)
anchor = "if ($DryRun) {"
assert raw.count(anchor) == 1
raw = raw.replace(anchor, "if ($RunId -ne 'm1-residual-decode-02' -or $commandHash -ne 'e42fa300b69b3f899efb1c249f7ea0e48f3ce4b815902ba6a4621ebb4060077d') { throw 'Only the fixed archived-recording decoder is permitted.' }\n" + anchor)
destination = E / 'harness/run_residual_decode.ps1'
with destination.open('x', encoding='utf-8', newline='\n') as f:
    f.write(raw)
record = {'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
          'wrapper_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
          'changes': replacements, 'scope_mib': 1536, 'decoder_heap_mib': 512,
          'physical_reserve_mib': 3072, 'commit_reserve_mib': 4096,
          'margin_bytes': 536870912, 'failed_run': control['run_id'],
          'failed_workload_launched': False, 'next_run': 'm1-residual-decode-02'}
with (W / 'residual-budget-preparation.json').open('x') as f:
    json.dump(record, f, indent=2)
    f.write('\n')
print(json.dumps(record))
