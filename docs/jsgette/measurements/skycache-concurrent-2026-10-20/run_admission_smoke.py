from pathlib import Path
source = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-19/chec/work/run_mapping_smoke.py').read_text()
source = source.replace('mapping-validator', 'mapping-admission-v2-validator')
source = source.replace('mapping-smoke', 'admission-smoke')
source = source.replace('with shared repository mapping codec', 'with bounded reuse of successful admission witnesses')
exec(compile(source, 'run_admission_smoke.py', 'exec'))
