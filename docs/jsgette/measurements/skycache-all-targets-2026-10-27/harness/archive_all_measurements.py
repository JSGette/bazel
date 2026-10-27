"""Archive compact, reviewable full-graph evidence after all measurements stop."""
from skycache_all_common import *
import copy
import gzip
import shutil

DEST=SOURCE/'docs/jsgette/measurements/skycache-all-targets-2026-10-27'
WORK=Path(__file__).parent

def compressed(path,data):
    path.write_bytes(gzip.compress(json.dumps(data,indent=2).encode()+b'\n',mtime=0))

def sanitized_result(data):
    d=copy.deepcopy(data)
    for name in ['source','fixture']:
        if name in d:
            diff=d[name].pop('diff','')
            d[name]['diff_sha256']=hashlib.sha256(diff.encode()).hexdigest()
    for row in d.get('runs',[]):
        if 'successful_targets' in row:
            row['successful_targets_count']=len(row.pop('successful_targets'))
        if 'argv' in row:
            row['argv']=[a.split('=',1)[0]+'=<redacted>'
                if any(word in a.lower() for word in ['password','token=','authorization','api-key','api_key','remote_header=','bes_header='])
                else a for a in row['argv']]
    return d

def main():
    pipeline=json.loads((ROOT/'pipeline-status.json').read_text());assert pipeline['status']=='passed'
    final_tag=pipeline['cohort']
    matrix=json.loads((ROOT/(final_tag+'-results.json')).read_text())
    assert matrix['status']=='passed'
    assert all(matrix[k] for k in ['source_unchanged','fixture_unchanged','binary_hashes_unchanged'])
    assert not (OUT/'server/server.pid.txt').exists()
    comparison=json.loads((ROOT/'comparison.json').read_text())
    assert comparison['source_results_sha256']==sha(ROOT/(final_tag+'-results.json'))
    assert all(v['n']==3 for v in comparison['summary'].values())
    DEST.mkdir(exist_ok=False)
    for name in ['comparison.json','remote-availability.json','public-workload.json','local-seed-provenance.json',
                 'wsl-memory-change.json','clock-calibration-2.json','default-local-availability.json',
                 'local-admission-budget.json','local-seed-witness-check-v3.json','local-warmup-thread-dump.json',
                 'local-restore-oom.json','local-restore-input-failure.json']:
        if (ROOT/name).exists():shutil.copy2(ROOT/name,DEST/name)
    evidence=[];preparation=[]
    for path in sorted(ROOT.glob('*-results.json')):
        data=json.loads(path.read_text())
        if not data.get('runs'):continue
        compressed(DEST/(path.name+'.gz'),sanitized_result(data))
        evidence.append(dict(path=str(path),bytes=path.stat().st_size,sha256=sha(path)))
        for r in data['runs']:
            if r.get('preparation'):
                preparation.append(dict(tag=data['tag'],status=data['status'],failure=data.get('failure'),
                    **{k:r.get(k) for k in ['label','exit_code','wall_s','failure_kind','cache_errors','remote_committed',
                        'outputs_match','targets_match','cache_storage','configured_targets']}))
            for suffix in ['.profile.gz','.bep.json','-outputs.json']:
                p=ROOT/(r['label']+suffix)
                if p.exists():evidence.append(dict(path=str(p),bytes=p.stat().st_size,sha256=sha(p)))
            if path.name in [final_tag+'-results.json','local-matrix-results.json','local-matrix-512-results.json',
                             'local-matrix-512-heap14-results.json','local-prime-results.json','cache-prime-5-results.json']:
                for suffix in ['.log','-timeline.jsonl','-empty-graph.log','-final-graph.log']:
                    p=ROOT/(r['label']+suffix)
                    if p.exists():(DEST/(p.name+'.gz')).write_bytes(gzip.compress(p.read_bytes(),mtime=0))
    save(DEST/'preparation-attempts.json',preparation)
    for name in ['expected-outputs.json','expected-targets.json']:
        (DEST/(name+'.gz')).write_bytes(gzip.compress((ROOT/name).read_bytes(),mtime=0))
    for folder,names in [('compat',['provenance.json','go-sdk-provenance.json']),
                         ('tools',['provenance.json','autoconf-system.json','foreign-tools-system.json'])]:
        (DEST/folder).mkdir()
        for name in names:
            if (ROOT/folder/name).exists():shutil.copy2(ROOT/folder/name,DEST/folder/name)
    (DEST/'harness').mkdir()
    names=['skycache_all_common.py','skycache_all_benchmark.py','retention_cold_pages.py',
           'run_all_local_measurements.py','run_all_local_512.py','run_all_with_priming.py','trim_all_priming_heap.py',
           'analyze_all_measurements.py','archive_all_measurements.py','write_all_baseline_report.py','record_all_remote_limit.py',
           'record_all_memory.py','monitor_all_host_memory.ps1','inspect_all_admission_budget.py',
           'inspect_all_witnesses.py','inspect_all_restore_threads.py','inspect_all_profiles.py',
           'record_all_restore_oom.py','stop_failed_all_restore.py','inspect_all_java_log.py']
    for name in names:shutil.copy2(WORK/name,DEST/'harness'/name)
    shutil.copytree(ROOT/'remote-attempt-harness',DEST/'remote-attempt-harness')
    shutil.copytree(ROOT/'metadata-256-attempt-harness',DEST/'metadata-256-attempt-harness')
    shutil.copytree(ROOT/'metadata-512-heap10-attempt-harness',DEST/'metadata-512-heap10-attempt-harness')
    (DEST/'local-warmup-thread-dump.txt.gz').write_bytes(gzip.compress((ROOT/'local-warmup-thread-dump.txt').read_bytes(),mtime=0))
    for p in sorted(ROOT.glob('java-log-snapshot-*.txt')):
        (DEST/(p.name+'.gz')).write_bytes(gzip.compress(p.read_bytes(),mtime=0))
    for name in ['local-matrix-512-warmup-thread-dump.txt','local-restore-oom-jvm-tail.log']:
        if (ROOT/name).exists():(DEST/(name+'.gz')).write_bytes(gzip.compress((ROOT/name).read_bytes(),mtime=0))
    if (WORK/'all-host-memory.jsonl').exists():
        shutil.copy2(WORK/'all-host-memory.jsonl',DEST/'host-memory.jsonl')
    save(DEST/'provenance.json',dict(native_root=str(ROOT),source_head=matrix['source']['head'].strip(),
        raw_results_sha256=sha(ROOT/(final_tag+'-results.json')),recordings=evidence,
        notes=['Raw recordings and all cache data remain at the native root; no cache blobs or build outputs are committed.',
               'Archived result copies omit the large fixture diff and repeated successful-target lists. Original file hashes are recorded.',
               'Sensitive-looking command-line arguments are redacted. Inherited environment values are never archived.',
               'Remote warm/cold were not measured: the full workload exceeded the current maximum transfer budget during seeding.',
               'A new isolated disk action cache and read-only external action cache were used for every primary arm after the external server rejected uploads.',
               'The full local publication needs 313868680 bytes of admission metadata. The default 256 MiB budget produced no cache reuse; the final local arms use the existing 512 MiB option.',
               'The WSL memory limit was raised to 20 GiB before any primary sample. A 10 GiB cache restore failed with JVM OOM; the final heap limit is recorded in every primary command.']))
    print(DEST)

if __name__=='__main__':main()
