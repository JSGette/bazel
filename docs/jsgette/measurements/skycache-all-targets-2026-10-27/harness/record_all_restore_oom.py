from skycache_all_common import *
import shutil

tag='local-matrix-512'
d=json.loads((ROOT/(tag+'-results.json')).read_text());r=d['runs'][-1]
assert d['status']=='failed' and r['exit_code']==33
bep=ROOT/(r['label']+'.bep.json')
aborts=[]
for line in bep.read_text().splitlines():
    e=json.loads(line)
    if 'aborted' in e:aborts.append(e['aborted'])
assert any(e.get('reason')=='OUT_OF_MEMORY' for e in aborts)
save(ROOT/'local-restore-oom.json',dict(tag=tag,label=r['label'],exit_code=r['exit_code'],
    wall_s=r['wall_s'],java_heap_gib=10,wsl_limit_gib=20,metadata_budget_mib=512,
    aborted_events=aborts,swap_pages_delta=r['swap_pages_delta'],
    next_cohort_java_heap_gib=14,primary_samples_completed=0))
snapshot=ROOT/'metadata-512-heap10-attempt-harness';snapshot.mkdir()
for name in ['skycache_all_common.py','skycache_all_benchmark.py','run_all_local_512.py']:
    shutil.copy2(Path(__file__).parent/name,snapshot/name)
for name in ['server/jvm.out','jvm.out']:
    p=OUT/name
    if p.exists() and p.is_file():
        with p.open('rb') as f:
            f.seek(max(0,p.stat().st_size-256*1024))
            (ROOT/'local-restore-oom-jvm-tail.log').write_bytes(f.read())
print('Recorded 10 GiB restore OOM and preserved the harness.')
