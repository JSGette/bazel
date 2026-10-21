from pathlib import Path
s = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-19/chec/work/run_traffic_benchmark.py').read_text()
s = s.replace('traffic-stats-', 'binding-first-control-')
s = s.replace("'binding-first-control-'+v+'-binary.json'", "'binding-first-'+v+'-binary.json'")
s = s.replace('Measure overhead of logical traffic statistics with the unchanged 512-root combined admission/validation benchmark.',
    'Check binding-first admission overhead when all four publications have compatible bindings.')
exec(compile(s, 'run_binding_control.py', 'exec'))
