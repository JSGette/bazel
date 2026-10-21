from pathlib import Path
import fcntl
import hashlib
import json
import statistics
import subprocess
import sys

mode = sys.argv[1]
assert mode in ('baseline','finish')
work = Path('/home/jgett/REPOS/.work/skycache-concurrent')
repo = Path('/home/jgett/REPOS/bazel-skycache-concurrent')
fixture = json.loads((work/'binding-first-fixture.json').read_text())
args = [fixture[k] for k in ('cache','workspace','output_base','sampled_roots','environment_file_local_only')]
assert hashlib.sha256(Path(args[3]).read_bytes()).hexdigest()==fixture['sampled_roots_sha256']
assert hashlib.sha256(Path(args[4]).read_bytes()).hexdigest()==fixture['environment_file_sha256']
metadata = {v:json.loads((work/('binding-first-'+v+'-binary.json')).read_text())
            for v in (('before',) if mode=='baseline' else ('before','after'))}
for m in metadata.values():
    assert hashlib.sha256(Path(m['path']).read_bytes()).hexdigest()==m['sha256']
result_file = work/'binding-first-paired.json'
if mode=='baseline':
    assert not result_file.exists()
    result = {'purpose':'Mixed old/current tool-binding publication admission plus validation of 512 current roots.',
              'scope':'Includes admission/index opening, excludes snapshot creation/final revalidation. Current-thread Java allocation; no peak-memory or whole-build claim.',
              'warmups_per_jvm':2,'measured_rounds_per_jvm':5,'runs':[]}
    schedule = [(0,'before')]
else:
    result = json.loads(result_file.read_text())
    assert len(result['runs'])==1 and not result.get('passed')
    assert metadata['before']['benchmark_source_sha256']==metadata['after']['benchmark_source_sha256']
    schedule = [(0,'after'),(1,'after'),(1,'before'),(2,'before'),(2,'after')]
result['binaries'] = {v:{k:x for k,x in m.items() if k!='diff'} for v,m in metadata.items()}
java = '/home/jgett/REPOS/.work/skyframe-go/work/build-output/external/rules_java++toolchains+remotejdk25_linux/bin/java'

def inventory():
    root = Path(fixture['cache'])
    return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob('*.manifest'))}

with open('/home/jgett/REPOS/.work/skyframe-measurements.lock','a') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX)
    for pair,variant in schedule:
        assert inventory()==fixture['publication_inventory']
        argv = [java,'-Xms256m','-Xmx2g','-jar',metadata[variant]['path'],*args]
        completed = subprocess.run(argv,cwd=repo,capture_output=True,text=True,timeout=600)
        (work/f'binding-first-{pair}-{variant}.log').write_text(completed.stdout+'\n'+completed.stderr)
        if completed.returncode:
            print(completed.stderr[-2000:],flush=True)
        completed.check_returncode()
        row = json.loads(completed.stdout)
        assert row['sampled_roots']==512 and len(row['rounds'])==5
        records = result['runs'][0]['rounds'][0]['fresh_stats']['records_read'] if result['runs'] else row['rounds'][0]['fresh_stats']['records_read']
        for r in row['rounds']:
            assert r['fresh_stats']['records_read']==records and r['fresh_stats']['rejected_roots']==0
            assert r['store_stats']['shared_reads']>0 and r['store_stats']['writes']==0
            assert r['store_stats']['rejected_publications']==4
            assert r['store_stats']['peak_candidate_count']==6 and r['store_stats']['read_limit_skips']==0
            assert r['store_stats']['cas_get_requests']==records and r['store_stats']['cas_get_hits']==records
        assert inventory()==fixture['publication_inventory']
        result['runs'].append({'pair':pair,'mode':variant,'argv':argv,**row})
        result_file.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'pair':pair,'mode':variant,'fresh_median_ms':statistics.median(r['fresh_ms'] for r in row['rounds']),
                          'records':records}),flush=True)
    if mode=='finish':
        result['medians'] = {v:{metric:statistics.median(r[metric] for run in result['runs'] if run['mode']==v for r in run['rounds'])
            for metric in ('fresh_ms','fresh_allocated_bytes','memo_ms','memo_allocated_bytes')} for v in ('before','after')}
        result['passed'] = True
        result_file.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result['medians'],indent=2),flush=True)
