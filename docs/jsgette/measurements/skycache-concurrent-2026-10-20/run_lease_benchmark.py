from pathlib import Path
s=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-19/chec/work/run_traffic_benchmark.py').read_text()
s=s.replace('traffic-stats-','lease-protocol-')
s=s.replace('admission-validation-fixture.json','lease-protocol-fixture.json')
s=s.replace('Measure overhead of logical traffic statistics with the unchanged 512-root combined admission/validation benchmark.',
    'Measure lease/pin overhead using identical immutable data in isolated legacy and lease-aware layouts.')
exec(compile(s,'run_lease_benchmark.py','exec'))
