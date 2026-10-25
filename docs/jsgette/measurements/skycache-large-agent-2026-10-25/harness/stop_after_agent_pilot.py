import json, os, signal, time
from pathlib import Path
root = Path('/home/jgett/REPOS/.work/skycache-large-remote-2026-10-25/agent')
pid = 3769
for attempt in range(1800):
    state = json.loads((root / 'results.json').read_text())
    if state.get('status') == 'failed':
        print('Runner ended at validation failure; no interruption sent', flush=True)
        break
    if state.get('pilot', {}).get('passed'):
        command = Path(f'/proc/{pid}/cmdline').read_bytes()
        assert b'benchmark_agent_remote.py' in command and str(root).encode() in command
        (root / 'pilot-stop-reason.txt').write_text(
            'Deliberate interruption after completed first-reuse pilot. Initial seed needs a '
            'second preparation publication before steady-state measurements. Preserve pilot '
            'evidence; build a separate stabilized comparison. No product failure implied.\n')
        os.kill(pid, signal.SIGINT)
        print('Stopped expansion after completed pilot', flush=True)
        break
    time.sleep(1)
else:
    raise RuntimeError('Pilot did not finish within monitor window')
