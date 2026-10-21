"""Seed the new binary explicitly, then exercise a mixed cache with two independent servers."""
from pathlib import Path
s = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-19/chec/work/recheck_traffic_datadog.py').read_text()
s = s.replace("saved = work/'traffic-stats-datadog-recheck'", "saved = work/'binding-first-datadog-recheck'")
s = s.replace('mapping-traffic-v2-validator', 'mapping-binding-validator')
s = s.replace("'purpose':'Large-workload traffic and invalidation correctness; not a fresh-cache or controlled timing comparison.'",
    "'purpose':'Mixed obsolete/current publication correctness and invalidation deadlines; not a controlled whole-build timing comparison.'")
s = s.replace("for phase in ('baseline-readers','independent-edits','warm-revert'):",
'''for phase in ('seed-writers','baseline-readers','independent-edits','warm-revert'):
            if phase == 'baseline-readers':
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(shutdown,('a','b')))''')
s = s.replace("                assert row['native_read']['requests']>0 and float(row['native_read']['formatted_bytes'].split()[0])>0,row",
'''                if phase != 'seed-writers':
                    assert row['native_read']['requests']>0 and float(row['native_read']['formatted_bytes'].split()[0])>0,row''')
s = s.replace("                else:\n                    assert row['server_pid']==expected_pids[name],row",
'''                elif phase != 'seed-writers':
                    assert row['server_pid']==expected_pids[name],row''')
s = s.replace("'resources':'Two 4 GiB heaps", "'seed_protocol':'Two explicit new-binary writers followed by server shutdown and fresh baseline readers. Keep every pre-existing publication unchanged.',\n    'resources':'Two 4 GiB heaps")
exec(compile(s, 'recheck_binding_datadog.py', 'exec'))
