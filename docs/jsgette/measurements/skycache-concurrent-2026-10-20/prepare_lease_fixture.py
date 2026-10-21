from pathlib import Path
import hashlib
import json
import os

work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
original=json.loads((work/'admission-validation-fixture.json').read_text())
source=Path(original['cache'])
destination=work/'lease-protocol-fixture'
assert not destination.exists()
assert {str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source.rglob('*.manifest'))} == original['publication_inventory']
destination.mkdir()
count=0
for relative in ('objects/packed-v1','publications-v3'):
    origin=source/relative
    for top in (destination,destination/'leased-v1'):
        (top/relative).mkdir(parents=True)
        for path in sorted(origin.rglob('*')):
            assert not path.is_symlink(),path
            target=top/relative/path.relative_to(origin)
            if path.is_dir():
                target.mkdir()
            else:
                assert path.is_file()
                os.link(path,target)
                assert path.stat().st_ino==target.stat().st_ino
                count+=1
result=dict(original)
result.update({'cache':str(destination),'original_immutable_cache':str(source),
    'protocol':'Identical immutable packs/manifests hardlinked into legacy and lease-aware layouts. Source and content unchanged. No collection is run on this fixture.',
    'hardlinks_created':count,
    'publication_inventory':{str(p.relative_to(destination)):hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted(destination.rglob('*.manifest'))}})
assert len(result['publication_inventory'])==8
assert {str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source.rglob('*.manifest'))} == original['publication_inventory']
(work/'lease-protocol-fixture.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'hardlinks_created':count,'manifests':8,'new_data_bytes':0}))
