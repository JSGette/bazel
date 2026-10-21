from pathlib import Path
source = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-19/chec/work/recheck_traffic_datadog.py').read_text()
source = source.replace("saved = work/'traffic-stats-datadog-recheck'", "saved = work/'traffic-stats-datadog-recheck-v2'")
source = source.replace("'reused_fixture':str(root),", "'reused_fixture':str(root), 'seed_attempt':'traffic-stats-datadog-recheck (two successful current-binary writers; reader expectation failed)',")
exec(compile(source,'recheck_traffic_datadog_v2.py','exec'))
