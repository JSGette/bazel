from pathlib import Path
import hashlib
import json
import shutil
import sys

mode = sys.argv[1]
work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
helpers = Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-19/chec/work')
if mode == 'before':
    original = json.loads((work/'admission-validation-after-binary.json').read_text())
    source = Path(original['path'])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original['sha256']
    destination = work/'traffic-stats-before.jar'
    assert not destination.exists()
    shutil.copy2(source,destination)
    original.update({'mode':'before','path':str(destination),
        'baseline_provenance':'Exact immutable Step35 after jar, Cache&Carry built before traffic implementation. Step36 changes Python probe only.',
        'product_commit':'83e8c0f84e0266a3cfb6730dbf741e292799e2e3'})
    original.pop('diff')
    (work/'traffic-stats-before-binary.json').write_text(json.dumps(original,indent=2)+'\n')
    print('Preserved baseline jar',original['sha256'])
elif mode == 'after':
    source = (helpers/'prepare_admission_validation_benchmark.py').read_text()
    source = source.replace("'admission-validation-'", "'traffic-stats-'")
    exec(compile(source,'prepare_traffic_benchmark.py','exec'))
else:
    raise ValueError(mode)
