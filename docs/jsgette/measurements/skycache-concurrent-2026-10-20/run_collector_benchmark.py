from pathlib import Path
import fcntl
import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time

repo=Path('/home/jgett/REPOS/bazel-skycache-concurrent')
work=Path('/home/jgett/REPOS/.work/skycache-concurrent')
sys.path.insert(0,str(repo/'tools/skycache'))
from storage_preflight import check_storage

original=json.loads((work/'admission-validation-fixture.json').read_text())
source=Path(original['cache'])
root=work/'collector-measurement-review'
assert not root.exists()
storage=check_storage([root])
root.mkdir()
jar=Path('/mnt/c/Users/jgett/Documents/Codex/2026-10-19/chec/outputs/binaries/local-cache-collection.jar')
assert not jar.exists()
shutil.copy2(repo/'bazel-bin/src/test/java/com/google/devtools/build/lib/skyframe/serialization/local/LocalCacheCollectionBenchmark_deploy.jar',jar)
jar.chmod(0o444)
def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()
inventory={}
for subdir in ('objects/packed-v1','publications-v3'):
    for p in sorted((source/subdir).rglob('*')):
        assert not p.is_symlink()
        if p.is_file(): inventory[str(p.relative_to(source))]={'size':p.stat().st_size,'sha256':sha(p)}
assert {n:v['sha256'] for n,v in inventory.items() if n.endswith('.manifest')}==original['publication_inventory']
ids=[Path(n).stem for n in inventory if n.endswith('.manifest')]
assert len(ids)==4 and len(inventory)==12
java='/home/jgett/REPOS/.tools/skycache-benchmark-jdk25/bin/java'
result={'purpose':'Bounded collector scan, coordination and unlink costs on real Datadog publication metadata.',
        'scope':'Fresh JVM per run; timings reported inside collector exclude JVM startup. No cold-SSD, build speedup or physical disk-space recovery claim.',
        'fixture':'Separate disposable hardlinks to the preserved four-publication Datadog fixture. No source files modified; no GC on original or lease-protocol fixture.',
        'binary':str(jar),'binary_sha256':sha(jar),'source_inventory':inventory,'initial_storage':storage,
        'new_pack_data_bytes':0,'runs':[]}
out=root/'results.json'
with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    for repeat in range(3):
        for mode in ('retain','pinned','evict'):
            dest=root/f'{repeat}-{mode}'
            for name,entry in inventory.items():
                p=source/name
                q=dest/'leased-v1'/name
                q.parent.mkdir(parents=True,exist_ok=True)
                os.link(p,q)
                assert q.stat().st_ino==p.stat().st_ino
            argv=[java,'-Xms64m','-Xmx256m','-jar',str(jar),str(dest),str(2**63-1 if mode=='retain' else 1),'0']
            if mode=='pinned': argv+=ids
            started=time.monotonic()
            run=subprocess.run(argv,cwd=repo,text=True,capture_output=True,timeout=120)
            (root/f'{repeat}-{mode}.log').write_text(run.stdout+'\n'+run.stderr)
            run.check_returncode()
            stats=json.loads(run.stdout)
            assert stats['status']=='complete' and stats['publications']==4
            assert stats['known_bytes_before']==sum(v['size'] for v in inventory.values())
            assert stats['manifest_scan_bytes']==sum(v['size'] for n,v in inventory.items() if n.endswith('.manifest'))
            assert stats['deleted_packs']==(4 if mode=='evict' else 0)
            assert stats['deleted_publications']==(4 if mode=='evict' else 0)
            assert stats['active_slots']==(len(set(i[:3] for i in ids)) if mode=='pinned' else 0)
            remaining={str(p.relative_to(dest/'leased-v1')):sha(p) for p in (dest/'leased-v1').rglob('*.manifest')}
            assert remaining==({} if mode=='evict' else original['publication_inventory'])
            result['runs'].append({'repeat':repeat,'mode':mode,'argv':argv,'process_seconds':time.monotonic()-started,**stats})
            out.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'repeat':repeat,'mode':mode,**stats}),flush=True)
    assert all(sha(source/name)==entry['sha256'] for name,entry in inventory.items())
    result['source_hashes_preserved']=True
    result['medians']={mode:{key:statistics.median(r[key] for r in result['runs'] if r['mode']==mode)
                            for key in ('elapsed_ms','catalog_ms','deleted_logical_bytes')}
                       for mode in ('retain','pinned','evict')}
    result['passed']=True
    result['final_storage']=check_storage([root])
    out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['medians'],indent=2),flush=True)
