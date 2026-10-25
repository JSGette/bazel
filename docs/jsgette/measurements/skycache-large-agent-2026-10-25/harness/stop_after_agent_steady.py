import json, os, signal, time
from pathlib import Path
root = Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent-steady')
matches = []
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit():
        continue
    try:
        args = (proc / 'cmdline').read_bytes().split(b'\0')
        if any(arg.endswith(b'/benchmark_agent_remote_steady.py') for arg in args) and str(root).encode() in args:
            matches.append(int(proc.name))
    except OSError:
        pass
assert len(matches) == 1, matches
pid = matches[0]
print('Waiting for both validated output-policy pilots; runner pid=' + str(pid), flush=True)
for attempt in range(18000):
    state = json.loads((root / 'results.json').read_text())
    if state.get('status') == 'failed':
        print('Runner ended at validation failure; no interruption sent', flush=True)
        break
    if state.get('pilot', {}).get('both_policies_passed'):
        command = Path(f'/proc/{pid}/cmdline').read_bytes()
        assert b'benchmark_agent_remote_steady.py' in command and str(root).encode() in command
        (root / 'pilot-stop-reason.txt').write_text(
            'Deliberate interruption after both complete three-arm output-policy pilots passed. '
            'The first-use remote read took 599 seconds, so report one controlled sample per '
            'mode/policy rather than repeating a known pathological remote read six more times. '
            'All published timings are individual pilot/preparation observations, not medians. '
            'No product failure implied. Reproduce the completed scope with --pilot-only.\n')
        os.kill(pid, signal.SIGINT)
        print('Stopped expansion after both output-policy pilots', flush=True)
        break
    time.sleep(0.2)
else:
    raise RuntimeError('Pilot did not finish within one-hour monitor window')
